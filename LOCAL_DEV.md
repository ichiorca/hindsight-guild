# Local development

Spin up the UI + API + Mongo on your laptop. No GCP credentials required —
BigQuery / Secret Manager / Vertex are stubbed when `LOCAL_DEV=1` is set,
and the dataset is a small Mongo-only seed.

What works in local mode:
  - Weekly Review (with a pre-staged `agent_skill` promotion_request on
    `house-style` so the AgentSkillPromotionCard + markdown diff render
    immediately, no waiting for cron jobs)
  - Skills, Voice, Capabilities, Experiments pages — all Mongo-backed
  - Approve/Reject on Skill promotions (writes through to Mongo +
    syncs `SKILL.md` to disk via the mtime-aware refresh path)

What doesn't work locally (returns empty data, not an error):
  - Queue, This-Week Summary, Rubric Trend, Live Ops, Before-vs-After —
    these read BigQuery. Set up a real GCP project to enable them.
  - `/api/draft` real-mode — requires the A2A pipeline service. The
    UI's Drafting page works in synthetic-fallback mode: set
    `DRAFTING_FALLBACK=synthetic` and it composes a draft from Mongo
    customer_voice + messaging_library.

---

## Prerequisites

  - Docker Desktop (for Mongo)
  - Python 3.12+
  - Node 20+ (for the React UI)

## 1. Install Python deps

```bash
pip install -e ".[dev]"
```

## 2. Start Mongo

```bash
docker compose up -d
# verify
docker compose ps             # mongo should be "running (healthy)"
```

Mongo is now at `mongodb://localhost:27017`. Data persists in a named
Docker volume (`mongo_data`); `docker compose down -v` wipes it.

## 3. Seed Mongo

Env vars live in **`.env`** at the repo root (already gitignored). The
seed auto-loads it; no `export` / `$env:` needed.

Two modes:

```bash
python -m scripts.local_seed              # CLEAN (default): only reference data
python -m scripts.local_seed --with-demo  # DEMO: + mock voice/negatives/experiments
                                          #        + a pre-staged WeeklyReview card
```

**Clean** writes just the 9 Skill documents the system needs to function
(4 playbook skills + 5 agent_skills with their SKILL.md bodies). All
other collections are empty. This is the right starting state when you
want to run the real pipeline and watch the self-evolution loop produce
genuine proposals from your own activity.

**Demo** additionally seeds:
- 30 `customer_voice` quotes (so Research has something to surface)
- 20 `negative_examples` (rubric grounding has examples to ground against)
- 10 `messaging_library` claims
- 5 `experiments`
- A pre-staged `promotion_request` on `house-style` so the
  AgentSkillPromotionCard renders on the WeeklyReview page immediately
  — useful when you want to walk someone through the approve-the-diff
  flow without first generating ~14 days of real telemetry.

Either mode is idempotent — re-run any time to reset to that state.

## 4. Start the API

`uvicorn` has a native `--env-file` flag that picks up the same `.env`.
Also exclude `.venv` from the file-watcher so package writes (pip installs
in another shell, IDE indexing, etc.) don't trigger reload loops:

```bash
uvicorn services.web_api.main:app \
  --env-file .env \
  --reload --reload-exclude .venv --reload-exclude node_modules \
  --port 8080
```

PowerShell one-liner (no backslash continuation):

```powershell
uvicorn services.web_api.main:app --env-file .env --reload --reload-exclude .venv --reload-exclude node_modules --port 8080
```

`--env-file .env` is **required** in local-dev mode — without it
`LOCAL_DEV=1` isn't set and the API tries to import `google.cloud.bigquery`
at module load. If you see `ImportError: cannot import name 'bigquery'`,
double-check the flag is on the command line.

The API now serves at `http://localhost:8080`. Health check:

```bash
curl http://localhost:8080/api/health
# {"ok": true}
```

## 5. Start the UI

In a second shell:

```bash
cd web
npm install         # first time only
npm run dev
```

Vite serves at `http://localhost:5173` and proxies `/api/*` to the local
API on `:8080`.

Open **http://localhost:5173/weekly-review** — you should see:
  - Headline: *"Ready to update the Skill library. house-style is dragging
    brand voice on linkedin, email (baseline 0.798). Diff below."*
  - Decision card titled **"Update Skill library: house-style"** with the
    `v1 → v2` chip, channel-at-risk pills (linkedin/email tinted red),
    and the unified diff rendered line-by-line.

