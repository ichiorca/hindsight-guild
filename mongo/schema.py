"""MongoDB schema, indexes, vector search index. Idempotent.

Run:
    python -m mongo.schema apply
    python -m mongo.schema verify

Or invoke from Python:
    from mongo.schema import apply, verify
    apply()

`mongo/seed.py` is the back-compat entry point that calls apply().

Atlas builds the vector search index asynchronously — first calls to
mongodb.vector-search may fail with "no index" for 30-120s after creation.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.operations import SearchIndexModel

# google.cloud.secretmanager is imported lazily inside get_mongo_uri()
# so local-only callers (e.g., scripts/local_seed via MONGO_URI_DIRECT)
# don't need google-cloud-secret-manager installed.

PROJECT_ID = os.environ.get("PROJECT_ID", "hindsight-guild-mvp")
DB_NAME = os.environ.get("MONGO_DB", "hindsight_guild")
SECRET_NAME = os.environ.get("MONGO_SECRET_NAME", "mongo_uri_writer")

COLLECTIONS = [
    "experiments", "skills", "approvals",
    "negative_examples", "customer_voice", "messaging_library",
    # Mongo mirror of telemetry.actions / telemetry.outcomes. shared/telemetry.py
    # dual-writes here so local-dev (no BigQuery) keeps full traceability and
    # the Queue UI can read drafts without GCP credentials. Idempotent on
    # telemetry_id (the _id), so re-emits don't dup.
    "actions", "outcomes",
    # Attribution map: telemetry_id → external_ids (hubspot_email_id,
    # linkedin_post_urn, google_ads_campaign_id) so outcome_attach handlers
    # know what to look up. Populated by agents at publish/send time.
    "attribution_map",
    # Lifecycle Email Agent — sequences drafted and queued, never autosent.
    "email_sequences",
    # Paid Media Analyst Agent — variants drafted in PAUSED state.
    "paid_variants",
    # Ops/QA Agent — incidents found by the monitor pass.
    "ops_incidents",
    # Ops/QA Agent — URLs / pixels / forms to watch (founder-managed list).
    "ops_targets",
    # Positioning Agent — proposed claims/messaging changes pending approval.
    "positioning_proposals",
    # Agent Skills loader — one row per Tier 2/3 skill load (see shared/skills.py).
    # Append-only telemetry: "which agent loaded which skill when".
    "skill_usage",
    # Paid Media Analyst — configurable stop-loss thresholds. One doc per
    # (platform, icp_segment) tuple, plus a "_default" row used when no
    # specific config exists. The drafter reads this collection BEFORE
    # building stop-loss recommendations so thresholds can be tuned in
    # the UI without redeploying.
    "paid_thresholds",
    # PRD-01 (AEO) — one row per draft where aeo_reviser applied structural
    # rewrites. Consumed by PRD-03's aeo_miner to mine recurring rewrite
    # kinds and propose AEO playbook updates.
    "aeo_audits",
    # PRD-01 (AEO) — one row per logged citation by an AI search engine
    # (Perplexity / ChatGPT / Google AI Overviews / Claude). Manually
    # populated via `scripts/aeo/log_citation.py` in MVP 1; backs the
    # "Cited by AI engines" tile on Quality Signals.
    "aeo_citations",
    # PRD-02 (Signals) — event log of ICP-relevant signals observed by
    # signal_watcher_agent across HN / Reddit / RSS / etc. Each row may
    # trigger a draft via signal_router_agent; the action carries
    # triggered_by_signal_id back-ref.
    "signals",
    # PRD-02 (Signals) — adapter config. One row per (source kind,
    # named query). The watcher polls each enabled source on its
    # configured cadence.
    "signal_sources",
    # PRD-03 (Self-Critique) — proposed paid-platform actions (pause,
    # reallocate budget). Distinct from skill-level proposals (which
    # live on the singleton `skills.self_critique_proposal` field):
    # paid actions execute against the Google Ads / Meta Ads
    # integration adapters, not the skill library.
    "paid_actions_proposed",
    # PRD-03 (Self-Critique) — one row per nightly miner tick. Audit
    # log so the founder can see "what did the miners scan, what did
    # they propose, what errored" without grovelling through logs.
    "self_critique_runs",
]


# History collections — append-only pre-image of every canonical mutation.
# See mongo/history.py and mongo/MEMORY_ARCHITECTURE.md.
HISTORY_COLLECTIONS = [f"history.{c}" for c in COLLECTIONS]


# Derived collections — computed from canonical + event log on a schedule.
# Each doc carries a `_derived` block with freshness_sla.
DERIVED_COLLECTIONS = [
    "derived.skill_track_records",       # per-playbook rubric rollup
    "derived.agent_skill_track_records", # per-Agent-Skill rubric rollup
                                          # (cross-channel; powers Skill-level
                                          # self-critique)
    "derived.icp_profiles",              # weekly ICP summary
    "derived.competitor_signals",        # daily Research roll-up
]


def get_mongo_uri() -> str:
    # Local-dev override — same convention as shared/mongo_tools.py.
    direct = os.environ.get("MONGO_URI_DIRECT")
    if direct:
        return direct
    from google.cloud import secretmanager
    sm = secretmanager.SecretManagerServiceClient()
    name = f"projects/{PROJECT_ID}/secrets/{SECRET_NAME}/versions/latest"
    return sm.access_secret_version(name=name).payload.data.decode()


def _client() -> MongoClient:
    return MongoClient(get_mongo_uri())


def apply() -> None:
    """Create collections, indexes, vector search index. Idempotent."""
    db = _client()[DB_NAME]

    # Canonical state collections
    for c in COLLECTIONS:
        if c not in db.list_collection_names():
            db.create_collection(c)

    # History collections (append-only pre-images for time-travel queries)
    for c in HISTORY_COLLECTIONS:
        if c not in db.list_collection_names():
            db.create_collection(c)

    # Derived collections (recomputed on a schedule)
    for c in DERIVED_COLLECTIONS:
        if c not in db.list_collection_names():
            db.create_collection(c)

    # experiments — supports the CMO Planner's running/decided/by-tag queries
    db.experiments.create_index([("state", ASCENDING), ("created_at", DESCENDING)])
    db.experiments.create_index([("icp_segment", ASCENDING),
                                  ("channel", ASCENDING),
                                  ("state", ASCENDING)])
    db.experiments.create_index([("tags", ASCENDING)])
    db.experiments.create_index([("decided_at", DESCENDING)])

    # skills — promotion_gate scans by candidate presence; CMO Planner finds
    # self_critique_proposals; Content Agent finds by _id.
    # Two SEPARATE single-field indexes, not a compound one: a skill's
    # applies_to.channels and applies_to.icp_segments are BOTH arrays, and
    # MongoDB rejects a compound index spanning two array fields
    # ("cannot index parallel arrays"). The compound index builds on an empty
    # collection but makes every insert with both arrays fail. Separate
    # multikey indexes cover filtering by channel or by icp_segment.
    db.skills.create_index([("applies_to.channels", ASCENDING)])
    db.skills.create_index([("applies_to.icp_segments", ASCENDING)])
    db.skills.create_index([("self_critique_proposal.status", ASCENDING)])
    db.skills.create_index([("promotion_request.status", ASCENDING)])

    # approvals — unique by telemetry_id so Sheet syncs upsert cleanly
    db.approvals.create_index([("telemetry_id", ASCENDING)], unique=True)
    db.approvals.create_index([("decided_at", DESCENDING)])

    # negative_examples — hot path is find_sorted by (channel, category) ORDER
    # BY ts DESC. The compound + the ts DESC together cover this.
    db.negative_examples.create_index([("rejection_category", ASCENDING),
                                        ("channel", ASCENDING)])
    db.negative_examples.create_index([("ts", DESCENDING)])
    db.negative_examples.create_index([("tags", ASCENDING)])

    # messaging_library — Review Agent validates claims by (ICP, status)
    db.messaging_library.create_index([("applies_to_icp", ASCENDING),
                                        ("status", ASCENDING)])

    # customer_voice — exact-match by ICP / theme; vector index for semantic
    db.customer_voice.create_index([("icp_segment", ASCENDING)])
    db.customer_voice.create_index([("theme", ASCENDING)])

    # attribution_map — outcome_attach lookups by telemetry_id
    db.attribution_map.create_index([("telemetry_id", ASCENDING)], unique=True)

    # actions — dedup key so re-emits of the same (telemetry_id, agent,
    # action_type) tuple don't create duplicate rows in the Mongo mirror.
    db.actions.create_index(
        [("telemetry_id", ASCENDING), ("agent", ASCENDING),
         ("action_type", ASCENDING)],
        unique=True, name="idx_actions_dedup",
    )

    # email_sequences — Lifecycle Email Agent (queue → founder approval)
    db.email_sequences.create_index([("status", ASCENDING),
                                       ("icp_segment", ASCENDING)])
    db.email_sequences.create_index([("created_at", DESCENDING)])

    # paid_variants — Paid Media Analyst Agent (paused queue)
    db.paid_variants.create_index([("status", ASCENDING),
                                     ("platform", ASCENDING)])
    db.paid_variants.create_index([("campaign_id", ASCENDING)])

    # ops_incidents — Ops/QA Agent
    db.ops_incidents.create_index([("status", ASCENDING),
                                     ("severity", ASCENDING)])
    db.ops_incidents.create_index([("opened_at", DESCENDING)])

    # ops_targets — what Ops/QA monitors (founder-curated)
    db.ops_targets.create_index([("kind", ASCENDING)])

    # paid_thresholds — Paid Media stop-loss config. Look up by (platform,
    # icp_segment); fall back to the "_default" row if nothing matches.
    db.paid_thresholds.create_index([("platform", ASCENDING),
                                       ("icp_segment", ASCENDING)])

    # Seed a sensible default if the collection is empty. The drafter
    # ALWAYS reads from here at runtime — never hard-code thresholds.
    if db.paid_thresholds.count_documents({}) == 0:
        db.paid_thresholds.insert_one({
            "_id": "_default",
            "platform": "*",
            "icp_segment": "*",
            "daily_spend_floor_usd": 100.0,
            "min_conversions_per_24h": 1,
            "min_ctr_pct": 0.5,
            "min_hours_running": 12,
            "rationale": (
                "Default stop-loss: if a campaign spends > $100/day AND "
                "has < 1 conversion in 24h AND has been running > 12h, "
                "flag for pause. CTR below 0.5% is a secondary trigger. "
                "Tune per-platform / per-ICP by inserting more specific rows."
            ),
            "updated_at_iso": "2025-11-01T00:00:00Z",
        })

    # positioning_proposals — Positioning Agent
    db.positioning_proposals.create_index([("status", ASCENDING)])
    db.positioning_proposals.create_index([("applies_to_icp", ASCENDING)])

    # skill_usage — Tier 2/3 skill load telemetry (Claude Code-compatible
    # progressive disclosure observability). See shared/skills.py.
    db.skill_usage.create_index([("skill_name", ASCENDING),
                                   ("ts", DESCENDING)])
    db.skill_usage.create_index([("agent_name", ASCENDING),
                                   ("ts", DESCENDING)])
    db.skill_usage.create_index([("ts", DESCENDING)])

    # aeo_audits (PRD-01) — chronological scans for the PRD-03 aeo_miner.
    db.aeo_audits.create_index([("ts", DESCENDING)])
    db.aeo_audits.create_index([("telemetry_id", ASCENDING)])

    # aeo_citations (PRD-01) — 28-day sparkline query on the Cited-by tile.
    db.aeo_citations.create_index([("ts", DESCENDING)], name="ts_desc")
    db.aeo_citations.create_index([("telemetry_id", ASCENDING)],
                                   name="telemetry_id_sparse", sparse=True)

    # signals (PRD-02) — router pull query is processed=false ORDER BY
    # score DESC; per-source dashboards filter by source ORDER BY ts DESC.
    # evidence_url unique-sparse dedupes re-emit of the same thread.
    db.signals.create_index([("source", ASCENDING), ("ts", DESCENDING)])
    db.signals.create_index([("processed", ASCENDING), ("score", DESCENDING)])
    db.signals.create_index([("evidence_url", ASCENDING)],
                              unique=True, sparse=True,
                              name="idx_evidence_url_unique")

    # signal_sources (PRD-02) — watcher reads enabled=true rows on each
    # tick; the name field is the human-facing primary key in the UI.
    db.signal_sources.create_index([("enabled", ASCENDING)])
    db.signal_sources.create_index([("name", ASCENDING)], unique=True)

    # paid_actions_proposed (PRD-03) — Weekly Review pulls pending
    # rows for the founder; the apply path looks up by variant + kind
    # to dedupe re-proposals of the same action.
    db.paid_actions_proposed.create_index(
        [("status", ASCENDING), ("proposed_at", DESCENDING)])
    db.paid_actions_proposed.create_index(
        [("variant_id", ASCENDING), ("kind", ASCENDING), ("status", ASCENDING)])

    # self_critique_runs (PRD-03) — list-by-recency for /live ticker
    # + the ops drilldown.
    db.self_critique_runs.create_index([("started_at", DESCENDING)])

    # Seed agentic-commerce signal_sources so the watcher has something to
    # poll. Merchants are the primary ICP. ENABLED out of the box: HN + Reddit
    # via RSS. The `*-rss` Reddit sources need NO credentials — Reddit's public
    # JSON is rate-limited/blocked, so we read its Atom `.rss` feeds through the
    # generic rss adapter instead. The OAuth-based `reddit` sources are kept but
    # DISABLED: enable one (with REDDIT_USE_OAUTH=1 + the reddit_* secrets) for
    # richer scoring (upvotes/comments), and disable the matching `*-rss` source
    # so the same subreddit isn't double-fetched.
    if db.signal_sources.count_documents({}) == 0:
        db.signal_sources.insert_many([
            {
                "name": "hn-agentic-commerce",
                "source": "hn",
                "enabled": True,   # PRIMARY ICP — merchants going agent-ready
                "config": {
                    "query": ('"agentic commerce" OR "agent checkout" OR '
                              '"instant checkout" OR "ChatGPT shop" OR '
                              '"agent-ready" OR ACP OR UCP'),
                    "min_points": 3,
                },
                "icp_segment": "seg_merchant_dtc",
                "poll_interval_sec": 1800,
                "last_polled_at": None,
                "cursor": None,
                "score_floor": 0.5,
                "topic_hint_template": "Make your store agent-ready: stop {pain}",
                "default_channel": "blog",
            },
            {
                "name": "reddit-ecommerce-rss",
                "source": "rss",   # Reddit via .rss — no credentials needed
                "enabled": True,   # PRIMARY ICP — merchants
                "config": {
                    "feed_url": "https://www.reddit.com/r/ecommerce/new/.rss",
                },
                "icp_segment": "seg_merchant_dtc",
                "poll_interval_sec": 1800,
                "last_polled_at": None,
                "cursor": None,
                "score_floor": 0.5,
                "topic_hint_template": None,
                "default_channel": "blog",   # merchant how-to/discussion -> blog
            },
            {
                "name": "reddit-shopify-rss",
                "source": "rss",   # Reddit via .rss — no credentials needed
                "enabled": True,   # PRIMARY ICP — merchants
                "config": {
                    "feed_url": "https://www.reddit.com/r/shopify/new/.rss",
                },
                "icp_segment": "seg_merchant_dtc",
                "poll_interval_sec": 1800,
                "last_polled_at": None,
                "cursor": None,
                "score_floor": 0.5,
                "topic_hint_template": None,
                "default_channel": "linkedin",
            },
            {
                "name": "reddit-agenticcommerce-rss",
                "source": "rss",   # the on-topic sub, via .rss — no credentials
                "enabled": True,   # PRIMARY ICP — agentic-commerce discussion
                "config": {
                    "feed_url": "https://www.reddit.com/r/agenticcommerce/new/.rss",
                },
                "icp_segment": "seg_merchant_dtc",
                "poll_interval_sec": 1800,
                "last_polled_at": None,
                "cursor": None,
                "score_floor": 0.5,
                "topic_hint_template": "Make your store agent-ready: stop {pain}",
                "default_channel": "substack",   # analytical sub -> reflective essay
            },
            {
                "name": "reddit-ecommerce",
                "source": "reddit",   # OAuth path — needs REDDIT_USE_OAUTH=1
                "enabled": False,     # flip on once a Reddit app exists; disable reddit-ecommerce-rss
                "config": {
                    "subreddit": "ecommerce",
                    "min_upvotes": 5,
                },
                "icp_segment": "seg_merchant_dtc",
                "poll_interval_sec": 1800,
                "last_polled_at": None,
                "cursor": None,
                "score_floor": 0.5,
                "topic_hint_template": None,
                "default_channel": "linkedin",
            },
            {
                "name": "reddit-aiagents",
                "source": "reddit",   # OAuth path — needs REDDIT_USE_OAUTH=1
                "enabled": False,
                "config": {
                    "subreddit": "aiagents",
                    "min_upvotes": 5,
                },
                "icp_segment": "seg_agent_platform",
                "poll_interval_sec": 1800,
                "last_polled_at": None,
                "cursor": None,
                "score_floor": 0.5,
                "topic_hint_template": None,
                "default_channel": "blog",
            },
            {
                "name": "hn-agent-payments",
                "source": "hn",
                "enabled": False,
                "config": {
                    "query": ('"agent payments" OR AP2 OR x402 OR '
                              '"agentic checkout" OR "Visa Intelligent Commerce" '
                              'OR "Mastercard Agent Pay"'),
                    "min_points": 3,
                },
                "icp_segment": "seg_payments_network",
                "poll_interval_sec": 1800,
                "last_polled_at": None,
                "cursor": None,
                "score_floor": 0.5,
                "topic_hint_template": None,
                "default_channel": "blog",
            },
            {
                # Google News RSS is QUERY-FILTERED at the source, so every item
                # is on-topic (unlike a generic retail feed, whose flat 0.5 base
                # would flood the queue past the score floor). This is the right
                # shape for a generic RSS source in a niche domain.
                "name": "rss-google-news-agentic",
                "source": "rss",
                "enabled": True,   # topical industry news for e-comm leaders
                "config": {
                    "feed_url": ("https://news.google.com/rss/search?q="
                                 "%22agentic+commerce%22+OR+%22AI+shopping%22+OR+"
                                 "%22agentic+checkout%22&hl=en-US&gl=US&ceid=US:en"),
                },
                "icp_segment": "seg_ecom_leader",
                "poll_interval_sec": 3600,
                "last_polled_at": None,
                "cursor": None,
                "score_floor": 0.5,
                "topic_hint_template": None,
                "default_channel": "linkedin",
            },
        ])

    # Cross-collection memory-architecture indexes
    # Provenance + workspace filtering is a hot path for audits.
    for c in COLLECTIONS:
        db[c].create_index([("_workspace", ASCENDING)],
                            name="idx_workspace", sparse=True)
        db[c].create_index([("_owner", ASCENDING)],
                            name="idx_owner", sparse=True)
        db[c].create_index([("_provenance.trust_tier", ASCENDING),
                             ("_provenance.confidence", DESCENDING)],
                            name="idx_trust", sparse=True)
        db[c].create_index([("_provenance.updated_at", DESCENDING)],
                            name="idx_updated_at", sparse=True)

    # History collections — index by original_id so get_at() is fast
    for hc in HISTORY_COLLECTIONS:
        db[hc].create_index([("_original_id", ASCENDING),
                              ("_superseded_at", DESCENDING)],
                             name="idx_time_travel")

    # Derived collections — by freshness for staleness queries
    for dc in DERIVED_COLLECTIONS:
        db[dc].create_index([("_derived.derived_at", DESCENDING)],
                             name="idx_derived_at", sparse=True)
        db[dc].create_index([("_derived.stale", ASCENDING)],
                             name="idx_stale", sparse=True)

    # Vector search index — Atlas Automated Embedding (autoEmbed).
    #
    # Atlas embeds the `text` field on insert and the query string on
    # $vectorSearch with a managed Voyage AI model — no client-side embedding
    # and no Voyage API key (see agents/_mongodb_tools.mongodb_vector_search).
    # We index `text` directly (not a precomputed `embedding` vector). The
    # `filter` fields make icp_segment / theme usable as $vectorSearch
    # pre-filters.
    #
    # Model: voyage-4-lite — cheapest of the managed Voyage models, in line
    # with the project's lean cost cap. Bump to voyage-4 for higher recall.
    #
    # NOTE: Automated Embedding is in public preview (Atlas, May 2026) and may
    # require a paid cluster tier; on M0 the mongodb_vector_search tool falls
    # back to a plain field-filter find() automatically.
    existing = [idx["name"] for idx in db.customer_voice.list_search_indexes()]
    if "customer_voice_vector" not in existing:
        model = SearchIndexModel(
            name="customer_voice_vector",
            type="vectorSearch",
            definition={
                "fields": [
                    {
                        "type": "autoEmbed",
                        "modality": "text",
                        "path": "text",
                        "model": "voyage-4-lite",
                    },
                    {"type": "filter", "path": "icp_segment"},
                    {"type": "filter", "path": "theme"},
                ]
            },
        )
        db.customer_voice.create_search_index(model)
        print("Vector index 'customer_voice_vector' created (autoEmbed) — "
              "Atlas building asynchronously (30-120s).")
    else:
        print("Vector index 'customer_voice_vector' already exists.")

    print(f"Schema applied to {DB_NAME}.")
    print(f"Collections: {db.list_collection_names()}")


def verify() -> bool:
    """Verify schema is in place. Exits nonzero if anything's missing."""
    db = _client()[DB_NAME]
    missing = [c for c in COLLECTIONS if c not in db.list_collection_names()]
    if missing:
        print(f"Missing collections: {missing}", file=sys.stderr)
        return False

    vec = [idx for idx in db.customer_voice.list_search_indexes()
           if idx["name"] == "customer_voice_vector"]
    if not vec:
        print("Vector index 'customer_voice_vector' missing", file=sys.stderr)
        return False

    status = vec[0].get("status", "unknown")
    print(f"Schema OK. Collections: {len(COLLECTIONS)}. "
          f"Vector index status: {status}")
    return status in ("READY", "STEADY", "ACTIVE")


def wait_for_vector_index(timeout_s: int = 180) -> bool:
    """Block until the vector index reports READY (or timeout)."""
    db = _client()[DB_NAME]
    start = time.time()
    while time.time() - start < timeout_s:
        vec = [idx for idx in db.customer_voice.list_search_indexes()
               if idx["name"] == "customer_voice_vector"]
        if vec and vec[0].get("status") in ("READY", "STEADY", "ACTIVE"):
            print(f"Vector index ready after {int(time.time() - start)}s")
            return True
        time.sleep(5)
    print("Timed out waiting for vector index", file=sys.stderr)
    return False


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("command", choices=["apply", "verify", "wait"])
    args = p.parse_args()
    if args.command == "apply":
        apply()
    elif args.command == "verify":
        sys.exit(0 if verify() else 1)
    elif args.command == "wait":
        sys.exit(0 if wait_for_vector_index() else 1)
