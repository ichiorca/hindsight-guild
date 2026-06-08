"""C10 — Outcome attach Cloud Run job.

Reads pending outcome slots from telemetry.outcomes, fills them from source
APIs (GA4 BigQuery export, HubSpot, Google Ads, LinkedIn Ads), updates
MongoDB experiment state when decision thresholds cross.

Scheduled every 6h by Cloud Scheduler. Idempotent — re-runs on the same data
produce the same outcome.

Uses pymongo directly (not MCP) — this is a non-LLM caller, MCP indirection
is unnecessary and adds latency.
"""
from __future__ import annotations

import logging
import os
from datetime import UTC, datetime

from google.cloud import bigquery

from shared import mongo_tools
from shared.clients import bigquery_client

# Workers use the writer secret. Atlas enforces server-side; this is just
# the secret pointer.
mongo_tools.use_secret("mongo_uri_writer")

from services.outcome_attach.sources import SOURCES  # noqa: E402

PROJECT_ID = os.environ["PROJECT_ID"]
BQ = bigquery_client()
log = logging.getLogger(__name__)


def main():
    pending = BQ.query(f"""
        SELECT telemetry_id, slot_name, metric, source, expected_by
        FROM `{PROJECT_ID}.telemetry.outcomes`
        WHERE status = 'pending' AND expected_by <= CURRENT_TIMESTAMP()
        LIMIT 500
    """).result()

    for row in pending:
        try:
            handler = SOURCES.get(row.source)
            if not handler:
                log.error("no handler for source %s", row.source)
                continue
            value = handler(row.telemetry_id, row.metric)
            if value is None:
                _maybe_expire(row)
                continue
            _mark_filled(row.telemetry_id, row.slot_name, value, row.source)
            _maybe_decide_experiment(row.telemetry_id, value)
        except Exception as e:
            log.exception("attach_failed: %s/%s: %s", row.telemetry_id, row.slot_name, e)


def _mark_filled(telemetry_id: str, slot_name: str, value: float, source: str) -> None:
    BQ.query(
        f"""
        UPDATE `{PROJECT_ID}.telemetry.outcomes`
        SET status='filled', value=@v, filled_at=CURRENT_TIMESTAMP(),
            provenance=JSON_OBJECT('source', @src)
        WHERE telemetry_id=@id AND slot_name=@slot
        """,
        job_config=bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("v", "FLOAT64", value),
            bigquery.ScalarQueryParameter("src", "STRING", source),
            bigquery.ScalarQueryParameter("id", "STRING", telemetry_id),
            bigquery.ScalarQueryParameter("slot", "STRING", slot_name),
        ]),
    ).result()


def _maybe_expire(row) -> None:
    age_days = (datetime.now(UTC) - row.expected_by).days
    if age_days > 7:
        BQ.query(
            f"""
            UPDATE `{PROJECT_ID}.telemetry.outcomes` SET status='expired'
            WHERE telemetry_id=@id AND slot_name=@slot
            """,
            job_config=bigquery.QueryJobConfig(query_parameters=[
                bigquery.ScalarQueryParameter("id", "STRING", row.telemetry_id),
                bigquery.ScalarQueryParameter("slot", "STRING", row.slot_name),
            ]),
        ).result()


def _experiment_for(telemetry_id: str):
    """(experiment_id, variant_id) for a telemetry_id, or None.

    BigQuery is primary; LOCAL_DEV (no BQ client) reads the dual-written Mongo
    ``actions`` collection."""
    if BQ is None:
        from shared import telemetry_reads
        return telemetry_reads.action_experiment_for(telemetry_id)
    result = BQ.query(
        f"""
        SELECT experiment_id, variant_id FROM `{PROJECT_ID}.telemetry.actions`
        WHERE telemetry_id=@id
        """,
        job_config=bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("id", "STRING", telemetry_id),
        ]),
    ).result()
    row = next(iter(result), None)
    if not row:
        return None
    return (row.experiment_id, row.variant_id)


