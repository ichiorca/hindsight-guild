"""AEO loop / workflow tests (PRD-01).

Covers the deterministic mechanics of the AEO sub-pipeline
(agents/aeo_agent.py: Scorer -> Reviser -> EscalationGate, max 2 iterations)
WITHOUT the LLM:

  * composite scoring math (_composite, SUB_SIGNAL_WEIGHTS)
  * scorer output parsing (_parse_aeo_score)
  * the deterministic scoring tools (content_quality, passage_blocks)
  * channel-skip (non blog/substack/linkedin -> null score)
  * scorer after-callback re-derives the composite from sub_signals
  * reviser after-callback splits the AEO_REWRITES block off the draft body
  * the escalation gate's exit conditions
  * Mongo side-effects (eval_scores injection + aeo_audits write) against a
    local test DB (auto-skips when Mongo is unreachable)

The full LLM loop (real sub-signal estimation + actual rewriting) needs a
Gemini key; that's an opt-in live run, not this file. The pieces here are what
actually decide whether the loop exits, what score it reports, and what it
persists — all of it deterministic.
"""
from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace

import pytest

# agents._common reads TELEMETRY_DISABLED at import time. This file is collected
# before tests/unit/test_callbacks.py, which sets it "0" to verify emit_action;
# match that so collection order can't lock the flag on and break that test.
# Our assertions don't depend on it — the telemetry chain (try/except-wrapped)
# runs after the state mutations we check.
os.environ["TELEMETRY_DISABLED"] = "0"

from agents import aeo_agent as A  # noqa: E402
from scripts.aeo import content_quality, passage_blocks  # noqa: E402

# ---------------------------------------------------------------------------
# Composite scoring math
# ---------------------------------------------------------------------------

def test_composite_matches_weights():
    sub = {
        "answer_first": 0.8, "question_form_h2s": 0.6,
        "self_contained_blocks": 1.0, "specific_stats": 0.5,
        "definition_patterns": 0.4, "entity_grounding": 0.7,
    }
    expected = round(
        0.8 * 0.25 + 0.6 * 0.15 + 1.0 * 0.20
        + 0.5 * 0.15 + 0.4 * 0.10 + 0.7 * 0.15, 3)
    assert A._composite(sub) == expected
    # weights sum to 1.0 → all-1.0 sub-signals → 1.0
    assert A._composite({k: 1.0 for k in A.SUB_SIGNAL_WEIGHTS}) == 1.0


def test_composite_none_on_missing_key():
    assert A._composite({"answer_first": 0.5}) is None      # incomplete
    assert A._composite({}) is None


def test_parse_aeo_score_shapes():
    assert A._parse_aeo_score({"a": 1}) == {"a": 1}                  # dict
    assert A._parse_aeo_score('{"a": 1}') == {"a": 1}               # json str
    assert A._parse_aeo_score('```json\n{"a": 1}\n```') == {"a": 1}  # fenced
    assert A._parse_aeo_score("not json") is None
    assert A._parse_aeo_score(123) is None


# ---------------------------------------------------------------------------
# Deterministic scoring tools (the Scorer's FunctionTools)
# ---------------------------------------------------------------------------

def test_content_quality_good_beats_slop():
    clean = (
        "Agent checkout fails when the cart API returns a malformed coupon "
        "field. According to Ahrefs 2025, the median AI agent abandons after a "
        "single error. Shopify merchants lost 12 percent of agent sessions to "
        "this in Q1. Stripe and PayPal both shipped agent payment mandates to "
        "address it. The fix is a machine-readable checkout that exposes price, "
        "stock, and shipping as structured fields. " * 6)
    slop = (
        "It's important to note that when it comes to agentic commerce, we must "
        "delve into the ever-evolving landscape. In conclusion, leverage the "
        "power of synergy to unlock the potential. Furthermore, at the end of "
        "the day, this is a game-changer. " * 6)

    cq_clean = content_quality.analyse(clean)
    cq_slop = content_quality.analyse(slop)

    assert cq_clean["overall_quality"] > cq_slop["overall_quality"]
    # slop trips the QRG triggers; clean does not
    assert "ai-patterns" in cq_slop["flags"] or "filler" in cq_slop["flags"]
    assert "ai-patterns" not in cq_clean["flags"]
    assert cq_clean["information_density"] > cq_slop["information_density"]


