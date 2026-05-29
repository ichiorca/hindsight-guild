# End-to-end driver scripts

Relocated here from `scripts/`. These are **standalone drivers**, not pytest
tests — they keep the `e2e_` prefix (not `test_`) so `pytest` does NOT collect
them. Run each directly as a module from the repo root.

They split into two tiers by what they need:

## No LLM — run in LOCAL_DEV against a live API (synthetic drafting)
Need the web API on a port + Mongo on `localhost:27017`. `--boot` self-spawns
the API; otherwise point `--base` at a running one.

| Script | What it proves | Run |
| --- | --- | --- |
| `e2e_smoke` | Every page's GET endpoints return 200 + the shape the UI reads (24 checks / 12 pages incl. Signals, Learning, AEO). | `python -m tests.e2e.e2e_smoke --boot` |
| `e2e_handoffs` | `/api/draft` routes each of 12 agents to the right synthetic shape. | `python -m tests.e2e.e2e_handoffs --boot` |
| `e2e_prd_features` | PRD-01/02/03 data plane (AEO, Signals, Self-Critique) via Mongo + HTTP. | `python -m tests.e2e.e2e_prd_features --base http://localhost:8080` |
| `e2e_memory_tiers` | 4-layer memory architecture audit (provenance / history / derived freshness). Warn-only audit phases. | `python -m tests.e2e.e2e_memory_tiers` |

## Real LLM — need `GOOGLE_API_KEY` (or Vertex ADC)
Drive real Gemini calls; slower, non-deterministic. Don't run two at once
(rate limits).

| Script | What it proves | Run |
| --- | --- | --- |
| `e2e_workflows` | Every agent in-process via real LLM → populates Mongo with authentic data. | `python -m tests.e2e.e2e_workflows` |
| `e2e_skill_evolution` | Full self-learning loop: edits → track records → Self-Critique → accept → promotion gate → version flip. | `python -m tests.e2e.e2e_skill_evolution` |
| `e2e_experiment_lifecycle` | CMO authors an experiment → drafts per variant → outcome attach → promotion gate. Needs `API_BASE`. | `python -m tests.e2e.e2e_experiment_lifecycle` |

Shared bootstrap (`.env` load, LOCAL_DEV defaults, UTF-8 stdio, banner
helpers) lives in `scripts/_test_bootstrap.py` (also used by
`scripts/local_seed.py`), imported as `from scripts._test_bootstrap import …`.
