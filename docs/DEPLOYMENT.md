# Deploying Hindsight Guild to Google Cloud

This is the **pre-setup checklist you complete once**, by hand, before the
GitHub Actions `deploy` workflow can run. The workflow itself is keyless and
push-button; everything below is the groundwork it assumes already exists.

> Legend: 🧑 = **you do this once**, 🤖 = the deploy workflow does it for you.

---

## 1. Architecture (what gets deployed)

| Layer | GCP resource | Notes |
|---|---|---|
| Public website (React SPA) | **Firebase Hosting** | `firebase.json` rewrites `/api/**` + `/media/**` to the `web-api` Cloud Run service, so the app is same-origin. Default URL `https://<project>.web.app`. |
| API + UI backend | **Cloud Run** `web-api` (public) | FastAPI. The only public backend. |
| Agents | **Cloud Run** `a2a-*` ×13 (private) | One image (`agent-base`), different ASGI target each. Reached only by `sa-agents`. |
| Webhooks | **Cloud Run** `edit-capture-handler`, `slack-approval-handler` (public), `substack-publisher` (private) | |
| Cron workers | **Cloud Run Jobs** ×11 + **Cloud Scheduler** | Schedules live in `deploy/env.sh`. |
| Images | **Artifact Registry** repo `hindsight-guild` | Built in parallel by **Cloud Build** (`cloudbuild.yaml`). |
| Config + credentials | **Secret Manager** | Every token/URI. Code reads `PENDING` as "not configured". |
| Primary datastore | **MongoDB Atlas** (external) | DB name `hindsight_guild`. |
| Telemetry / analytics | **BigQuery** | datasets `telemetry`, `training`, `analytics`, `monitoring`. |
| LLM | **Vertex AI** (Gemini) | `sa-agents` has `roles/aiplatform.user`; prod uses Vertex (no API key). |

Deploy auth = **Workload Identity Federation** (GitHub OIDC → a deploy SA). No
service-account JSON key is ever stored in GitHub.

---

## 2. Local tools (on your machine, for the one-time setup)

```bash
# gcloud
gcloud --version            # https://cloud.google.com/sdk/docs/install
# Atlas CLI (for MongoDB)    # https://www.mongodb.com/docs/atlas/cli/
atlas --version
# Node 20 (for mongo/seed + firebase if you deploy locally)
node --version
jq --version
```

Authenticate once: `gcloud auth login` and `atlas auth login`.

---

## 3. 🧑 Bootstrap the GCP project + data plane

The repo's `setup.sh` does the heavy lifting (project, APIs incl. Firebase,
GCS buckets, BigQuery datasets + schema, Atlas M0 cluster, secret stubs):

```bash
export PROJECT_ID=hindsight-guild-prod      # your real project id
export REGION=us-central1                    # must match GCP_REGION + firebase.json
export BILLING_ACCOUNT=XXXXXX-XXXXXX-XXXXXX   # gcloud beta billing accounts list
./setup.sh
```

Prefer to do it by hand? The minimum is: create the project + link billing, then

```bash
gcloud services enable \
  run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
  secretmanager.googleapis.com cloudscheduler.googleapis.com \
  aiplatform.googleapis.com bigquery.googleapis.com modelarmor.googleapis.com \
  iam.googleapis.com iamcredentials.googleapis.com sts.googleapis.com \
  firebase.googleapis.com firebasehosting.googleapis.com --project "$PROJECT_ID"
```

…plus the GCS buckets + BigQuery datasets/schema that `setup.sh` creates.

---

## 4. 🧑 Runtime service accounts

```bash
PROJECT_ID=$PROJECT_ID ./scripts/create_agent_identity.sh
# creates sa-agents (runtime) + sa-scheduler (cron invoker) with their roles
```

---

## 5. 🧑 MongoDB Atlas (users, DB, network, seed)

```bash
PROJECT_ID=$PROJECT_ID ./scripts/create_mongo_users.sh
#   → creates read-only + read/write users on DB `hindsight_guild`
#   → writes mongo_uri_readonly + mongo_uri_writer secrets
python mongo/seed.py            # collections + Atlas vector index
```

Then in the Atlas UI (or CLI): **Network Access → allow Cloud Run egress.**
For an M0 cluster the simplest is `0.0.0.0/0` (TLS + auth still required);
tighten to VPC peering / PrivateLink later for production.

> The DB is `hindsight_guild`. If you had data under the old `agentic_marketing`
> name, it is **not** migrated — this is a clean DB (you chose the full rename).

---

## 6. 🧑 Populate Secret Manager with real values

`setup.sh` creates each secret with the literal value `PENDING`. Replace the
ones you actually use (the app degrades gracefully on `PENDING` for optional
integrations). Set a real value with:

```bash
printf '%s' 'THE-REAL-VALUE' | gcloud secrets versions add <name> --data-file=- --project "$PROJECT_ID"
```

