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

    # Prod-guard: even with a valid admin token, refuse to wipe+reseed a
    # non-local Mongo unless the service env explicitly names the target via
    # MONGO_SEED_CONFIRM (= PROJECT_ID on the Secret Manager path). Single
    # choke point shared with the CLI / local_seed; see mongo/cli_seed.py.
    from mongo.cli_seed import SeedGuardError, guard_destructive_seed
    try:
        guard_destructive_seed("admin seed")
    except SeedGuardError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e

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

    # Agent skills (house-style, copywriting, ...) — NOT covered by
    # cmd_load_all's playbook seed. UPSERT them into the skills collection so
    # the agent_skill rows carry skill_kind/current_version/versions (the
    # Skills page renders the kind chip + SKILL.md from these). Upsert, not
    # wipe, so the playbook skills cmd_load_all just seeded survive.
    try:
        from mongo.data.agent_skills import build_agent_skill_docs
        from shared import mongo_tools
        coll = mongo_tools.db()["skills"]
        docs = build_agent_skill_docs()
        for d in docs:
            coll.update_one({"_id": d["_id"]}, {"$set": d}, upsert=True)
        out["agent_skills"] = f"upserted {len(docs)}"
    except Exception as e:  # noqa: BLE001
        log.exception("admin seed: agent_skills upsert failed")
        out["agent_skills"] = f"error: {e}"

    return out


# ---------------------------------------------------------------------------
# Cron control panel — list every scheduled job/endpoint + trigger it manually.
#
# SECURITY: the web-api is allUsers-invocable, so these endpoints are reachable
# from the public internet. They are guarded by an OPTIONAL token: if the
# ADMIN_SEED_TOKEN secret is set on the service, the X-Admin-Token header is
# required; if it is NOT set, the endpoints are open. Hackathon posture is
# "open" — set the secret post-evaluation to lock the panel down with zero code
# changes. (Triggering a cron is at worst a re-run of an idempotent job, but
# eval-harness/promotion-gate cost Vertex quota + make promotion decisions, so
# lock this down before any real exposure.)
# ---------------------------------------------------------------------------

# Region the Cloud Run jobs live in (GOOGLE_CLOUD_LOCATION is set in deploy/env).
_REGION = (os.environ.get("REGION")
           or os.environ.get("GOOGLE_CLOUD_LOCATION")
           or "us-central1")
_PROJECT = os.environ.get("PROJECT_ID", "hindsight-guild-mvp")

# The full scheduled-work registry. `kind`:
#   - "job"      → a Cloud Run job, triggered via the Run Admin API (needs
#                  sa-agents to have run.jobs.run on the job — granted in
#                  deploy/06-bind-iam.sh).
#   - "endpoint" → runs in-process in web-api; we call the handler directly.
# `schedule` mirrors deploy/env.sh (UTC) for display. `danger` flags crons that
# cost real money or make irreversible decisions → the UI asks to confirm.
CRONS: list[dict] = [
    {"name": "self-critique", "kind": "job", "schedule": "0 0 * * *",
     "description": "PRD-03 self-critique miners — writes self_critique_runs + proposals (powers the Self-Learning page).",
     "danger": False},
    {"name": "signal-watcher", "kind": "endpoint", "target": "poll-now",
     "schedule": "0 0 * * *",
     "description": "Poll HN / Reddit / RSS sources for new signals.",
     "danger": False},
    {"name": "signal-router", "kind": "endpoint", "target": "route-now",
     "schedule": "30 0 * * *",
     "description": "Route pending signals into channel drafts (enqueues draft jobs).",
     "danger": False},
    {"name": "outcome-attach", "kind": "job", "schedule": "0 3 */2 * *",
     "description": "Attach realized outcomes (opens/clicks/etc.) to drafting actions.",
     "danger": False},
    {"name": "eval-harness", "kind": "job", "schedule": "0 3 * * *",
     "description": "Nightly re-grade of recent drafts on all 6 rubrics (Vertex Eval — spends quota).",
     "danger": True},
    {"name": "derive-track-records", "kind": "job", "schedule": "30 3 * * *",
     "description": "Roll up per-skill / per-channel track records from telemetry.",
     "danger": False},
    {"name": "drift-detect", "kind": "job", "schedule": "30 4 * * *",
     "description": "Detect rubric-score drift vs baseline; open drift investigations.",
     "danger": False},
    {"name": "ops-qa-sweep", "kind": "job", "schedule": "0 5 * * *",
     "description": "Probe monitored ops targets; open ops_incidents on failures.",
     "danger": False},
    {"name": "paid-media-sweep", "kind": "job", "schedule": "30 */6 * * *",
     "description": "Sweep paid-media spend; open stop-loss incidents per thresholds.",
     "danger": False},
    {"name": "positioning-review", "kind": "job", "schedule": "0 22 * * SUN",
     "description": "Weekly positioning review — proposes messaging updates.",
     "danger": True},
    {"name": "promotion-gate", "kind": "job", "schedule": "0 23 * * SUN",
     "description": "Evaluate skill candidates and PROMOTE winners to current (irreversible-ish).",
     "danger": True},
    {"name": "snapshot-mongo", "kind": "job", "schedule": "0 * * * *",
     "description": "Hourly Mongo snapshot (M0 has no Atlas backups).",
     "danger": False},
    {"name": "substack-publish-sweep", "kind": "job", "schedule": "*/15 * * * *",
     "description": "Retry any stuck Substack publishes.",
     "danger": False},
]
_CRON_BY_NAME = {c["name"]: c for c in CRONS}


