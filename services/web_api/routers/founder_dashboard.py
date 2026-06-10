"""Founder Dashboard — the business-value view of the guild's work.

Three endpoints turn telemetry the system already stores into the numbers a
founder (or a judge) actually cares about:

  /api/founder-dashboard   assets shipped, approval rate, attributed
                           outcomes, and the ROI card: founder minutes spent
                           vs freelancer-equivalent cost vs API cost. Every
                           dollar/minute figure is an ESTIMATE built from the
                           explicit assumptions returned alongside it — the
                           UI shows them; nothing here pretends to be a bill.
  /api/learning-receipts   concrete before→after proof of the learning loop:
                           a rejected/edited draft paired with the next draft
                           on the same channel, per-rubric deltas, and the
                           rubric that moved most. Mongo-only on purpose —
                           receipts are real pipeline drafts, never synthetic.
  /api/learning-curve      daily quality index (mean of brand_voice,
                           claim_support, answer_extractability) + rejection
                           counts, so the Learning page can chart the system
                           getting better. BQ when available, Mongo fallback
                           (same convention as rubric_trends).
"""
from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter

from shared import mongo_tools

router = APIRouter()
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# ROI assumptions — every figure derived from these is labeled an estimate in
# the UI and the assumptions ship in the API response. Env-overridable so a
# founder can calibrate to their own market without a deploy.
# ---------------------------------------------------------------------------

def _f(env: str, default: float) -> float:
    try:
        return float(os.environ.get(env, default))
    except ValueError:
        return default


# Minutes a founder spends per queue decision (read draft + decide) and per
# drafting request (typing the brief). Deliberately conservative.
MINUTES_PER_DECISION = _f("ROI_MINUTES_PER_DECISION", 2.0)
MINUTES_PER_DRAFT_REQUEST = _f("ROI_MINUTES_PER_DRAFT_REQUEST", 0.5)

# What producing one asset of this channel typically costs from a freelancer
# / agency (research + draft + revision round), USD. Mid-market rates.
FREELANCER_RATE_BY_CHANNEL: dict[str, float] = {
    "blog": _f("ROI_RATE_BLOG", 300.0),
    "substack": _f("ROI_RATE_SUBSTACK", 250.0),
    "linkedin": _f("ROI_RATE_LINKEDIN", 100.0),
    "email": _f("ROI_RATE_EMAIL", 150.0),
    "lifecycle_email": _f("ROI_RATE_EMAIL", 150.0),
    "google_ads": _f("ROI_RATE_ADS", 120.0),
    "meta_ads": _f("ROI_RATE_ADS", 120.0),
    "linkedin_ads": _f("ROI_RATE_ADS", 120.0),
}
FREELANCER_RATE_DEFAULT = _f("ROI_RATE_DEFAULT", 120.0)

# Vertex AI cost per full pipeline run (research→content→AEO→image→review→
# eval at the configured sample rate), USD. Gemini Flash-class pricing.
API_COST_PER_DRAFT = _f("ROI_API_COST_PER_DRAFT", 0.35)


def _assumptions() -> dict:
    return {
        "minutes_per_decision": MINUTES_PER_DECISION,
        "minutes_per_draft_request": MINUTES_PER_DRAFT_REQUEST,
        "freelancer_rate_by_channel": FREELANCER_RATE_BY_CHANNEL,
        "freelancer_rate_default": FREELANCER_RATE_DEFAULT,
        "api_cost_per_draft_usd": API_COST_PER_DRAFT,
        "note": ("Estimates, not invoices: founder time = decisions x "
                 "minutes/decision + drafts x minutes/request; freelancer "
                 "equivalent = per-channel mid-market rates; API cost = "
                 "drafts x per-pipeline-run Gemini estimate."),
    }


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

_RUBRIC_KEYS = ("brand_voice", "claim_support", "claim_risk", "icp_relevance",
                "originality", "conversion_intent", "answer_extractability")


