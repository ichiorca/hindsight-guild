# Hindsight Guild

**A marketing team of one's own — that remembers, learns, and levels up.**
Built on Gemini + MongoDB Atlas.

Hindsight Guild is an autonomous marketing team for **solo founders**: a guild
of Gemini agents on Google Cloud Agent Builder that researches, drafts,
fact-checks, and routes content for approval — then learns from every decision.
Its memory, judgment, and learning all live in **MongoDB Atlas**, queried live
through the MongoDB MCP server. The team **remembers** what worked (Atlas as the
system of record), **learns** from what got rejected (rubric grounding on past
negatives), and **levels up** its own playbooks over time (skill promotion).

Built around real ADK 1.x multi-agent primitives, the Agent2Agent protocol,
Vertex AI Memory Bank, Vertex AI Gen AI Evaluation Service, MongoDB Atlas via
the MongoDB MCP server, and Model Armor.

It runs **lean** — a hard **cost cap** ($1k/mo at solo-founder scale), not a
permission to ship stubs.

## How the agents work as a team

```
                    ┌─────────────────────────┐
                    │  CMO Planner (Mon)      │
                    │  uses AgentTool to call │
                    │  Analytics + Research,  │
                    │  drafts weekly memo,    │
                    │  posts to Slack         │
                    └────┬──────────┬─────────┘
                         │          │ AgentTool
                  ┌──────▼────┐ ┌───▼──────────┐
                  │ Analytics │ │  Research    │
                  │ (BQ q)    │ │  Agent       │
                  └───────────┘ └──────────────┘
                                       ▲
                                       │
SequentialAgent: Drafting pipeline (Tue)│
  ┌─────────┐    ┌─────────┐    ┌──────┴──┐
  │ Research│───▶│ Content │───▶│ Review  │
  └─────────┘    └─────────┘    └─────────┘
   writes         reads {research}        writes
   state['research_findings']  state['draft']  state['review']
                                            │
                            after_agent_callback
                                            │
                              Vertex AI Eval Service
                                  (all 6 rubrics)
                                            │
                              telemetry.actions (BQ)
                                            │
                              ┌─────────────┼─────────────┐
                              ▼             ▼             ▼
                      eval_harness    drift_detect   self_critique
                      (nightly all-   (28d window   (weekly Gemini
                       rubric re-grade) drop → exp)  → playbook revs)
                              │             │             │
                              └─────────────┼─────────────┘
                                            ▼
                                    promotion_gate (weekly)
                                            │
                              raises promotion_request on skill
                              founder reviews → flips current_version
```

Every agent is exposed via **A2A** (`to_a2a()`), so Cloud Run jobs and external
callers invoke them as remote services — not Python imports. The diagram shows
the core weekly + drafting flow; the full guild also includes **Positioning**,
**Paid Media**, **Lifecycle Email**, **Customer Voice**, **Ops/QA**, **AEO**
(answer-engine optimization), and **ImageBrief** specialists, plus two
cross-cutting loops:

- **Signal-triggered drafting** — `signal_watcher` + `signal_router` turn
  inbound signals into draft requests routed to the right channel agent.
- **Closed-loop learning** — the drafting pipeline runs an inline
  Critique→Revise step before review; nightly miners + the weekly
  `self_critique` agent propose `SKILL.md` revisions that the
  `promotion_gate` re-verifies and the founder approves to flip
  `current_version`.

## Repo layout

