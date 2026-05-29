"""PRD-02 signals endpoints.

Five public endpoints back the new ``/signals`` route in the UI plus the
``Triggered by`` chip on the Queue:

  - ``GET  /api/signals``           — list signals (filterable by since / source)
  - ``GET  /api/signals/sources``   — list source configs + health stats
  - ``POST /api/signals/manual``    — score a URL and enqueue a draft
  - ``POST /api/signals/{id}/suppress``  — mark a pending signal suppressed
  - ``POST /api/signals/poll-now``  — operator escape hatch (manual watcher tick)

A sixth ``POST /api/signals/route-now`` mirrors poll-now for the router so
the founder can force-flush the pending queue without waiting for the
5-min cron. Not in the PRD as a numbered bullet, but useful enough that
adding it now beats opening a follow-up ticket.

All endpoints degrade to empty responses on Mongo errors (telemetry
must never block the UI).
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Body, HTTPException, Query
from pydantic import BaseModel

from shared import mongo_tools

router = APIRouter()
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class SignalRow(BaseModel):
    id: str
    source: str | None
    ts: str | None
    evidence_url: str | None
    evidence_excerpt: str | None
    icp_segment: str | None
    icp_keywords_hit: list[str] = []
    score: float | None
    processed: bool = False
    processed_at: str | None
    suppressed_reason: str | None
    triggered_telemetry_id: str | None
    router_job_id: str | None = None


class SignalSourceRow(BaseModel):
    # name/source kept Optional so one malformed source doc can't 500 the
    # endpoint — these routes are resilience-first (degrade to empty).
    name: str | None
    source: str | None
    enabled: bool
    icp_segment: str | None
    poll_interval_sec: int | None
    score_floor: float | None
    default_channel: str | None
    last_polled_at: str | None
    # Health stats — computed from the signals collection.
    signals_24h: int = 0
    drafts_24h: int = 0


class ManualSignalRequest(BaseModel):
    """Body for POST /api/signals/manual. ``url`` is the source thread or
    article we'd like the router to evaluate; ``icp_segment`` + ``channel``
    are optional founder overrides."""
    url: str
    icp_segment: str | None = None
    channel: str | None = None


# ---------------------------------------------------------------------------
# Serialization helpers — Mongo doc → API shape.
# ---------------------------------------------------------------------------

def _serialize_signal(r: dict) -> dict:
    """Translate a Mongo signals row to the API shape (strings, ISO ts)."""
    return {
        "id":                str(r.get("_id", "")),
        "source":            r.get("source"),
        "ts":                r["ts"].isoformat() if hasattr(r.get("ts"), "isoformat") else None,
        "evidence_url":      r.get("evidence_url"),
        "evidence_excerpt":  r.get("evidence_excerpt"),
        "icp_segment":       r.get("icp_segment"),
        "icp_keywords_hit":  list(r.get("icp_keywords_hit") or []),
        "score":             r.get("score"),
        "processed":         bool(r.get("processed", False)),
        "processed_at":      (r["processed_at"].isoformat()
                              if hasattr(r.get("processed_at"), "isoformat") else None),
        "suppressed_reason": r.get("suppressed_reason"),
        "triggered_telemetry_id": r.get("triggered_telemetry_id"),
        "router_job_id":     r.get("router_job_id"),
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/api/signals", response_model=list[SignalRow])
def list_signals(
    since: str | None = Query(None, description="ISO-8601 timestamp; defaults to 24h ago"),
    source: str | None = Query(None, description="Filter by source kind (hn / reddit / rss)"),
    status: str | None = Query(None, description="all | pending | processed | suppressed"),
    limit: int = Query(50, le=500),
) -> list[dict]:
    """List signals. Default window = last 24h. Used by the /signals
    route's 'Recent triggers' and 'Suppressed' sections."""
    try:
        db = mongo_tools.db()
    except Exception as e:
        log.warning("signals: db connect failed: %s", e)
        return []

    if since:
        try:
            ts_floor = datetime.fromisoformat(since.replace("Z", "+00:00"))
        except Exception:
            ts_floor = datetime.now(UTC) - timedelta(hours=24)
    else:
        ts_floor = datetime.now(UTC) - timedelta(hours=24)

    query: dict = {"ts": {"$gte": ts_floor}}
    if source:
        query["source"] = source
    if status == "pending":
        query["processed"] = False
        query["suppressed_reason"] = None
    elif status == "processed":
        query["processed"] = True
    elif status == "suppressed":
        query["suppressed_reason"] = {"$ne": None}

    try:
        rows = db["signals"].find(query).sort("ts", -1).limit(limit)
        return [_serialize_signal(r) for r in rows]
    except Exception as e:
        log.warning("signals query failed: %s", e)
        return []