def _numeric_scores(val: object) -> dict[str, float]:
    """Numeric rubric entries only (drops judge_model/scored_at provenance)."""
    if not isinstance(val, dict):
        return {}
    return {k: float(v) for k, v in val.items()
            if k in _RUBRIC_KEYS and isinstance(v, (int, float))
            and not isinstance(v, bool)}


def _snippet(raw: object, limit: int = 180) -> str:
    if not isinstance(raw, dict):
        return ""
    draft = raw.get("draft")
    if isinstance(draft, dict):  # substack structured shape
        draft = draft.get("headline") or draft.get("body_markdown") or ""
    if not isinstance(draft, str):
        return ""
    return draft.strip().replace("\n", " ")[:limit]


def _drafted_by_channel_bq(bq, project_id: str, days: int) -> dict[str, int]:
    sql = f"""
    SELECT channel, COUNT(*) AS n
    FROM `{project_id}.telemetry.actions`
    WHERE action_type LIKE 'draft_%'
      AND ts >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {int(days)} DAY)
    GROUP BY channel
    """
    return {r.channel or "unknown": r.n for r in bq.query(sql).result()}


def _drafted_by_channel_mongo(days: int) -> dict[str, int]:
    cutoff = datetime.now(UTC) - timedelta(days=days)
    out: dict[str, int] = {}
    rows = mongo_tools.db()["actions"].aggregate([
        {"$match": {"action_type": {"$regex": "^draft_"},
                    "ts": {"$gte": cutoff}}},
        {"$group": {"_id": "$channel", "n": {"$sum": 1}}},
    ])
    for r in rows:
        out[r["_id"] or "unknown"] = r["n"]
    return out


# ---------------------------------------------------------------------------
# /api/founder-dashboard
# ---------------------------------------------------------------------------

@router.get("/api/founder-dashboard")
def founder_dashboard(days: int = 7) -> dict:
    from services.web_api.main import BQ, PROJECT_ID
    days = max(1, min(int(days), 90))
    cutoff = datetime.now(UTC) - timedelta(days=days)
    db = mongo_tools.db()

    # Assets drafted, per channel. BQ carries the complete telemetry stream
    # (live dual-write + seeded history); Mongo mirror is the fallback.
    if BQ is not None:
        try:
            by_channel = _drafted_by_channel_bq(BQ, PROJECT_ID, days)
        except Exception as e:  # noqa: BLE001
            log.warning("founder-dashboard BQ drafted query failed: %s", e)
            by_channel = _drafted_by_channel_mongo(days)
    else:
        by_channel = _drafted_by_channel_mongo(days)
    drafted = sum(by_channel.values())

    # Founder decisions (Mongo is canonical for approvals on every path).
    decisions = list(db["approvals"].find(
        {"decided_at": {"$gte": cutoff}},
        {"decision": 1, "decided_at": 1, "channel": 1}))
    n_approve = sum(1 for d in decisions if d.get("decision") == "approve")
    n_edit = sum(1 for d in decisions if d.get("decision") == "edit")
    n_reject = sum(1 for d in decisions if d.get("decision") == "reject")
    n_decided = len(decisions)

    # Daily approval-rate trend (approve+edit count as shipped-with-input).
    daily: dict[str, dict[str, int]] = {}
    for d in decisions:
        ts = d.get("decided_at")
        if not hasattr(ts, "date"):
            continue
        day = ts.date().isoformat()
        bucket = daily.setdefault(day, {"approve": 0, "edit": 0, "reject": 0})
        key = d.get("decision")
        if key in bucket:
            bucket[key] += 1
    approval_trend = [
        {"day": day, **counts,
         "rate": round((counts["approve"] + counts["edit"])
                       / max(1, sum(counts.values())), 3)}
        for day, counts in sorted(daily.items())
    ]

    # Published assets + attributed outcomes.
    published = db["attribution_map"].count_documents(
        {"approved_at": {"$gte": cutoff}})
    outcomes: dict[str, Any] = {"filled": 0, "pending": 0, "total_value": 0.0}
    if BQ is not None:
        try:
            sql = f"""
            SELECT status, COUNT(*) AS n, SUM(COALESCE(value, 0)) AS total
            FROM `{PROJECT_ID}.telemetry.outcomes`
            WHERE expected_by >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(),
                                               INTERVAL {int(days)} DAY)
            GROUP BY status
            """
            for r in BQ.query(sql).result():
                if r.status == "filled":
                    outcomes["filled"] = r.n
                    outcomes["total_value"] = round(float(r.total or 0), 4)
                elif r.status == "pending":
                    outcomes["pending"] = r.n
        except Exception as e:  # noqa: BLE001
            log.warning("founder-dashboard BQ outcomes query failed: %s", e)

    # ROI card. All estimates; assumptions ship with the payload.
    founder_minutes = round(
        n_decided * MINUTES_PER_DECISION + drafted * MINUTES_PER_DRAFT_REQUEST)
    freelancer_usd = round(sum(
        n * FREELANCER_RATE_BY_CHANNEL.get(ch, FREELANCER_RATE_DEFAULT)
        for ch, n in by_channel.items()))
    api_cost_usd = round(drafted * API_COST_PER_DRAFT, 2)

    return {
        "days": days,
        "assets": {
            "drafted": drafted,
            "decided": n_decided,
            "approved": n_approve,
            "edited": n_edit,
            "rejected": n_reject,
            "published": published,
            "by_channel": by_channel,
        },
        "approval_trend": approval_trend,
        "outcomes": outcomes,
        "roi": {
            "founder_minutes": founder_minutes,
            "freelancer_equivalent_usd": freelancer_usd,
            "api_cost_usd": api_cost_usd,
            "leverage": (round(freelancer_usd / api_cost_usd, 1)
                         if api_cost_usd else None),
            "assumptions": _assumptions(),
        },
    }