Click **Approve & sync to disk** to walk through the full path: the
promotion_request flips `current_version` in Mongo (the source of truth),
and `skills/house-style/SKILL.md` reconciles lazily on the next
`read_skill("house-style")` call — `SkillRegistry.read_body` compares
the on-disk content to Mongo's current_version `body_md` and atomically
rewrites the file (temp-file + `os.replace`) before serving. Reload the
page; the request disappears.

## Populating the Experiments page (end-to-end lifecycle test)

The `/experiments` route starts empty because no one writes to the
`experiments` collection by default — the production path is the
weekly CMO Planner cron + the daily `drift_detect` Cloud Run job, and
neither runs locally. `tests/e2e/e2e_experiment_lifecycle.py` drives the
full lifecycle in one shot so you can see real data appear on the page.

```bash
# Prereqs: Docker Mongo up, `.env` populated (GOOGLE_API_KEY required),
# scripts/local_seed.py already ran. The web_api + UI dev servers don't
# need to be running — the test prints what they'll show when started.
python -m tests.e2e.e2e_experiment_lifecycle
```

What runs (≈3–5 min total):

1. **CMO Planner authors an experiment.** Real Gemini call, operator-mode
   prompt. The agent calls `mongodb.insert-one` on the `experiments`
   collection and returns the inserted `_id`.
2. **Drafting pipeline runs under each variant.** `_RUNS_PER_VARIANT`
   real pipeline executions (Research → Content → CritiqueLoop →
   ImageBrief → Review → Finalizer) per variant. Each emits a Mongo
   `actions` row tagged with `experiment_id` + `variant_id`.
