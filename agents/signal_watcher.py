"""PRD-02 signal_watcher_agent.

Polls all enabled ``signal_sources`` on a schedule (every 30 min by
default; see live.py). For each new event:

  1. Dedupe by ``evidence_url`` against existing ``signals`` rows
     (unique sparse index enforces this at the DB level too).
  2. Apply ICP regex matching from the source's ``icp_segment`` config
     to derive an ICP-fit boost.
  3. Compute the final score: ``base_score(raw) * icp_fit_boost``
     (multiplicatively, capped at 1.0).
  4. Insert into ``signals``.
  5. Mirror into ``customer_voice`` with ``source_kind: community_signal``
     so the existing Customer Voice Agent can surface public-discussion
     pain alongside sales-call quotes. (Per PRD-02 §3 in scope; the
     spec's Customer Voice Agent already lists community posts as an
     input — we're feeding it.)

The watcher is NOT an ADK LlmAgent — it's a pure-Python schedulable
job. Called by Cloud Scheduler in production; in MVP 1 you trigger
it via ``POST /api/signals/poll-now`` or by calling ``run_once()``
directly from a Python shell.

Kill switch: env var ``SIGNAL_WATCHER_DISABLED=1`` makes ``run_once``
return ``{status: "disabled"}`` immediately. Useful for muting all
ingestion during a paid-experiment window.
"""
from __future__ import annotations

import importlib
import logging
import os
from datetime import UTC, datetime

from pymongo.errors import DuplicateKeyError

from shared import mongo_tools

log = logging.getLogger(__name__)

# Adapter modules are looked up by ``signal_sources.source`` value. Keep
# the mapping explicit so a typo'd ``source: "redit"`` row fails loud
# instead of trying to dynamic-import a nonexistent module.
_ADAPTER_MODULES = {
    "hn":     "scripts.signals.hn_adapter",
    "reddit": "scripts.signals.reddit_adapter",
    "rss":    "scripts.signals.rss_adapter",
}


# ---------------------------------------------------------------------------
# ICP regex boost — per PRD-02 §6 the multipliers stack multiplicatively
# and the final score is capped at 1.0. The patterns here are seeded for
# the founder's known ICPs; PRD-03's signal_miner will propose additions
# from accepted signal-drafts over time.
# ---------------------------------------------------------------------------

# Agentic-commerce ICP segments. signalCommerce sells the testing/trust
# (agent-readiness + protocol-conformance) layer, so buying signals cluster
# around merchants going agent-ready, agent-checkout breakage, and the
# protocols/surfaces that mediate agent transactions. Merchants are the
# primary ICP. These keys are signal-layer-only (see ICP_LABELS in web for
# display names); the legacy app taxonomy is unchanged.
_ICP_KEYWORDS = {
    # PRIMARY — DTC / e-commerce merchants that must become "agent-ready".
    "seg_merchant_dtc": (
        "agentic commerce", "agent-ready", "ai shopping", "chatgpt shop",
        "instant checkout", "shopify", "magento", "dtc", "d2c",
        "product feed", "agent checkout", "sell on chatgpt", "gemini shopping",
    ),
    # E-commerce / digital / growth leaders at brands + retailers.
    "seg_ecom_leader": (
        "agentic commerce", "conversational commerce", "agent checkout",
        "ai agent traffic", "conversion rate", "aov", "checkout flow",
        "headless commerce", "product catalog", "omnichannel", "retail media",
    ),
    # Payment providers, PSPs, card networks / issuers enabling agent payments.
    "seg_payments_network": (
        "agent payments", "agentic checkout", "ap2", "acp", "ucp", "x402",
        "tokenization", "payment mandate", "verifiable credential",
        "agent authentication", "visa intelligent commerce",
        "mastercard agent pay", "issuer", "chargeback",
    ),
    # Agentic buyer platforms + teams building shopping/commerce agents.
    "seg_agent_platform": (
        "shopping agent", "mcp", "model context protocol", "a2a", "webmcp",
        "ucp", "acp", "checkout api", "tool calling", "google ai mode",
        "copilot checkout", "agent framework", "build an agent",
    ),
}

