"""Nightly eval re-run — all 6 rubrics, all yesterday's drafts.

Pulls yesterday's drafting actions from telemetry.actions (where eval_scores
may have been computed against a stale judge or just brand_voice+claim_support),
re-evaluates against all six rubrics using the current Vertex AI Eval Service
judge, and writes back enriched scores to telemetry.actions.eval_scores.

Why we need this separately from the inline at-draft-time eval:
  1. Inline eval scores only two rubrics for cost/latency. The nightly job
     scores all six.
  2. Judge models drift. Re-grading against the current judge baseline gives
     us a consistent quality signal over time.
  3. Calibration sets get re-graded quarterly; nightly catches calibration
     shifts faster than the drift detector alone.

Scheduled by Cloud Scheduler at 03:00 UTC daily, before the drift detector
runs at 04:30.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime

from google.cloud import bigquery

from shared.clients import bigquery_client
from shared.rubrics import ALL_RUBRICS, JUDGE_MODEL, evaluate_batch

PROJECT_ID = os.environ["PROJECT_ID"]
BQ = bigquery_client()
log = logging.getLogger(__name__)


def main():
    rows = _pull_yesterdays_drafts()
    if not rows:
        log.info("no drafts to re-evaluate")
        return

    log.info("re-evaluating %d drafts on all %d rubrics",
             len(rows), len(ALL_RUBRICS))

    # vertexai.evaluation operates on a pandas DataFrame; rows must include
    # 'candidate' and may include channel + icp_description for the rubrics
    # that use them.
    eval_inputs = [
        {
            "candidate": _draft_text(r),
            "channel": r["channel"],
            "icp_description": r.get("icp_description", ""),
        }
        for r in rows
    ]
    metrics_table = evaluate_batch(eval_inputs)

    # Write enriched eval_scores back to telemetry.actions. Stamp judge
    # provenance so a re-grade is attributable to the judge that produced
    # it (judges drift; this re-grade overwrites the inline scores).
    scored_at = datetime.now(UTC).isoformat()
    for i, row in enumerate(rows):
        scores = {}
        for r in ALL_RUBRICS:
            col = f"{r.name}/score"
            if col in metrics_table.columns:
                try:
                    scores[r.name] = float(metrics_table.iloc[i][col]) / 5.0
                except (ValueError, TypeError):
                    pass
        scores["judge_model"] = JUDGE_MODEL
        scores["scored_at"] = scored_at
        _update_action_scores(row["telemetry_id"], scores)

    log.info("re-evaluation complete: %d actions updated", len(rows))


def _pull_yesterdays_drafts() -> list[dict]:
    sql = f"""
    SELECT telemetry_id, channel, raw
    FROM `{PROJECT_ID}.telemetry.actions`
    WHERE action_type LIKE 'draft_%'
      AND DATE(ts) = CURRENT_DATE() - 1
      AND raw IS NOT NULL
    LIMIT 500
    """
    return [dict(r) for r in BQ.query(sql).result()]


def _draft_text(row: dict) -> str:
    """The full draft body is captured in raw.draft when the agent emits.

    Substack drafts are dicts ({headline, subtitle, body_markdown}); flatten
    them to the body text so the rubric judge scores prose, not the
    Python-repr of a dict.
    """
    try:
        raw = row.get("raw") or {}
        if isinstance(raw, str):
            raw = json.loads(raw)
        draft = raw.get("draft") or raw.get("output_text") or ""
        if isinstance(draft, dict):
            return (
                draft.get("body_markdown")
                or draft.get("body")
                or draft.get("text")
                or ""
            )
        return draft
    except Exception:
        return ""


def _update_action_scores(telemetry_id: str, scores: dict[str, float]) -> None:
    BQ.query(
        f"""
        UPDATE `{PROJECT_ID}.telemetry.actions`
        SET eval_scores = PARSE_JSON(@scores)
        WHERE telemetry_id = @id
        """,
        job_config=bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("scores", "STRING", json.dumps(scores)),
            bigquery.ScalarQueryParameter("id", "STRING", telemetry_id),
        ]),
    ).result()


if __name__ == "__main__":
    main()