| Secret | Needed for | Required? |
|---|---|---|
| `mongo_uri_readonly`, `mongo_uri_writer` | all DB access | **yes** (step 5 sets these) |
| `voyage_api_key` | MongoDB vector auto-embed | recommended |
| `slack_webhook_url` | CMO planner approvals → Slack | if using Slack |
| `ga4_property_id`, `hubspot_api_token` | outcome attribution | if using those sources |
| `google_ads_developer_token`, `google_ads_client_id`, `google_ads_client_secret`, `google_ads_refresh_token`, `google_ads_login_customer_id` | paid-media + Ads attribution | if using Google Ads |
| `linkedin_access_token` | LinkedIn outcomes | optional |
| `substack_api_key`, `substack_publication_host`, `substack_publication_id` | Substack publishing | if publishing to Substack |

`a2a_url_*`, `edit_capture_handler_url`, `substack_publisher_url` are written
**automatically** by the deploy (🤖) — leave them.

Optional: `./scripts/create_model_armor_template.sh` (LLM safety floor).

**LLM auth:** production uses **Vertex AI** via `sa-agents` (`roles/aiplatform.user`)
— no Gemini API key needed. Confirm Gemini is available in your `REGION`.

---

## 7. 🧑 Firebase Hosting (the public site)

```bash
# Add Firebase to the existing GCP project (one-time):
npx --yes firebase-tools projects:addfirebase "$PROJECT_ID"
```

Edit **`.firebaserc`** and set `"default"` to your `$PROJECT_ID` (or rely on the
workflow's `--project` flag — it passes `FIREBASE_PROJECT`). The default site is
`https://<project>.web.app`. Custom domain comes later (§10).

> `firebase.json` hardcodes the rewrite region as `us-central1`. If your
> `REGION` is different, update the two `"region"` fields in `firebase.json`.

---

## 8. 🧑 Workload Identity Federation for GitHub

```bash
PROJECT_ID=$PROJECT_ID GITHUB_REPO=ichiorca/hindsight-guild ./scripts/setup_github_wif.sh
```

It creates `sa-deployer` (with deploy roles), a WIF pool + GitHub OIDC provider
scoped to **your repo only**, lets the repo impersonate `sa-deployer`, and
prints the two values for the next step.

---

## 9. 🧑 GitHub repository variables

GitHub → your repo → **Settings → Secrets and variables → Actions → Variables**
(these are *Variables*, not Secrets — WIF is keyless, nothing here is sensitive):

| Variable | Value |
|---|---|
| `GCP_PROJECT_ID` | `hindsight-guild-prod` |
| `GCP_REGION` | `us-central1` (optional; defaults to `us-central1`) |
| `GCP_WIF_PROVIDER` | printed by step 8 (`projects/<num>/locations/global/workloadIdentityPools/github-pool/providers/github-provider`) |
| `GCP_DEPLOY_SA` | `sa-deployer@<project>.iam.gserviceaccount.com` |
| `FIREBASE_PROJECT` | optional; defaults to `GCP_PROJECT_ID` |

Optional but recommended: protect the `production` GitHub *Environment* (add
required reviewers) so a deploy needs an approval click.

---

## 10. 🤖 Run the deploy

GitHub → **Actions → `deploy` → Run workflow** (branch `main`).
Leave `skip_image_build` off for the first run; turn it on for config-only
redeploys (skips the ~5–7 min Cloud Build).

The workflow (`.github/workflows/deploy.yml`) then:

1. Authenticates via WIF (no key).
2. `deploy/01` — Cloud Build builds + pushes all 16 images to Artifact Registry.
3. `deploy/02` — deploys `web-api`, the 13 `a2a-*` agents, and the HTTP handlers; records each service URL into Secret Manager.
4. `deploy/03` — deploys the 11 Cloud Run Jobs.
5. `deploy/04` — wires the Cloud Scheduler triggers.
6. `deploy/06` — binds cross-service IAM (invokers + secret access).
7. `deploy/05` — builds the SPA and publishes it to Firebase Hosting.

The run summary prints the UI and `web-api` URLs.

---

## 11. After the first deploy (manual follow-ups)

- **Apps Script** (`apps_script/Code.gs`): set `HANDLER_URL` to the
  `edit-capture-handler` URL (`gcloud run services describe edit-capture-handler --region=$REGION --format='value(status.url)'`).
- **Slack**: put your real incoming-webhook URL in `slack_webhook_url`.
- **Seed demo data** (optional): `python -m demo.seed_demo`.
- **Open the site**: `https://<project>.web.app`.
- **Custom domain** (when ready): Firebase console → Hosting → *Add custom
  domain* → add the A / TXT records it shows at your DNS registrar. No code
  change needed — the SPA already uses relative `/api`.

---

## Costs & gotchas

- Cloud Run scales to zero; Atlas M0 is free; you pay mainly for Cloud Build
  minutes, Vertex AI tokens, and any always-on traffic.
- A2A services are **private** (`--no-allow-unauthenticated`); only `sa-agents`
  can call them. `web-api` and the webhook handlers are public by design.
- Keep `GCP_REGION`, `REGION` in `deploy/env.sh`, and the `region` in
  `firebase.json` in sync.
- Re-running the workflow is safe — every step is idempotent.
