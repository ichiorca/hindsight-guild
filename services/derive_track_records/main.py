"""Recompute skill track records as a DERIVED collection.

Replaces the practice of writing track_record inline on the skill doc.
Now the canonical state.skills (or legacy `skills`) collection holds only
identity + versioning + ownership; the performance numbers live in
derived.skill_track_records with a freshness SLA.

Why this matters: track_record was being stored as canonical state but was
actually a function of (skill_id, skill_version) over the event log. Writers
mutated it inconsistently. Readers couldn't tell when it was last computed.

Runs nightly after eval_harness. Reads telemetry.actions, writes to
derived.skill_track_records as a full replace.
"""
from __future__ import annotations

import logging
import os
from datetime import UTC, datetime

from shared import mongo_tools
from shared.clients import bigquery_client

mongo_tools.use_secret("mongo_uri_writer")

PROJECT_ID = os.environ["PROJECT_ID"]
BQ = bigquery_client()
log = logging.getLogger(__name__)

FRESHNESS_SLA = "PT24H"   # ISO-8601 duration — derived data older than this is stale

# Aggregation window for the displayed track record. 0 = all-time (the
# historical default; the /skills UI shows lifetime performance). Note this
# is intentionally DECOUPLED from services/promotion_gate, which evaluates
# candidates on a fixed 30-day window: promotion decisions need recency, the
# displayed track record is a lifetime view. The window is stamped onto each
# derived doc (_derived.window_days) so consumers know which they're reading
# rather than guessing.
TRACK_RECORD_WINDOW_DAYS = int(os.environ.get("TRACK_RECORD_WINDOW_DAYS", "0"))


def _window_clause() -> str:
    """Optional ``AND ts >= ...`` fragment for the aggregation window."""
    if TRACK_RECORD_WINDOW_DAYS > 0:
        return (f"AND ts >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), "
                f"INTERVAL {TRACK_RECORD_WINDOW_DAYS} DAY)")
    return ""


def main():
    db = mongo_tools.db()
    now = datetime.now(UTC)

    sql = f"""
    SELECT
      skill_id, skill_version,
      COUNT(*) AS action_count,
      AVG(CAST(JSON_VALUE(eval_scores, '$.brand_voice')      AS FLOAT64)) AS mean_brand_voice,
      AVG(CAST(JSON_VALUE(eval_scores, '$.claim_support')    AS FLOAT64)) AS mean_claim_support,
      AVG(CAST(JSON_VALUE(eval_scores, '$.claim_risk')       AS FLOAT64)) AS mean_claim_risk,
      AVG(CAST(JSON_VALUE(eval_scores, '$.icp_relevance')    AS FLOAT64)) AS mean_icp_relevance,
      AVG(CAST(JSON_VALUE(eval_scores, '$.originality')      AS FLOAT64)) AS mean_originality,
      AVG(CAST(JSON_VALUE(eval_scores, '$.conversion_intent') AS FLOAT64)) AS mean_conversion_intent,
      MIN(ts) AS first_seen,
      MAX(ts) AS last_seen
    FROM `{PROJECT_ID}.telemetry.actions`
    WHERE eval_scores IS NOT NULL
      {_window_clause()}
    GROUP BY skill_id, skill_version
    """
    # BigQuery is primary. LOCAL_DEV (no BQ client) falls back to the identical
    # Mongo aggregation over the dual-written `actions` collection; rows come
    # back as dicts keyed like the BQ SELECT aliases either way.
    if BQ is None:
        from shared import telemetry_reads
        rows = telemetry_reads.skill_track_records_rollup(
            window_days=TRACK_RECORD_WINDOW_DAYS, db=db)
    else:
        rows = [dict(r) for r in BQ.query(sql).result()]
    log.info("computed track records for %d (skill, version) pairs", len(rows))

    # Full replace pattern — wipe then bulk insert
    db["derived.skill_track_records"].delete_many({})

    docs = []
    for r in rows:
        docs.append({
            "_id": f"{r['skill_id']}@{r['skill_version']}",
            "skill_id": r["skill_id"],
            "skill_version": r["skill_version"],
            "action_count": r["action_count"],
            "mean_brand_voice": r["mean_brand_voice"],
            "mean_claim_support": r["mean_claim_support"],
            "mean_claim_risk": r["mean_claim_risk"],
            "mean_icp_relevance": r["mean_icp_relevance"],
            "mean_originality": r["mean_originality"],
            "mean_conversion_intent": r["mean_conversion_intent"],
            "first_seen": r["first_seen"],
            "last_seen": r["last_seen"],
            "_derived": {
                "derived_at": now,
                "derived_by": "service:derive_track_records",
                "derived_from": [
                    {"kind": "bigquery_table",
                     "id": f"{PROJECT_ID}.telemetry.actions"},
                ],
                "freshness_sla": FRESHNESS_SLA,
                "window_days": TRACK_RECORD_WINDOW_DAYS,  # 0 = all-time
                "stale": False,
            },
        })
    if docs:
        db["derived.skill_track_records"].insert_many(docs)

    log.info("wrote %d derived records to derived.skill_track_records", len(docs))

    # Agent Skill rollup — same metrics, but attributed via skills_loaded
    # rather than the action's owning playbook. This is what lets the
    # Self-Critique Agent identify systematic issues with cross-channel
    # Skills like house-style. UNNEST flattens the ARRAY<STRING> column.
    _derive_agent_skill_track_records(db, now)


