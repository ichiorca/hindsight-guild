"""Tier 2 — signal_router.run_once against a real local Mongo.

Stubs only the network edge (``httpx.post`` to /api/draft); the decision logic
— rate limits (5/tick, 20/24h/ICP), 7-day duplicate suppression, channel +
topic-hint selection, processed-marking, kill switches — runs against actual
Mongo.

Auto-skips when no local Mongo is reachable (see tests/unit/conftest.py).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from bson import ObjectId

from agents import signal_router

_POST = "agents.signal_router.httpx.post"


@pytest.fixture(autouse=True)
def _clear_kill_switches(monkeypatch):
    monkeypatch.delenv("SIGNAL_AUTO_DRAFT", raising=False)
    monkeypatch.delenv("SIGNAL_ROUTER_DISABLED", raising=False)
    monkeypatch.delenv("SIGNAL_MAX_PER_TICK", raising=False)


class _FakeResp:
    def __init__(self, job_id="j1"):
        self._job_id = job_id

    def raise_for_status(self):
        return None

    def json(self):
        return {"job_id": self._job_id}


def _fake_post(captured):
    """Return an httpx.post stub that records each call's json payload."""
    def _post(url, json=None, timeout=None):  # noqa: A002 - mirror httpx kwarg
        captured.append({"url": url, "json": json})
        return _FakeResp()
    return _post


def _seed_signal(db, score=0.9, icp="seg_merchant_dtc", source="hn",
                 excerpt="How do I make my store agent-ready for ChatGPT?", **over):
    row = {
        "source": source, "ts": datetime.now(UTC),
        "evidence_url": f"https://x/{ObjectId()}", "evidence_excerpt": excerpt,
        "icp_segment": icp, "score": score,
        "processed": False, "suppressed_reason": None, "raw": {},
    }
    row.update(over)
    return db.signals.insert_one(row).inserted_id


def _seed_triggered_action(db, icp, signal_id=None, ts=None):
    db.actions.insert_one({
        "ts": ts or datetime.now(UTC),
        "icp_segment": icp,
        "triggered_by_signal_id": signal_id or ObjectId(),
    })


# ---------------------------------------------------------------------------
# Status short-circuits
# ---------------------------------------------------------------------------

def test_auto_draft_kill_switch(signal_db, monkeypatch):
    monkeypatch.setenv("SIGNAL_AUTO_DRAFT", "0")
    _seed_signal(signal_db)
    out = signal_router.run_once(db=signal_db, api_url="http://t")
    assert out["status"] == "disabled"


def test_router_disabled_kill_switch(signal_db, monkeypatch):
    monkeypatch.setenv("SIGNAL_ROUTER_DISABLED", "1")
    _seed_signal(signal_db)
    out = signal_router.run_once(db=signal_db, api_url="http://t")
    assert out["status"] == "disabled"


def test_no_pending_signals(signal_db):
    out = signal_router.run_once(db=signal_db, api_url="http://t")
    assert out["status"] == "no_pending"


# ---------------------------------------------------------------------------
# Happy path — enqueue + mark processed
# ---------------------------------------------------------------------------

def test_enqueues_and_marks_processed(signal_db):
    sid = _seed_signal(signal_db)
    captured: list[dict] = []
    with patch(_POST, side_effect=_fake_post(captured)):
        out = signal_router.run_once(db=signal_db, api_url="http://t")

    assert out["total_enqueued"] == 1
    # Payload carries the back-ref + a derived channel (hn question → blog).
    assert len(captured) == 1
    payload = captured[0]["json"]
    assert payload["triggered_by_signal_id"] == str(sid)
    assert payload["channel"] == "blog"
    assert payload["icp_segment"] == "seg_merchant_dtc"

    sig = signal_db.signals.find_one({"_id": sid})
    assert sig["processed"] is True
    assert sig["router_job_id"] == "j1"


def test_channel_and_topic_from_source_doc(signal_db):
    signal_db.signal_sources.insert_one({
        "name": "hn-merchant", "source": "hn", "icp_segment": "seg_merchant_dtc",
        "default_channel": "substack",
        "topic_hint_template": "Make your store agent-ready: stop {pain}",
    })
    _seed_signal(signal_db, source="hn", icp="seg_merchant_dtc",
                 excerpt="we keep losing sales to agent checkout")
    captured: list[dict] = []
    with patch(_POST, side_effect=_fake_post(captured)):
        signal_router.run_once(db=signal_db, api_url="http://t")

    payload = captured[0]["json"]
    assert payload["channel"] == "substack"            # source default wins
    assert payload["topic_hint"].startswith("Make your store agent-ready: stop sales")


# ---------------------------------------------------------------------------
# Rate limits (PRD-02 §8)
# ---------------------------------------------------------------------------