def _maybe_guard(token: str | None) -> None:
    """Token guard that is ENFORCED only when ADMIN_SEED_TOKEN is configured.

    No secret set (hackathon) → open. Secret set → X-Admin-Token must match.
    This lets the panel be locked down later by just creating the secret, with
    no code change."""
    expected = os.environ.get("ADMIN_SEED_TOKEN")
    if expected and token != expected:
        raise HTTPException(status_code=403, detail="invalid or missing admin token")


@router.get("/api/admin/crons")
def list_crons(x_admin_token: str | None = Header(default=None)) -> dict:
    """List every scheduled job + endpoint the cron panel can trigger."""
    _maybe_guard(x_admin_token)
    # `secured` tells the UI whether a token is required (so it can show/hide the
    # token field). The list itself is static config — cheap, no API calls.
    return {
        "secured": bool(os.environ.get("ADMIN_SEED_TOKEN")),
        "region": _REGION,
        "crons": CRONS,
    }


@router.post("/api/admin/crons/{name}/run")
def run_cron(name: str, x_admin_token: str | None = Header(default=None)) -> dict:
    """Trigger one cron now. Cloud Run jobs fire via the Run Admin API (async —
    returns immediately with the execution handle); endpoint crons run their
    handler in-process and return its result."""
    _maybe_guard(x_admin_token)
    cron = _CRON_BY_NAME.get(name)
    if not cron:
        raise HTTPException(status_code=404, detail=f"unknown cron {name!r}")

    if cron["kind"] == "endpoint":
        return {"ok": True, "name": name, "kind": "endpoint",
                "result": _run_endpoint_cron(cron["target"])}

    # Cloud Run job → Run Admin API jobs:run (mirrors what Cloud Scheduler does).
    try:
        import google.auth
        from google.auth.transport.requests import AuthorizedSession

        creds, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"])
        session = AuthorizedSession(creds)
        url = (f"https://run.googleapis.com/v2/projects/{_PROJECT}"
               f"/locations/{_REGION}/jobs/{name}:run")
        r = session.post(url, timeout=30)
        if r.status_code == 403:
            raise HTTPException(
                status_code=403,
                detail=("web-api's service account lacks run.jobs.run on this "
                        "job. Grant it (deploy/06-bind-iam.sh) and redeploy."))
        r.raise_for_status()
        op = r.json()
        return {"ok": True, "name": name, "kind": "job",
                "execution": op.get("name", ""), "status": "started"}
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        log.exception("run_cron(%s) failed", name)
        raise HTTPException(status_code=502,
                            detail=f"failed to trigger {name}: {e}") from e


def _run_endpoint_cron(target: str) -> dict:
    """Invoke an in-process signal endpoint's handler directly.

    Pass keyword args EXPLICITLY: these are FastAPI handlers, so their
    ``Query(...)`` defaults resolve to FieldInfo objects (not None) when called
    as plain functions. poll_now ignores ``source`` in v1, but pass None anyway
    so it stays correct if that ever changes."""
    from services.web_api.routers.signals import poll_now, route_now
    if target == "poll-now":
        return poll_now(source=None)
    if target == "route-now":
        return route_now()
    raise HTTPException(status_code=400, detail=f"unknown endpoint target {target!r}")
