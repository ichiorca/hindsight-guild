"""Admin endpoints — protected one-shot operations (data seeding, etc.).

Guarded by the ADMIN_SEED_TOKEN secret so they can't be triggered by the
public internet (the web-api is allUsers-invocable for the UI). With no token
configured the endpoints refuse to run.
"""
from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Header, HTTPException

router = APIRouter()
log = logging.getLogger(__name__)


def _require_admin(token: str | None) -> None:
    expected = os.environ.get("ADMIN_SEED_TOKEN")
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="seeding disabled — ADMIN_SEED_TOKEN not set on the service",
        )
    if not token or token != expected:
        raise HTTPException(status_code=403, detail="invalid or missing admin token")


@router.post("/api/admin/seed")
def admin_seed(x_admin_token: str | None = Header(default=None)) -> dict:
    """Run the Mongo schema bootstrap (indexes + the three sample signal
    sources) and the canonical demo seed (customer_voice, negative_examples,
    messaging_library, experiments, playbook skills).

    Re-running is deterministic (collections are reset + reseeded). Returns a
    per-step status so a partial failure is visible. Pass the shared secret in
    the ``X-Admin-Token`` header.
    """
    _require_admin(x_admin_token)
    out: dict = {}

    # Schema bootstrap: indexes + signal_sources sample rows. apply() connects
    # via its own get_mongo_uri(), which resolves the same way the rest of the
    # service does in cloud.
    try:
        from mongo import schema
        schema.apply()
        out["schema_bootstrap"] = "ok"
    except Exception as e:  # noqa: BLE001 — surface the per-step error
        log.exception("admin seed: schema bootstrap failed")
        out["schema_bootstrap"] = f"error: {e}"

    # Canonical demo data (voice/messaging/experiments/skills/negatives).
    try:
        from mongo.cli_seed import cmd_load_all
        cmd_load_all()
        out["seed_load_all"] = "ok"
    except Exception as e:  # noqa: BLE001
        log.exception("admin seed: cmd_load_all failed")
        out["seed_load_all"] = f"error: {e}"

    return out
