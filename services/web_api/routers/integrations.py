"""Outbound integration status / health + AEO citations."""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter

from shared import mongo_tools

router = APIRouter()
log = logging.getLogger(__name__)


@router.get("/api/integrations/status")
def integrations_status() -> dict:
    """Tell the UI which outbound integrations are configured.

    Used to gate the 'Will publish to X on ship' badge per queue card.
    The shape is one entry per integration adapter, listing the
    channels it serves and whether its credentials are present.
    """
    from shared.integrations import status_snapshot
    return status_snapshot()


@router.get("/api/aeo/cited-by")
def aeo_cited_by(days: int = 28) -> list[dict]:
    """Backs the "Cited by AI engines" tile on the Quality Signals page
    (PRD-01 M5). Returns rows from the ``aeo_citations`` collection,
    most-recent first. Manually populated in MVP 1 via
    ``python -m scripts.aeo.log_citation ...``; MVP 2 wires the
    Perplexity / Brave Search APIs.
    """
    cutoff = datetime.now(UTC) - timedelta(days=max(1, days))
    rows: list[dict] = []
    try:
        cursor = mongo_tools.db()["aeo_citations"].find(
            {"ts": {"$gte": cutoff}},
        ).sort("ts", -1).limit(200)
        for r in cursor:
            rows.append({
                "id":            str(r.get("_id", "")),
                "ts":            r.get("ts").isoformat() if r.get("ts") else None,
                "platform":      r.get("platform"),
                "query":         r.get("query"),
                "cited_url":     r.get("cited_url"),
                "evidence_url":  r.get("evidence_url"),
                "telemetry_id":  r.get("telemetry_id"),
                "added_by":      r.get("added_by"),
            })
    except Exception as e:
        log.warning("aeo/cited-by query failed: %s", e)
    return rows


@router.get("/api/integrations/health")
def integrations_health() -> list[dict]:
    """Per-source runtime health for outcome-attribution integrations.

    Sources (ga4, hubspot, google_ads, linkedin_ads, substack) record a
    success or failure each time outcome_attach pulls a metric. This
    endpoint returns their current state so the founder can see at a
    glance whether attribution is silently broken — previously these
    failures were just `log.warning` and `return None`, indistinguishable
    from "no match found."

    Status values:
      - healthy: success in last 24h, < 3 consecutive failures
      - degraded: last success > 24h ago OR 3-10 consecutive failures
      - broken: > 10 consecutive failures OR no success in 72h
      - unknown: source has never been called
    """
    from services.outcome_attach.health import snapshot
    return snapshot()
