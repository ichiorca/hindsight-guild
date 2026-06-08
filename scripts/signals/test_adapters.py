"""Adapter unit tests — no network. We stub httpx with a fake response.

Coverage per PRD-02 §12 M2:
  - poll() returns correctly-shaped event dicts
  - cursor advances on success
  - dedupe by cursor: events older than cursor get filtered
  - base_score formulas match the PRD §6 recipes
  - min-threshold filters drop low-engagement items
"""
from __future__ import annotations

import os
from unittest.mock import patch

from scripts.signals import hn_adapter, reddit_adapter, rss_adapter


class _FakeResp:
    """Minimal httpx.Response stand-in. ``json()`` returns the canned
    payload; ``status_code`` defaults to 200; ``content`` exposes XML
    bytes for the RSS test."""
    def __init__(self, payload=None, status_code=200, content=b""):
        self._payload = payload
        self.status_code = status_code
        self.content = content
        self.text = ""

    def json(self):
        return self._payload


# ---------------------------------------------------------------------------
# HN
# ---------------------------------------------------------------------------

_HN_PAYLOAD = {
    "hits": [
        {
            "objectID": "12345",
            "title": "Ask HN: How do you handle CSM handoff at renewal?",
            "comment_text": None,
            "num_comments": 18,
            "points": 24,
            "created_at_i": 1748390400,
        },
        {
            "objectID": "12346",
            "title": "Another revops thread",
            "num_comments": 3,
            "points": 8,
            "created_at_i": 1748386800,
        },
        {
            "objectID": "12347",
            "title": None,
            "comment_text": "<p>This is a <i>comment</i> body.</p>",
            "num_comments": None,
            "points": None,
            "created_at_i": 1748383200,
            "story_title": "RevOps stories",
        },
    ]
}


def test_hn_poll_normalizes_events():
    source = {"name": "hn-test", "config": {"query": "RevOps"}, "cursor": None}
    with patch.object(hn_adapter.httpx, "get", return_value=_FakeResp(_HN_PAYLOAD)):
        events = hn_adapter.poll(source)
    assert len(events) == 3
    assert all(e["source"] == "hn" for e in events)
    assert events[0]["evidence_url"] == "https://news.ycombinator.com/item?id=12345"
    # cursor advanced to newest created_at_i
    assert source["cursor"] == 1748390400
    # comment-text path: HTML tags stripped
    assert events[2]["evidence_excerpt"] == "This is a comment body."


def test_hn_poll_respects_min_points():
    source = {"name": "hn-test",
              "config": {"query": "RevOps", "min_points": 10},
              "cursor": None}
    with patch.object(hn_adapter.httpx, "get", return_value=_FakeResp(_HN_PAYLOAD)):
        events = hn_adapter.poll(source)
    # First story (24 pts) keeps; second (8 pts) drops; third (None pts) keeps.
    titles = [e.get("evidence_excerpt") for e in events]
    assert "Ask HN: How do you handle CSM handoff at renewal?" in titles
    assert "Another revops thread" not in titles


def test_hn_poll_cursor_filter_via_numericFilters():
    """When cursor is set we pass numericFilters to Algolia — the API
    does the filtering, not the adapter — so we just verify the param
    is wired."""
    source = {"name": "hn-test", "config": {"query": "X"}, "cursor": 1748000000}
    captured = {}

    def fake_get(url, params=None, **kw):
        captured["url"] = url
        captured["params"] = params
        return _FakeResp({"hits": []})

    with patch.object(hn_adapter.httpx, "get", side_effect=fake_get):
        hn_adapter.poll(source)
    assert captured["params"]["numericFilters"] == "created_at_i>1748000000"


def test_hn_base_score():
    # Recipe: 0.4 + 0.05*min(num_comments,20) + 0.01*min(points,30)
    assert hn_adapter.base_score({"num_comments": 0, "points": 0}) == 0.4
    assert hn_adapter.base_score({"num_comments": 20, "points": 30}) == 1.0
    assert hn_adapter.base_score({"num_comments": 100, "points": 100}) == 1.0
    # 18 comments + 24 points -> 0.4 + 0.9 + 0.24 = 1.54 -> capped at 1.0
    assert hn_adapter.base_score({"num_comments": 18, "points": 24}) == 1.0
    # mid: 5 comments + 10 points -> 0.4 + 0.25 + 0.10 = 0.75
    assert hn_adapter.base_score({"num_comments": 5, "points": 10}) == 0.75


# ---------------------------------------------------------------------------
# Reddit
# ---------------------------------------------------------------------------