```
hindsight-guild/
├── pyproject.toml                 # pinned deps incl. ADK 1.x, a2a-sdk, google-ads
├── setup.sh                       # GCP + APIs + Atlas + Firebase bootstrap (C0)
├── LOCAL_DEV.md                   # run the whole stack locally (Mongo + synthetic fallbacks)
├── docker-compose.yml             # local MongoDB
├── cloudbuild.yaml                # parallel image builds (16 images)
├── firebase.json / .firebaserc    # Firebase Hosting: SPA + /api,/media rewrites → web-api
├── .github/workflows/
│   ├── ci.yml                     # ruff + pytest(unit) + web build, on push/PR
│   └── deploy.yml                 # manual, keyless (WIF) deploy of the whole stack
├── deploy/                        # phased manual deploy scripts — see deploy/README.md
│   ├── all.sh / env.sh            # orchestrator + shared SA/image/job/schedule maps
│   ├── 01-build-images.sh         # cloudbuild.yaml submit
│   ├── 02-deploy-services.sh      # web-api + 13 A2A + HTTP handlers
│   ├── 03-deploy-jobs.sh          # 11 Cloud Run jobs
│   ├── 04-schedulers.sh           # 11 Cloud Scheduler triggers
│   ├── 05-deploy-ui.sh            # build SPA → Firebase Hosting
│   └── 06-bind-iam.sh             # cross-service IAM
├── docs/
│   ├── DEPLOYMENT.md              # one-time pre-setup checklist + deploy guide
│   └── prds/                      # product specs (AEO, signal-drafting, closed-loop)
├── sql/schema.sql                 # 3 BQ tables + 3 views (C1)
├── mongo/
│   ├── schema.py                  # 15 canonical collections + history.* + derived.* + vector index
│   ├── seed.py                    # back-compat shim → `python -m mongo.schema apply`
│   ├── mcp_server.py              # MongoDB MCP server launcher (RO/RW)
│   └── history.py / queries.py    # provenance + pre-image capture; query helpers
├── shared/
│   ├── telemetry.py               # BQ emitter + Pydantic schemas
│   ├── rubrics.py                 # Vertex AI Eval Service, all 6 rubrics
│   ├── mongo_tools.py             # pymongo helpers (RO/RW secret routing)
│   ├── clients.py                 # lazy BQ/secret clients (LOCAL_DEV fallbacks)
│   ├── skills.py                  # skill registry + read_skill tools + on-disk reconcile
│   ├── memory.py                  # VertexAiMemoryBankService (ADK)
│   ├── allocator.py / provenance.py / bigquery_helper.py / imagen.py
│   └── integrations/              # GA4 / HubSpot / Google Ads / LinkedIn clients
├── prompts/                       # versioned prompt templates per playbook
├── agents/                        # ADK agents — every one exposed via A2A (to_a2a)
│   ├── pipeline.py                # SequentialAgent(Research→Content→Review→Critique→Revise)
│   ├── research/content/review/analytics/cmo_planner.py  # core drafting + weekly CMO cycle
│   ├── positioning/paid_media/lifecycle_email/customer_voice/ops_qa/image_brief/aeo_agent.py
│   ├── signal_router.py / signal_watcher.py              # signal-triggered drafting
│   ├── self_critique.py / self_critique_runner.py / _miners/   # closed-loop learning
│   ├── critique.py / reviser.py / finalizer.py           # inline critique → revise loop
│   ├── a2a_server.py / a2a_client.py                     # A2A exposure + worker client
│   ├── _factory.py _models.py _prompts.py _mcp.py _mongodb_tools.py _skills_config.py _schema_constants.py
│   └── cmo_planner_visual/        # Agent Designer YAML (demo variant; not a Python package)
├── services/                      # Cloud Run jobs + HTTP services
│   ├── web_api/                   # FastAPI — the UI's /api backend (public)
│   ├── outcome_attach/            # GA4 + HubSpot + Google Ads + LinkedIn attribution
│   ├── eval_harness/              # nightly all-6-rubric re-grade
│   ├── derive_track_records/ drift_detect/              # rubric rollups + 28d drift → exp
│   ├── self_critique/ promotion_gate/ positioning_review/   # weekly learning + gates
│   ├── paid_media_sweep/ ops_qa_sweep/ snapshot_mongo/
│   ├── substack_publisher/ substack_publish_sweep/      # publishing + stuck-publish retry
│   ├── edit_capture_handler/      # Gemini edit classifier (approval Sheet → handler)
│   └── slack_approval_handler/
├── skills/                        # versioned SKILL.md playbooks (house-style, aeo, …)
├── web/                           # React + Vite SPA — the public website (Firebase Hosting)
│   ├── src/                       # routes, components, lib/api.ts (same-origin /api)
│   └── package.json               # build → dist/
├── apps_script/Code.gs            # approval Sheet → edit-capture-handler sync
├── scripts/
│   ├── create_agent_identity.sh / create_mongo_users.sh / create_model_armor_template.sh
│   └── setup_github_wif.sh        # one-time Workload Identity Federation for CI deploy
├── dashboards/README.md           # Looker Studio build steps
├── demo/                          # seed_demo + run_pipeline / run_cmo_planner / run_research
└── tests/{unit,integration,e2e}/
```

## What's different from the initial cut

Everything below was either weak or stubbed in the first pass; now real:

| Component | Was | Now |
|---|---|---|
| Multi-agent team | 4 independent agents | SequentialAgent pipeline + AgentTool composition |
| Cross-agent calls | Python imports | A2A protocol via `to_a2a()` |
| Memory Bank | Guessed import path | Real `VertexAiMemoryBankService` |
| Rubric harness | 2 hand-rolled Gemini calls | Vertex AI Eval Service, all 6 PointwiseMetric rubrics |
| Self-learning loop | inline 2-rubric scoring only | nightly all-6-rubric re-grade (`eval_harness`) + weekly `promotion_gate` |
| HubSpot / GA / LI handlers | `return None` | Real REST + GAQL + BQ-export queries with tenacity retry |
| Edit classifier | regex heuristic | Gemini-2.5-Flash structured output |
| Model Armor | template only | floor settings + VERTEX_AI integration + template binding |
| Analytics agent | absent | Real LlmAgent used by CMO via AgentTool |
| DECISIONS.md | created unilaterally | removed |

## Run order

> **Deploying to GCP via CI?** See **`docs/DEPLOYMENT.md`** for the recommended
> path: a one-time pre-setup checklist, then a manual, keyless GitHub Actions
> deploy (`.github/workflows/deploy.yml`, Workload Identity Federation) that
> runs all of the below for you and publishes the UI to Firebase Hosting. The
> manual scripts here remain the source of truth that workflow orchestrates.
>
> To run the whole thing **locally** (Dockerized Mongo + synthetic fallbacks,
> no cloud creds), see **`LOCAL_DEV.md`**.

