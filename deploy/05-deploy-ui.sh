#!/usr/bin/env bash
# Build the SPA and deploy it to Firebase Hosting.
#
# The app calls the API at the SAME ORIGIN (web/src/lib/api.ts hardcodes
# BASE="/api"), and firebase.json rewrites /api/** and /media/** to the
# web-api Cloud Run service. So there is NO API URL to bake into the bundle —
# the static build is portable and the rewrite wires it to whichever web-api
# is live.
#
# Prereqs (see docs/DEPLOYMENT.md):
#   - web-api already deployed (deploy/02-deploy-services.sh).
#   - Firebase added to the GCP project; Hosting initialized.
#   - The deploy identity has roles/firebasehosting.admin + run.viewer.
#
# Env:
#   FIREBASE_PROJECT — Firebase/GCP project id (defaults to PROJECT_ID).

# shellcheck source=deploy/env.sh
source "$(dirname "$0")/env.sh"

FIREBASE_PROJECT="${FIREBASE_PROJECT:-$PROJECT_ID}"
here="$(dirname "$0")"
repo_root="$(cd "${here}/.." && pwd)"

echo "==> Building web/ (Vite → web/dist)"
( cd "${repo_root}/web" && npm ci && npm run build )

echo "==> Deploying web/dist to Firebase Hosting (project ${FIREBASE_PROJECT})"
# firebase-tools picks up firebase.json + .firebaserc at the repo root. We run
# it via npx so no global install is required (CI installs it once).
( cd "${repo_root}" && npx --yes firebase-tools deploy \
    --only hosting \
    --project "${FIREBASE_PROJECT}" \
    --non-interactive )

echo ""
echo "==> UI deployed to Firebase Hosting."
echo "    URL: https://hindsight-guild.web.app  (Hosting site 'hindsight-guild')"
echo "    (firebase.json pins hosting.site=hindsight-guild; the default"
echo "     ${FIREBASE_PROJECT}.web.app site is left untouched)"