_REDDIT_PAYLOAD = {
    "data": {
        "children": [
            {"data": {
                "id": "abc1",
                "title": "Best stack for solo SaaS founders?",
                "selftext": "Looking for recommendations on…",
                "score": 42,
                "num_comments": 15,
                "created_utc": 1748390000,
                "permalink": "/r/SaaS/comments/abc1/best_stack/",
            }},
            {"data": {
                "id": "abc2",
                "title": "Cheap PostHog alternatives",
                "selftext": "",
                "score": 8,
                "num_comments": 2,
                "created_utc": 1748386000,
                "permalink": "/r/SaaS/comments/abc2/cheap_posthog/",
            }},
            {"data": {
                "id": "abc3",
                "title": "Removed post test",
                "selftext": "[removed]",
                "score": 5,
                "num_comments": 0,
                "created_utc": 1748385000,
                "permalink": "/r/SaaS/comments/abc3/removed/",
            }},
        ]
    }
}


def test_reddit_poll_normalizes_and_skips_removed():
    source = {"name": "reddit-test",
              "config": {"subreddit": "SaaS"}, "cursor": None}
    with patch.object(reddit_adapter.httpx, "get", return_value=_FakeResp(_REDDIT_PAYLOAD)):
        events = reddit_adapter.poll(source)
    assert len(events) == 2  # removed post dropped
    assert events[0]["evidence_url"].startswith("https://www.reddit.com/r/SaaS/")
    assert source["cursor"] == 1748390000


def test_reddit_poll_respects_min_upvotes():
    source = {"name": "reddit-test",
              "config": {"subreddit": "SaaS", "min_upvotes": 20},
              "cursor": None}
    with patch.object(reddit_adapter.httpx, "get", return_value=_FakeResp(_REDDIT_PAYLOAD)):
        events = reddit_adapter.poll(source)
    titles = [e["evidence_excerpt"] for e in events]
    assert any("Best stack for solo SaaS founders" in t for t in titles)
    assert not any("Cheap PostHog" in t for t in titles)


def test_reddit_poll_cursor_filters_older_items():
    source = {"name": "reddit-test",
              "config": {"subreddit": "SaaS"},
              "cursor": 1748387000}  # only one post is newer
    with patch.object(reddit_adapter.httpx, "get", return_value=_FakeResp(_REDDIT_PAYLOAD)):
        events = reddit_adapter.poll(source)
    assert len(events) == 1
    assert events[0]["raw"]["id"] == "abc1"


def test_reddit_poll_rate_limited_returns_empty():
    source = {"name": "reddit-test",
              "config": {"subreddit": "SaaS"}, "cursor": None}
    with patch.object(reddit_adapter.httpx, "get",
                       return_value=_FakeResp(None, status_code=429)):
        events = reddit_adapter.poll(source)
    assert events == []
    assert source["cursor"] is None   # cursor unchanged on failure


def test_reddit_base_score():
    # Recipe: 0.3 + 0.02*upvotes + 0.05*num_comments
    assert reddit_adapter.base_score({"score": 0, "num_comments": 0}) == 0.3
    assert reddit_adapter.base_score({"score": 42, "num_comments": 15}) == 1.0  # capped
    # mid: 10 upvotes + 5 comments -> 0.3 + 0.2 + 0.25 = 0.75
    assert reddit_adapter.base_score({"score": 10, "num_comments": 5}) == 0.75
    # supports `ups` fallback
    assert reddit_adapter.base_score({"ups": 5, "num_comments": 0}) == 0.4


# ---------------------------------------------------------------------------
# Reddit OAuth path (REDDIT_USE_OAUTH=1) — network stubbed
# ---------------------------------------------------------------------------

def test_reddit_oauth_path_uses_bearer_token():
    """With the flag on + a token available, the adapter hits oauth.reddit.com
    with an Authorization: bearer header (same parsing as the public path)."""
    reddit_adapter.reset_token_cache()
    captured: dict = {}

    def fake_get(url, params=None, headers=None, **kw):
        captured["url"] = url
        captured["headers"] = headers or {}
        return _FakeResp(_REDDIT_PAYLOAD)

    source = {"name": "reddit-test", "config": {"subreddit": "ecommerce"}, "cursor": None}
    with patch.dict(os.environ, {"REDDIT_USE_OAUTH": "1"}, clear=False), \
         patch.object(reddit_adapter, "_get_oauth_token", return_value="tok123"), \
         patch.object(reddit_adapter.httpx, "get", side_effect=fake_get):
        events = reddit_adapter.poll(source)

    assert captured["url"] == "https://oauth.reddit.com/r/ecommerce/new"
    assert captured["headers"].get("Authorization") == "bearer tok123"
    assert len(events) == 2  # parsing unchanged; removed post dropped


