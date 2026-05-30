#!/usr/bin/env bash
# Build every container image in parallel via Cloud Build.
#
# Usage:
#   ./deploy/01-build-images.sh
#
# The UI is NOT built here — it's a static Vite bundle served by Firebase
# Hosting (deploy/05-deploy-ui.sh), so there's no web-api URL to bake.
#
# Idempotent. Cloud Build caches Dockerfile layers between runs.

# shellcheck source=deploy/env.sh
source "$(dirname "$0")/env.sh"

echo "==> Ensuring Artifact Registry repo '${AR_REPO}' exists in ${REGION}"
gcloud artifacts repositories create "$AR_REPO" \
  --repository-format=docker \
  --location="$REGION" \
  --description="Container images for hindsight-guild" \
  --project="$PROJECT_ID" 2>/dev/null || true

# cloudbuild.yaml tags each image :latest and :$SHORT_SHA. SHORT_SHA is only
# auto-populated for *trigger*-based builds, not `gcloud builds submit`, so we
# pass it explicitly — otherwise the tag becomes "<image>:" (invalid). Falls
# back to the git short sha, or "manual" outside a checkout.
SHORT_SHA="$(git -C "$(dirname "$0")/.." rev-parse --short=7 HEAD 2>/dev/null || echo manual)"

echo "==> Submitting cloudbuild.yaml (16 images, parallel; sha=${SHORT_SHA})"

# `gcloud builds submit` uploads the source tree (respecting .gcloudignore)
# and runs cloudbuild.yaml inside the Cloud Build VM.
gcloud builds submit . \
  --config=cloudbuild.yaml \
  --project="$PROJECT_ID" \
  --substitutions="_REGION=${REGION},SHORT_SHA=${SHORT_SHA}"

echo ""
echo "==> Images built. Pushed to:"
echo "    ${AR_PREFIX}/<name>:latest"
echo "    ${AR_PREFIX}/<name>:<short-sha>"
