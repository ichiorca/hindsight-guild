#!/usr/bin/env bash
# One-time setup: Workload Identity Federation so the GitHub Actions
# `deploy` workflow can authenticate to GCP WITHOUT a service-account key.
#
# What it creates:
#   1. sa-deployer        — the SA GitHub impersonates, with deploy roles.
#   2. WIF pool+provider  — trusts GitHub's OIDC tokens, scoped to YOUR repo.
#   3. workloadIdentityUser binding — lets that repo impersonate sa-deployer.
#
# It prints the two values you paste into GitHub repo variables:
#   GCP_WIF_PROVIDER, GCP_DEPLOY_SA
#
# Re-runnable (every create is guarded / idempotent).
#
# Usage:
#   PROJECT_ID=hindsight-guild-prod GITHUB_REPO=ichiorca/hindsight-guild \
#     ./scripts/setup_github_wif.sh
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?must set PROJECT_ID}"
GITHUB_REPO="${GITHUB_REPO:?must set GITHUB_REPO (e.g. ichiorca/hindsight-guild)}"
POOL="${POOL:-github-pool}"
PROVIDER="${PROVIDER:-github-provider}"
DEPLOYER="sa-deployer@${PROJECT_ID}.iam.gserviceaccount.com"

PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"

echo "==> Enabling required APIs"
gcloud services enable \
  iamcredentials.googleapis.com sts.googleapis.com iam.googleapis.com \
  --project "$PROJECT_ID"

echo "==> 1. Deploy service account: ${DEPLOYER}"
gcloud iam service-accounts create sa-deployer \
  --display-name "GitHub Actions deployer" --project "$PROJECT_ID" 2>/dev/null || true

# Roles the deploy workflow needs to build images, deploy Cloud Run
# services/jobs, wire schedulers, manage the deploy-time secrets, set
# cross-service IAM, and publish the Firebase Hosting site.
DEPLOY_ROLES=(
  roles/run.admin                       # deploy services + jobs, set their IAM
  roles/cloudbuild.builds.editor        # gcloud builds submit
  roles/storage.admin                   # upload source to the _cloudbuild staging bucket
  roles/artifactregistry.admin          # create repo + push images
  roles/cloudscheduler.admin            # create/update scheduler triggers
  roles/secretmanager.admin             # create/version + bind the URL secrets
  roles/iam.serviceAccountUser          # actAs sa-agents / sa-scheduler on deploy
  roles/firebasehosting.admin           # deploy the public site
  roles/serviceusage.serviceUsageConsumer
)
for role in "${DEPLOY_ROLES[@]}"; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${DEPLOYER}" --role="$role" \
    --condition=None >/dev/null
done

echo "==> 2. Workload Identity pool + GitHub OIDC provider"
gcloud iam workload-identity-pools create "$POOL" \
  --location=global --display-name="GitHub Actions" \
  --project "$PROJECT_ID" 2>/dev/null || true

# attribute-condition restricts trust to THIS repo — without it, any GitHub
# repo on the planet could mint tokens for your pool.
gcloud iam workload-identity-pools providers create-oidc "$PROVIDER" \
  --location=global --workload-identity-pool="$POOL" \
  --display-name="GitHub" \
  --issuer-uri="https://token.actions.githubusercontent.com" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref" \
  --attribute-condition="assertion.repository=='${GITHUB_REPO}'" \
  --project "$PROJECT_ID" 2>/dev/null || true

POOL_FULL="projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL}"

echo "==> 3. Let ${GITHUB_REPO} impersonate ${DEPLOYER}"
gcloud iam service-accounts add-iam-policy-binding "$DEPLOYER" \
  --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/${POOL_FULL}/attribute.repository/${GITHUB_REPO}" \
  --project "$PROJECT_ID" >/dev/null

PROVIDER_FULL="${POOL_FULL}/providers/${PROVIDER}"

cat <<EOF

================================================================
  Workload Identity Federation ready.

  Paste these into GitHub → Settings → Secrets and variables →
  Actions → Variables (NOT secrets — these are identifiers):

    GCP_WIF_PROVIDER = ${PROVIDER_FULL}
    GCP_DEPLOY_SA    = ${DEPLOYER}
================================================================
EOF
