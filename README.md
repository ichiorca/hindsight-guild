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
callers invoke them as remote services — not Python imports.

## Repo layout

```
agentic-marketing/
├── pyproject.toml                 # pinned deps incl. google-ads, a2a-sdk
├── setup.sh                       # GCP + Atlas bootstrap (C0)
├── cloudbuild.yaml                # parallel image builds (17 images)
├── deploy/                        # phased deploy scripts — see deploy/README.md
│   ├── all.sh                     # full deploy orchestrator
│   ├── env.sh                     # shared SA/image/job/schedule maps
│   ├── 01-build-images.sh         # cloudbuild.yaml submit
│   ├── 02-deploy-services.sh      # 13 A2A + 4 HTTP services
│   ├── 03-deploy-jobs.sh          # 11 Cloud Run jobs
│   ├── 04-schedulers.sh           # 11 Cloud Scheduler triggers
│   ├── 05-deploy-ui.sh            # UI rebuild + deploy
│   └── 06-bind-iam.sh             # cross-service IAM
├── sql/schema.sql                 # 3 BQ tables + 2 views (C1)
├── mongo/seed.py                  # 6 Mongo collections + vector index (C2)
├── shared/
│   ├── telemetry.py               # BQ emitter + Pydantic schemas
│   ├── rubrics.py                 # Vertex AI Eval Service, all 6 rubrics
│   ├── mongo_tools.py             # pymongo helpers (RO/RW secret routing)
│   ├── allocator.py               # weighted-random, Vizier-swap-ready
│   └── memory.py                  # VertexAiMemoryBankService (ADK)
├── prompts/                       # versioned prompt templates per playbook
├── tool_hub/MIGRATE.md            # Cloud API Registry intent doc
├── agents/
│   ├── _prompts.py                # system prompts; cross-agent state vars
│   ├── _mcp.py                    # MongoDB MCPToolset factory (RO/RW)
│   ├── _common.py                 # Model Armor + telemetry callbacks
│   ├── research.py                # LlmAgent, output_key='research_findings'
│   ├── content.py                 # LlmAgent reading {research_findings}
│   ├── review.py                  # LlmAgent reading {draft}
│   ├── analytics.py               # LlmAgent, used as tool by CMO
│   ├── cmo_planner.py             # LlmAgent w/ AgentTool(research|analytics)
│   ├── pipeline.py                # SequentialAgent(Research→Content→Review)
│   ├── a2a_server.py              # to_a2a() exposure per agent
│   ├── a2a_client.py              # call_agent() helper for workers
│   └── cmo_planner_visual/        # Agent Designer YAML (demo-flair variant; intentionally not a Python package)
├── services/
│   ├── outcome_attach/            # GA4 + HubSpot + Google Ads + LinkedIn
│   ├── drift_detect/              # 28d rubric drop → investigation exp
│   ├── self_critique/             # Gemini playbook revisions weekly
│   ├── eval_harness/              # NIGHTLY all-6-rubric re-grade
│   ├── promotion_gate/            # WEEKLY skill promotion request engine
│   ├── edit_capture_handler/      # Gemini-based edit classifier
│   └── slack_approval_handler/
├── apps_script/Code.gs            # Sheet → handler sync
├── scripts/
│   ├── create_agent_identity.sh
│   ├── create_model_armor_template.sh   # floor settings + template
│   └── create_mongo_users.sh            # RO + writer Atlas users
├── dashboards/README.md           # Looker Studio build steps
├── demo/
│   ├── seed_demo.py               # 30-day synthetic state
│   ├── run_pipeline.py            # invoke Research→Content→Review
│   ├── run_cmo_planner.py         # invoke the CMO Planner
│   └── run_research.py            # Model Armor demo runner
└── tests/{unit,integration}/
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

```bash
cd agentic-marketing
export PROJECT_ID=agentic-marketing-mvp REGION=us-central1 BILLING_ACCOUNT=<id>

./setup.sh                                # GCP project, APIs, Atlas M0, secrets
./scripts/create_mongo_users.sh           # RO + writer Atlas users → Secret Manager
./scripts/create_agent_identity.sh        # sa-agents + sa-scheduler
./scripts/create_model_armor_template.sh  # floor + template + binding
python mongo/seed.py                      # collections + vector index

./deploy/all.sh                           # 17 images, 17 services, 11 jobs, 11 schedulers, IAM
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

python demo/seed_demo.py                  # 30 days of state, run 60+ min before demo

# Drafting via the SequentialAgent pipeline
python demo/run_pipeline.py --icp seg_revops_director --channel linkedin

# Weekly CMO cycle
python demo/run_cmo_planner.py
```

## Testing

```bash
pip install -e ".[dev]"
pytest tests/unit -q              # mocked; no cloud creds needed
INTEGRATION_TEST=1 pytest tests/integration -q   # against live cloud
```

The headline test is `tests/integration/test_reject_then_redraft.py`: inject a
fresh negative, re-score similar drafts, confirm the rubric grounding picks up
the new negative and lowers scores on like patterns. The A2A handshake test
verifies cross-agent calls work over the protocol.

## Conventions

- **Region:** `us-central1`. Atlas colocated.
- **Models:** Gemini 3.5 Flash (frontier) for Content + CMO Planner + Lifecycle Email + Positioning + Paid Media + Self-Critique + Reviser; Gemini 3.1 Flash-Lite (cost-efficient) for Research, Review, Analytics, Ops/QA, Customer Voice, ImageBrief, Critique, rubric judge, edit classifier. Both GA on Vertex AI; `gemini-3-pro-preview` was discontinued 2026-03-26.
- **Secrets:** Never in code. All in Secret Manager.
- **Mongo access:** Content + Review use `mongo_uri_readonly`; Research, CMO, workers use `mongo_uri_writer`. Atlas enforces server-side.
- **Telemetry:** Every agent emits one row to `telemetry.actions` via `after_agent_callback`. Outcome slots created at the same time, filled async by `services/outcome_attach`.
- **Eval:** All 6 rubrics live, inline at draft time (Eval Service) + nightly re-grade by `eval_harness`.
- **Cost cap:** $1k/mo. See cost model in `agentic_marketing_technical_spec_lean.md` §7.

## Caveats — read before deploying

- **ADK 1.x API drift:** Pinned versions in `pyproject.toml`. If `to_a2a` import path or `LlmAgent.output_key` shape differs in your install, see referenced docs.
- **Memory Bank requires Agent Engine ID:** Set `AGENT_ENGINE_ID` env var after the agent engine is created. The code raises if not set.
- **Vertex AI Eval Service usage:** Counted toward your project quota. Synchronous calls at draft time + nightly batch can run several hundred eval calls/day.
- **Model Armor flag drift:** Floor-settings + template binding flags may differ slightly across `gcloud` versions. Verify against the docs linked in `scripts/create_model_armor_template.sh`.
- **A2A service auth:** Deployed with `--no-allow-unauthenticated`. Configure caller IAM bindings (`gcloud run services add-iam-policy-binding`) so workers can invoke agents.
