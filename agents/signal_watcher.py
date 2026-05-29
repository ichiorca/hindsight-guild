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

_ICP_KEYWORDS = {
    "seg_revops_director":  ("revops", "renewal", "csm", "handoff", "nrr"),
    "seg_founder_b2b":      ("founder", "saas", "startup", "go-to-market", "gtm"),
    "seg_pmm_growth":       ("positioning", "pmm", "launch", "messaging", "product marketing"),
    "seg_ae_growth":        ("sales", "ae", "quota", "pipeline", "outbound"),
    "seg_saas_founder":     ("founder", "saas", "indie", "bootstrap"),
}

_PAIN_KEYWORDS = (
    "broken", "slipping", "leaking", "drift", "drop", "missed", "stalled",
    "frustration", "headache", "pain", "struggling",
)

_SOLUTION_INTENT_KEYWORDS = (
    "looking for", "recommend", "alternative to", "tool", "anyone using",
    "what do you use", "best way",
)

_NEGATIVE_PATTERN_KEYWORDS = (
    "don't recommend", "avoid", "switching from", "stopped using",
    "doesn't work", "disappointed",
)


def _icp_fit_boost(text: str, icp_segment: str | None) -> float:
    """Compute the multiplicative ICP-fit boost for an event's text.

    Multipliers (from PRD-02 §6):
      * 1.4 for primary ICP keyword match
      * 1.3 for pain keyword
      * 1.5 for solution-intent keyword
      * 1.6 for negative-pattern keyword

    Competitor name is omitted in MVP 1 — we don't have a seeded
    competitor list yet; PRD-03's signal_miner can propose one.

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
