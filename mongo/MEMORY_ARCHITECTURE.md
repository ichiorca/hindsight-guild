# Memory Architecture

## Four layers, one boundary rule

The system has four memory layers. They are kept separate because they answer
different questions and have different retention/mutation semantics.

```
┌─────────────────────────────────────────────────────────────────────┐
│ LAYER 1 · EVENT LOG (BigQuery)                                       │
│   What actually happened. Append-only. Immutable.                    │
│   - telemetry.actions   — every agent action                         │
│   - telemetry.outcomes  — slot fills, decisions                      │
│   - training.edits      — founder edit events                        │
│   Retention: forever (partitioned by ts).                            │
└─────────────────────────────────────────────────────────────────────┘
        │ source of truth for "what happened"
        ▼
┌─────────────────────────────────────────────────────────────────────┐
│ LAYER 2 · CANONICAL STATE (MongoDB · state.* and history.*)          │
│   What is true now. Versioned via history.* collections.             │
│   Every document carries a _provenance block.                        │
│                                                                       │
│   state.skills          history.skills          (every version)      │
│   state.experiments     history.experiments                          │
│   state.messaging       history.messaging                            │
│   state.positioning_proposals                                         │
│   state.email_sequences                                              │
│   state.paid_variants                                                │
│   state.ops_incidents                                                │
│   state.ops_targets                                                  │
│   state.attribution_map                                              │
│   state.approvals                                                    │
│   state.negative_examples (append-mostly; rarely mutated)            │
│   state.customer_voice    (append-mostly; rarely mutated)            │
│                                                                       │
│   Mutation rule: before any update to state.*, write the previous    │
│   document to history.* with a _superseded_at timestamp.             │
│   See mongo/history.py for the write-through helper.                 │
└─────────────────────────────────────────────────────────────────────┘
        │ derived from canonical state + event log
        ▼
┌─────────────────────────────────────────────────────────────────────┐
│ LAYER 3 · DERIVED STATE (MongoDB · derived.* + BigQuery views)       │
│   Recomputed from canonical + event log. Has freshness SLA.          │
│                                                                       │
│   derived.skill_track_records      — refreshed nightly from BQ       │
│   derived.icp_profiles             — refreshed weekly                │
│   derived.competitor_signals       — refreshed daily                 │
│   analytics.rubric_trend_28d       — BQ view, real-time              │
│   analytics.before_vs_after        — BQ scheduled query, 15-min      │
│                                                                       │
│   Every derived document carries _derived_at, _derived_from, and     │
│   _freshness_sla. Reads MUST check freshness; stale reads emit a     │
│   warning to telemetry.                                              │
└─────────────────────────────────────────────────────────────────────┘
        │ scoped session memory per agent invocation
        ▼
┌─────────────────────────────────────────────────────────────────────┐
│ LAYER 4 · EPISODIC MEMORY (Vertex AI Memory Bank · scoped)           │
│   Per-agent / per-workspace / per-ICP / per-campaign memories that   │
│   the LLM extracts asynchronously from completed sessions.           │
│   Used for cross-session continuity and cold-start priors.           │
│                                                                       │
│   scope conventions:                                                 │
│     workspace:<id>:agent:<agent_id>                                  │
│     workspace:<id>:icp_segment:<seg_id>                              │
│     workspace:<id>:channel:<channel>                                 │
│     workspace:<id>:campaign:<id>                                     │
│     workspace:<id>:skill:<skill_id>                                  │
└─────────────────────────────────────────────────────────────────────┘
```

## The boundary rule

**Canonical state is never overwritten without history capture.** The write
helper in `mongo/history.py` enforces this — every update copies the
pre-image to `history.<collection>` first, then mutates `state.<collection>`.

If you skip the helper and call `db.update_one` directly, you've broken the
guarantee. The Atlas users `agent-readonly` and `agent-writer` aren't enough
to prevent this — it's an application-level discipline backed by a code-
review check.

## Provenance schema — every canonical document

Every document in `state.*` carries a `_provenance` block:

