"""Per-source health tracker for outcome_attach.

Why: every `query_<source>` in sources.py used to return ``None`` on both
"no match" and "real error" — auth failed, API down, rate limit hit. The
caller couldn't tell which, so persistent integration failures were
invisible. The lifecycle test surfaced this directly (experiments never
decided because all the source-fetches silently no-op'd).

What this gives you:
  - A Mongo collection ``integration_health`` (one doc per source) with
    last_success / last_failure timestamps, consecutive_failures counter,
    and a derived status.
  - Two thin helpers (``record_success``, ``record_failure``) the source
    functions call at the right boundaries. "No match" is intentionally
    NOT counted as a failure — a draft that just hasn't earned any GA4
    sessions yet should not page anyone.
  - A ``status_for(source)`` helper the /api/integrations/status endpoint
    uses to render red/yellow/green per source.

The thresholds match what an operator would want to see at a glance:
  - **healthy** — at least one success in the last 24h, < 3 consecutive
    failures since
  - **degraded** — last success > 24h ago OR 3-10 consecutive failures
  - **broken** — > 10 consecutive failures, OR no success in 72h
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from shared import mongo_tools

log = logging.getLogger(__name__)

# Append-only by construction — the doc per source is upserted, not
# history-captured. The collection sits next to actions/outcomes as a
# pure-telemetry collection.
_COLLECTION = "integration_health"


def record_success(source: str, metric: str) -> None:
    """Bump the source's last_success and reset its failure counter."""
    now = datetime.now(UTC)
    try:
        mongo_tools.db()[_COLLECTION].update_one(
            {"_id": source},
            {
                "$set": {
                    "source": source,
                    "last_success_at": now,
                    "last_success_metric": metric,
                    "consecutive_failures": 0,
                },
                "$inc": {"total_successes": 1},
            },
            upsert=True,
        )
    except Exception as e:
        # Health tracking must never block the source itself.
        log.debug("integration_health record_success failed: %s", e)


def record_failure(source: str, metric: str, error: str) -> None:
    """Record a failure event. Increments consecutive_failures."""
    now = datetime.now(UTC)
    try:
        mongo_tools.db()[_COLLECTION].update_one(
            {"_id": source},
            {
                "$set": {
                    "source": source,
                    "last_failure_at": now,
                    "last_failure_metric": metric,
                    # Cap error length so a giant stacktrace doesn't blow
                    # up the doc.
                    "last_failure_error": str(error)[:500],
                },
                "$inc": {
                    "consecutive_failures": 1,
                    "total_failures": 1,
                },
            },
            upsert=True,
        )
    except Exception as e:
        log.debug("integration_health record_failure failed: %s", e)


def status_for(doc: dict) -> str:
    """Derive a healthy / degraded / broken status from a health doc."""
    now = datetime.now(UTC)
    last_success = doc.get("last_success_at")
    consec = int(doc.get("consecutive_failures") or 0)

    # If we've never seen a success, that's broken — the source has never
    # actually worked since the system started tracking it.
    if not last_success:
        return "broken" if consec > 0 else "unknown"

    if last_success.tzinfo is None:
        last_success = last_success.replace(tzinfo=UTC)
    age = now - last_success

    if consec > 10 or age > timedelta(hours=72):
        return "broken"
    if consec >= 3 or age > timedelta(hours=24):
        return "degraded"
    return "healthy"


def snapshot() -> list[dict]:
    """Return the current health state of every known source. Used by
    /api/integrations/status to render the founder-facing red/yellow/green
    indicator.
    """
    out: list[dict] = []
    try:
        for doc in mongo_tools.db()[_COLLECTION].find({}):
            out.append({
                "source": doc.get("source") or doc.get("_id"),
                "status": status_for(doc),
                "last_success_at": doc.get("last_success_at"),
                "last_success_metric": doc.get("last_success_metric"),
                "last_failure_at": doc.get("last_failure_at"),
                "last_failure_metric": doc.get("last_failure_metric"),
                "last_failure_error": doc.get("last_failure_error"),
                "consecutive_failures": doc.get("consecutive_failures", 0),
                "total_successes": doc.get("total_successes", 0),
                "total_failures": doc.get("total_failures", 0),
            })
    except Exception as e:
        log.warning("integration_health snapshot failed: %s", e)
    return sorted(out, key=lambda r: r["source"])
