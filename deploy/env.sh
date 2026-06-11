# Shared deploy-time variables. `source`d by every deploy/*.sh.
#
# Required (will error if unset):
#   PROJECT_ID — GCP project id
#
# Optional (with defaults):
#   REGION     — defaults to us-central1
#
# Anything downstream needs (SA emails, image refs, the A2A/JOBS maps)
# is derived from those two so callers only have to set PROJECT_ID.

set -euo pipefail

: "${PROJECT_ID:?must set PROJECT_ID (e.g. export PROJECT_ID=hindsight-guild-mvp)}"
REGION="${REGION:-us-central1}"

# Service accounts (created by scripts/create_agent_identity.sh).
SA="sa-agents@${PROJECT_ID}.iam.gserviceaccount.com"
SCHED_SA="sa-scheduler@${PROJECT_ID}.iam.gserviceaccount.com"

# Artifact Registry layout.
AR_REPO="hindsight-guild"
AR_HOST="${REGION}-docker.pkg.dev"
AR_PREFIX="${AR_HOST}/${PROJECT_ID}/${AR_REPO}"

# Convenience: image ref builder. Usage: $(image_ref agent-base)
image_ref() {
  printf '%s/%s:latest' "$AR_PREFIX" "$1"
}

# ---------------------------------------------------------------------------
# A2A agent services. Key = Cloud Run service short name (deployed as
# `a2a-<name>`). Value = ASGI app attribute on agents.a2a_server.
# ---------------------------------------------------------------------------
declare -A A2A_APPS=(
  [research]="research_a2a"
  [content]="content_a2a"
  [review]="review_a2a"
  [analytics]="analytics_a2a"
  [pipeline]="pipeline_a2a"
  [cmo]="cmo_a2a"
  [positioning]="positioning_a2a"
  [customer_voice]="customer_voice_a2a"
  [lifecycle_email]="lifecycle_email_a2a"
  [paid_media]="paid_media_a2a"
  [ops_qa]="ops_qa_a2a"
  [self_critique]="self_critique_a2a"
  [image_brief]="image_brief_a2a"
)

# ---------------------------------------------------------------------------
# Cloud Run jobs. Key = job name (Cloud Run job + Artifact Registry image
# short name; "-" not "_"). Value = Python module path the image's CMD runs.
# Both must agree with cloudbuild.yaml's image names.
# ---------------------------------------------------------------------------
declare -A JOBS=(
  [outcome-attach]="services.outcome_attach.main"
  [drift-detect]="services.drift_detect.main"
  [self-critique]="services.self_critique.main"
  [eval-harness]="services.eval_harness.main"
  [promotion-gate]="services.promotion_gate.main"
  [positioning-review]="services.positioning_review.main"
  [paid-media-sweep]="services.paid_media_sweep.main"
  [ops-qa-sweep]="services.ops_qa_sweep.main"
  [snapshot-mongo]="services.snapshot_mongo.main"
  [derive-track-records]="services.derive_track_records.main"
  [substack-publish-sweep]="services.substack_publish_sweep.main"
)

# ---------------------------------------------------------------------------
# Scheduler triggers. Key = job name (matches JOBS keys above). Value =
# cron expression (UTC). Order matters when several jobs feed each other:
#   eval-harness → derive-track-records → drift-detect → ops-qa-sweep
#   positioning-review → promotion-gate (both Sunday evening)
# ---------------------------------------------------------------------------
declare -A SCHEDULES=(
  [outcome-attach]="0 3 */2 * *"          # every 2nd day 03:00 UTC (~48h; 6h was aggressive for slot deadlines measured in days)
  [eval-harness]="0 3 * * *"              # nightly 03:00
  [derive-track-records]="30 3 * * *"     # nightly 03:30 (after eval-harness)
  [drift-detect]="30 4 * * *"             # daily 04:30 (after derive)
  [ops-qa-sweep]="0 5 * * *"              # daily 05:00 (after drift-detect)
  [self-critique]="0 0 * * *"             # daily 00:00 UTC — PRD-03 miners (writes self_critique_runs)
  [positioning-review]="0 22 * * SUN"     # Sunday 22:00 (before promotion-gate)
  [promotion-gate]="0 23 * * SUN"         # Sunday 23:00 (final proposal cut)
  [paid-media-sweep]="30 */6 * * *"       # every 6h on the half-hour
  [snapshot-mongo]="0 * * * *"            # hourly (M0 has no Atlas backups)
  [substack-publish-sweep]="*/15 * * * *" # every 15 min (stuck-publish retry)
)

# ---------------------------------------------------------------------------
# HTTP endpoint schedulers (Cloud Scheduler -> web-api endpoint, NOT a Cloud
# Run Job). PRD-02 signal ingestion + routing run IN-PROCESS inside web-api
# (POST /api/signals/poll-now and /route-now), so we poke those endpoints on a
# cron instead of standing up dedicated jobs. Key = scheduler trigger name;
# value = "<cron UTC>|<endpoint path>". The router is staggered 30 min after
# the watcher so it routes the signals that same daily poll just wrote
# (otherwise a same-minute router run would lag a full day).
# ---------------------------------------------------------------------------
declare -A SIGNAL_ENDPOINT_SCHEDULES=(
  [signal-watcher]="0 0 * * *|/api/signals/poll-now"   # daily 00:00 UTC — poll HN/Reddit/RSS
  [signal-router]="30 0 * * *|/api/signals/route-now"  # daily 00:30 UTC — route pending signals -> drafts
)