def test_passage_blocks_qualifies_well_sized_attributed_section():
    # ~150 self-contained, attributed words, no cross-references.
    _words = ["agent", "checkout", "cart", "coupon", "field", "machine",
              "readable", "merchant", "store", "reliability"] * 16  # 160 words
    para = "According to Ahrefs research, " + " ".join(_words[:148]) + "."
    good = f"## How agent checkout fails\n\n{para}\n"
    res = passage_blocks.detect_blocks(good)
    assert res["n_h2_sections"] == 1
    blk = res["h2_sections"][0]["blocks"][0]
    assert blk["in_target_range"] is True            # 134-167 words
    assert blk["self_contained_score"] >= 0.8
    assert blk["qualifies"] is True
    assert res["self_contained_blocks_signal"] == 1.0

    bad = "## Section one\n\nAs discussed above, this is short.\n"
    res_bad = passage_blocks.detect_blocks(bad)
    blk_bad = res_bad["h2_sections"][0]["blocks"][0]
    assert blk_bad["qualifies"] is False             # short + backward-ref
    assert res_bad["self_contained_blocks_signal"] == 0.0


# ---------------------------------------------------------------------------
# Scorer after-callback (channel-skip + composite re-derivation)
# ---------------------------------------------------------------------------

def _ctx(state: dict):
    return SimpleNamespace(state=state)


def test_channel_skip_emits_null_score():
    # email has no answer-engine surface → score None, no inject (returns early)
    state = {"channel": "email", "telemetry_id": "t",
             "aeo_score": {"sub_signals": {"answer_first": 1.0}}}
    A._aeo_scorer_after_callback(_ctx(state))
    assert state["aeo_score"]["answer_extractability"] is None
    assert "skipped" in state["aeo_score"]["rationale"]


def test_scorer_recomputes_composite_from_sub_signals():
    sub = {"answer_first": 0.8, "question_form_h2s": 0.6,
           "self_contained_blocks": 1.0, "specific_stats": 0.5,
           "definition_patterns": 0.4, "entity_grounding": 0.7}
    # blog channel → not skipped; scorer re-derives the composite from sub
    state = {"channel": "blog", "telemetry_id": "t",
             "aeo_score": {"sub_signals": sub, "answer_extractability": 0.99}}
    A._aeo_scorer_after_callback(_ctx(state))
    # ignores the LLM's self-reported 0.99 and recomputes from sub_signals
    assert state["aeo_score"]["answer_extractability"] == A._composite(sub)


def test_scorer_retains_last_good_score_on_degraded_pass():
    """A later scorer pass that returns unparseable prose must NOT null a
    previously-good score (which the gate would read as a channel-skip)."""
    sub = {"answer_first": 0.8, "question_form_h2s": 0.6,
           "self_contained_blocks": 1.0, "specific_stats": 0.5,
           "definition_patterns": 0.4, "entity_grounding": 0.7}
    state = {"channel": "blog", "telemetry_id": "t",
             "aeo_score": {"sub_signals": sub}}

    # Pass 1 — valid: produces a good score and stashes it.
    A._aeo_scorer_after_callback(_ctx(state))
    good = state["aeo_score"]["answer_extractability"]
    assert good == A._composite(sub)
    assert isinstance(state.get("_aeo_last_good"), dict)

    # Pass 2 — output_key overwrites aeo_score with the LLM's conversational,
    # unparseable reply; the callback runs again on the same session state.
    state["aeo_score"] = "Thank you for the context. I have noted the feedback."
    A._aeo_scorer_after_callback(_ctx(state))

    # The degraded pass retains the last good score instead of nulling it.
    assert state["aeo_score"]["answer_extractability"] == good
    assert state["aeo_score"]["sub_signals"] == sub


def test_scorer_nulls_when_no_prior_good_score():
    """First-pass failure with no prior good score still yields None (the gate
    then legitimately treats it as a skip — nothing valid was produced)."""
    state = {"channel": "blog", "telemetry_id": "t",
             "aeo_score": "sorry, I could not score this."}
    A._aeo_scorer_after_callback(_ctx(state))
    assert state["aeo_score"]["answer_extractability"] is None


# ---------------------------------------------------------------------------
# Reviser after-callback (split AEO_REWRITES off the draft body)
# ---------------------------------------------------------------------------

