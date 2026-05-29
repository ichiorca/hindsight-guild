#!/usr/bin/env bash
# C12 — Single hackathon service account for all agents + workers.
# Per-agent SAs are a Phase-2 upgrade.
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?must set PROJECT_ID}"
SA="sa-agents@${PROJECT_ID}.iam.gserviceaccount.com"

gcloud iam service-accounts create sa-agents \
  --display-name "Hackathon agents SA" --project "$PROJECT_ID" 2>/dev/null || true

ROLES=(
  roles/aiplatform.user
  roles/bigquery.dataEditor
  roles/bigquery.jobUser
  roles/secretmanager.secretAccessor
  roles/logging.logWriter
  roles/monitoring.metricWriter
  roles/storage.objectAdmin   # GCS calibration sets
  roles/run.invoker
)
for role in "${ROLES[@]}"; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:$SA" --role="$role" --condition=None \
    1>/dev/null
done

# Also create a Cloud Scheduler invoker SA for the cron triggers
gcloud iam service-accounts create sa-scheduler \
  --display-name "Cloud Scheduler invoker" --project "$PROJECT_ID" 2>/dev/null || true
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:sa-scheduler@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/run.invoker" --condition=None 1>/dev/null

echo "SA ready: $SA"
echo "Scheduler SA ready: sa-scheduler@${PROJECT_ID}.iam.gserviceaccount.com"