# Secrets that sa-agents needs read access to. setup.sh creates these as
# "PENDING" stubs; deploy/06-bind-iam.sh grants secretAccessor on each.
SECRETS_FOR_AGENTS=(
  google_api_key
  mongo_uri
  mongo_uri_readonly
  mongo_uri_writer
  slack_webhook_url
  ga4_property_id
  hubspot_api_token
  google_ads_developer_token
  google_ads_client_id
  google_ads_client_secret
  google_ads_refresh_token
  google_ads_login_customer_id
  linkedin_access_token
  reddit_client_id
  reddit_client_secret
  reddit_username
  reddit_password
  devto_api_key
  substack_api_key
  substack_publication_host
  substack_publication_id
  edit_capture_handler_url
  substack_publisher_url
)

# Model tiers — resolved centrally in shared/models.py (HEAVY/LIGHT). Set them
# here to point a deployment at whatever its endpoint serves. Defaults are the
# Gemini 3 generation: gemini-3.5-flash (heavy) + gemini-3.1-flash-lite
# (light), GA on both Vertex AI and the Gemini Developer API. Override with
# MODEL_HEAVY / MODEL_LIGHT — no code change.
MODELS="MODEL_HEAVY=${MODEL_HEAVY:-gemini-3.5-flash},MODEL_LIGHT=${MODEL_LIGHT:-gemini-3.1-flash-lite}"

# Diagram renderer URL — shared/diagrams.py reads MERMAID_RENDERER_URL to render
# infographic/excalidraw diagrams (image_brief). Resolved from the live
# mermaid-renderer service (deployed independently); empty if it isn't up yet,
# in which case diagrams degrade to a "pending" stub until the next deploy.
MERMAID_RENDERER_URL="$(gcloud run services describe mermaid-renderer --region="$REGION" --project="$PROJECT_ID" --format='value(status.url)' 2>/dev/null || true)"

# genai client config for the agents. USE_VERTEXAI=true -> Vertex AI (SA-based;
# no API key needed). GOOGLE_API_KEY is still mounted (GENAI_SECRETS) but is
# ignored while USE_VERTEXAI=true, so flipping a scope to the Developer API
# is a one-line change. The Vertex AI Eval service (shared/rubrics.py) runs here.
# Inline rubric-scoring sample rate (agents/_common._should_eval). 0.5 = score
# 1-in-2 drafts at draft time so the founder sees eval scores + reviewer
# feedback on more drafts (the nightly eval-harness still re-grades the rest).
# Each scored draft is ~6 extra judge calls, so this trades Vertex Eval quota
# for coverage; raise toward 1.0 only if quota allows.
EVAL_SAMPLE_RATE="${EVAL_SAMPLE_RATE:-0.25}"

# Forbid the silent MCP→pymongo fallback in deployed agents (agents/_mcp.py):
# the agent image ships Node + mongodb-mcp-server, so a fallback there means
# something is broken — fail loudly rather than quietly serving reads over
# pymongo while the demo claims "agents read Atlas via MCP". Set to 0 to relax.
MONGODB_REQUIRE_MCP="${MONGODB_REQUIRE_MCP:-1}"

# Gemini backend per scope. ALL agents run on the paid Gemini Developer API
# (GOOGLE_API_KEY secret): the Gemini 3 models (MODELS above) are served
# there but NOT on this project's Vertex AI, and the paid key's rate limits
# beat Vertex's dynamic shared quota (which 429'd drafting bursts) anyway.
# GOOGLE_GENAI_USE_VERTEXAI is set PER SERVICE in 02-deploy-services.sh from
# these knobs (it is NOT in the GENAI bundle).
USE_VERTEXAI_DEFAULT="${USE_VERTEXAI_DEFAULT:-false}"
PIPELINE_USE_VERTEXAI="${PIPELINE_USE_VERTEXAI:-false}"

# The Vertex AI Eval Service judge CANNOT move to the Developer API — pin it
# to a model this project's Vertex actually serves (the 2.5 family), or the
# inline eval + nightly eval-harness 404 once MODEL_LIGHT points at a 3.x
# model. shared/rubrics.py reads JUDGE_MODEL before falling back to light().
JUDGE_MODEL="${JUDGE_MODEL:-gemini-2.5-flash-lite}"

GENAI="GOOGLE_CLOUD_PROJECT=${PROJECT_ID},GOOGLE_CLOUD_LOCATION=${REGION},MERMAID_RENDERER_URL=${MERMAID_RENDERER_URL},EVAL_SAMPLE_RATE=${EVAL_SAMPLE_RATE},JUDGE_MODEL=${JUDGE_MODEL},${MODELS}"

# web-api reads analytics (rubric trend, weekly summary, live feed, skill
# samples, founder dashboard) from BigQuery; Mongo remains the operational
# store + fallback. Set to 0 to force Mongo-only reads (thin local installs).
WEB_API_USE_BQ="${WEB_API_USE_BQ:-1}"

# Mounted as an env var (Cloud Run --set-secrets) so the Developer-API genai
# client can authenticate. sa-agents gets secretAccessor via SECRETS_FOR_AGENTS.
GENAI_SECRETS="GOOGLE_API_KEY=google_api_key:latest"
