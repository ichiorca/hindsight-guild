#!/usr/bin/env bash
# Build every container image in parallel via Cloud Build.
#
# Usage:
#   ./deploy/01-build-images.sh                  # uses default VITE_API_URL=/api
#   ./deploy/01-build-images.sh https://web-api-xxx.run.app
#                                                # bakes that URL into the UI bundle
#
# Idempotent. Cloud Build caches Dockerfile layers between runs.

# shellcheck source=deploy/env.sh
source "$(dirname "$0")/env.sh"

WEB_API_URL="${1:-/api}"

echo "==> Ensuring Artifact Registry repo '${AR_REPO}' exists in ${REGION}"
gcloud artifacts repositories create "$AR_REPO" \
  --repository-format=docker \
  --location="$REGION" \
  --description="Container images for hindsight-guild" \
  --project="$PROJECT_ID" 2>/dev/null || true

echo "==> Submitting cloudbuild.yaml (17 images, parallel)"
echo "    web-api URL baked into UI bundle: ${WEB_API_URL}"

# `gcloud builds submit` uploads the source tree (respecting .gcloudignore)
# and runs cloudbuild.yaml inside the Cloud Build VM.
gcloud builds submit . \
  --config=cloudbuild.yaml \
  --project="$PROJECT_ID" \
  --substitutions="_REGION=${REGION},_WEB_API_URL=${WEB_API_URL}"

echo ""
echo "==> Images built. Pushed to:"
echo "    ${AR_PREFIX}/<name>:latest"
echo "    ${AR_PREFIX}/<name>:<short-sha>"
