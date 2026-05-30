"""FastAPI bridge between the React UI and the rest of the system.

Responsibilities:
  - Reads from BigQuery (rubric trend, this-week summary, drift, attribution).
  - Reads / writes MongoDB (experiments, skills, queue items, approvals,
    customer voice, negative examples).
  - Calls the deployed A2A agents (Research → Content → Review pipeline, CMO
    Planner) for live drafting and weekly memo generation.
  - Calls the edit_capture_handler when the founder decides on a draft so the
    same learning loop fires whether decisions come from the Sheet or the UI.

Endpoint design is product-first (the UI shapes the call), not table-first.

Code layout: this module owns the FastAPI app instance, GCP client globals
(BQ, _sm), and a few cross-cutting helpers (_secret_optional). Every
endpoint lives in a per-domain router under ``routers/``. Routers import
``BQ`` / ``PROJECT_ID`` / ``bigquery`` from here as a stable namespace so
LOCAL_DEV mode (BQ=None) propagates uniformly across all routers.
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# google.cloud.bigquery + secretmanager are imported lazily below — in
# LOCAL_DEV=1 mode the laptop doesn't need google-cloud-bigquery /
# google-cloud-secret-manager installed at all. The references inside
# BQ-touching endpoints are dead code there (each starts with
# `if BQ is None: return ...`).
bigquery = None       # type: ignore[assignment]
secretmanager = None  # type: ignore[assignment]

from shared import mongo_tools  # noqa: E402

mongo_tools.use_secret("mongo_uri_writer")

app = FastAPI(title="Hindsight Guild — Web API", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve generated images locally. In LOCAL_DEV, shared/imagen.py writes PNGs
# to drafts/media/<telemetry_id>/<id>.png and returns /media/... URLs the UI
# can fetch. Browsers refuse file:/// from an http:// origin, so this mount
# is what bridges the gap. Cloud deployments serve via GCS public URLs
# instead — this mount is harmless if the directory doesn't exist yet.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_LOCAL_MEDIA_DIR = Path(
    os.environ.get("MEDIA_DIR_LOCAL", str(_REPO_ROOT / "drafts" / "media"))
)
_LOCAL_MEDIA_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=str(_LOCAL_MEDIA_DIR)), name="media")

PROJECT_ID = os.environ.get("PROJECT_ID", "hindsight-guild-mvp")
REGION = os.environ.get("REGION", "us-central1")

# LOCAL_DEV=1 lets the API boot without GCP credentials. BigQuery + Secret
# Manager clients are not initialized; endpoints that need them return
# empty defaults instead of 500ing. Mongo-backed endpoints (queue,
# capabilities, skills, weekly-review) work end-to-end against a local
# Mongo started via docker compose.
LOCAL_DEV = os.environ.get("LOCAL_DEV", "").lower() in ("1", "true", "yes")

BQ: Any | None
_sm: Any | None
if LOCAL_DEV:
    BQ = None
    _sm = None
else:
    # Only reach for GCP deps when not in local-dev. Lets the laptop run
    # without google-cloud-bigquery / google-cloud-secret-manager installed.
    from google.cloud import bigquery as _bq_mod  # noqa: E402
    from google.cloud import secretmanager as _sm_mod  # noqa: E402
    bigquery = _bq_mod      # re-export so endpoints below can use it
    secretmanager = _sm_mod
    BQ = bigquery.Client(project=PROJECT_ID)
    _sm = secretmanager.SecretManagerServiceClient()

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers shared by multiple routers.
# ---------------------------------------------------------------------------

def _secret_optional(name: str) -> str | None:
    """Read a Secret Manager secret; return None on any failure. Used for URLs
    that might not exist yet during local dev."""
    try:
        full = f"projects/{PROJECT_ID}/secrets/{name}/versions/latest"
        val = _sm.access_secret_version(name=full).payload.data.decode()
        if val and val != "PENDING":
            return val
    except Exception:
        return None
    return None


# ---------------------------------------------------------------------------
# Per-domain routers. Each module owns the endpoints for one domain and
# imports cross-cutting state (BQ, PROJECT_ID, _secret_optional) from this
# module. FastAPI matches on path so registration order doesn't affect
# routing — keep these grouped by mental domain for readability.
# ---------------------------------------------------------------------------

from services.web_api.routers import (  # noqa: E402
    agents,
    capabilities,
    drafting,
    experiments,
    health,
    integrations,
    live,
    queue,
    rubric_trends,
    self_critique,
    signals,
    skills,
    voice,
    weekly_review,
)

app.include_router(health.router)
app.include_router(queue.router)
app.include_router(integrations.router)
app.include_router(experiments.router)
app.include_router(skills.router)
app.include_router(self_critique.router)
app.include_router(signals.router)
app.include_router(rubric_trends.router)
app.include_router(voice.router)
app.include_router(drafting.router)
app.include_router(weekly_review.router)
app.include_router(capabilities.router)
app.include_router(agents.router)
app.include_router(live.router)


# ---------------------------------------------------------------------------
# Startup hook for the live-broadcaster background task. Kept on the app
# (not the router) because @router.on_event isn't a stable FastAPI surface.
# The broadcaster + WS endpoint live in routers/drafting.py; we just wire
# the asyncio loop in at startup.
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def _start_live_broadcaster() -> None:
    loop = asyncio.get_event_loop()
    drafting._live_broadcaster.attach_loop(loop)
    # Fire-and-forget background task; FastAPI cancels it on shutdown.
    loop.create_task(drafting._live_broadcaster.run())
