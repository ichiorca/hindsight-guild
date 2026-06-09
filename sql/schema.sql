-- C1 — BigQuery schema. Apply with:
--   envsubst < sql/schema.sql | bq query --use_legacy_sql=false
-- The setup.sh script does this for you.
--
-- Tables:
--   telemetry.actions   — append-only, one row per agent action (drafts, data pulls, etc.)
--   telemetry.outcomes  — outcome slots created at action emit, filled async by outcome_attach
--   training.edits      — founder edits captured from the approval Sheet
--
-- Views (no app writes):
--   analytics.skill_track_record  — per-skill mean rubric scores
--   analytics.rubric_trend_28d    — rolling daily means per agent/channel
--   analytics.before_vs_after     — per-skill score deltas across versions
--                                   (powers the "did the change help?" panel)

CREATE TABLE IF NOT EXISTS `${PROJECT_ID}.telemetry.actions` (
  telemetry_id    STRING NOT NULL,
  ts              TIMESTAMP NOT NULL,
  agent           STRING NOT NULL,
  skill_id        STRING NOT NULL,
  -- Agent Skills loaded during this action (Tier 2/3 read_skill calls).
  -- Enables Skill-level rubric rollups even when one playbook references
  -- many shared Skills (house-style, copywriting, etc.).
  skills_loaded   ARRAY<STRING>,
  skill_version   STRING NOT NULL,
  action_type     STRING NOT NULL,
  channel         STRING,
  experiment_id   STRING,
  variant_id      STRING,
  prompt_file     STRING,
  approval_id     STRING,
  eval_scores     JSON,
  edit_summary    JSON,
  model_armor     JSON,
  trace_id        STRING,
  raw             JSON
)
PARTITION BY DATE(ts)
CLUSTER BY agent, experiment_id;

-- Idempotent migration: add skills_loaded to telemetry.actions on existing
-- deployments. CREATE TABLE IF NOT EXISTS above only catches fresh installs;
-- this ALTER catches projects that ran the prior schema before this column
-- was introduced. Safe to re-run.
ALTER TABLE `${PROJECT_ID}.telemetry.actions`
  ADD COLUMN IF NOT EXISTS skills_loaded ARRAY<STRING>;

CREATE TABLE IF NOT EXISTS `${PROJECT_ID}.telemetry.outcomes` (
  telemetry_id    STRING NOT NULL,
  slot_name       STRING NOT NULL,
  metric          STRING NOT NULL,
  source          STRING NOT NULL,
  expected_by     TIMESTAMP NOT NULL,
  filled_at       TIMESTAMP,
  value           FLOAT64,
  provenance      JSON,
  status          STRING NOT NULL
)
PARTITION BY DATE(expected_by)
CLUSTER BY status, source;

CREATE TABLE IF NOT EXISTS `${PROJECT_ID}.training.edits` (
  edit_id          STRING NOT NULL,
  telemetry_id     STRING NOT NULL,
  ts               TIMESTAMP NOT NULL,
  before_text      STRING,
  after_text       STRING,
  edit_categories  ARRAY<STRING>,
  rejection_reason STRING
);

CREATE OR REPLACE VIEW `${PROJECT_ID}.analytics.skill_track_record` AS
SELECT
  skill_id, skill_version,
  COUNT(*) AS action_count,
  AVG(CAST(JSON_VALUE(eval_scores, '$.brand_voice') AS FLOAT64))   AS mean_brand_voice,
  AVG(CAST(JSON_VALUE(eval_scores, '$.claim_support') AS FLOAT64)) AS mean_claim_support
FROM `${PROJECT_ID}.telemetry.actions`
WHERE eval_scores IS NOT NULL
GROUP BY skill_id, skill_version;

CREATE OR REPLACE VIEW `${PROJECT_ID}.analytics.rubric_trend_28d` AS
SELECT
  DATE(ts) AS day,
  agent,
  channel,
  AVG(CAST(JSON_VALUE(eval_scores, '$.brand_voice') AS FLOAT64))   AS mean_brand_voice,
  AVG(CAST(JSON_VALUE(eval_scores, '$.claim_support') AS FLOAT64)) AS mean_claim_support,
  -- AEO rubric (PRD-01) — keeps the BQ path field-compatible with the Mongo
  -- fallback in services/web_api/routers/rubric_trends.py.
  AVG(CAST(JSON_VALUE(eval_scores, '$.answer_extractability') AS FLOAT64)) AS mean_answer_extractability,
  COUNT(*) AS n
FROM `${PROJECT_ID}.telemetry.actions`
WHERE ts >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 28 DAY)
  AND eval_scores IS NOT NULL
GROUP BY day, agent, channel;

-- before_vs_after: per-skill score by version, used by /api/before-vs-after to
-- answer "did promoting v2 actually help?". period = the skill_version string,
-- so the UI can pivot it into a side-by-side bar chart.
CREATE OR REPLACE VIEW `${PROJECT_ID}.analytics.before_vs_after` AS
SELECT
  skill_id,
  skill_version,
  skill_version AS period,
  AVG(CAST(JSON_VALUE(eval_scores, '$.brand_voice') AS FLOAT64))   AS mean_brand_voice,
  AVG(CAST(JSON_VALUE(eval_scores, '$.claim_support') AS FLOAT64)) AS mean_claim_support,
  COUNT(*) AS n
FROM `${PROJECT_ID}.telemetry.actions`
WHERE eval_scores IS NOT NULL
GROUP BY skill_id, skill_version;
