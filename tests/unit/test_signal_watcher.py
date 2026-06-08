"""Tier 2 — signal_watcher.run_once against a real local Mongo.

Stubs only the network edge (the HN adapter's poll/base_score); everything
else — scoring, capping, dedupe via the unique sparse index, the score-floor
gate on the customer_voice mirror, cursor persistence, per-source failure
isolation — runs against actual Mongo so the behaviour matches production.

Auto-skips when no local Mongo is reachable (see tests/unit/conftest.py).
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from agents import signal_watcher

_HN = "scripts.signals.hn_adapter"


@pytest.fixture(autouse=True)
def _clear_kill_switch(monkeypatch):
    monkeypatch.delenv("SIGNAL_WATCHER_DISABLED", raising=False)


def _seed_source(db, **over):
    doc = {
        "name": "hn-x", "source": "hn", "enabled": True,
        "icp_segment": "seg_merchant_dtc", "score_floor": 0.5,
        "cursor": None, "config": {"query": "x"},
    }
    doc.update(over)
    db.signal_sources.insert_one(doc)
    return doc


def _event(url="https://news.ycombinator.com/item?id=1",
           excerpt="agentic commerce: instant checkout on shopify"):
    return {"source": "hn", "evidence_url": url, "evidence_excerpt": excerpt,
            "created_at_i": 1, "raw": {"num_comments": 20, "points": 30}}


# ---------------------------------------------------------------------------
# Status short-circuits
# ---------------------------------------------------------------------------

def test_kill_switch_disables(signal_db, monkeypatch):
    monkeypatch.setenv("SIGNAL_WATCHER_DISABLED", "1")
    _seed_source(signal_db)
    out = signal_watcher.run_once(db=signal_db)
    assert out["status"] == "disabled"
    assert out["total_new"] == 0
    assert signal_db.signals.count_documents({}) == 0


def test_no_enabled_sources(signal_db):
    _seed_source(signal_db, enabled=False)
    out = signal_watcher.run_once(db=signal_db)
    assert out["status"] == "no_sources"


# ---------------------------------------------------------------------------
# Scoring + insert
# ---------------------------------------------------------------------------

def test_score_is_base_times_boost_capped_at_one(signal_db):
    _seed_source(signal_db, score_floor=0.5)
    with patch(f"{_HN}.poll", return_value=[_event()]), \
         patch(f"{_HN}.base_score", return_value=1.0):
        out = signal_watcher.run_once(db=signal_db)

    assert out["status"] == "ok"
    assert out["total_new"] == 1
    sig = signal_db.signals.find_one({"evidence_url": "https://news.ycombinator.com/item?id=1"})
    # base 1.0 × boost(merchant ICP × protocol-surface > 1) → capped at 1.0
    assert sig["score"] == 1.0
    assert sig["processed"] is False
    assert sig["suppressed_reason"] is None
    assert sig["icp_keywords_hit"] == ["agentic commerce", "instant checkout", "shopify"]
    assert sig["source_name"] == "hn-x"   # watcher stamps the source name for routing


def test_dedupe_by_evidence_url(signal_db):
    _seed_source(signal_db)
    with patch(f"{_HN}.poll", return_value=[_event()]), \
         patch(f"{_HN}.base_score", return_value=1.0):
        first = signal_watcher.run_once(db=signal_db)
        second = signal_watcher.run_once(db=signal_db)  # same evidence_url

    assert first["total_new"] == 1
    assert second["total_new"] == 0  # unique sparse index dropped the dup
    assert signal_db.signals.count_documents({}) == 1


# ---------------------------------------------------------------------------
# customer_voice mirror is gated on the score floor
# ---------------------------------------------------------------------------

def test_below_floor_marked_and_not_mirrored(signal_db):
    _seed_source(signal_db, score_floor=0.9)
    # no keywords → boost 1.0; base 0.5 → score 0.5 < floor 0.9
    ev = _event(excerpt="a routine weekly status update")
    with patch(f"{_HN}.poll", return_value=[ev]), \
         patch(f"{_HN}.base_score", return_value=0.5):
        out = signal_watcher.run_once(db=signal_db)

    assert out["total_new"] == 1  # still recorded as a signal...
    sig = signal_db.signals.find_one({})
    assert sig["score"] == 0.5
    assert sig["suppressed_reason"] == "below_threshold"
    # ...but NOT mirrored into customer_voice (junk shouldn't pollute reads).
    assert signal_db.customer_voice.count_documents({}) == 0


def test_above_floor_mirrors_into_customer_voice(signal_db):
    _seed_source(signal_db, score_floor=0.5)
    with patch(f"{_HN}.poll", return_value=[_event()]), \
         patch(f"{_HN}.base_score", return_value=1.0):
        signal_watcher.run_once(db=signal_db)

    sig = signal_db.signals.find_one({})
    voice = signal_db.customer_voice.find_one({"signal_id": sig["_id"]})
    assert voice is not None
    assert voice["source_kind"] == "community_signal"
    assert voice["icp_segment"] == "seg_merchant_dtc"


# ---------------------------------------------------------------------------
# Bookkeeping + resilience
# ---------------------------------------------------------------------------

def test_cursor_and_last_polled_persisted(signal_db):
    src = _seed_source(signal_db)

    def _poll(source_doc, db=None):
        source_doc["cursor"] = 999  # adapter advances cursor in place
        return [_event()]

    with patch(f"{_HN}.poll", side_effect=_poll), \
         patch(f"{_HN}.base_score", return_value=1.0):
        signal_watcher.run_once(db=signal_db)

    stored = signal_db.signal_sources.find_one({"_id": src["_id"]})
    assert stored["cursor"] == 999
    assert stored["last_polled_at"] is not None


def test_one_bad_source_does_not_block_others(signal_db):
    _seed_source(signal_db, name="bogus", source="not_a_real_kind")
    _seed_source(signal_db, name="hn-good", source="hn")
    with patch(f"{_HN}.poll", return_value=[_event()]), \
         patch(f"{_HN}.base_score", return_value=1.0):
        out = signal_watcher.run_once(db=signal_db)

    assert out["status"] == "ok"
    assert out["total_new"] == 1  # the good source still produced a signal
    bogus = next(s for s in out["sources"] if s["name"] == "bogus")
    assert bogus["errors"]  # the unknown adapter kind was reported, not raised
