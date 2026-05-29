# Looker Studio dashboard — manual build

One report, five blocks, BigQuery as data source. ~30 min to build.

## Data source

In Looker Studio: **Add data → BigQuery → My Projects → ${PROJECT_ID}**.
Pick `analytics` as the dataset.

## Scheduled queries (run these in BigQuery first)

Save as scheduled queries with a 15-minute cadence:

```sql
-- analytics.this_week_summary
CREATE OR REPLACE TABLE analytics.this_week_summary AS
SELECT
  COUNTIF(action_type LIKE 'draft_%') AS drafts,
  COUNTIF(edit_summary IS NOT NULL) AS edits,
  COUNTIF(approval_id IS NOT NULL) AS approvals,
  COUNTIF(JSON_VALUE(model_armor, '$.decision') = 'block') AS armor_blocks
FROM telemetry.actions
WHERE ts >= TIMESTAMP_TRUNC(CURRENT_TIMESTAMP(), WEEK);
```

```sql
-- analytics.outcome_health
CREATE OR REPLACE TABLE analytics.outcome_health AS
SELECT source, status, COUNT(*) AS n,
       COUNTIF(expected_by < CURRENT_TIMESTAMP() AND status='pending') AS overdue
FROM telemetry.outcomes
WHERE expected_by >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 7 DAY)
GROUP BY source, status;
```

```sql
-- analytics.before_vs_after  — the self-learning evidence panel
CREATE OR REPLACE TABLE analytics.before_vs_after AS
WITH bounds AS (
  SELECT skill_id, skill_version, MIN(ts) AS first_seen, MAX(ts) AS last_seen
  FROM telemetry.actions WHERE eval_scores IS NOT NULL
  GROUP BY skill_id, skill_version
),
period_scores AS (
  SELECT a.skill_id, a.skill_version,
         CASE
           WHEN a.ts <= TIMESTAMP_ADD(b.first_seen, INTERVAL 10 DAY) THEN 'first_10_days'
           WHEN a.ts >= TIMESTAMP_SUB(b.last_seen, INTERVAL 10 DAY)  THEN 'last_10_days'
           ELSE 'middle'
         END AS period,
         CAST(JSON_VALUE(a.eval_scores, '$.brand_voice')   AS FLOAT64) AS bv,
         CAST(JSON_VALUE(a.eval_scores, '$.claim_support') AS FLOAT64) AS cs
  FROM telemetry.actions a JOIN bounds b USING(skill_id, skill_version)
  WHERE a.eval_scores IS NOT NULL
)
SELECT skill_id, skill_version, period,
       AVG(bv) AS mean_brand_voice, AVG(cs) AS mean_claim_support, COUNT(*) AS n
FROM period_scores WHERE period IN ('first_10_days', 'last_10_days')
GROUP BY skill_id, skill_version, period;
```

You also need an hourly MongoDB → BigQuery export of the `experiments`
collection to `analytics.experiments_snapshot` for Block 4. Run as a small
Cloud Run job (~10 lines: read all docs, BQ.insert_rows_json with full replace).

## Dashboard blocks

| # | Type | Source | Purpose |
|---|---|---|---|
| 1 | Scorecard set (4 metrics) | `analytics.this_week_summary` | This week: drafts, edits, approvals, armor_blocks |
| 2 | Time-series (line chart) | `analytics.rubric_trend_28d` view | brand_voice + claim_support by day, broken by channel |
| 3 | Stacked bar | `analytics.outcome_health` | Outcomes by status per source |
| 4 | Table | `analytics.experiments_snapshot` | Running + recently decided experiments |
| 5 | Paired bar | `analytics.before_vs_after` | First-10-days vs last-10-days rubric scores per skill version — the "is it learning?" panel |

## Export & version

After building, **File → Make a copy → Save as template** and download the
JSON config. Commit to `dashboards/founder_view.lookerstudio.json`.

## Refresh during demo

Looker Studio's auto-refresh can lag. **Hit the Refresh button 5 minutes
before demo time.**