```jsonc
{
  "_provenance": {
    "kind": "human" | "agent" | "system" | "ingestion",
    "actor_id": "founder@yours.co"  | "research_agent" | "cron:promotion_gate",
    "source": {
      "kind": "experiment_outcome|customer_voice_ingest|founder_decision|self_critique|drift_detect|...",
      "ref_id": "<id of the source record>",
      "ref_collection": "<collection name, e.g. state.experiments>"
    },
    "created_at": "<iso8601>",
    "updated_at": "<iso8601>",
    "confidence": 0.0,
    "evidence_count": 0,
    "trust_tier": "verified|inferred|hypothesis|stale",
    "supersedes": ["<id of previous version>"],
    "ttl": null
  },
  "_workspace": "default",
  "_owner": "<agent_id or 'founder'>",
  // ... domain payload ...
}
```

Trust tiers (used by queries to filter):
- **verified** — Founder-approved or directly observed (telemetry-backed).
- **inferred** — Agent-derived from verified sources with high confidence.
- **hypothesis** — Agent proposal awaiting verification (e.g. positioning_proposals).
- **stale** — Was verified once but the underlying source has changed.

## Workspace + owner fields

Every canonical doc has `_workspace` (defaults to `"default"`, but the
field exists for multi-tenant readiness) and `_owner` (the agent ID or
`"founder"` that authoritatively maintains it).

Ownership transitions (e.g. when the founder approves a positioning
proposal) are themselves recorded in `history.*` so the audit trail
includes the chain of custody, not just the value changes.

## Derived state contract

```jsonc
{
  "_derived": {
    "derived_at": "<iso8601>",
    "derived_by": "service:derive_track_records",
    "derived_from": [
      {"kind": "bigquery_view", "id": "analytics.skill_track_record"},
      {"kind": "mongo_collection", "id": "state.skills"}
    ],
    "freshness_sla": "PT24H",
    "stale": false
  }
}
```

Readers MUST honor `freshness_sla`. If `derived_at + freshness_sla` is in
the past, set `stale=true` and let the calling agent decide whether to
proceed with stale data or trigger a refresh.

## Layer ownership

| Layer | Writer | Reader | Mutability |
|---|---|---|---|
| Event log (BQ) | agents (callbacks), workers | analytics agent, dashboards | append-only |
| Canonical state | agents (RW Atlas user), founder (via UI) | all | history-versioned mutation |
| Derived state | cron services only | all | full replace |
| Episodic memory | agents (via ADK Memory Bank) | agents | extracted asynchronously |

## Snapshots + replication

- **Hourly:** `services/snapshot_mongo` dumps every `state.*` collection to
  `gs://${PROJECT_ID}-snapshots/mongo/<yyyymmdd-HH>/` via `mongodump`. Retains
  7 daily snapshots + 30 hourly snapshots.
- **Atlas-managed backups:** available on M10+ (not M0). When you upgrade,
  flip on continuous backup + point-in-time recovery and the snapshot job
  becomes a redundant belt-and-suspenders.
- **Change streams:** the writer Atlas user can open a change stream against
  `state.*` for real-time replication to downstream systems. Used today
  only for telemetry; ready to be expanded for multi-region or for an
  edge cache.

## What changed vs the original schema

| Before | After | Why |
|---|---|---|
| `skills.track_record` field on canonical doc | `derived.skill_track_records` separate collection | derived data was masquerading as canonical |
| In-place mutation of state | `history.*` capture before mutation | reconstruct historical state |
| Ad-hoc provenance fields | Consistent `_provenance` block | queryable trust filtering |
| Single workspace assumed | `_workspace` on every doc | multi-tenant ready |
| Single Atlas region | + Hourly mongodump to GCS | survivable single-region failure |

## What we deliberately don't fix yet

- **Multi-region replica set:** requires Atlas M10+ ($60+/mo). Documented; not done.
- **Per-agent Atlas users:** still two (RO + RW). True least-privilege would mean 11 users; deferred per cost/complexity.
- **Online Archive tiering** for cold partitions: requires M10+.
- **CDC to downstream:** change streams are ready, no consumers wired.

These are upgrade paths, not architectural debt.