def test_max_per_tick_caps_enqueues(signal_db, monkeypatch):
    monkeypatch.setenv("SIGNAL_MAX_PER_TICK", "3")
    for _ in range(5):
        _seed_signal(signal_db)
    captured: list[dict] = []
    with patch(_POST, side_effect=_fake_post(captured)):
        out = signal_router.run_once(db=signal_db, api_url="http://t")

    assert out["total_enqueued"] == 3       # capped at SIGNAL_MAX_PER_TICK
    assert len(captured) == 3
    assert signal_db.signals.count_documents({"processed": False}) == 2


def test_max_per_tick_default_is_three(signal_db):
    for _ in range(5):
        _seed_signal(signal_db)
    captured: list[dict] = []
    with patch(_POST, side_effect=_fake_post(captured)):
        out = signal_router.run_once(db=signal_db, api_url="http://t")

    assert out["total_enqueued"] == 3       # default cap when env unset


def test_round_robin_spans_channels(signal_db):
    """Selection round-robins across channels so one tick spans draft types,
    even when a single channel holds all the highest scores."""
    signal_db.signal_sources.insert_many([
        {"name": "src-li", "source": "rss", "icp_segment": "seg_merchant_dtc",
         "default_channel": "linkedin"},
        {"name": "src-bl", "source": "hn", "icp_segment": "seg_ecom_leader",
         "default_channel": "blog"},
    ])
    # 3 high-score linkedin signals + 1 lower-score blog signal; cap = 3.
    for _ in range(3):
        _seed_signal(signal_db, source="rss", icp="seg_merchant_dtc", score=0.9)
    _seed_signal(signal_db, source="hn", icp="seg_ecom_leader", score=0.6)

    captured: list[dict] = []
    with patch(_POST, side_effect=_fake_post(captured)):
        out = signal_router.run_once(db=signal_db, api_url="http://t")

    assert out["total_enqueued"] == 3
    channels = {c["json"]["channel"] for c in captured}
    # blog (0.6) gets in despite a lower score than the 3rd linkedin (0.9)
    assert channels == {"linkedin", "blog"}


def test_per_icp_24h_quota_blocks(signal_db):
    icp = "seg_merchant_dtc"
    for _ in range(20):  # 20 already-triggered actions in the last 24h for this ICP
        _seed_triggered_action(signal_db, icp)
    sid = _seed_signal(signal_db, icp=icp)
    captured: list[dict] = []
    with patch(_POST, side_effect=_fake_post(captured)):
        out = signal_router.run_once(db=signal_db, api_url="http://t")

    assert out["total_enqueued"] == 0
    assert captured == []  # no draft enqueued
    sig = signal_db.signals.find_one({"_id": sid})
    assert sig["suppressed_reason"] == "rate_limit"
    assert sig["processed"] is True
    assert any("24h_quota_full" in s["reason"] for s in out["suppressed"])


def test_duplicate_within_7d_suppressed(signal_db):
    sid = _seed_signal(signal_db)
    # A prior action already references this exact signal within the window.
    _seed_triggered_action(signal_db, icp="seg_merchant_dtc", signal_id=sid)
    captured: list[dict] = []
    with patch(_POST, side_effect=_fake_post(captured)):
        out = signal_router.run_once(db=signal_db, api_url="http://t")

    assert out["total_enqueued"] == 0
    assert captured == []
    sig = signal_db.signals.find_one({"_id": sid})
    assert sig["suppressed_reason"] == "duplicate_url"
    assert any(s["reason"] == "duplicate_within_7d" for s in out["suppressed"])


def test_old_triggered_action_does_not_suppress(signal_db):
    sid = _seed_signal(signal_db)
    # Same back-ref but 8 days ago → outside the 7-day suppression window.
    _seed_triggered_action(signal_db, icp="seg_merchant_dtc", signal_id=sid,
                           ts=datetime.now(UTC) - timedelta(days=8))
    captured: list[dict] = []
    with patch(_POST, side_effect=_fake_post(captured)):
        out = signal_router.run_once(db=signal_db, api_url="http://t")

    assert out["total_enqueued"] == 1  # the stale action doesn't block


# ---------------------------------------------------------------------------
# Enqueue failure
# ---------------------------------------------------------------------------

def test_enqueue_failure_keeps_signal_unprocessed(signal_db):
    sid = _seed_signal(signal_db)
    with patch(_POST, side_effect=RuntimeError("boom")):
        out = signal_router.run_once(db=signal_db, api_url="http://t")

    assert out["total_enqueued"] == 0
    assert any("enqueue_failed" in s["reason"] for s in out["suppressed"])
    sig = signal_db.signals.find_one({"_id": sid})
    assert sig["processed"] is False  # left for the next tick to retry