# ---------------------------------------------------------------------------
# /api/learning-receipts — before→after proof, real drafts only
# ---------------------------------------------------------------------------

@router.get("/api/learning-receipts")
def learning_receipts(days: int = 28, limit: int = 8) -> list[dict]:
    days = max(1, min(int(days), 90))
    limit = max(1, min(int(limit), 25))
    cutoff = datetime.now(UTC) - timedelta(days=days)
    db = mongo_tools.db()

    negatives = list(db["approvals"].find(
        {"decided_at": {"$gte": cutoff},
         "decision": {"$in": ["reject", "edit"]}},
    ).sort("decided_at", -1).limit(60))

    receipts: list[dict] = []
    for appr in negatives:
        before = db["actions"].find_one({
            "telemetry_id": appr.get("telemetry_id"),
            "eval_scores": {"$ne": None},
        })
        if not before:
            continue
        before_scores = _numeric_scores(before.get("eval_scores"))
        if not before_scores:
            continue
        after = db["actions"].find_one({
            "channel": before.get("channel"),
            "action_type": {"$regex": "^draft_"},
            "ts": {"$gt": before["ts"]},
            "telemetry_id": {"$ne": before["telemetry_id"]},
            "eval_scores": {"$ne": None},
        }, sort=[("ts", 1)])
        if not after:
            continue
        after_scores = _numeric_scores(after.get("eval_scores"))
        shared = sorted(set(before_scores) & set(after_scores))
        if not shared:
            continue
        deltas = {k: round(after_scores[k] - before_scores[k], 3)
                  for k in shared}
        top_rubric = max(deltas, key=lambda k: abs(deltas[k]))
        receipts.append({
            "channel": before.get("channel"),
            "decision": appr.get("decision"),
            "reason": appr.get("rejection_reason") or "",
            "decided_at": (appr.get("decided_at").isoformat()
                           if hasattr(appr.get("decided_at"), "isoformat")
                           else None),
            "before": {
                "telemetry_id": before["telemetry_id"],
                "ts": before["ts"].isoformat(),
                "scores": before_scores,
                "snippet": _snippet(before.get("raw")),
            },
            "after": {
                "telemetry_id": after["telemetry_id"],
                "ts": after["ts"].isoformat(),
                "scores": after_scores,
                "snippet": _snippet(after.get("raw")),
            },
            "deltas": deltas,
            "top_rubric": top_rubric,
            "top_delta": deltas[top_rubric],
            "improved": deltas[top_rubric] > 0,
        })
        if len(receipts) >= limit:
            break
    return receipts