def test_reddit_oauth_falls_back_to_public_without_token():
    """Flag on but no token (missing creds) → public .json path, no auth header."""
    reddit_adapter.reset_token_cache()
    captured: dict = {}

    def fake_get(url, params=None, headers=None, **kw):
        captured["url"] = url
        captured["headers"] = headers or {}
        return _FakeResp(_REDDIT_PAYLOAD)

    source = {"name": "reddit-test", "config": {"subreddit": "ecommerce"}, "cursor": None}
    with patch.dict(os.environ, {"REDDIT_USE_OAUTH": "1"}, clear=False), \
         patch.object(reddit_adapter, "_get_oauth_token", return_value=None), \
         patch.object(reddit_adapter.httpx, "get", side_effect=fake_get):
        reddit_adapter.poll(source)

    assert captured["url"] == "https://www.reddit.com/r/ecommerce/new.json"
    assert "Authorization" not in captured["headers"]


def test_reddit_oauth_token_fetch_and_cache():
    """Token is fetched once (client_credentials when no user/pass) and cached."""
    reddit_adapter.reset_token_cache()
    calls = {"n": 0}

    def fake_post(url, data=None, auth=None, headers=None, **kw):
        calls["n"] += 1
        calls["grant"] = (data or {}).get("grant_type")
        calls["auth"] = auth
        return _FakeResp({"access_token": "abc", "expires_in": 3600})

    with patch.dict(os.environ, {"REDDIT_USE_OAUTH": "1",
                                 "REDDIT_CLIENT_ID": "cid",
                                 "REDDIT_CLIENT_SECRET": "csecret"}, clear=False), \
         patch.object(reddit_adapter.httpx, "post", side_effect=fake_post):
        t1 = reddit_adapter._get_oauth_token()
        t2 = reddit_adapter._get_oauth_token()   # served from cache

    assert t1 == "abc" and t2 == "abc"
    assert calls["n"] == 1                         # only one token request
    assert calls["grant"] == "client_credentials"  # no user/pass → app-only
    assert calls["auth"] == ("cid", "csecret")
    reddit_adapter.reset_token_cache()


# ---------------------------------------------------------------------------
# RSS / Atom
# ---------------------------------------------------------------------------

_RSS_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Test feed</title>
    <link>https://example.com</link>
    <item>
      <title>First post on AI agents</title>
      <link>https://example.com/post-1</link>
      <pubDate>Tue, 27 May 2026 14:00:00 GMT</pubDate>
      <description>&lt;p&gt;A discussion of AI agents.&lt;/p&gt;</description>
    </item>
    <item>
      <title>Older post about marketing</title>
      <link>https://example.com/post-2</link>
      <pubDate>Mon, 26 May 2026 10:00:00 GMT</pubDate>
      <description>Older content here</description>
    </item>
  </channel>
</rss>
"""

_ATOM_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Test feed</title>
  <entry>
    <title>Atom post one</title>
    <link href="https://example.com/atom-1"/>
    <published>2026-05-27T14:00:00Z</published>
    <summary>An Atom summary.</summary>
  </entry>
</feed>
"""


def test_rss_poll_parses_items():
    source = {"name": "rss-test",
              "config": {"feed_url": "https://example.com/feed"},
              "cursor": None}
    with patch.object(rss_adapter.httpx, "get",
                       return_value=_FakeResp(content=_RSS_XML)):
        events = rss_adapter.poll(source)
    assert len(events) == 2
    assert events[0]["source"] == "rss"
    assert events[0]["evidence_url"] == "https://example.com/post-1"
    assert "AI agents" in events[0]["evidence_excerpt"]
    # Cursor = newest published timestamp
    assert source["cursor"] == events[0]["published_ts"]
    assert source["cursor"] > 0


def test_rss_poll_cursor_filters_older():
    # The XML fixture has post-2 at "Mon, 26 May 2026 10:00:00 GMT" (~1779789600)
    # and post-1 at "Tue, 27 May 2026 14:00:00 GMT" (~1779890400). Set cursor
    # between them so only post-1 (newer) passes the filter.
    source = {"name": "rss-test",
              "config": {"feed_url": "https://example.com/feed"},
              "cursor": 1779800000}
    with patch.object(rss_adapter.httpx, "get",
                       return_value=_FakeResp(content=_RSS_XML)):
        events = rss_adapter.poll(source)
    # Only one post is newer than the cursor
    assert len(events) == 1
    assert events[0]["evidence_url"] == "https://example.com/post-1"


def test_atom_poll_parses_entries():
    source = {"name": "atom-test",
              "config": {"feed_url": "https://example.com/atom"},
              "cursor": None}
    with patch.object(rss_adapter.httpx, "get",
                       return_value=_FakeResp(content=_ATOM_XML)):
        events = rss_adapter.poll(source)
    assert len(events) == 1
    assert events[0]["evidence_url"] == "https://example.com/atom-1"
    assert "Atom summary" in events[0]["evidence_excerpt"]


def test_rss_base_score_flat_05():
    assert rss_adapter.base_score({}) == 0.5
    assert rss_adapter.base_score({"title": "anything"}) == 0.5
