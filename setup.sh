#!/usr/bin/env bash
# C0 — Project bootstrap. Idempotent: re-runnable. See agentic_marketing_implementation_plan.md §3.
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-agentic-marketing-mvp}"
REGION="${REGION:-us-central1}"
BILLING_ACCOUNT="${BILLING_ACCOUNT:?must set BILLING_ACCOUNT (find via: gcloud beta billing accounts list)}"

echo "==> Bootstrapping $PROJECT_ID in $REGION"

# 1. Project
gcloud projects create "$PROJECT_ID" --name="Agentic Marketing MVP" || true
gcloud config set project "$PROJECT_ID"
gcloud beta billing projects link "$PROJECT_ID" --billing-account "$BILLING_ACCOUNT"

# 2. APIs (one call per API name to keep errors readable)
APIS=(
  aiplatform.googleapis.com
  agentbuilder.googleapis.com
  bigquery.googleapis.com
  bigquerystorage.googleapis.com
  run.googleapis.com
  cloudscheduler.googleapis.com
  cloudfunctions.googleapis.com
  secretmanager.googleapis.com
  logging.googleapis.com
  monitoring.googleapis.com
  storage.googleapis.com
  artifactregistry.googleapis.com
  cloudbuild.googleapis.com
  iam.googleapis.com
  modelarmor.googleapis.com
)
for api in "${APIS[@]}"; do
  echo "==> Enabling $api"
  gcloud services enable "$api"
done

# 3. GCS buckets
for b in state prompts evals ingest media snapshots; do
  gsutil mb -l "$REGION" "gs://${PROJECT_ID}-${b}" || true
done
# Media bucket needs public read for the email/web channel previews
gsutil iam ch allUsers:objectViewer "gs://${PROJECT_ID}-media" 2>/dev/null || true

# 4. BigQuery datasets
bq --location=US mk -d --description "Agent telemetry" "${PROJECT_ID}:telemetry" 2>/dev/null || true
bq --location=US mk -d --description "Training corpora" "${PROJECT_ID}:training" 2>/dev/null || true
bq --location=US mk -d --description "Analytics derived views" "${PROJECT_ID}:analytics" 2>/dev/null || true
bq --location=US mk -d --description "Monitoring rollups" "${PROJECT_ID}:monitoring" 2>/dev/null || true

# 5. Run BigQuery schema (C1). Substitute ${PROJECT_ID} into the SQL via envsubst.
envsubst < sql/schema.sql | bq query --use_legacy_sql=false

# 6. MongoDB Atlas — programmatic via Atlas CLI
# Prereq: `atlas auth login` interactive or ATLAS_PUB/PRIV env vars exported.
if ! command -v atlas &>/dev/null; then
  echo "ERROR: install the Atlas CLI: https://www.mongodb.com/docs/atlas/cli/current/install-atlas-cli/" >&2
  exit 1
fi
atlas projects create "${PROJECT_ID}-atlas" 2>/dev/null || true
ATLAS_PROJECT_ID="$(atlas projects list -o json | jq -r ".results[] | select(.name==\"${PROJECT_ID}-atlas\") | .id")"
atlas clusters create agentic-mvp \
  --provider GCP --region "${REGION^^}" --tier M0 \
  --projectId "$ATLAS_PROJECT_ID" 2>/dev/null || true

echo "==> Atlas cluster created. Waiting for it to be IDLE (this can take 3-5 min)..."
atlas clusters watch agentic-mvp --projectId "$ATLAS_PROJECT_ID"

# 7. Capture Atlas connection string into Secret Manager
# We populate this interactively because the CLI doesn't include passwords by default.
echo ""
echo "==> Atlas cluster ready. Now we need a database user."
echo "    Option A: run scripts/create_mongo_users.sh now (creates RO + writer users)."
echo "    Option B: paste a mongodb+srv://... connection string with embedded credentials."
read -rp "Paste connection string (or press Enter to skip and run create_mongo_users.sh after): " MONGO_URI
if [ -n "$MONGO_URI" ]; then
  printf '%s' "$MONGO_URI" | gcloud secrets create mongo_uri --data-file=- --project "$PROJECT_ID" 2>/dev/null \
    || printf '%s' "$MONGO_URI" | gcloud secrets versions add mongo_uri --data-file=- --project "$PROJECT_ID"
fi

# 8. Initial empty secrets that components will fill later.
# Each is created with the literal value "PENDING" so code can detect "not yet
# configured" without 404'ing the API. Fill them in (gcloud secrets versions
# add ...) before the relevant flow runs:
#   - slack_webhook_url ............. used by cmo_planner.slack_approval (Phase 2)
#   - ga4_property_id ............... outcome_attach GA4 queries
#   - hubspot_api_token ............. outcome_attach HubSpot queries
#   - google_ads_developer_token .... outcome_attach Google Ads queries
#   - google_ads_client_id .......... Google Ads OAuth client
#   - google_ads_client_secret ...... Google Ads OAuth client
#   - google_ads_refresh_token ...... Google Ads OAuth refresh token
#   - google_ads_login_customer_id .. Google Ads MCC customer id
#   - linkedin_access_token ......... LinkedIn outcome source (when wired)
#   - substack_api_key .............. substack_publisher auth
#   - substack_publication_host ..... substack_publisher target publication
#   - substack_publication_id ....... substack_publisher target publication
#   - voyage_api_key ................ Voyage AI auto-embed for mongo MCP
#   - edit_capture_handler_url ...... web_api → edit_capture_handler bridge
#                                     (deploy.sh overwrites this once the
#                                     handler is up)
for s in \
    slack_webhook_url \
    ga4_property_id \
    hubspot_api_token \
    google_ads_developer_token \
    google_ads_client_id \
    google_ads_client_secret \
    google_ads_refresh_token \
    google_ads_login_customer_id \
    linkedin_access_token \
    substack_api_key \
    substack_publication_host \
    substack_publication_id \
    voyage_api_key \
    edit_capture_handler_url ; do
  echo -n "PENDING" | gcloud secrets create "$s" --data-file=- --project "$PROJECT_ID" 2>/dev/null || true
done

echo ""
echo "==> Bootstrap complete."
echo "    Next: ./scripts/create_mongo_users.sh    (creates RO + writer Atlas users, writes mongo_uri_readonly + mongo_uri_writer secrets)"
echo "          python mongo/seed.py               (creates collections + vector index)"
echo "          ./scripts/create_agent_identity.sh (creates sa-agents service account)"
echo "          ./scripts/create_model_armor_template.sh"
