# Deployment

Everything needed to take this project from `gcloud projects create` to a
running multi-agent system. Run order matters — the bootstrap scripts up
top assume nothing exists; the deploy scripts in `deploy/` assume the
project, service accounts, secrets, and Atlas cluster are already there.

## One-time bootstrap (run from repo root)

```bash
export PROJECT_ID=hindsight-guild-mvp
export REGION=us-central1                  # optional, this is the default
export BILLING_ACCOUNT=<billing-id>        # gcloud beta billing accounts list

./setup.sh                                 # project + APIs + GCS + BQ + Atlas + secrets
./scripts/create_mongo_users.sh            # RO + writer Atlas users
./scripts/create_agent_identity.sh         # sa-agents + sa-scheduler
./scripts/create_model_armor_template.sh   # prompt screening template
python mongo/seed.py                       # Mongo collections + vector index
```

## Deploy (idempotent — re-run any time)

```bash
./deploy/all.sh
```

That orchestrates the six phases below. Each is also runnable on its own
when you only want to redeploy part of the stack.

| Phase | Script | What it does |
|------:|---|---|
| 1 | `01-build-images.sh` | Submits `cloudbuild.yaml` to Cloud Build: builds **17 images** in parallel (agent-base + 11 worker jobs + 4 HTTP services + UI), pushes to Artifact Registry tagged `:latest` and `:$SHORT_SHA`. |
| 2 | `02-deploy-services.sh` | Deploys **13 A2A agent services** (one Cloud Run service per agent, all from the same `agent-base` image) + **4 HTTP services** (edit-capture-handler, slack-approval-handler, substack-publisher, web-api). Writes service URLs into Secret Manager so callers can resolve them at runtime. |
| 3 | `03-deploy-jobs.sh` | Deploys **11 Cloud Run jobs** (the cron workers). |
| 4 | `04-schedulers.sh` | Wires **11 Cloud Scheduler triggers** to the jobs. Schedules live in `deploy/env.sh`. |
| 5 | `05-deploy-ui.sh` | Rebuilds the UI image with the live `web-api` URL baked into the JS bundle, then deploys to the `ui` Cloud Run service. |
| 6 | `06-bind-iam.sh` | Grants the cross-service IAM bindings (`run.invoker` on A2A services, `secretAccessor` on shared secrets, scheduler→job permissions). |

## What lives where

- `cloudbuild.yaml` (repo root) — Cloud Build pipeline definition. All 17 builds run with `waitFor: ['-']` for fan-out.
- `Dockerfile` (repo root) — the agent base image (Python 3.12 + Node 22 + `mongodb-mcp-server`). Used by all 13 A2A services.
- `services/*/Dockerfile` — one per worker job + HTTP service. Slim Python images.
- `web/Dockerfile` — two-stage Vite build + nginx static serve.
- `.gcloudignore` — keeps node_modules, .venv, secrets, and dev-only directories out of the Cloud Build source upload.
- `deploy/env.sh` — shared variables (PROJECT_ID, REGION, SA emails, image refs, A2A_APPS / JOBS / SCHEDULES maps, SECRETS_FOR_AGENTS list). Every deploy script `source`s this.
- `deploy/all.sh` — orchestrator. Phases 1-6 in order.

## Partial-redeploy recipes

```bash
# Rebuild + redeploy a single service after a code change.
./deploy/01-build-images.sh                    # builds all (fast with cache)
gcloud run services update a2a-content \
  --image="$(REGION=us-central1 source deploy/env.sh && image_ref agent-base)" \
  --region=us-central1

# UI only (faster than running 01-build-images.sh for a UI tweak).
./deploy/05-deploy-ui.sh

# Add a new secret + grant access. Edit deploy/env.sh's SECRETS_FOR_AGENTS,
# then re-run 06.
./deploy/06-bind-iam.sh
```

## Adding a new agent

1. Write `agents/new_agent.py` and a `new_agent_a2a` attribute on `agents/a2a_server.py`.
2. Add to `A2A_APPS` in `deploy/env.sh`.
3. Run `./deploy/02-deploy-services.sh` (creates the new `a2a-new-agent` service from the existing `agent-base` image).
4. Run `./deploy/06-bind-iam.sh` (grants `sa-agents` invoker on the new service).

No image rebuild needed — every A2A service runs the same `agent-base` image with a different `--args` ASGI target.

## Adding a new cron job

1. Write `services/new_sweep/{main.py,Dockerfile,__init__.py}`.
2. Add a build step for it to `cloudbuild.yaml` (copy one of the existing service entries).
3. Add to both `JOBS` and `SCHEDULES` in `deploy/env.sh`.
4. Re-run `./deploy/all.sh` (or just 01 → 03 → 04 → 06).

## Troubleshooting

**"Permission denied" when a worker calls an A2A service.** Phase 6 didn't run, or `sa-agents` doesn't have `roles/run.invoker` on the target. Re-run `06-bind-iam.sh`.

**Scheduler trigger fires but the job doesn't run.** `sa-scheduler` is missing `roles/run.invoker` on the job. Re-run `06-bind-iam.sh`.

**"Secret not found" at agent startup.** `setup.sh` creates each secret with the literal value `PENDING` so the binding exists from day one — but the agent will still fail if the secret value hasn't been filled in. Update the version: `printf 'real-value' | gcloud secrets versions add <name> --data-file=-`.

**UI shows blank page after deploy.** The bundle was built with the wrong `VITE_API_URL`. Re-run `./deploy/05-deploy-ui.sh` — it fetches the current web-api URL and rebuilds.

**Cloud Build runs out of memory.** `cloudbuild.yaml` uses `E2_HIGHCPU_8`. If you bump the number of services higher, escalate to `E2_HIGHCPU_32`.
