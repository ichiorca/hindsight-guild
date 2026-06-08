"""Live poller smoke test (PRD-02) — hits the REAL HN / Reddit / RSS endpoints.

Unlike scripts/signals/test_adapters.py (which stubs the network), this proves
the pollers actually work against live sources, and that signal_watcher.run_once
writes scored signals + the customer_voice mirror into a local Mongo.

Opt-in (network-dependent; HN/Reddit can rate-limit) — auto-skips without the
flag so it never runs in CI:

    docker compose up -d mongo
    LIVE_SIGNAL_TEST=1 python -m pytest tests/integration/test_signal_pollers_live.py -q -s

The end-to-end test uses a throwaway ``hindsight_guild_polltest`` DB and cleans
up after itself.
"""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("LIVE_SIGNAL_TEST") != "1",
    reason="Set LIVE_SIGNAL_TEST=1 to hit live HN/Reddit/RSS endpoints.",
)

# The agentic-commerce HN query (matches the seeded hn-agentic-commerce source).
HN_QUERY = ('"agentic commerce" OR "agent checkout" OR "instant checkout" '
            'OR "ChatGPT shop" OR "agent-ready" OR ACP OR UCP')

# Broader on-topic query for the end-to-end test so HN reliably returns recent
# items (proves the full poll -> score -> write chain). The narrow production
# query above can legitimately be empty at any given minute.
HN_QUERY_BROAD = '"AI agent" OR "agentic commerce" OR MCP OR "AI shopping"'


def _assert_event_shape(events: list[dict]) -> None:
    for e in events:
        assert e.get("source"), f"missing source: {e}"
        url = e.get("evidence_url")
        assert isinstance(url, str) and url.startswith("http"), f"bad url: {url!r}"
        assert "evidence_excerpt" in e
        assert isinstance(e.get("raw"), dict)


def test_hn_adapter_live():
    """HN Algolia is free + unauthenticated + reliable — the strongest signal
    that the poller works."""
    from scripts.signals import hn_adapter

    src = {"name": "hn-live", "config": {"query": HN_QUERY, "min_points": 0},
           "cursor": None}
    events = hn_adapter.poll(src)
    assert isinstance(events, list)
    print(f"\n  [HN] {len(events)} live events for the agentic-commerce query")
    _assert_event_shape(events)
    if events:
        score = hn_adapter.base_score(events[0]["raw"])
        assert 0.0 <= score <= 1.0
        assert src["cursor"] is not None  # cursor advanced on a successful poll
        print(f"  [HN] sample: {events[0]['evidence_excerpt'][:90]!r} "
              f"base_score={score}")
    else:
        print("  [HN] 0 results right now (query narrow this moment) -- "
              "adapter still returned a clean empty list")


def test_reddit_adapter_live():
    """Reddit's unauth JSON can 429/403; the adapter returns [] (never raises)
    in that case, so we skip rather than fail when it's blocked."""
    from scripts.signals import reddit_adapter

    src = {"name": "reddit-live",
           "config": {"subreddit": "ecommerce", "min_upvotes": 0},
           "cursor": None}
    events = reddit_adapter.poll(src)
    assert isinstance(events, list)
    if not events:
        pytest.skip("Reddit returned no events (likely rate-limited/blocked for "
                    "unauthenticated reads). Adapter handled it gracefully.")
    print(f"\n  [Reddit] {len(events)} live events from r/ecommerce")
    _assert_event_shape(events)
    assert 0.0 <= reddit_adapter.base_score(events[0]["raw"]) <= 1.0


def test_rss_adapter_live():
    from scripts.signals import rss_adapter

    src = {"name": "rss-live",
           "config": {"feed_url": ("https://news.google.com/rss/search?q="
                                   "%22agentic+commerce%22&hl=en-US&gl=US&ceid=US:en")},
           "cursor": None}
    events = rss_adapter.poll(src)
    assert isinstance(events, list)
    if not events:
        pytest.skip("RSS feed returned no items (feed down/changed). "
                    "Adapter handled it gracefully.")
    print(f"\n  [RSS] {len(events)} live items from the feed")
    _assert_event_shape(events)
    assert rss_adapter.base_score(events[0]["raw"]) == 0.5  # RSS is flat 0.5


def test_watcher_end_to_end_live():
    """The real thing: an enabled HN source → signal_watcher.run_once polls HN
    live → scored signals + a customer_voice mirror land in local Mongo."""
    from pymongo import ASCENDING, MongoClient

    uri = os.environ.get("MONGO_URI_DIRECT") or "mongodb://localhost:27017"
    client = MongoClient(uri, serverSelectionTimeoutMS=1000)
    try:
        client.admin.command("ping")
    except Exception:
        pytest.skip("local MongoDB not reachable (docker compose up -d mongo)")

    db = client["hindsight_guild_polltest"]

    def _clean():
        for c in ("signals", "signal_sources", "customer_voice"):
            db[c].delete_many({})

    _clean()
    db["signals"].create_index([("evidence_url", ASCENDING)], unique=True,
                               sparse=True, name="idx_evidence_url_unique")
    db["signal_sources"].insert_one({
        "name": "hn-live-e2e", "source": "hn", "enabled": True,
        "config": {"query": HN_QUERY_BROAD, "min_points": 0},
        "icp_segment": "seg_merchant_dtc",
        "score_floor": 0.0,   # floor 0 → everything lands + mirrors
        "cursor": None, "poll_interval_sec": 1800,
        "topic_hint_template": "Make your store agent-ready: stop {pain}",
        "default_channel": "blog",
    })

    try:
        os.environ.pop("SIGNAL_WATCHER_DISABLED", None)
        from agents import signal_watcher
        out = signal_watcher.run_once(db=db)
        print(f"\n  [watcher] run_once -> {out}")
        assert out["status"] == "ok"

        n = db["signals"].count_documents({})
        print(f"  [watcher] {n} signals written to Mongo")
        if n == 0:
            pytest.skip("HN returned 0 results for the query at this moment; "
                        "watcher ran cleanly but had nothing to write.")

        sig = db["signals"].find_one({})
        assert 0.0 <= sig["score"] <= 1.0
        assert sig["icp_segment"] == "seg_merchant_dtc"
        assert sig["evidence_url"].startswith("http")
        # floor 0 → above-floor → mirrored into customer_voice as community_signal
        mirrored = db["customer_voice"].count_documents(
            {"source_kind": "community_signal"})
        assert mirrored >= 1
        print(f"  [watcher] customer_voice mirror rows: {mirrored} | "
              f"sample score={sig['score']} hits={sig.get('icp_keywords_hit')}")
    finally:
        _clean()
        client.close()