3. **Outcomes synthesized.** Production reads GA4 via BigQuery; LOCAL_DEV
   has neither, so the test writes synthesized 72h-engagement values
   onto each action — `A_control` ≈ 0.40–0.45, `B_urgency` ≈ 0.50–0.55
   (a clean 5pp lift, above the experiment's 3pp MDE).
4. **Decision fires.** A Mongo-equivalent of
   `services.outcome_attach._maybe_decide_experiment` calls the SAME
   `mongo_tools.transition_experiment_state` helper, so the audit trail
   in `history.experiments` is identical to production.
5. **Drift investigation opens.** The test synthesizes 28 days of
   declining `brand_voice` scores on a second channel (`email`) and runs
   the Mongo equivalent of `services.drift_detect._detect_drift`. The
   `_open_investigation` document shape matches the production helper
   byte-for-byte.
6. **Promotion gate runs.** Mongo-equivalent of
   `services.promotion_gate._evaluate_candidate` aggregates eval scores
   by `skill_version`; when the candidate beats the incumbent by
   ≥ MDE, raises a `promotion_request` on `skills.linkedin_post`.
7. **API verification.** Hits `/api/experiments/running|decided|drift`
   and `/api/skills` on `localhost:8080` and prints what the React UI
   will see. (If `web_api` isn't running, the writes are still durable
   in Mongo — the data appears the moment you start `uvicorn`.)

After it finishes, open <http://localhost:5173/experiments> and the
page should show:

- **Recently decided** → the urgency-framing experiment with a `+5pp` badge
- **Drift investigations** → the `brand_voice` drop on `email`
- Nothing in **Running** for the urgency experiment (it's decided now)

To see the promotion request, open <http://localhost:5173/weekly-review>
— `skills.linkedin_post` now has a `promotion_request` with
`status="awaiting_approval"`.

### Re-running

The script's phase 1 cleans every doc its previous run created (test
experiments, drift investigations, synthetic actions, history rows,
promotion requests sourced from this test). Pass `--keep` to skip the
cleanup. Pass `--phases 0,1,2` (etc.) to run a subset.

### When it fails

- **"GOOGLE_API_KEY not set"** — populate it in `.env`.
- **"Mongo unreachable"** — `docker compose up -d`.
- **"linkedin_post skill not found"** — `python -m scripts.local_seed`.
- **"Agent did not insert the experiment"** — the LLM missed the
  `insert-one` tool call. The harness falls back automatically. To debug,
  re-run with just `--phases 0,1,2` and read the agent trace; the
  CMO Planner prompt in `agents/_prompts.py` may need a stricter
  operator-mode example.
- **Gemini free-tier rate limit (`429`)** — drop `_RUNS_PER_VARIANT`
  from 2 to 1 at the top of the script, or wait a minute and re-run
  with `--phases 3,4,5,6,7,8` (phase 2 already wrote the experiment).

## Running the full drafting pipeline locally

The Research → Content → ImageBrief → Review → Finalizer pipeline normally
runs on Vertex AI. For local runs we route ADK to the **Gemini API direct
endpoint** (free tier from Google AI Studio) and degrade the other GCP
pieces:

| Piece               | Local behavior                                |
|---------------------|-----------------------------------------------|
| LLM calls           | Gemini API direct (your `GOOGLE_API_KEY`)     |
| Image generation    | `mode="stub"`, `public_url=None`              |
| BigQuery telemetry  | Skipped (`TELEMETRY_DISABLED=1`)              |
| Vertex AI Eval Service | Swallowed (rubric scoring stays `None`)    |
| Memory Bank         | No-op (`AGENT_ENGINE_ID` unset)               |
| MongoDB MCP         | Default read path; falls back to pymongo if Node is absent |
| Vector search       | Atlas auto-embed; falls back to `find()` off-Atlas |

### Setup (one-time)

1. **Get a Gemini API key.** Visit
   <https://aistudio.google.com/app/apikey> → "Create API key". No GCP
   project needed. Free tier handles ~60 requests/min — plenty for a
   handful of pipeline runs.

2. **Paste it into `.env`:**
   ```
   GOOGLE_API_KEY=AIza...your-key...
   ```
   The other env vars (`GOOGLE_GENAI_USE_VERTEXAI=0`, `TELEMETRY_DISABLED=1`)
   are already set.

3. **Make sure Mongo is up + seeded:**
   ```bash
   docker compose up -d
   python -m scripts.local_seed
   ```

### Two ways to run the pipeline locally

| Path                 | When to use                                    |
|----------------------|------------------------------------------------|
| **CLI runner**       | Quickest single-shot test, no UI involved.    |
| **UI + A2A service** | Click "Draft" in the WeeklyReview-adjacent Drafting page. Triggers a real pipeline run, polled to completion. |

### Path 1 — CLI runner (one-shot, no UI)

```bash
python -m demo.run_pipeline --icp seg_founder_b2b --channel substack \
    --topic "post-LLM GTM motion"
```

Expected flow (≈30–60 sec):
```
=== Pipeline run ============================
  telemetry_id : act_abc123def456
  channel      : substack
  icp          : seg_founder_b2b
  topic        : post-LLM GTM motion
=============================================

[research_agent] running...
[content_agent] running...
[image_brief_agent] running...
[review_agent] running...
[finalizer] running...

=== Pipeline output =========================
--- Research findings ---
  customer_voice (3 quotes):
    - We were shipping fast but the GTM motion never caught up.
    - ...
  approved_claims (3 claims):
    - ...

--- Draft (channel=substack) ---
  HEADLINE: Why post-LLM GTM motion ...
  SUBTITLE: ...

  ## A pattern across the teams I work with
  ...

--- Image (mode=stub) ---
  url: None
  alt_text: ...
  reason: vertexai_init_failed

--- Review ---
  recommendation: pass
  flags (0):
  notes: ...

--- Finalizer envelope (A2A response payload) ---
{ "telemetry_id": "...", "channel": "substack", "draft": {...}, ... }
```

### Path 2 — UI button (real pipeline behind /api/draft, async + polled)

Clicking **"Draft"** on the `/draft` UI page kicks off the same pipeline,
but routed through the web API. The architecture is two cooperating
processes plus the UI:

```
        UI (5173)
           │  POST /api/draft        ← returns {job_id, status:pending} instantly
           ▼
       web_api (8080)  ── enqueues a background task ──┐
           ▲                                           │
           │  GET /api/draft/{job_id}                  │
           │  (UI polls every 2s)                      │
           │                                           ▼
           └──────────────  pipeline A2A service (8005) ──┐
                                                          │ Runs
                                  Research → Content → ImageBrief → Review → Finalizer
                                                          │
                            ◄─── JSON-RPC envelope ────────┘
```

**Start the pipeline A2A service** in a dedicated shell:

```bash
uvicorn agents.a2a_server:pipeline_a2a --env-file .env --port 8005
```

This brings up the SequentialAgent (Research → Content → ImageBrief →
Review → Finalizer) as a stand-alone A2A server. It listens on port 8005,
reads `GOOGLE_API_KEY` + `MONGO_URI_DIRECT` from `.env`, and uses the same
local Mongo for the MCP toolset.

**Point the web_api at it** by setting in `.env`:

```
A2A_URL_PIPELINE=http://localhost:8005
```

The `useDraft` mutation in the UI now does the right thing automatically:

  1. POSTs to `/api/draft` → gets `{job_id, status: "pending"}` in <100ms.
  2. Polls `GET /api/draft/{job_id}` every 2s.
  3. When the job flips to `status: "done"`, returns the flat envelope
     (`{telemetry_id, draft, research_findings, image, review, ...}`)
     just like the synchronous version used to.

No component-side code changes — the existing simulated step progression
in `Drafting.tsx` runs alongside the real poll cycle. You'll see
"Research listening...", "Content drafting...", etc., for the same
30–60s the actual pipeline takes.

**Total local-dev shells:**

1. `docker compose up -d`  (Mongo, background)
2. `uvicorn agents.a2a_server:pipeline_a2a --env-file .env --port 8005`
3. `uvicorn services.web_api.main:app --env-file .env --reload --reload-exclude .venv --reload-exclude node_modules --port 8080`
4. `cd web && npm run dev`

**Synthetic fallback** stays available — set `A2A_URL_PIPELINE=` (blank) +
`DRAFTING_FALLBACK=synthetic` to skip the pipeline and get a fast
Mongo-composed mock. Useful when you're iterating on UI without
spending API quota.

### What can go wrong

- **`GOOGLE_API_KEY not set`** — fill it in `.env`.
- **`npx mongodb-mcp-server failed to start`** — Node has to be on PATH;
  `node --version` should print v18+. If it's missing, install Node 20.
- **Pipeline hangs at a sub-agent** — usually rate-limit on the free
  Gemini tier. Wait a minute and retry.
- **Empty `research_findings.customer_voice`** — vector-search uses Atlas
  Automated Embedding, which needs an Atlas cluster with the autoEmbed index
  ready. Off-Atlas (local Docker Mongo) or while the index is still building,
  `mongodb_vector_search` falls back to plain `find()`; if seed data doesn't
  match the ICP exactly you may see empty arrays. Use `seg_founder_b2b` /
  `seg_revops_director` / `seg_pmm_growth` (seeded ICPs).
- **UI hangs after clicking Draft** — `A2A_URL_PIPELINE` is set but the
  pipeline service isn't actually running, or it's running on a different
  port. Check the web_api logs and `curl http://localhost:8080/api/draft/<job_id>` —
  the job's `error` field tells you which connection failed.
- **`job_id not found`** — the in-memory job cache is capped at 100 and
  uvicorn `--reload` wipes it. Reload of the API while a draft is in flight
  loses the job; the UI will surface it as an error and the user re-clicks.

## Cleanup

```bash
docker compose down              # stop Mongo (keep data)
docker compose down -v           # stop + wipe data
```

## Tearing back to a fresh state

```bash
# nuke everything Mongo
docker compose down -v && docker compose up -d
# revert the SKILL.md you just promoted (if you clicked approve)
git checkout skills/house-style/SKILL.md
rm -rf skills/house-style/versions

# re-seed
python -m scripts.local_seed
```

---

## Environment-variable cheat sheet

| Var                  | What it does                                                |
|----------------------|-------------------------------------------------------------|
| `MONGO_URI_DIRECT`   | Bypass Secret Manager, use this URI directly. Required.    |
| `MONGO_DB`           | Database name (default: `agentic_marketing`).               |
| `LOCAL_DEV`          | `1` = web_api skips BigQuery init; degrades to empty data.  |
| `DRAFTING_FALLBACK`  | `synthetic` = `/api/draft` composes from Mongo when A2A is unreachable. |
| `PROJECT_ID`         | Required by some modules at import time; any string works locally. |
| `TELEMETRY_DISABLED` | `1` = agent callbacks skip BQ telemetry writes.             |

## Troubleshooting

**`pymongo.errors.ServerSelectionTimeoutError` on seed**
Mongo isn't up yet. `docker compose ps` should show `(healthy)`. If not,
`docker compose logs mongo`.

**`ImportError: cannot import name 'bigquery'` on `uvicorn`**
`LOCAL_DEV=1` must be set in the same shell that runs uvicorn. Check
`echo $LOCAL_DEV`.

**UI loads but every page is empty**
Mongo isn't seeded, or the API can't reach it. Hit
`curl http://localhost:8080/api/skills` — should return the 9 skill docs.

**Skill changes don't appear after approve**
The mtime auto-refresh runs on every `read_skill` call. The Skills page
caches in React Query for 60s; hard-reload to clear.
