# MongoDB + MCP — the backing store and how agents reach it

MongoDB Atlas is the system's structured corpus + transactional state. It's
reached two ways:

1. **By LLM-driven agents** through the **MongoDB MCP server** (the
   `mongodb-mcp-server` npm package, run as a stdio subprocess from each
   agent process via `MCPToolset`). This is the hackathon-required
   integration surface — every Gemini call that decides to query Mongo
   does so via MCP, so policy enforcement, audit, and Tool Hub governance
   all sit on one boundary.

2. **By non-LLM workers** (outcome_attach, drift_detect, self_critique,
   eval_harness, promotion_gate, seed_demo) and the rubric harness's
   negatives lookup, through the thin `pymongo`-based wrappers in
   `shared/mongo_tools.py`. MCP indirection isn't useful when there's no
   LLM picking the call — and would just add a subprocess hop.

Atlas server-side governance is the hard line. Two Atlas users
(`agent-readonly`, `agent-writer`) created by
`scripts/create_mongo_users.sh` enforce read vs write at the DB layer,
regardless of which path a caller took.

## Folder map

```
mongo/
├── README.md               # this file
├── __init__.py
├── schema.py               # collections + indexes + vector search index — the real work
├── seed.py                 # back-compat shim → calls schema.apply()
├── mcp_server.py           # MCP server lifecycle helpers, local dev runner, tool catalog
├── queries.py              # reference queries used across the codebase
├── cli.py                  # operator CLI: python -m mongo.cli <subcommand>
├── data/                   # the actual seed-data content lives here
│   ├── __init__.py
│   ├── skills.py
│   ├── customer_voice.py
│   ├── negative_examples.py
│   ├── messaging_library.py
│   └── experiments.py
└── calibration/README.md   # rubric calibration sets in GCS (not Mongo)
```

`demo/seed_demo.py` is a thin orchestrator that calls `mongo.schema.apply()`,
loads from `mongo.data.*`, and generates the synthetic BigQuery telemetry.
The actual Mongo content lives here, in `mongo/data/`.

## Collections (six)

| Collection | Used by | Vector index? | Purpose |
|---|---|---:|---|
| `customer_voice` | Content (vector-search), Research (insert-many) | yes (1 of 1 on M0) | Voice quotes per ICP, themed |
| `messaging_library` | Content + Review (find by ICP + status) | no | Approved claims with evidence URLs |
| `negative_examples` | Review (find by channel + category), Rubric harness (find_sorted ts DESC) | no | Rejected drafts → grounding for rubric judge |
| `skills` | Content (find_one current_version), Promotion gate (upsert promotion_request), Self-critique (upsert proposal) | no | Versioned playbooks with track records |
| `experiments` | CMO Planner (find running + decided), Drift detector (upsert investigation), Outcome attach (transition state) | no | Experiment registry — the unit of learning |
| `approvals` | Edit-capture handler (upsert by telemetry_id), CMO planner (history) | no | Approval records per telemetry_id |

Vector search index: `customer_voice.customer_voice_vector`, defined with Atlas
**Automated Embedding** (`type: autoEmbed`, model `voyage-4-lite`, managed
server-side by Atlas — see `mongo/schema.py`). We index the `text` field
directly; Atlas embeds it on insert and embeds the query string on
`$vectorSearch` — there is no client-side embedding code and no Voyage API key.
The M0 free tier allows exactly one search index, which is why it lives on
`customer_voice` and the other collections use exact-match indexes. (Automated
Embedding is in public preview and may require a paid tier; on M0 the
`mongodb_vector_search` tool falls back to a plain field-filter `find()`.)

## MongoDB MCP server — what tools it exposes

The `mongodb-mcp-server` package (run via `npx -y mongodb-mcp-server`) exposes
the following MCP tools per its protocol:

| Tool name | What it does | Available with `--readOnly` |
|---|---|:-:|
| `find` | filter, sort, limit, projection | yes |
| `find-one` | first match | yes |
| `aggregate` | pipeline stages | yes |
| `count` | count documents | yes |
| `distinct` | distinct values for a field | yes |
| `vector-search` | semantic search; **Atlas auto-embeds the query server-side** | yes |
| `list-collections` | enumerate | yes |
| `list-indexes` | enumerate | yes |
| `insert-one` / `insert-many` | inserts; **Atlas auto-embeds the indexed `text` field on insert** | no |
| `update-one` / `update-many` | updates | no |
| `delete-one` / `delete-many` | deletes | no |
| `create-collection` | create | no |
| `drop-collection` | drop | no (and Tool Hub policy denies) |
| `atlas-*` | Atlas admin (create-cluster, list-orgs, etc.) | no (Tool Hub policy denies) |

Per-agent scope is enforced THREE ways:
1. Atlas DB user (`agent-readonly` cannot write at the DB layer).
2. MCP server `--readOnly` flag (process-level).
3. Tool Hub policy (Phase 2; deferred — see `tool_hub/MIGRATE.md`).

## Auto-embedding via Atlas Automated Embedding

Embeddings are **managed entirely by Atlas**, not by the application. The
`customer_voice_vector` index is declared with `type: autoEmbed` (model
`voyage-4-lite`), so Atlas embeds the `text` field on insert and embeds the
query string at `$vectorSearch` time — server-side, with a model it manages.

Consequences:
- **No client-side embedding code** and **no Voyage API key** in the app. There
  is no `MDB_MCP_EMBEDDING_PROVIDER` / `MDB_MCP_VOYAGE_API_KEY` to set; the only
  env var the MCP server needs is the connection string.
- Inserts pass plain text; `$vectorSearch` queries pass plain text (a `query`,
  not a precomputed `queryVector`). See
  `agents/_mongodb_tools.mongodb_vector_search`.

```
MDB_MCP_CONNECTION_STRING=<from Secret Manager>
```

See `mongo/mcp_server.py:build_env()` for how `agents/_mcp.py` injects this.

## Quick start

```bash
# 1. Apply schema (creates collections + indexes + vector search index)
python mongo/seed.py     # or: python -m mongo.schema apply

# 2. Load seed data
python -m mongo.cli load-all

# 3. Verify
python -m mongo.cli list-collections
python -m mongo.cli running-experiments

# 4. Locally test the MCP server (outside an agent process)
bash mongo/mcp_server.py --health-check
```
