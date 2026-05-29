"""Seed / load reference data into MongoDB.

Pulled out of mongo/cli.py so scripts (e.g., scripts/local_seed.py) can
import cmd_load_all directly without dragging in the argparse harness.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from shared import mongo_tools


def seed_playbook_skills(actor_id: str = "seed") -> int:
    """Wipe + reseed the playbook ``skills`` collection (and its history).

    Exposed as a reusable helper so scripts/local_seed.py can delegate
    the clean-mode playbook-seed step here instead of duplicating it.
    Returns the number of playbook skill docs written.
    """
    from mongo.data.skills import SKILLS
    from mongo.history import seed_canonical

    db = mongo_tools.db()
    db["skills"].delete_many({})
    db["history.skills"].delete_many({})

    seed_canonical("skills", SKILLS, actor_id=actor_id,
                    kind="ingestion", trust_tier="verified")
    return len(SKILLS)


def cmd_load_all(args=None):
    """Idempotent load. Re-running re-inserts (after delete) deterministically.

    Every seeded doc gets a default ``_provenance`` block (kind=ingestion,
    trust_tier=verified) and a matching ``history.<coll>`` create row, so
    canonical state stays architecturally compliant from the first byte.

    Embeddings on customer_voice.text auto-populate via the MongoDB MCP
    server's Voyage AI integration on insert; first vector-search after
    load may need ~30-60s for indexing to catch up.
    """
    import random

    from mongo.data.customer_voice import build_voice_docs
    from mongo.data.experiments import EXPERIMENTS
    from mongo.data.messaging_library import MESSAGING_CLAIMS
    from mongo.data.negative_examples import NEGATIVE_EXAMPLES
    from mongo.data.skills import SKILLS
    from mongo.history import seed_canonical

    rng = random.Random(42)
    voice = build_voice_docs(rng)

    db = mongo_tools.db()
    # Wipe canonical + history for the non-skills collections we touch
    # below; the skills collection is handled by seed_playbook_skills().
    for coll in ["customer_voice", "negative_examples",
                  "messaging_library", "experiments"]:
        db[coll].delete_many({})
        db[f"history.{coll}"].delete_many({})

    seed_playbook_skills(actor_id="seed")

    seed_canonical("customer_voice", voice, actor_id="seed",
                    kind="ingestion", trust_tier="verified")
    seed_canonical("negative_examples", NEGATIVE_EXAMPLES, actor_id="seed",
                    kind="ingestion", trust_tier="verified")
    seed_canonical("messaging_library", MESSAGING_CLAIMS, actor_id="seed",
                    kind="ingestion", trust_tier="verified")
    seed_canonical("experiments", EXPERIMENTS, actor_id="seed",
                    kind="ingestion", trust_tier="verified")

    print(f"Loaded: {len(SKILLS)} skills, {len(voice)} quotes, "
          f"{len(NEGATIVE_EXAMPLES)} negatives, {len(MESSAGING_CLAIMS)} claims, "
          f"{len(EXPERIMENTS)} experiments.")


def cmd_ingest_quotes(args):
    """Bulk-load customer_voice from a JSONL file.

    Each line: {"text": "...", "icp_segment": "...", "theme": "...",
                "persona": "...", "source": "...", "sentiment": "positive"}
    The MCP server's auto-embed populates the embedding field on insert.
    Each quote gets a default ``_provenance`` block (kind=ingestion,
    trust_tier=verified) and a history.customer_voice create row.
    """
    from mongo.history import seed_canonical

    path = Path(args.file)
    if not path.exists():
        print(f"File not found: {path}", file=sys.stderr)
        sys.exit(1)

    docs = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    if not docs:
        print("Empty file.")
        return
    seed_canonical("customer_voice", docs, actor_id="ingest_quotes_cli",
                    kind="ingestion", trust_tier="verified")
    print(f"Inserted {len(docs)} quotes. Allow 30-60s for embedding propagation.")
