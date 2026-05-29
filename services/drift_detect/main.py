"""C10b — Daily drift detector.

Scans rubric trends; opens an investigation experiment in MongoDB for any
sustained drop. This is what the demo's Moment 5 narrates as "the system
flagged this drop last week."

Idempotent — investigation IDs are derived from (rubric, channel, day) so
a drift that persists for multiple days produces one ongoing investigation,
not many duplicates.
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

DRIFT_THRESHOLD = 0.10
MIN_SAMPLE = 20

RUBRICS = ["brand_voice", "claim_support"]


def main():
    for rubric in RUBRICS:
        drift_events = _detect_drift(rubric)
        for ev in drift_events:
            exp_id = _drift_exp_id(rubric, ev["channel"], ev["day"])
            existing = mongo_tools.find_one("experiments", {"_id": exp_id})
            if existing:
                log.info("drift already opened: %s", exp_id)
                continue
            _open_investigation(rubric, ev)


def _detect_drift(rubric: str) -> list[dict]:
    """Find (channel, day) cells where today's mean dropped > threshold vs trailing 28d."""
    sql = f"""
    WITH today AS (
      SELECT channel,
             AVG(CAST(JSON_VALUE(eval_scores, '$.{rubric}') AS FLOAT64)) AS mean_today,
             COUNT(*) AS n_today
      FROM `{PROJECT_ID}.telemetry.actions`
      WHERE DATE(ts) >= CURRENT_DATE() - 3 AND eval_scores IS NOT NULL
      GROUP BY channel
    ),
    baseline AS (
      SELECT channel,
             AVG(CAST(JSON_VALUE(eval_scores, '$.{rubric}') AS FLOAT64)) AS mean_baseline
      FROM `{PROJECT_ID}.telemetry.actions`
      WHERE DATE(ts) BETWEEN CURRENT_DATE() - 31 AND CURRENT_DATE() - 4
        AND eval_scores IS NOT NULL
      GROUP BY channel
    )
    SELECT today.channel, mean_today, mean_baseline, n_today
    FROM today JOIN baseline USING(channel)
    WHERE mean_baseline - mean_today >= {DRIFT_THRESHOLD}
      AND n_today >= {MIN_SAMPLE}
    """
    results = BQ.query(sql).result()
    return [{"channel": r.channel, "day": str(datetime.utcnow().date()),
             "mean_today": r.mean_today, "mean_baseline": r.mean_baseline,
             "drop": r.mean_baseline - r.mean_today, "n": r.n_today}
            for r in results]


def _drift_exp_id(rubric: str, channel: str, day: str) -> str:
    return f"exp_drift_{rubric}_{channel}_{day.replace('-', '')}"


def _open_investigation(rubric: str, ev: dict) -> None:
    """Create a running investigation experiment in MongoDB.

    Routed through the history helpers so re-runs of the drift detector
    (e.g., schedule overlap) produce a history.experiments trail rather
    than silently overwriting the canonical doc.
    """
    from mongo.history import (
        DocumentNotFound,
        insert_with_provenance,
        update_with_history,
    )

    exp = {
        "_id": _drift_exp_id(rubric, ev["channel"], ev["day"]),
        "title": f"Investigate {rubric} drop on {ev['channel']}",
        "hypothesis": (
            f"Rubric {rubric} dropped {ev['drop']:.2f} on {ev['channel']} "
            f"over the last 3 days vs trailing 28d baseline. Most likely "
            f"cause: a recent prompt change. Diff playbook history to "
            f"identify the change."
        ),
        "channel": ev["channel"],
        "variants": [
            {"id": "baseline_period", "playbook_version": "PRIOR",
             "metric_value": ev["mean_baseline"]},
            {"id": "current_period", "playbook_version": "CURRENT",
             "metric_value": ev["mean_today"]},
        ],
        "success_metric": f"{rubric}_score",
        "state": "running",
        "created_at": datetime.now(UTC),
        "tags": ["drift", "investigation", "auto_opened"],
        "auto_opened_by": "drift_detector",
    }
    existing = mongo_tools.find_one("experiments", {"_id": exp["_id"]})
    if existing is None:
        insert_with_provenance(
            "experiments", exp,
            actor_id="drift_detector",
            change_kind="drift_investigation_opened",
        )
    else:
        # Re-run on the same day — refresh the metrics + hypothesis.
        try:
            update_with_history(
                "experiments",
                {"_id": exp["_id"]},
                {"$set": {k: v for k, v in exp.items() if k != "_id"}},
                actor_id="drift_detector",
                change_kind="drift_investigation_refreshed",
            )
        except DocumentNotFound:
            # Concurrent delete; fall back to insert.
            insert_with_provenance(
                "experiments", exp,
                actor_id="drift_detector",
                change_kind="drift_investigation_opened",
            )
    log.warning("drift investigation opened: %s (drop=%.2f, n=%d)",
                exp["_id"], ev["drop"], ev["n"])


if __name__ == "__main__":
    main()
