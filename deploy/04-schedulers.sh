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

# ---------------------------------------------------------------------------
# HTTP endpoint schedulers — POST to web-api endpoints rather than running a
# Cloud Run Job. PRD-02 signal poll/route run in-process inside web-api, so we
# resolve its URL and hit /api/signals/poll-now + /route-now on a cron.
# Service invocation uses an OIDC token (audience = the service URL), unlike
# the OAuth used above for the run.googleapis.com Jobs API.
# ---------------------------------------------------------------------------
WEB_API_URL="$(gcloud run services describe web-api \
  --region="$REGION" --project="$PROJECT_ID" \
  --format='value(status.url)' 2>/dev/null || true)"

if [[ -z "$WEB_API_URL" ]]; then
  echo "  !! web-api service URL not found — deploy web-api (deploy/02) before"
  echo "     wiring signal endpoint schedulers; skipping those."
else
  echo "==> Wiring ${#SIGNAL_ENDPOINT_SCHEDULES[@]} web-api endpoint schedulers"
  for name in "${!SIGNAL_ENDPOINT_SCHEDULES[@]}"; do
    spec="${SIGNAL_ENDPOINT_SCHEDULES[$name]}"
    schedule="${spec%%|*}"      # cron, before the '|'
    path="${spec##*|}"          # endpoint path, after the '|'
    trigger="${name}-trigger"
    uri="${WEB_API_URL}${path}"

    echo "  -> ${trigger}  (${schedule})  ${path}"

    if gcloud scheduler jobs describe "$trigger" \
         --location="$REGION" --project="$PROJECT_ID" &>/dev/null; then
      gcloud scheduler jobs update http "$trigger" \
        --location="$REGION" \
        --schedule="$schedule" \
        --uri="$uri" \
        --http-method=POST \
        --oidc-service-account-email="$SCHED_SA" \
        --oidc-token-audience="$WEB_API_URL" \
        --project="$PROJECT_ID"
    else
      gcloud scheduler jobs create http "$trigger" \
        --location="$REGION" \
        --schedule="$schedule" \
        --uri="$uri" \
        --http-method=POST \
        --oidc-service-account-email="$SCHED_SA" \
        --oidc-token-audience="$WEB_API_URL" \
        --project="$PROJECT_ID"
    fi
  done
fi

echo ""
echo "==> Schedulers wired. Trigger one manually to smoke-test:"
echo "    gcloud scheduler jobs run outcome-attach-trigger --location=${REGION}"
echo "    gcloud scheduler jobs run signal-watcher-trigger --location=${REGION}"
