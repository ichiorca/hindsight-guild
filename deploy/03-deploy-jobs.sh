#!/usr/bin/env bash
# Deploy every Cloud Run *job* (the cron workers) from pre-built images.
# Schedulers are wired in 04-schedulers.sh.

# shellcheck source=deploy/env.sh
source "$(dirname "$0")/env.sh"

echo "==> Deploying ${#JOBS[@]} Cloud Run jobs"
for job in "${!JOBS[@]}"; do
  # Each job's image was tagged in cloudbuild.yaml with the same short
  # name as the Cloud Run job (kebab-case). The image's own CMD already
  # runs `python -m services.<x>.main`, so no --command override needed.
  echo "  -> ${job}  ($(image_ref "$job"))"
  gcloud run jobs deploy "$job" \
    --image="$(image_ref "$job")" \
    --region="$REGION" \
    --service-account="$SA" \
    --set-env-vars="PROJECT_ID=${PROJECT_ID},REGION=${REGION}" \
    --memory=1Gi --cpu=1 \
    --task-timeout=900s \
    --project="$PROJECT_ID"
done

echo ""
echo "==> Jobs deployed. Next: deploy/04-schedulers.sh"
