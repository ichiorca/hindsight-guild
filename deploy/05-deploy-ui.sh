#!/usr/bin/env bash
# Rebuild + redeploy the UI. The VITE_API_URL is baked into the JS bundle
# at build time, so this script:
#   1. Fetches the live web-api URL from Cloud Run.
#   2. Builds a fresh UI image with that URL substituted.
#   3. Deploys the new image to the `ui` Cloud Run service.
#
# Run this any time web-api moves region or gets a new vanity URL.

# shellcheck source=deploy/env.sh
source "$(dirname "$0")/env.sh"

WEB_API_URL=$(gcloud run services describe web-api \
  --region="$REGION" --project="$PROJECT_ID" --format='value(status.url)' 2>/dev/null || true)

if [ -z "$WEB_API_URL" ]; then
  echo "ERROR: web-api service not found in ${REGION}. Run deploy/02-deploy-services.sh first." >&2
  exit 1
fi

echo "==> Building UI image with VITE_API_URL=${WEB_API_URL}"
UI_IMAGE="$(image_ref ui)"

# Submit *only* the UI build, not the whole cloudbuild.yaml fan-out.
# Uses the same Cloud Build builder image but limits scope to web/.
gcloud builds submit web \
  --tag="$UI_IMAGE" \
  --project="$PROJECT_ID" \
  --substitutions=_VITE_API_URL="$WEB_API_URL" \
  2>&1 | tail -50 || {
    # `gcloud builds submit --tag` doesn't accept --substitutions in older
    # gcloud versions. Fall back to passing the build arg via an ad-hoc
    # cloudbuild step.
    echo "==> Falling back to inline build args"
    gcloud builds submit web \
      --config=- \
      --project="$PROJECT_ID" <<EOF
steps:
- name: gcr.io/cloud-builders/docker
  args:
    - build
    - --build-arg
    - VITE_API_URL=${WEB_API_URL}
    - -t
    - ${UI_IMAGE}
    - .
images:
  - ${UI_IMAGE}
EOF
  }

echo "==> Deploying ui service"
gcloud run deploy ui \
  --image="$UI_IMAGE" \
  --region="$REGION" \
  --memory=256Mi \
  --allow-unauthenticated \
  --project="$PROJECT_ID"

UI_URL=$(gcloud run services describe ui \
  --region="$REGION" --project="$PROJECT_ID" --format='value(status.url)')

echo ""
echo "==> UI deployed."
echo "    UI:      ${UI_URL}"
echo "    web-api: ${WEB_API_URL}"