```bash
cd hindsight-guild
export PROJECT_ID=hindsight-guild-mvp REGION=us-central1 BILLING_ACCOUNT=<id>

./setup.sh                                # GCP project, APIs (incl. Firebase), Atlas M0, secrets
./scripts/create_mongo_users.sh           # RO + writer Atlas users → Secret Manager
./scripts/create_agent_identity.sh        # sa-agents + sa-scheduler
./scripts/create_model_armor_template.sh  # floor + template + binding
python mongo/seed.py                      # collections + vector index

./deploy/all.sh                           # 16 images, 17 services, 11 jobs, 11 schedulers,
                                          # IAM, + UI → Firebase Hosting
                                          # (see deploy/README.md for per-phase control)

# Find the Agent Engine ID (created via `adk deploy` or Agent Engine console),
# then expose it to the agent runtime so Memory Bank works:
export AGENT_ENGINE_ID=<id>

# Manual:
#   - Build approval Sheet + paste Apps Script per apps_script/README.md.
#     Sheet header (row 1) must be:
#        telemetry_id | channel | original_draft | approved_text |
#        decision | rejection_reason | decided_by | decided_at | synced
#   - In the Apps Script editor: File → Project properties → Script
#     properties → add HANDLER_URL = <edit-capture-handler Cloud Run URL>/handle
#     (no more code edits per deploy — getHandlerUrl_() reads this).
#   - Populate slack_webhook_url secret (until then, slack_approval logs
#     'webhook_unconfigured' and returns successfully without notifying).
#   - Build Looker dashboard per dashboards/README.md
#   - Open the public site: https://<PROJECT_ID>.web.app (Firebase Hosting).
#     Add a custom domain later in the Firebase console → Hosting.

python demo/seed_demo.py                  # 30 days of state, run 60+ min before demo

# Drafting via the SequentialAgent pipeline
python demo/run_pipeline.py --icp seg_revops_director --channel linkedin

# Weekly CMO cycle
python demo/run_cmo_planner.py
```

## Testing

```bash
pip install -e ".[dev]"
ruff check .                      # lint (also the CI gate)
pytest tests/unit -q              # mocked; no cloud creds needed
INTEGRATION_TEST=1 pytest tests/integration -q   # against live cloud
python -m tests.e2e.e2e_smoke     # end-to-end drivers (need a running stack; see tests/e2e/)
```

The headline integration test is `tests/integration/test_reject_then_redraft.py`:
inject a fresh negative, re-score similar drafts, confirm the rubric grounding
picks up the new negative and lowers scores on like patterns. The A2A handshake
test verifies cross-agent calls work over the protocol. `tests/e2e/` holds
full-loop drivers (smoke, agent handoffs, PRD features, memory tiers,
skill-evolution) that exercise the live API + agents.

## Conventions

- **Region:** `us-central1`. Atlas colocated.
- **Models:** Gemini 3.5 Flash (frontier) for Content + CMO Planner + Lifecycle Email + Positioning + Paid Media + Self-Critique + Reviser; Gemini 3.1 Flash-Lite (cost-efficient) for Research, Review, Analytics, Ops/QA, Customer Voice, ImageBrief, Critique, rubric judge, edit classifier. Both GA on Vertex AI; `gemini-3-pro-preview` was discontinued 2026-03-26.
- **Secrets:** Never in code. All in Secret Manager.
- **Mongo access:** Content + Review use `mongo_uri_readonly`; Research, CMO, workers use `mongo_uri_writer`. Atlas enforces server-side.
- **Telemetry:** Every agent emits one row to `telemetry.actions` via `after_agent_callback`. Outcome slots created at the same time, filled async by `services/outcome_attach`.
- **Eval:** All 6 rubrics live, inline at draft time (Eval Service) + nightly re-grade by `eval_harness`.
- **Cost cap:** ~$1k/mo at solo-founder scale (Cloud Run scales to zero, Atlas
  M0 free tier; spend is mostly Cloud Build minutes + Vertex AI tokens).

## Caveats — read before deploying

- **ADK 1.x API drift:** Pinned versions in `pyproject.toml`. If `to_a2a` import path or `LlmAgent.output_key` shape differs in your install, see referenced docs.
- **Memory Bank requires Agent Engine ID:** Set `AGENT_ENGINE_ID` env var after the agent engine is created. The code raises if not set.
- **Vertex AI Eval Service usage:** Counted toward your project quota. Synchronous calls at draft time + nightly batch can run several hundred eval calls/day.
- **Model Armor flag drift:** Floor-settings + template binding flags may differ slightly across `gcloud` versions. Verify against the docs linked in `scripts/create_model_armor_template.sh`.
- **A2A service auth:** Deployed with `--no-allow-unauthenticated`. Configure caller IAM bindings (`gcloud run services add-iam-policy-binding`) so workers can invoke agents.
