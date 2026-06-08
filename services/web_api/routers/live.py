"""Live ops — REST endpoints for "what's running right now" and the full
24-hour activity stream.

The WebSocket push for /api/ws/live lives in routers/drafting.py because
it's tightly coupled to the in-memory _JOBS table and the _LiveBroadcaster
fan-out. These REST endpoints share the same _live_now_payload() builder
so the wire shape is identical across transports.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter

from shared import mongo_tools

router = APIRouter()


@router.get("/api/live/now")
def live_now():
    """Lightweight "what's running this second" endpoint — REST fallback
    for clients that can't open a WebSocket (curl, SSR). The WS endpoint
    /api/ws/live pushes the same payload on every job state change."""
    # Reuse the WS payload builder so REST + WS clients see identical shape.
    from services.web_api.routers.drafting import _live_now_payload
    return _live_now_payload()


@router.get("/api/live")
def live_ops():
    """Heartbeats + recent activity stream + scheduled job status.

    The UI polls this on a short interval to render the "Live Ops" surface —
    what every agent and worker has done in the last hour.
    """
    from services.web_api.main import BQ, PROJECT_ID
    if BQ is None:
        return _live_ops_from_mongo()
    # Recent activity — last 50 actions across all agents
    recent_sql = f"""
    SELECT telemetry_id, ts, agent, action_type, channel, skill_id, skill_version,
           eval_scores, model_armor
    FROM `{PROJECT_ID}.telemetry.actions`
    WHERE ts >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 24 HOUR)
    ORDER BY ts DESC
    LIMIT 50
    """
    recent = [dict(r) for r in BQ.query(recent_sql).result()]

    # Per-agent heartbeat (last action timestamp)
    hb_sql = f"""
    SELECT agent, MAX(ts) AS last_seen, COUNT(*) AS n_24h
    FROM `{PROJECT_ID}.telemetry.actions`
    WHERE ts >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 24 HOUR)
    GROUP BY agent
    """
    heartbeats = [dict(r) for r in BQ.query(hb_sql).result()]

    # Outcome attacher status — how many slots are pending vs filled in last 24h
    attach_sql = f"""
    SELECT status, COUNT(*) AS n
    FROM `{PROJECT_ID}.telemetry.outcomes`
    WHERE expected_by >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 24 HOUR)
    GROUP BY status
    """
    outcome_status = {r.status: r.n for r in BQ.query(attach_sql).result()}

    # Scheduled jobs (static list — we know what we deployed)
    scheduled_jobs = [
        {"name": "outcome-attach",   "schedule": "every 6h",          "category": "fill outcomes"},
        {"name": "eval-harness",     "schedule": "nightly 03:00 UTC", "category": "re-grade rubrics"},
        {"name": "drift-detect",     "schedule": "daily 04:30 UTC",   "category": "open investigations"},
        {"name": "self-critique",    "schedule": "Mon 09:00 UTC",     "category": "propose revisions"},
        {"name": "self-critique-runner", "schedule": "nightly 02:00 UTC", "category": "miner proposals"},
        {"name": "promotion-gate",   "schedule": "Sun 23:00 UTC",     "category": "raise promotions"},
        {"name": "signal-watcher",   "schedule": "weekly Sun 00:00 UTC", "category": "signal ingestion"},
        {"name": "signal-router",    "schedule": "weekly Sun 00:30 UTC", "category": "signal routing"},
    ]

    return {
        "recent_actions": recent,
        "heartbeats": heartbeats,
        "outcome_status": outcome_status,
        "scheduled_jobs": scheduled_jobs,
        "server_time": datetime.now(UTC).isoformat(),
    }


def _live_ops_from_mongo() -> dict:
    """LOCAL_DEV Live Ops payload built from the Mongo `actions` mirror +
    `outcomes` collection. Matches the BQ-backed live_ops shape so the UI
    renders identical components."""
    db = mongo_tools.db()
    cutoff = datetime.now(UTC) - timedelta(hours=24)

    recent_cursor = db["actions"].find(
        {"ts": {"$gte": cutoff}},
        {"telemetry_id": 1, "ts": 1, "agent": 1, "action_type": 1,
         "channel": 1, "skill_id": 1, "skill_version": 1,
         "eval_scores": 1, "model_armor": 1},
    ).sort("ts", -1).limit(50)
    recent_actions = []
    for r in recent_cursor:
        recent_actions.append({
            "telemetry_id": r.get("telemetry_id"),
            "ts": r["ts"].isoformat() if hasattr(r.get("ts"), "isoformat") else str(r.get("ts")),
            "agent": r.get("agent"),
            "action_type": r.get("action_type"),
            "channel": r.get("channel"),
            "skill_id": r.get("skill_id"),
            "skill_version": r.get("skill_version"),
            "eval_scores": r.get("eval_scores"),
            "model_armor": r.get("model_armor"),
        })

    # Per-agent heartbeat
    hb_pipeline = [
        {"$match": {"ts": {"$gte": cutoff}}},
        {"$group": {
            "_id": "$agent",
            "last_seen": {"$max": "$ts"},
            "n_24h": {"$sum": 1},
        }},
        {"$sort": {"last_seen": -1}},
    ]
    heartbeats = []
    for r in db["actions"].aggregate(hb_pipeline):
        heartbeats.append({
            "agent": r["_id"],
            "last_seen": r["last_seen"].isoformat() if hasattr(r.get("last_seen"), "isoformat") else str(r.get("last_seen")),
            "n_24h": r["n_24h"],
        })

    # Outcome fill status
    outcome_status: dict[str, int] = {}
    for r in db["outcomes"].aggregate([
        {"$match": {"expected_by": {"$gte": cutoff}}},
        {"$group": {"_id": "$status", "n": {"$sum": 1}}},
    ]):
        outcome_status[r["_id"]] = r["n"]

    # Scheduled jobs — static list (we know what's deployed in prod) plus
    # an honest "local-dev" note so the panel isn't misleading.
    scheduled_jobs = [
        {"name": "outcome-attach", "schedule": "every 6h", "category": "outcomes"},
        {"name": "eval-harness", "schedule": "daily 03:00 UTC", "category": "eval"},
        {"name": "drift-detect", "schedule": "daily 04:30 UTC", "category": "drift"},
        {"name": "self-critique", "schedule": "weekly Mon 09:00 UTC", "category": "learning"},
        {"name": "self-critique-runner", "schedule": "nightly 02:00 UTC", "category": "learning"},
        {"name": "promotion-gate", "schedule": "weekly Sun 23:00 UTC", "category": "learning"},
        {"name": "positioning-review", "schedule": "weekly Sun 22:00 UTC", "category": "positioning"},
        {"name": "ops-qa-sweep", "schedule": "daily 05:00 UTC", "category": "ops"},
        {"name": "snapshot-mongo", "schedule": "hourly", "category": "ops"},
        {"name": "derive-track-records", "schedule": "nightly 04:00 UTC", "category": "analytics"},
        {"name": "substack-publish-sweep", "schedule": "every 15m", "category": "publish"},
        {"name": "signal-watcher", "schedule": "weekly Sun 00:00 UTC", "category": "signal ingestion"},
        {"name": "signal-router", "schedule": "weekly Sun 00:30 UTC", "category": "signal routing"},
    ]

    return {
        "recent_actions": recent_actions,
        "heartbeats": heartbeats,
        "outcome_status": outcome_status,
        "scheduled_jobs": scheduled_jobs,
        "server_time": datetime.now(UTC).isoformat(),
    }