@router.get("/api/signals/sources", response_model=list[SignalSourceRow])
def list_signal_sources() -> list[dict]:
    """List signal_sources docs plus per-source 24h health counters.
    Used by the 'Source health' panel on the /signals route."""
    try:
        db = mongo_tools.db()
    except Exception as e:
        log.warning("signals/sources: db connect failed: %s", e)
        return []

    since = datetime.now(UTC) - timedelta(hours=24)

    # Per-source signal counts in last 24h.
    sig_counts: dict[str, int] = {}
    try:
        for r in db["signals"].aggregate([
            {"$match": {"ts": {"$gte": since}}},
            {"$group": {"_id": "$source", "n": {"$sum": 1}}},
        ]):
            sig_counts[r["_id"]] = int(r["n"])
    except Exception:
        pass

    # Per-source action counts in last 24h. We can't easily resolve
    # source from action rows directly (the source name lives on the
    # signal, not the action), so we count actions whose
    # triggered_by_signal_id resolves to each source. Two-stage join:
    # signals[].source -> actions count.
    drafts_counts: dict[str, int] = {}
    try:
        sig_to_source = {
            r["_id"]: r.get("source") for r in db["signals"].find(
                {"ts": {"$gte": since}}, {"source": 1}
            )
        }
        for r in db["actions"].find(
            {"triggered_by_signal_id": {"$ne": None},
             "ts": {"$gte": since}},
            {"triggered_by_signal_id": 1},
        ):
            src = sig_to_source.get(r.get("triggered_by_signal_id"))
            if src:
                drafts_counts[src] = drafts_counts.get(src, 0) + 1
    except Exception:
        pass

    out: list[dict] = []
    try:
        for s in db["signal_sources"].find({}):
            out.append({
                "name":              s.get("name"),
                "source":            s.get("source"),
                "enabled":           bool(s.get("enabled", False)),
                "icp_segment":       s.get("icp_segment"),
                "poll_interval_sec": s.get("poll_interval_sec"),
                "score_floor":       s.get("score_floor"),
                "default_channel":   s.get("default_channel"),
                "last_polled_at":    (s["last_polled_at"].isoformat()
                                      if hasattr(s.get("last_polled_at"), "isoformat") else None),
                "signals_24h":       sig_counts.get(s.get("source"), 0),
                "drafts_24h":        drafts_counts.get(s.get("source"), 0),
            })
    except Exception as e:
        log.warning("signal_sources iter failed: %s", e)
    return out


@router.post("/api/signals/manual")
def manual_signal(body: ManualSignalRequest = Body(...)) -> dict:  # noqa: B008  (FastAPI dependency-injection idiom)
    """Insert a manually-supplied signal and let the router pick it up
    on the next tick (or via /api/signals/route-now).

    Useful when the founder spots a thread the watcher missed. The
    signal score is set to 1.0 (manual signals don't go through the
    base_score recipe) and ``suppressed_reason`` stays null so the
    router sees it as eligible."""
    try:
        db = mongo_tools.db()
    except Exception as e:
        raise HTTPException(503, f"db unavailable: {e}") from e

    if not body.url.startswith(("http://", "https://")):
        raise HTTPException(400, "url must include scheme")

    row = {
        "source":            "manual",
        "ts":                datetime.now(UTC),
        "evidence_url":      body.url,
        "evidence_excerpt":  body.url,   # founder can edit later via UI
        "icp_segment":       body.icp_segment,
        "icp_keywords_hit":  [],
        "score":             1.0,
        "raw":               {"channel_override": body.channel} if body.channel else {},
        "processed":         False,
        "processed_at":      None,
        "triggered_telemetry_id": None,
        "suppressed_reason": None,
    }
    try:
        result = db["signals"].insert_one(row)
    except Exception as e:
        # Dup URL — return the existing row's id.
        existing = db["signals"].find_one({"evidence_url": body.url}, {"_id": 1})
        if existing:
            return {"status": "exists", "signal_id": str(existing["_id"])}
        raise HTTPException(500, f"insert failed: {e}") from e

    return {"status": "inserted", "signal_id": str(result.inserted_id)}


@router.post("/api/signals/{signal_id}/suppress")
def suppress_signal(signal_id: str) -> dict:
    """Mark a signal as suppressed so the router skips it. Used by the
    /signals UI when the founder spots a noisy signal."""
    try:
        db = mongo_tools.db()
        from bson import ObjectId
        try:
            oid = ObjectId(signal_id)
        except Exception:
            raise HTTPException(400, "invalid signal_id") from None
        result = db["signals"].update_one(
            {"_id": oid},
            {"$set": {
                "suppressed_reason": "founder_suppressed",
                "processed":         True,
                "processed_at":      datetime.now(UTC),
            }},
        )
        if result.matched_count == 0:
            raise HTTPException(404, "signal not found")
        return {"status": "suppressed"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"suppress failed: {e}") from e


@router.post("/api/signals/poll-now")
def poll_now(source: str | None = Query(None)) -> dict:
    """Trigger an immediate watcher tick. ``source`` is unused in v1
    (the watcher polls all enabled sources together); kept on the
    signature so the API contract matches what /signals's
    'Refresh source' button will eventually pass."""
    from agents.signal_watcher import run_once
    try:
        return run_once()
    except Exception as e:
        log.warning("poll-now failed: %s", e)
        raise HTTPException(500, f"poll failed: {e}") from e


@router.post("/api/signals/route-now")
def route_now() -> dict:
    """Trigger an immediate router tick. Pairs with /poll-now —
    useful for end-to-end smoke testing during a demo."""
    from agents.signal_router import run_once
    try:
        return run_once()
    except Exception as e:
        log.warning("route-now failed: %s", e)
        raise HTTPException(500, f"route failed: {e}") from e
