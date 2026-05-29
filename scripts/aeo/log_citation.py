"""Manual citation logger — CLI for MVP 1.

When an AI search engine (Perplexity, ChatGPT, Google AI Overviews,
Claude) cites our content, the founder records it via this CLI. The
row lands in the ``aeo_citations`` Mongo collection and surfaces on
the "Cited by AI engines" tile on the Quality Signals page.

Usage::

    python -m scripts.aeo.log_citation \\
        --platform perplexity \\
        --query "what is the cost of CSM handoff" \\
        --cited-url https://example.com/blog/csm-handoff \\
        --evidence-url https://perplexity.ai/search/...

Optional flags:
    --telemetry-id act_xxxxxxxxxxxx     back-ref to the action that drafted cited-url
    --added-by founder                   defaults to "founder"

MVP 2 will replace this with the Perplexity API + Brave Search MCP
polling job. For now, paste-and-log is the lowest-friction surface.

Exit codes:
    0 — row inserted, _id printed.
    1 — argument error or DB connection failure.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime

# Bridge to LOCAL_DEV defaults. Both env vars are usually already set
# from .env when this runs from an activated venv, but the CLI may be
# invoked from a fresh shell.
os.environ.setdefault("LOCAL_DEV", "1")
os.environ.setdefault("MONGO_URI_DIRECT", "mongodb://localhost:27017")

_VALID_PLATFORMS = ("perplexity", "chatgpt", "google_aio", "claude")


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Log a citation of our content by an AI search engine.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--platform", required=True, choices=_VALID_PLATFORMS,
        help="Which AI engine cited us.",
    )
    p.add_argument(
        "--query", required=True,
        help="The user query that surfaced our content (max 500 chars).",
    )
    p.add_argument(
        "--cited-url", required=True, metavar="URL",
        help="The URL of OUR content that got cited.",
    )
    p.add_argument(
        "--evidence-url", default="", metavar="URL",
        help="URL of the citing surface (e.g., the Perplexity answer page). Optional but recommended.",
    )
    p.add_argument(
        "--telemetry-id", default="", metavar="ID",
        help="Optional act_xxxxxxxxxxxx of the draft action that produced cited-url. Lets the cited-by tile cross-link back to the queue card.",
    )
    p.add_argument(
        "--added-by", default="founder",
        help='Who logged this row. Defaults to "founder"; set to "perplexity_api" / "brave_mcp" when automated.',
    )
    return p


def _ensure_indexes(coll) -> None:
    """Create the two helpful indexes on first run. Idempotent — Mongo
    is fine with ``create_index`` for an existing equivalent index."""
    try:
        coll.create_index([("ts", -1)], name="ts_desc")
        coll.create_index(
            [("telemetry_id", 1)],
            name="telemetry_id_sparse",
            sparse=True,
        )
    except Exception:
        # Index creation is best-effort — a missing index just makes the
        # tile query a tiny bit slower, not broken.
        pass


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)

    query = (args.query or "").strip()
    if len(query) > 500:
        query = query[:497] + "..."

    cited_url = args.cited_url.strip()
    if not cited_url.startswith(("http://", "https://")):
        print(f"error: --cited-url must include scheme: {cited_url!r}", file=sys.stderr)
        return 1

    evidence_url = args.evidence_url.strip() or None
    if evidence_url and not evidence_url.startswith(("http://", "https://")):
        print(f"error: --evidence-url must include scheme: {evidence_url!r}", file=sys.stderr)
        return 1

    telemetry_id = args.telemetry_id.strip() or None

    try:
        from shared import mongo_tools
        coll = mongo_tools.db()["aeo_citations"]
    except Exception as e:
        print(f"error: could not connect to Mongo: {e}", file=sys.stderr)
        return 1

    _ensure_indexes(coll)

    row = {
        "ts":           datetime.now(UTC),
        "platform":     args.platform,
        "query":        query,
        "cited_url":    cited_url,
        "evidence_url": evidence_url,
        "telemetry_id": telemetry_id,
        "added_by":     args.added_by,
    }
    try:
        result = coll.insert_one(row)
    except Exception as e:
        print(f"error: insert failed: {e}", file=sys.stderr)
        return 1

    print(f"logged citation: _id={result.inserted_id} platform={args.platform} url={cited_url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