# ---------------------------------------------------------------------------
# /api/learning-curve — daily quality index + rejections fed back
# ---------------------------------------------------------------------------

@router.get("/api/learning-curve")
def learning_curve(days: int = 28) -> list[dict]:
    from services.web_api.main import BQ, PROJECT_ID
    days = max(1, min(int(days), 90))
    cutoff = datetime.now(UTC) - timedelta(days=days)
    db = mongo_tools.db()

    points: dict[str, dict[str, Any]] = {}
    if BQ is not None:
        sql = f"""
        SELECT DATE(ts) AS day,
          AVG((
            COALESCE(CAST(JSON_VALUE(eval_scores, '$.brand_voice') AS FLOAT64), 0)
            + COALESCE(CAST(JSON_VALUE(eval_scores, '$.claim_support') AS FLOAT64), 0)
            + COALESCE(CAST(JSON_VALUE(eval_scores, '$.answer_extractability') AS FLOAT64), 0)
          ) / NULLIF(
            CAST(JSON_VALUE(eval_scores, '$.brand_voice') IS NOT NULL AS INT64)
            + CAST(JSON_VALUE(eval_scores, '$.claim_support') IS NOT NULL AS INT64)
            + CAST(JSON_VALUE(eval_scores, '$.answer_extractability') IS NOT NULL AS INT64), 0)
          ) AS quality,
          COUNT(*) AS n
        FROM `{PROJECT_ID}.telemetry.actions`
        WHERE action_type LIKE 'draft_%' AND eval_scores IS NOT NULL
          AND ts >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {int(days)} DAY)
        GROUP BY day ORDER BY day
        """
        try:
            for r in BQ.query(sql).result():
                points[r.day.isoformat()] = {
                    "day": r.day.isoformat(),
                    "quality": round(float(r.quality or 0), 4),
                    "n": r.n, "rejections": 0,
                }
        except Exception as e:  # noqa: BLE001
            log.warning("learning-curve BQ query failed: %s", e)
    if not points:
        for r in db["actions"].aggregate([
            {"$match": {"ts": {"$gte": cutoff},
                        "action_type": {"$regex": "^draft_"},
                        "eval_scores": {"$ne": None}}},
            {"$group": {
                "_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$ts"}},
                "quality": {"$avg": {"$avg": [
                    "$eval_scores.brand_voice",
                    "$eval_scores.claim_support",
                    "$eval_scores.answer_extractability"]}},
                "n": {"$sum": 1}}},
            {"$sort": {"_id": 1}},
        ]):
            points[r["_id"]] = {"day": r["_id"],
                                "quality": round(r["quality"] or 0, 4),
                                "n": r["n"], "rejections": 0}

    # Rejections fed back into the rubric (the loop's raw material).
    for r in db["approvals"].aggregate([
        {"$match": {"decided_at": {"$gte": cutoff}, "decision": "reject"}},
        {"$group": {
            "_id": {"$dateToString": {"format": "%Y-%m-%d",
                                       "date": "$decided_at"}},
            "n": {"$sum": 1}}},
    ]):
        if r["_id"] in points:
            points[r["_id"]]["rejections"] = r["n"]
        else:
            points[r["_id"]] = {"day": r["_id"], "quality": None,
                                "n": 0, "rejections": r["n"]}

    return sorted(points.values(), key=lambda p: p["day"])
