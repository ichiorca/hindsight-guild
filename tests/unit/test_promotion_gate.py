"""Unit tests for the promotion gate."""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

os.environ.setdefault("PROJECT_ID", "test-project")

from services.promotion_gate import main as gate


def _stats(brand_voice, claim_support=0.85, claim_risk=0.85,
            icp_relevance=0.8, originality=0.7, conversion_intent=0.7, n=100,
            **sd):
    """Build a version-stats dict. Pass sd_<metric>=... to exercise the
    significance gate; omitted → significance falls back to MDE-only."""
    row = {
        "brand_voice": brand_voice,
        "claim_support": claim_support,
        "claim_risk": claim_risk,
        "icp_relevance": icp_relevance,
        "originality": originality,
        "conversion_intent": conversion_intent,
        "n": n,
    }
    row.update(sd)
    return row


# A bare skill dict — _evaluate_candidate now takes the doc (for per-skill
# success-metric selection), not just the id.
def _skill(skill_id="skill_x", **kw):
    return {"_id": skill_id, **kw}


def test_candidate_below_min_actions_is_skipped():
    with patch.object(gate, "_version_stats", return_value={
        "v_inc": _stats(0.7),
        "v_cand": _stats(0.9, n=10),  # not enough actions
    }), patch.object(gate, "_raise_promotion_request") as m_raise:
        gate._evaluate_candidate(_skill(), "v_inc", "v_cand")
    m_raise.assert_not_called()


def test_candidate_below_mde_is_skipped():
    with patch.object(gate, "_version_stats", return_value={
        "v_inc": _stats(0.80),
        "v_cand": _stats(0.83),  # +3pp, below MDE=5pp
    }), patch.object(gate, "_raise_promotion_request") as m_raise:
        gate._evaluate_candidate(_skill(), "v_inc", "v_cand")
    m_raise.assert_not_called()


def test_candidate_breaching_guardrail_is_skipped():
    with patch.object(gate, "_version_stats", return_value={
        "v_inc": _stats(0.80, claim_support=0.90),
        "v_cand": _stats(0.90, claim_support=0.80),  # 10pp drop on guardrail
    }), patch.object(gate, "_raise_promotion_request") as m_raise:
        gate._evaluate_candidate(_skill(), "v_inc", "v_cand")
    m_raise.assert_not_called()


def test_winning_candidate_raises_promotion_request():
    with patch.object(gate, "_version_stats", return_value={
        "v_inc": _stats(0.78),
        "v_cand": _stats(0.88),  # +10pp, no guardrail breach
    }), patch.object(gate, "_raise_promotion_request") as m_raise:
        gate._evaluate_candidate(_skill(), "v_inc", "v_cand")
    m_raise.assert_called_once()


def test_candidate_with_high_variance_lift_is_not_significant():
    # +10pp lift clears MDE, but the variance is huge relative to n, so the
    # z-score sits below the 95% bar. Must NOT promote (B4).
    with patch.object(gate, "_version_stats", return_value={
        "v_inc": _stats(0.78, n=50, sd_brand_voice=0.5),
        "v_cand": _stats(0.88, n=50, sd_brand_voice=0.5),
    }), patch.object(gate, "_raise_promotion_request") as m_raise:
        gate._evaluate_candidate(_skill(), "v_inc", "v_cand")
    m_raise.assert_not_called()


def test_candidate_with_tight_variance_lift_is_significant():
    # Same +10pp lift, but low variance → clearly separable → promote.
    with patch.object(gate, "_version_stats", return_value={
        "v_inc": _stats(0.78, n=100, sd_brand_voice=0.05),
        "v_cand": _stats(0.88, n=100, sd_brand_voice=0.05),
    }), patch.object(gate, "_raise_promotion_request") as m_raise:
        gate._evaluate_candidate(_skill(), "v_inc", "v_cand")
    m_raise.assert_called_once()


def test_conversion_skill_gates_on_conversion_intent():
    # brand_voice is flat (no lift) but conversion_intent jumps +10pp. A
    # conversion-named skill must promote on conversion_intent, not voice (B3).
    with patch.object(gate, "_version_stats", return_value={
        "v_inc": _stats(0.80, conversion_intent=0.70),
        "v_cand": _stats(0.80, conversion_intent=0.80),
    }), patch.object(gate, "_raise_promotion_request") as m_raise:
        gate._evaluate_candidate(_skill("paid_variant_google"), "v_inc", "v_cand")
    m_raise.assert_called_once()


def test_promote_after_approval_flips_current_version():
    fake_skill = {
        "_id": "linkedin_post",
        "current_version": "v3.txt",
        "candidates": ["v4_candidate.txt"],
        "history": ["v1.txt", "v2.txt", "v3.txt"],
    }
    mock_db = MagicMock()
    mock_db["skills"].find_one.return_value = fake_skill

    with patch.object(gate.mongo_tools, "db", return_value=mock_db):
        gate.promote_after_approval("linkedin_post", "v4_candidate.txt")

    mock_db["skills"].update_one.assert_called_once()
    call_args = mock_db["skills"].update_one.call_args
    update = call_args[0][1]
    assert update["$set"]["current_version"] == "v4_candidate.txt"
    assert update["$push"]["history"] == "v4_candidate.txt"
    assert update["$pull"]["candidates"] == "v4_candidate.txt"
    assert "promotion_request" in update["$unset"]
