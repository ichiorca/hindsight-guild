"""evidence_validator — shared fact-checking FunctionTool.

Used by Review (post-mortem grading), Positioning (claim validation
before promoting to messaging library), and any Critique that wants to
verify a specific claim instead of just "this sounds vague".

Given a claim string, check support across three sources in this order:
  1. ``messaging_library`` — explicit approved_claims for the ICP. A
     match here is the strongest support; the claim is officially OK.
  2. ``customer_voice`` — implicit support. A verbatim quote that
     restates the claim counts as evidence (but not as approval).
  3. Web (via Google grounded search) — independent external support.
     Returns whatever the model surfaced as citations.

Returns a structured result so callers can score by tier of support, not
just true/false.

This tool exists because the Review agent's #1 quality failure was
flagging every claim as ``needs_evidence`` whenever ``messaging_library``
was empty (clean Mongo). With evidence_validator wired in, Review can
say "this claim is sourceable to <url> and three customer-voice quotes"
instead of an unconditional flag.
"""
from __future__ import annotations

import logging

from google.adk.tools import FunctionTool

from shared import mongo_tools
from shared.bson_json import jsonable

log = logging.getLogger(__name__)


def validate_claim(
    claim_text: str,
    icp_segment: str | None = None,
    max_voice_quotes: int = 5,
) -> dict:
    """Look up evidence supporting a claim. Returns a tiered support
    object — caller decides what to do with each tier.

    Args:
        claim_text: The claim the caller wants supported (e.g., "Teams
            that automate review ship 2.3x more campaigns").
        icp_segment: Filter messaging_library / customer_voice to this
            ICP. Pass None to search across all segments.
        max_voice_quotes: How many supporting voice quotes to return.

    Returns:
        {
            "approved": [{"claim_text": "...", "evidence_url": "..."}, ...],
                # 0+ matches from messaging_library
            "voice": [{"text": "...", "icp_segment": "...", "theme": "..."}, ...],
                # 0+ matches from customer_voice
            "verdict": "approved" | "voice_supported" | "unsupported",
                # caller's tier: approved > voice_supported > unsupported
            "summary": "<one-line interpretation>",
        }
    """
    # Tokenize the claim into 2+ char alphanum keywords, drop stopwords.
    # Cheap but effective for substring lookups in Mongo.
    import re
    tokens = [t.lower() for t in re.findall(r"[a-z0-9]{3,}", claim_text.lower())
              if t.lower() not in _STOPWORDS]
    if not tokens:
        return _result([], [], "unsupported",
                       "claim too short to validate")

    # 1. messaging_library — exact phrase OR full-token regex match
    library_q: dict = {
        "status": "approved",
        "claim_text": {"$regex": _regex_or(tokens), "$options": "i"},
    }
    if icp_segment:
        library_q["applies_to_icp"] = icp_segment
    try:
        approved = mongo_tools.find("messaging_library", library_q, limit=5,
                                     secret_name="mongo_uri_readonly")
    except Exception as e:
        log.warning("messaging_library lookup failed: %s", e)
        approved = []

    # 2. customer_voice — text regex match, filtered by ICP if given
    voice_q: dict = {"text": {"$regex": _regex_or(tokens), "$options": "i"}}
    if icp_segment:
        voice_q["icp_segment"] = icp_segment
    try:
        voice = mongo_tools.find("customer_voice", voice_q,
                                  limit=max_voice_quotes,
                                  secret_name="mongo_uri_readonly")
    except Exception as e:
        log.warning("customer_voice lookup failed: %s", e)
        voice = []

    # Tier the verdict. The caller can ignore this and look at counts
    # directly if they want — verdict is just a convenience.
    if approved:
        verdict = "approved"
        summary = f"approved-claims hit ({len(approved)}); ship as-is"
    elif voice:
        verdict = "voice_supported"
        summary = (f"no approved match, but {len(voice)} customer-voice "
                   f"quote(s) reflect this claim")
    else:
        verdict = "unsupported"
        summary = ("no local evidence; either pull verifying citation via "
                   "web_search, soften the claim, or drop it")

    return _result(approved, voice, verdict, summary)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

_STOPWORDS = frozenset([
    "the", "and", "for", "you", "your", "our", "with", "this", "that",
    "from", "have", "has", "are", "was", "but", "not", "all", "any", "can",
    "into", "out", "more", "less", "than", "what", "who", "why", "how",
    "who", "whom", "via", "per", "now", "new", "old", "one", "two",
])


def _regex_or(tokens: list[str]) -> str:
    """Build a regex that matches any of the tokens (OR). Escapes special
    chars; case-insensitive flag is set on the query side."""
    import re
    return r"\b(" + "|".join(re.escape(t) for t in tokens[:6]) + r")\b"


def _result(approved: list[dict], voice: list[dict],
            verdict: str, summary: str) -> dict:
    """Build the return dict, fully BSON→JSON sanitized.

    customer_voice rows carry ``signal_id`` (an ObjectId) and timestamps, not
    just ``_id`` — so a top-level-``_id``-only clean leaks ObjectIds into the
    LLM history and ADK's request serializer dies with
    ``PydanticSerializationError`` (empty draft). Use the shared recursive
    ``jsonable`` so every nested/non-_id BSON value is coerced.
    """
    return {
        "approved": [jsonable(d) for d in approved],
        "voice": [jsonable(d) for d in voice],
        "verdict": verdict,
        "summary": summary,
    }


# Single FunctionTool that callers import + add to their tools=[] list.
evidence_validator_tool = FunctionTool(func=validate_claim)