# Pain — signalCommerce's thesis: when agent protocols break there is no
# fallback, just lost revenue. Apostrophe-free fragments so they match
# "fails"/"failed"/"failure" etc. without entity-decode surprises.
_PAIN_KEYWORDS = (
    "protocol break", "conformance gap", "not agent-ready", "broken checkout",
    "checkout fail", "failed checkout", "lost revenue", "cart abandonment",
    "abandoned cart", "checkout error", "hallucinated price", "out of stock",
    "invisible to ai", "integration broke", "stopped working", "lost sales",
)

_SOLUTION_INTENT_KEYWORDS = (
    "agent-ready", "test agent", "validate agent", "readiness score",
    "protocol conformance", "agent checkout testing", "how to sell on chatgpt",
    "observability", "looking for", "alternative to", "best way to",
    "anyone using",
)

_NEGATIVE_PATTERN_KEYWORDS = (
    "switching from", "stopped using", "doesnt support agents",
    "does not support agents", "doesnt work", "broke after", "avoid",
    "deprecated", "migrating off",
)

# Protocol / surface signal — revives PRD-02 §6's omitted "competitor" slot
# (x1.2) as an on-topic booster for the agentic-commerce protocols + buying
# surfaces. A post mentioning any of these is almost certainly in-domain.
_PLATFORM_KEYWORDS = (
    "ucp", "acp", "mcp", "a2a", "webmcp", "ap2", "x402",
    "chatgpt shop", "instant checkout", "copilot checkout", "google ai mode",
    "gemini shopping", "perplexity", "rufus", "visa intelligent commerce",
    "mastercard agent pay",
)


def _icp_fit_boost(text: str, icp_segment: str | None) -> float:
    """Compute the multiplicative ICP-fit boost for an event's text.

    Multipliers (PRD-02 §6, tuned for agentic commerce):
      * 1.4 for primary ICP keyword match (per icp_segment)
      * 1.3 for pain keyword (agent-checkout / conformance breakage)
      * 1.5 for solution-intent keyword
      * 1.6 for negative-pattern keyword (switching / dissatisfaction)
      * 1.2 for protocol/surface keyword (UCP/ACP/MCP/AP2/ChatGPT Shop/…)

    Hits stack multiplicatively; the caller caps base*boost at 1.0.
    Returns 1.0 (neutral) when no keywords match.
    """
    if not text:
        return 1.0
    lower = text.lower()
    boost = 1.0
    keywords = _ICP_KEYWORDS.get(icp_segment or "", ())
    if keywords and any(kw in lower for kw in keywords):
        boost *= 1.4
    if any(kw in lower for kw in _PAIN_KEYWORDS):
        boost *= 1.3
    if any(kw in lower for kw in _SOLUTION_INTENT_KEYWORDS):
        boost *= 1.5
    if any(kw in lower for kw in _NEGATIVE_PATTERN_KEYWORDS):
        boost *= 1.6
    if any(kw in lower for kw in _PLATFORM_KEYWORDS):
        boost *= 1.2
    return boost


def _icp_keywords_hit(text: str, icp_segment: str | None) -> list[str]:
    """Return the specific keywords that matched (for the signals row's
    ``icp_keywords_hit`` field — useful for /signals dashboard debugging)."""
    if not text or not icp_segment:
        return []
    lower = text.lower()
    return [kw for kw in _ICP_KEYWORDS.get(icp_segment, ()) if kw in lower]


# ---------------------------------------------------------------------------
# Customer-voice mirror — per the alignment doc, every signal also lands
# in customer_voice with source_kind="community_signal" so the existing
# Customer Voice Agent gets fed.
# ---------------------------------------------------------------------------