def test_reviser_splits_rewrites_block_off_draft():
    draft = ('# Agent-ready checkout\n\nFirst paragraph body.\n\n'
             'AEO_REWRITES: {"applied": ["reorder to answer-first", '
             '"H2 -> question"], "skipped": []}')
    state = {"draft": draft, "telemetry_id": "t", "channel": "blog",
             "aeo_score": {"answer_extractability": 0.6, "rewrites": []}}
    A._aeo_reviser_after_callback(_ctx(state))
    # the AEO_REWRITES tail is stripped from the draft body
    assert "AEO_REWRITES" not in state["draft"]
    assert state["draft"].rstrip().endswith("First paragraph body.")
    # applied rewrites merged into aeo_score
    assert "reorder to answer-first" in state["aeo_score"]["rewrites"]
    assert state["aeo_score"]["score_before"] == 0.6


# ---------------------------------------------------------------------------
# Escalation gate — exit conditions
# ---------------------------------------------------------------------------

def _gate_escalate(score_obj) -> bool:
    gate = A.AeoEscalationGate(name="gate")
    ctx = SimpleNamespace(
        session=SimpleNamespace(state={"aeo_score": score_obj}),
        invocation_id="inv-1",
    )

    async def _run():
        return [e async for e in gate._run_async_impl(ctx)]

    events = asyncio.run(_run())
    return events[-1].actions.escalate


def test_escalation_gate_exit_conditions():
    assert _gate_escalate({"answer_extractability": None}) is True    # skip
    assert _gate_escalate({"answer_extractability": 0.7}) is True     # at floor
    assert _gate_escalate({"answer_extractability": 0.9}) is True     # above
    assert _gate_escalate({"answer_extractability": 0.5}) is False    # keep looping
    assert _gate_escalate({}) is True                                 # no score → skip


# ---------------------------------------------------------------------------
# Mongo side-effects — eval_scores injection + aeo_audits write
# (auto-skips when no local Mongo)
# ---------------------------------------------------------------------------

@pytest.fixture
def aeo_db(monkeypatch):
    from pymongo import MongoClient

    from shared import mongo_tools

    client = MongoClient("mongodb://localhost:27017", serverSelectionTimeoutMS=800)
    try:
        client.admin.command("ping")
    except Exception:
        client.close()
        pytest.skip("local MongoDB not reachable (docker compose up -d mongo)")
    db = client["hindsight_guild_aeotest"]
    for c in ("actions", "aeo_audits"):
        db[c].delete_many({})
    # aeo_agent calls mongo_tools.db() with no args — point it at the test DB.
    monkeypatch.setattr(mongo_tools, "db", lambda *a, **k: db)
    try:
        yield db
    finally:
        for c in ("actions", "aeo_audits"):
            db[c].delete_many({})
        client.close()


def test_inject_answer_extractability_into_eval_scores(aeo_db):
    aeo_db.actions.insert_one({"telemetry_id": "t1",
                               "eval_scores": {"brand_voice": 0.8}})
    A._inject_aeo_into_eval_scores("t1", 0.82)
    row = aeo_db.actions.find_one({"telemetry_id": "t1"})
    assert row["eval_scores"]["answer_extractability"] == 0.82
    assert row["eval_scores"]["brand_voice"] == 0.8          # preserved


def test_inject_handles_null_eval_scores(aeo_db):
    aeo_db.actions.insert_one({"telemetry_id": "t2", "eval_scores": None})
    A._inject_aeo_into_eval_scores("t2", 0.65)
    row = aeo_db.actions.find_one({"telemetry_id": "t2"})
    assert row["eval_scores"] == {"answer_extractability": 0.65}


def test_write_aeo_audit_on_rewrite(aeo_db):
    state = {"telemetry_id": "t3", "channel": "blog",
             "aeo_score": {"rewrites": ["reorder H2"], "score_before": 0.5,
                           "answer_extractability": 0.72}}
    A._write_aeo_audit(state)
    audit = aeo_db.aeo_audits.find_one({"telemetry_id": "t3"})
    assert audit is not None
    assert audit["rewrites"] == ["reorder H2"]
    assert audit["score_after"] == 0.72
    assert audit["channel"] == "blog"


def test_write_aeo_audit_skips_when_no_rewrites(aeo_db):
    A._write_aeo_audit({"telemetry_id": "t4", "channel": "blog",
                        "aeo_score": {"rewrites": []}})
    assert aeo_db.aeo_audits.count_documents({}) == 0
