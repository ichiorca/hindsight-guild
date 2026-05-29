#!/usr/bin/env bash
# Create or update Cloud Scheduler triggers — one per Cloud Run job.
#
# Schedules + ordering rationale live in deploy/env.sh next to the
# SCHEDULES map.

# shellcheck source=deploy/env.sh
source "$(dirname "$0")/env.sh"

echo "==> Wiring ${#SCHEDULES[@]} Cloud Scheduler triggers"
for job in "${!SCHEDULES[@]}"; do
  schedule="${SCHEDULES[$job]}"
  trigger="${job}-trigger"
  uri="https://run.googleapis.com/v2/projects/${PROJECT_ID}/locations/${REGION}/jobs/${job}:run"

  echo "  -> ${trigger}  (${schedule})"

  # `gcloud scheduler jobs create` errors if the job exists; `update`
  # errors if it doesn't. Try create first, fall back to update so the
  # script is idempotent across re-runs.
  if gcloud scheduler jobs describe "$trigger" \
       --location="$REGION" --project="$PROJECT_ID" &>/dev/null; then
    gcloud scheduler jobs update http "$trigger" \
      --location="$REGION" \
      --schedule="$schedule" \
      --uri="$uri" \
      --http-method=POST \
      --oauth-service-account-email="$SCHED_SA" \
      --project="$PROJECT_ID"
  else
    gcloud scheduler jobs create http "$trigger" \
      --location="$REGION" \
      --schedule="$schedule" \
      --uri="$uri" \
      --http-method=POST \
      --oauth-service-account-email="$SCHED_SA" \
      --project="$PROJECT_ID"
  fi
done

echo ""
echo "==> Schedulers wired. Trigger one manually to smoke-test:"
echo "    gcloud scheduler jobs run outcome-attach-trigger --location=${REGION}"