def _customer_voice_row(event: dict, source_doc: dict, signal_id) -> dict:
    """Shape a customer_voice row from a signal event. The Customer
    Voice Agent's existing fields (text, source, persona, theme,
    sentiment, icp_segment) all apply; we add the two new fields
    (source_kind, signal_id) introduced in PRD-02 §5."""
    return {
        "text":         event.get("evidence_excerpt") or "",
        "source":       event.get("evidence_url") or "",
        "persona":      None,            # community signals are anonymous-ish
        "theme":        source_doc.get("name"),   # the source name = theme tag
        "sentiment":    None,            # not classified at watch time
        "icp_segment":  source_doc.get("icp_segment"),
        "source_kind":  "community_signal",
        "signal_id":    signal_id,
        "ts":           datetime.now(UTC),
    }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_once(db=None) -> dict:
    """Single watcher tick. Returns a dict summarizing what happened
    — used by /api/signals/poll-now and the test suite.

    Shape::

        {"status": "ok" | "disabled" | "no_sources",
         "sources": [
           {"name": str, "events_seen": int, "events_new": int,
            "errors": list[str]}
         ],
         "total_new": int}
    """
    if os.environ.get("SIGNAL_WATCHER_DISABLED", "").lower() in ("1", "true", "yes"):
        return {"status": "disabled", "sources": [], "total_new": 0}

    # pymongo Database objects don't support bool() — explicit None check.
    db = db if db is not None else mongo_tools.db()
    enabled = list(db["signal_sources"].find({"enabled": True}))
    if not enabled:
        return {"status": "no_sources", "sources": [], "total_new": 0}

    per_source: list[dict] = []
    total_new = 0
    for source_doc in enabled:
        result = _process_source(db, source_doc)
        per_source.append(result)
        total_new += result["events_new"]

    return {"status": "ok", "sources": per_source, "total_new": total_new}


def _process_source(db, source_doc: dict) -> dict:
    """Poll one source, write new signals, mirror into customer_voice.
    Returns the per-source result row for run_once's summary."""
    name = source_doc.get("name") or "<unnamed>"
    src_kind = source_doc.get("source") or ""
    result = {"name": name, "events_seen": 0, "events_new": 0, "errors": []}

    module_path = _ADAPTER_MODULES.get(src_kind)
    if not module_path:
        result["errors"].append(f"unknown source kind: {src_kind!r}")
        return result

    try:
        adapter = importlib.import_module(module_path)
    except Exception as e:
        result["errors"].append(f"adapter import failed: {e}")
        return result

    try:
        events = adapter.poll(source_doc, db)
    except Exception as e:
        result["errors"].append(f"poll failed: {e}")
        return result

    result["events_seen"] = len(events)
    score_floor = float(source_doc.get("score_floor") or 0.0)
    icp = source_doc.get("icp_segment")

    for ev in events:
        try:
            raw = ev.get("raw") or {}
            base = float(adapter.base_score(raw))
            text_for_match = (ev.get("evidence_excerpt") or "")
            boost = _icp_fit_boost(text_for_match, icp)
            score = round(min(1.0, base * boost), 3)

            row = {
                "source":            ev.get("source"),
                "source_name":       name,
                "ts":                datetime.now(UTC),
                "evidence_url":      ev.get("evidence_url"),
                "evidence_excerpt":  ev.get("evidence_excerpt"),
                "icp_segment":       icp,
                "icp_keywords_hit":  _icp_keywords_hit(text_for_match, icp),
                "score":             score,
                "raw":               raw,
                "processed":         False,
                "processed_at":      None,
                "triggered_telemetry_id": None,
                "suppressed_reason": "below_threshold" if score < score_floor else None,
            }

            try:
                insert_result = db["signals"].insert_one(row)
            except DuplicateKeyError:
                # Same evidence_url already in signals; the unique sparse
                # index dropped it. Not an error — drives the dedupe story.
                continue

            result["events_new"] += 1

            # Mirror into customer_voice. Only when above floor — junk
            # signals shouldn't pollute the Customer Voice Agent's reads.
            if score >= score_floor:
                try:
                    db["customer_voice"].insert_one(
                        _customer_voice_row(ev, source_doc, insert_result.inserted_id)
                    )
                except Exception as e:
                    # Mirror failure is non-fatal: the signals row already landed.
                    log.warning("customer_voice mirror failed for %s: %s",
                                ev.get("evidence_url"), e)
        except Exception as e:
            result["errors"].append(f"event processing failed: {e}")

    # Persist cursor + last_polled_at. The adapter updated source_doc.cursor
    # in place via poll(); we write both back here so the watcher's
    # bookkeeping is one Mongo write per source per tick.
    db["signal_sources"].update_one(
        {"_id": source_doc["_id"]},
        {"$set": {
            "cursor":         source_doc.get("cursor"),
            "last_polled_at": datetime.now(UTC),
        }},
    )

    return result
