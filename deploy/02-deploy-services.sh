#!/usr/bin/env bash
# Deploy every Cloud Run *service* (A2A agents + HTTP services) from
# pre-built images in Artifact Registry. Run 01-build-images.sh first.
#
# What this script does NOT do:
#   - Build images (that's 01).
#   - Deploy the UI (that's 05; it needs the web-api URL first).
#   - Deploy cron jobs (that's 03).
#   - Wire IAM bindings (that's 06).

# shellcheck source=deploy/env.sh
source "$(dirname "$0")/env.sh"

# ---------------------------------------------------------------------------
# A2A agent services (13) — every one runs the same agent-base image with a
# different ASGI app target. --no-allow-unauthenticated; workers reach them
# via sa-agents which gets run.invoker bound in 06-bind-iam.sh.
# ---------------------------------------------------------------------------
echo "==> Deploying ${#A2A_APPS[@]} A2A agent services from agent-base:latest"
for name in "${!A2A_APPS[@]}"; do
  app_attr="${A2A_APPS[$name]}"
  echo "  -> a2a-${name//_/-}  (agents.a2a_server:${app_attr})"
  gcloud run deploy "a2a-${name//_/-}" \
    --image="$(image_ref agent-base)" \
    --command="uvicorn" \
    --args="agents.a2a_server:${app_attr},--host,0.0.0.0,--port,8080" \
    --region="$REGION" \
    --service-account="$SA" \
    --set-env-vars="PROJECT_ID=${PROJECT_ID},REGION=${REGION},MONGODB_REQUIRE_MCP=${MONGODB_REQUIRE_MCP},${GENAI}" \
    --set-secrets="${GENAI_SECRETS}" \
    --memory=1Gi --cpu=1 \
    --no-allow-unauthenticated \
    --project="$PROJECT_ID"
done

# Persist each A2A URL into Secret Manager so callers (web_api, cron
# clients) can resolve `a2a_url_<name>` at runtime instead of hardcoding.
echo "==> Persisting A2A URLs to Secret Manager"
for name in "${!A2A_APPS[@]}"; do
  url=$(gcloud run services describe "a2a-${name//_/-}" \
    --region="$REGION" --project="$PROJECT_ID" \
    --format='value(status.url)')
  secret_name="a2a_url_${name}"
  if ! gcloud secrets describe "$secret_name" --project="$PROJECT_ID" &>/dev/null; then
    printf '%s' "$url" | gcloud secrets create "$secret_name" \
      --data-file=- --project="$PROJECT_ID"
  else
    printf '%s' "$url" | gcloud secrets versions add "$secret_name" \
      --data-file=- --project="$PROJECT_ID"
  fi
done

# ---------------------------------------------------------------------------
# HTTP services — each from its own pre-built image. Auth posture differs:
#   edit-capture-handler — public (Apps Script POSTs from Google's IP range)
#   slack-approval-handler — public (Slack webhooks)
#   substack-publisher — private (only edit_capture_handler + the
#                                 publish-sweep job invoke it)
#   web-api — public (the UI is a browser app)
# ---------------------------------------------------------------------------
echo "==> Deploying HTTP services"

echo "  -> edit-capture-handler (public)"
gcloud run deploy edit-capture-handler \
  --image="$(image_ref edit-capture-handler)" \
  --region="$REGION" \
  --service-account="$SA" \
  --set-env-vars="PROJECT_ID=${PROJECT_ID},REGION=${REGION},${GENAI}" \
    --set-secrets="${GENAI_SECRETS}" \
  --memory=512Mi \
  --allow-unauthenticated \
  --project="$PROJECT_ID"

EDIT_CAPTURE_URL=$(gcloud run services describe edit-capture-handler \
  --region="$REGION" --project="$PROJECT_ID" --format='value(status.url)')
printf '%s' "$EDIT_CAPTURE_URL" \
  | gcloud secrets versions add edit_capture_handler_url --data-file=- \
      --project="$PROJECT_ID" 2>/dev/null \
  || printf '%s' "$EDIT_CAPTURE_URL" \
       | gcloud secrets create edit_capture_handler_url --data-file=- \
           --project="$PROJECT_ID"

echo "  -> slack-approval-handler (public)"
gcloud run deploy slack-approval-handler \
  --image="$(image_ref slack-approval-handler)" \
  --region="$REGION" \
  --service-account="$SA" \
  --set-env-vars="PROJECT_ID=${PROJECT_ID},${GENAI}" \
    --set-secrets="${GENAI_SECRETS}" \
  --memory=256Mi \
  --allow-unauthenticated \
  --project="$PROJECT_ID"

echo "  -> substack-publisher (private)"
gcloud run deploy substack-publisher \
  --image="$(image_ref substack-publisher)" \
  --region="$REGION" \
  --service-account="$SA" \
  --set-env-vars="PROJECT_ID=${PROJECT_ID},REGION=${REGION},${GENAI}" \
    --set-secrets="${GENAI_SECRETS}" \
  --memory=512Mi \
  --no-allow-unauthenticated \
  --project="$PROJECT_ID"

PUBLISHER_URL=$(gcloud run services describe substack-publisher \
  --region="$REGION" --project="$PROJECT_ID" --format='value(status.url)')
printf '%s' "$PUBLISHER_URL" \
  | gcloud secrets versions add substack_publisher_url --data-file=- \
      --project="$PROJECT_ID" 2>/dev/null \
  || printf '%s' "$PUBLISHER_URL" \
       | gcloud secrets create substack_publisher_url --data-file=- \
           --project="$PROJECT_ID"

echo "  -> web-api (public)"
gcloud run deploy web-api \
  --image="$(image_ref web-api)" \
  --region="$REGION" \
  --service-account="$SA" \
  --set-env-vars="PROJECT_ID=${PROJECT_ID},REGION=${REGION},WEB_API_USE_BQ=${WEB_API_USE_BQ},${GENAI}" \
    --set-secrets="${GENAI_SECRETS}" \
  --memory=1Gi --cpu=1 \
  --allow-unauthenticated \
  --project="$PROJECT_ID"

WEB_API_URL=$(gcloud run services describe web-api \
  --region="$REGION" --project="$PROJECT_ID" --format='value(status.url)')

echo ""
echo "==> Service deploys complete."
echo "    web-api URL: ${WEB_API_URL}"
echo "    (Firebase Hosting rewrites /api → this service; no URL is baked.)"
