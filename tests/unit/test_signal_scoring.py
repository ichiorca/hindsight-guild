"""Tier 1 — pure-function unit tests for the signal layer.

No DB, no network. Covers:
  * signal_watcher._icp_fit_boost / _icp_keywords_hit  (agentic-commerce scoring)
  * signal_router._decide_channel / _build_topic_hint / _extract_pain  (routing)

Keyword tables are tuned for agentic commerce (merchants primary); see
agents/signal_watcher.py.
"""
from __future__ import annotations

import pytest

from agents.signal_router import _build_topic_hint, _decide_channel, _extract_pain
from agents.signal_watcher import _icp_fit_boost, _icp_keywords_hit

MERCHANT = "seg_merchant_dtc"


# ---------------------------------------------------------------------------
# _icp_fit_boost — multipliers stack multiplicatively (PRD-02 §6, agentic
#   commerce tuning): ICP ×1.4 · pain ×1.3 · solution ×1.5 · negative ×1.6 ·
#   protocol/surface ×1.2.  NOT capped here — caller caps base*boost at 1.0.
# ---------------------------------------------------------------------------

def test_boost_is_neutral_without_matches():
    assert _icp_fit_boost("just some unrelated words", MERCHANT) == 1.0
    assert _icp_fit_boost("", MERCHANT) == 1.0
    # ICP keyword present but no icp_segment configured → no ICP boost,
    # and "shopify" isn't a protocol/surface keyword → still neutral.
    assert _icp_fit_boost("shopify talk", None) == 1.0


@pytest.mark.parametrize("text,expected", [
    ("our shopify store",              1.4),        # primary merchant ICP keyword
    ("broken checkout on our store",   1.3),        # pain only
    ("looking for a fix",             1.5),         # solution intent
    ("switching from a vendor",        1.6),        # negative pattern
    ("we use mcp for tools",           1.2),        # protocol/surface only
])
def test_boost_individual_multipliers(text, expected):
    assert _icp_fit_boost(text, MERCHANT) == pytest.approx(expected)


def test_boost_stacks_all_buckets():
    # ICP(agentic commerce) × pain(broken checkout) × solution(looking for)
    # × negative(switching from) × protocol(acp)
    text = ("agentic commerce: broken checkout, looking for alternative, "
            "switching from acp tooling")
    assert _icp_fit_boost(text, MERCHANT) == pytest.approx(1.4 * 1.3 * 1.5 * 1.6 * 1.2)


def test_boost_is_case_insensitive():
    assert _icp_fit_boost("AGENTIC COMMERCE", MERCHANT) == pytest.approx(1.4)


# ---------------------------------------------------------------------------
# _icp_keywords_hit — which ICP keywords matched (for /signals debugging).
# ---------------------------------------------------------------------------

def test_keywords_hit_returns_matches_in_keyword_order():
    hits = _icp_keywords_hit(
        "our agentic commerce shop needs instant checkout on shopify", MERCHANT)
    assert hits == ["agentic commerce", "instant checkout", "shopify"]


def test_keywords_hit_empty_without_icp_or_text():
    assert _icp_keywords_hit("agentic commerce shopify", None) == []
    assert _icp_keywords_hit("", MERCHANT) == []


# ---------------------------------------------------------------------------
# _decide_channel — PRD-02 §8 heuristic; source default_channel wins.
# ---------------------------------------------------------------------------

def test_channel_default_from_source_doc_wins():
    sig = {"source": "hn", "evidence_excerpt": "How do I do X?"}
    assert _decide_channel(sig, {"default_channel": "substack"}) == "substack"


def test_channel_question_thread_on_hn_or_reddit_is_blog():
    assert _decide_channel(
        {"source": "hn", "evidence_excerpt": "How do I make my store agent-ready? ..."}, {}) == "blog"
    assert _decide_channel(
        {"source": "reddit", "evidence_excerpt": "What checkout works with ChatGPT? ..."}, {}) == "blog"


def test_channel_rss_and_reddit_anecdote_are_linkedin():
    assert _decide_channel({"source": "rss", "evidence_excerpt": "Big agentic commerce news."}, {}) == "linkedin"
    assert _decide_channel(
        {"source": "reddit", "evidence_excerpt": "My experience selling via ChatGPT."}, {}) == "linkedin"


def test_channel_fallback_is_blog():
    assert _decide_channel({"source": "hn", "evidence_excerpt": "My experience."}, {}) == "blog"


# ---------------------------------------------------------------------------
# _build_topic_hint / _extract_pain
# ---------------------------------------------------------------------------

def test_topic_hint_fills_template_pain_placeholder():
    sig = {"evidence_excerpt": "we are struggling with agent checkout failures lately"}
    hint = _build_topic_hint(sig, {"topic_hint_template": "Make your store agent-ready: stop {pain}"})
    assert hint.startswith("Make your store agent-ready: stop agent checkout")


def test_topic_hint_falls_back_to_excerpt_without_template():
    excerpt = "x" * 100
    assert _build_topic_hint({"evidence_excerpt": excerpt}, {}) == excerpt[:80]


def test_topic_hint_bad_template_falls_back_to_excerpt():
    sig = {"evidence_excerpt": "some excerpt text here"}
    assert _build_topic_hint(sig, {"topic_hint_template": "Stop {unknown_key}"}) == "some excerpt text here"[:80]


@pytest.mark.parametrize("text,expected", [
    ("we keep losing sales to ai agents",        "sales to ai agents"),
    ("stalled on agent checkout for weeks",      "agent checkout for weeks"),
    ("our checkout is broken again",             "our checkout"),
    ("nothing matches this pattern",             None),
])
def test_extract_pain(text, expected):
    assert _extract_pain(text) == expected