def _derive_agent_skill_track_records(db, now: datetime) -> None:
    """Per Agent Skill (house-style, copywriting, etc.) rubric rollup.

    Joins telemetry.actions with the UNNESTed skills_loaded array so each
    action contributes to every Skill it loaded. Cross-channel rollup —
    the WHERE on eval_scores naturally restricts to drafting actions.
    """
    sql = f"""
    SELECT
      skill_name,
      COUNT(*) AS action_count,
      AVG(CAST(JSON_VALUE(eval_scores, '$.brand_voice')      AS FLOAT64)) AS mean_brand_voice,
      AVG(CAST(JSON_VALUE(eval_scores, '$.claim_support')    AS FLOAT64)) AS mean_claim_support,
      AVG(CAST(JSON_VALUE(eval_scores, '$.claim_risk')       AS FLOAT64)) AS mean_claim_risk,
      AVG(CAST(JSON_VALUE(eval_scores, '$.icp_relevance')    AS FLOAT64)) AS mean_icp_relevance,
      AVG(CAST(JSON_VALUE(eval_scores, '$.originality')      AS FLOAT64)) AS mean_originality,
      AVG(CAST(JSON_VALUE(eval_scores, '$.conversion_intent') AS FLOAT64)) AS mean_conversion_intent,
      ARRAY_AGG(DISTINCT channel IGNORE NULLS) AS channels_seen,
      MIN(ts) AS first_seen,
      MAX(ts) AS last_seen
    FROM `{PROJECT_ID}.telemetry.actions`,
         UNNEST(skills_loaded) AS skill_name
    WHERE eval_scores IS NOT NULL
      {_window_clause()}
    GROUP BY skill_name
    """
    if BQ is None:
        from shared import telemetry_reads
        rows = telemetry_reads.agent_skill_track_records_rollup(
            window_days=TRACK_RECORD_WINDOW_DAYS, db=db)
    else:
        rows = [dict(r) for r in BQ.query(sql).result()]
    log.info("computed agent-skill track records for %d Skills", len(rows))

    db["derived.agent_skill_track_records"].delete_many({})
    docs = []
    for r in rows:
        docs.append({
            "_id": r["skill_name"],
            "skill_name": r["skill_name"],
            "action_count": r["action_count"],
            "mean_brand_voice": r["mean_brand_voice"],
            "mean_claim_support": r["mean_claim_support"],
            "mean_claim_risk": r["mean_claim_risk"],
            "mean_icp_relevance": r["mean_icp_relevance"],
            "mean_originality": r["mean_originality"],
            "mean_conversion_intent": r["mean_conversion_intent"],
            "channels_seen": list(r.get("channels_seen") or []),
            "first_seen": r["first_seen"],
            "last_seen": r["last_seen"],
            "_derived": {
                "derived_at": now,
                "derived_by": "service:derive_track_records",
                "derived_from": [
                    {"kind": "bigquery_table",
                     "id": f"{PROJECT_ID}.telemetry.actions"},
                ],
                "freshness_sla": FRESHNESS_SLA,
                "window_days": TRACK_RECORD_WINDOW_DAYS,  # 0 = all-time
                "stale": False,
            },
        })
    if docs:
        db["derived.agent_skill_track_records"].insert_many(docs)
    log.info("wrote %d derived records to derived.agent_skill_track_records",
             len(docs))


if __name__ == "__main__":
    main()
