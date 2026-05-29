"""Rubric trend, this-week summary, before-vs-after dashboards."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter

from shared import mongo_tools

router = APIRouter()


@router.get("/api/rubric-trend")
def rubric_trend(days: int = 28):
    from services.web_api.main import BQ, PROJECT_ID
    if BQ is None:
        return _rubric_trend_from_mongo(days=days)
    sql = f"""
    SELECT day, channel, mean_brand_voice, mean_claim_support, n
    FROM `{PROJECT_ID}.analytics.rubric_trend_28d`
    WHERE day >= DATE_SUB(CURRENT_DATE(), INTERVAL {int(days)} DAY)
    ORDER BY day ASC
    """
    return [dict(r) for r in BQ.query(sql).result()]


def _this_week_summary_from_mongo() -> dict:
    """LOCAL_DEV summary. Mirrors the BQ telemetry-actions counts from the
    Mongo `actions` mirror so the WeeklyReview headline numbers light up
    after even one pipeline run."""
    db = mongo_tools.db()
    # ISO week-start (Monday). Mongo doesn't have a TRUNC(WEEK) primitive
    # in older versions, so compute in Python.
    now = datetime.now(UTC)
    week_start = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0,
    )
    actions = list(db["actions"].find({"ts": {"$gte": week_start}}))
    drafts = sum(1 for a in actions if (a.get("action_type") or "").startswith("draft_"))
    armor_blocks = sum(
        1 for a in actions
        if isinstance(a.get("model_armor"), dict)
        and a["model_armor"].get("decision") == "block"
    )
    # Approvals: the LOCAL_DEV /api/decisions path writes directly to the
    # ``approvals`` collection (not back onto the action row), so we count
    # from there. ``decision='approve'`` means "ship it"; edits + rejects
    # are tracked separately in ``edits`` below.
    approvals = db["approvals"].count_documents({
        "decided_at": {"$gte": week_start},
        "decision": "approve",
    })
    edits = db["approvals"].count_documents({
        "decided_at": {"$gte": week_start},
        "decision": {"$in": ["edit", "reject"]},
    })
    return {
        "drafts": drafts,
        "approvals": approvals,
        "armor_blocks": armor_blocks,
        "total_actions": len(actions),
        "edits": edits,
    }


def _rubric_trend_from_mongo(days: int) -> list[dict]:
    """LOCAL_DEV rubric-trend. Aggregates the Mongo `actions` mirror by
    (day, channel) and averages brand_voice + claim_support +
    answer_extractability across draft rows that have eval_scores
    populated. ``answer_extractability`` comes from the AEO loop (PRD-01);
    nulls aggregate cleanly as missing values."""
    cutoff = datetime.now(UTC) - timedelta(days=days)
    pipeline = [
        {"$match": {
            "ts": {"$gte": cutoff},
            "action_type": {"$regex": "^draft_"},
            "eval_scores": {"$ne": None},
        }},
        {"$group": {
            "_id": {
                "day": {"$dateToString": {"format": "%Y-%m-%d", "date": "$ts"}},
                "channel": "$channel",
            },
            "mean_brand_voice": {"$avg": "$eval_scores.brand_voice"},
            "mean_claim_support": {"$avg": "$eval_scores.claim_support"},
            "mean_answer_extractability": {"$avg": "$eval_scores.answer_extractability"},
            "n": {"$sum": 1},
        }},
        {"$sort": {"_id.day": 1}},
    ]
    out = []
    for r in mongo_tools.db()["actions"].aggregate(pipeline):
        out.append({
            "day": r["_id"]["day"],
            "channel": r["_id"]["channel"],
            "mean_brand_voice": r["mean_brand_voice"] or 0.0,
            "mean_claim_support": r["mean_claim_support"] or 0.0,
            "mean_answer_extractability": r["mean_answer_extractability"] or 0.0,
            "n": r["n"],
        })
    return out


@router.get("/api/this-week-summary")
def this_week_summary():
    from services.web_api.main import BQ, PROJECT_ID
    if BQ is None:
        return _this_week_summary_from_mongo()
    sql = f"""
    SELECT
      COUNTIF(action_type LIKE 'draft_%') AS drafts,
      COUNTIF(approval_id IS NOT NULL) AS approvals,
      COUNTIF(JSON_VALUE(model_armor, '$.decision') = 'block') AS armor_blocks,
      COUNT(*) AS total_actions
    FROM `{PROJECT_ID}.telemetry.actions`
    WHERE ts >= TIMESTAMP_TRUNC(CURRENT_TIMESTAMP(), WEEK)
    """
    row = next(iter(BQ.query(sql).result()), None)
    if not row:
        return {"drafts": 0, "approvals": 0, "armor_blocks": 0, "total_actions": 0}
    edits = next(iter(BQ.query(
        f"SELECT COUNT(*) AS n FROM `{PROJECT_ID}.training.edits` "
        f"WHERE ts >= TIMESTAMP_TRUNC(CURRENT_TIMESTAMP(), WEEK)"
    ).result()), None)
    return {
        "drafts": row.drafts,
        "approvals": row.approvals,
        "armor_blocks": row.armor_blocks,
        "total_actions": row.total_actions,
        "edits": edits.n if edits else 0,
    }


@router.get("/api/before-vs-after")
def before_vs_after():
    from services.web_api.main import BQ, PROJECT_ID
    if BQ is None:
        return []
    sql = f"""
    SELECT skill_id, skill_version, period, mean_brand_voice, mean_claim_support, n
    FROM `{PROJECT_ID}.analytics.before_vs_after`
    ORDER BY skill_id, skill_version, period
    """
    return [dict(r) for r in BQ.query(sql).result()]
