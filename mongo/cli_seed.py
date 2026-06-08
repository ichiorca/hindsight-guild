"""Seed / load reference data into MongoDB.

Pulled out of mongo/cli.py so scripts (e.g., scripts/local_seed.py) can
import cmd_load_all directly without dragging in the argparse harness.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

from shared import mongo_tools


class SeedGuardError(RuntimeError):
    """Raised when a destructive seed targets a non-local Mongo unconfirmed."""


def _redact(uri: str) -> str:
    """Mask credentials in a Mongo URI so guard messages never leak secrets."""
    return re.sub(r"(mongodb(?:\+srv)?://)[^@]*@", r"\1<redacted>@", uri)


def _seed_target_is_local() -> bool:
    """True when the write target is a throwaway local/Docker Mongo.

    Drives off MONGO_URI_DIRECT: no override means the Secret Manager path,
    which is a remote (Atlas) cluster by definition. An ``mongodb+srv`` URI is
    always remote. localhost / 127.0.0.1 / the docker-compose ``mongo`` service
    host count as local.
    """
    direct = os.environ.get("MONGO_URI_DIRECT", "")
    if not direct:
        return False
    u = direct.lower()
    if "mongodb+srv" in u:
        return False
    return any(h in u for h in
               ("localhost", "127.0.0.1", "@mongo:", "//mongo:", "@mongo/", "//mongo/"))


def guard_destructive_seed(op: str = "seed") -> None:
    """Refuse a wipe+reseed against a non-local Mongo unless explicitly named.

    Local Docker/localhost targets are always allowed. A remote/Atlas target
    requires ``MONGO_SEED_CONFIRM`` to name it:
      * MONGO_URI_DIRECT set -> MONGO_SEED_CONFIRM must be a substring of it
        (e.g. the cluster name 'hindsight-guild').
      * Secret Manager path  -> MONGO_SEED_CONFIRM must equal PROJECT_ID.

    Raises SeedGuardError otherwise. This is the single choke point protecting
    every destructive seed entry (mongo.cli load-all, /api/admin/seed,
    scripts.local_seed); demo/seed_demo.py carries its own equivalent guard.
    """
    if _seed_target_is_local():
        return
    direct = os.environ.get("MONGO_URI_DIRECT", "")
    project = os.environ.get("PROJECT_ID", "hindsight-guild-mvp")
    confirm = os.environ.get("MONGO_SEED_CONFIRM", "")
    if confirm:
        if direct and confirm in direct:
            return
        if not direct and confirm == project:
            return
    if direct:
        target = _redact(direct)
        how = ("set MONGO_SEED_CONFIRM to a substring of MONGO_URI_DIRECT "
               "(e.g. the cluster name)")
    else:
        secret = os.environ.get("MONGO_SECRET_NAME", "mongo_uri_writer")
        target = f"Secret Manager '{secret}' in project '{project}'"
        how = f"set MONGO_SEED_CONFIRM={project}"
    raise SeedGuardError(
        f"Refusing destructive {op}: target Mongo is non-local ({target}) and "
        f"MONGO_SEED_CONFIRM does not name it. This wipes skills + reference "
        f"collections. To proceed deliberately, {how}."
    )


def seed_playbook_skills(actor_id: str = "seed") -> int:
    """Wipe + reseed the playbook ``skills`` collection (and its history).

    Exposed as a reusable helper so scripts/local_seed.py can delegate
    the clean-mode playbook-seed step here instead of duplicating it.
    Returns the number of playbook skill docs written.
    """
    guard_destructive_seed("skills reseed")
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
    guard_destructive_seed("load-all")
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