def _variant_stats(experiment_id: str, success_metric: str) -> dict:
    """Per-variant ``{variant_id: (mean_val, n)}`` for the decision.

    For ``*_score`` metrics, averages eval_scores; otherwise joins filled
    outcome slots. BigQuery primary; LOCAL_DEV falls back to the identical
    Mongo aggregation over the dual-written ``actions`` / ``outcomes``."""
    if BQ is None:
        from shared import telemetry_reads
        return telemetry_reads.experiment_variant_stats(experiment_id, success_metric)
    if success_metric.endswith("_score"):
        rubric = success_metric.replace("_score", "")
        summary = BQ.query(
            f"""
            SELECT variant_id,
                   AVG(CAST(JSON_VALUE(eval_scores, '$.{rubric}') AS FLOAT64)) AS mean_val,
                   COUNT(*) AS n
            FROM `{PROJECT_ID}.telemetry.actions`
            WHERE experiment_id=@exp AND eval_scores IS NOT NULL
            GROUP BY variant_id
            """,
            job_config=bigquery.QueryJobConfig(query_parameters=[
                bigquery.ScalarQueryParameter("exp", "STRING", experiment_id),
            ]),
        ).result()
    else:
        summary = BQ.query(
            f"""
            SELECT a.variant_id, AVG(o.value) AS mean_val, COUNT(*) AS n
            FROM `{PROJECT_ID}.telemetry.actions` a
            JOIN `{PROJECT_ID}.telemetry.outcomes` o USING(telemetry_id)
            WHERE a.experiment_id=@exp
              AND o.slot_name=@slot AND o.status='filled'
            GROUP BY a.variant_id
            """,
            job_config=bigquery.QueryJobConfig(query_parameters=[
                bigquery.ScalarQueryParameter("exp", "STRING", experiment_id),
                bigquery.ScalarQueryParameter("slot", "STRING", success_metric),
            ]),
        ).result()
    return {r.variant_id: (r.mean_val, r.n) for r in summary}


def _maybe_decide_experiment(telemetry_id: str, outcome_value: float) -> None:
    """Look up the experiment for this telemetry_id; transition state when decision rule met.

    experiments.success_metric is the SLOT NAME (e.g. 'engagement_72h'), not
    the bare metric. Two experiments observing the same metric at different
    horizons (24h vs 72h) are different success_metrics.

    Eval-score-backed experiments (success_metric ends in '_score') are
    decided from telemetry.actions.eval_scores instead. NOTE: this branch is
    only reached when an outcome slot fills, so eval-score-backed experiments
    need a separate cron (deferred to week 2 per DECISIONS.md).

    Data reads go through _experiment_for / _variant_stats, which use BigQuery
    in prod and the dual-written Mongo collections in LOCAL_DEV — the decision
    logic below is identical either way.
    """
    ref = _experiment_for(telemetry_id)
    if not ref or not ref[0]:
        return
    experiment_id = ref[0]

    exp = mongo_tools.find_one("experiments", {"_id": experiment_id})
    if not exp or exp.get("state") != "running":
        return

    success_metric = exp["success_metric"]
    by_variant = _variant_stats(experiment_id, success_metric)
    if len(by_variant) < 2:
        return

    min_n = exp.get("min_n_per_arm", 400)
    mde = exp.get("mde", 0.03)
    if all(n >= min_n for _, n in by_variant.values()):
        ranked = sorted(by_variant.items(), key=lambda kv: kv[1][0], reverse=True)
        winner, (winner_mean, _) = ranked[0]
        runner, (runner_mean, _) = ranked[1]
        if winner_mean - runner_mean >= mde:
            mongo_tools.transition_experiment_state(
                experiment_id, "decided",
                result={"winner": winner,
                        "lift": winner_mean - runner_mean,
                        "n": min_n},
                lesson=(f"Variant {winner} beat {runner} by "
                        f"{winner_mean - runner_mean:.3f} on {success_metric}"),
            )


if __name__ == "__main__":
    main()
