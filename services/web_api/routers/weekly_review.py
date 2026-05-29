"""Weekly review — the Monday-morning ritual rollup."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter

from mongo import queries
from shared import mongo_tools

router = APIRouter()


@router.get("/api/weekly-review")
def weekly_review():
    """Everything the founder needs for the 15-minute weekly review."""
    # Reuse the existing per-domain helpers so behavior stays identical
    # to the pre-split shape. These call the BQ-or-Mongo branches the
    # same way the standalone /api/this-week-summary + /api/rubric-trend
    # endpoints do.
    from services.web_api.routers.rubric_trends import (
        rubric_trend,
        this_week_summary,
    )

    summary = this_week_summary()
    decided = queries.recent_decisions(limit=5)
    running = queries.running_experiments()
    drift = queries.drift_investigations()
    proposals = queries.skills_with_self_critique_proposal()
    promotions = queries.skills_with_promotion_request()

    edit_categories = _top_edit_categories()
    rubric_trend_data = rubric_trend(days=28)

    return {
        "summary": summary,
        "edit_categories": edit_categories,
        "decided_experiments": decided,
        "running_experiments": running,
        "drift_investigations": drift,
        "self_critique_proposals": proposals,
        "promotion_requests": promotions,
        "rubric_trend": rubric_trend_data,
    }


def _top_edit_categories(limit: int = 5) -> list[dict]:
    from services.web_api.main import BQ, PROJECT_ID
    if BQ is None:
        # LOCAL_DEV: aggregate from Mongo approvals.edit_categories
        now = datetime.now(UTC)
        week_start = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0,
        )
        pipeline = [
            {"$match": {"decided_at": {"$gte": week_start},
                        "edit_categories": {"$exists": True, "$ne": []}}},
            {"$unwind": "$edit_categories"},
            {"$group": {"_id": "$edit_categories", "n": {"$sum": 1}}},
            {"$sort": {"n": -1}},
            {"$limit": limit},
        ]
        return [{"category": r["_id"], "n": r["n"]}
                for r in mongo_tools.db()["approvals"].aggregate(pipeline)]
    sql = f"""
    SELECT cat, COUNT(*) AS n
    FROM `{PROJECT_ID}.training.edits`, UNNEST(edit_categories) AS cat
    WHERE ts >= TIMESTAMP_TRUNC(CURRENT_TIMESTAMP(), WEEK)
    GROUP BY cat
    ORDER BY n DESC
    LIMIT {limit}
    """
    return [{"category": r.cat, "n": r.n} for r in BQ.query(sql).result()]
