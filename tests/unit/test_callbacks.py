"""Unit tests for agents._common callbacks (Eval Service path)."""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

# Keep telemetry enabled so we verify emit_action is called; mock BQ side.
os.environ["TELEMETRY_DISABLED"] = "0"

from agents._common import (  # noqa: E402
    make_after_callback,
    make_model_armor_callback,
)


def _fake_callback_context(state: dict | None = None,
                           invocation_id: str = "inv_123"):
    ctx = MagicMock()
    ctx.state = state if state is not None else {}
    ctx.invocation_id = invocation_id
    return ctx


def test_model_armor_callback_detects_block():
    cb = make_model_armor_callback()
    ctx = _fake_callback_context()
    response = MagicMock()
    response.block_reason = "MODEL_ARMOR"
    response.block_reason_message = "prompt_injection, jailbreak"
    response.content = None
    cb(ctx, response)
    assert ctx.state["model_armor"]["decision"] == "block"
    assert "prompt_injection" in ctx.state["model_armor"]["categories"]


def test_model_armor_callback_records_allow_and_caches_text():
    cb = make_model_armor_callback()
    ctx = _fake_callback_context()
    response = MagicMock()
    response.block_reason = None
    part = MagicMock()
    part.text = "Here is your draft."
    response.content = MagicMock()
    response.content.parts = [part]
    cb(ctx, response)
    assert ctx.state["model_armor"]["decision"] == "allow"
    assert ctx.state["_last_output"] == "Here is your draft."


def test_after_callback_emits_telemetry_with_eval_scores(monkeypatch):
    # Inline eval is SAMPLED (EVAL_SAMPLE_RATE, deterministic on the random
    # telemetry_id) — pin it to 1 so this test always exercises the scored
    # path instead of failing ~3 runs in 4 when the draft samples out.
    monkeypatch.setenv("EVAL_SAMPLE_RATE", "1")
    cb = make_after_callback(
        agent_name="content_agent",
        skill_id="linkedin_post",
        action_type="draft_linkedin",
        channel="linkedin",
    )
    # output_key on LlmAgent writes the agent's text into state['draft']
    ctx = _fake_callback_context(state={
        "skill_version": "v3.txt",
        "draft": "Drafted content goes here with specific evidence.",
        "model_armor": {"decision": "allow", "categories": []},
    })

    # score_draft(..., return_explanations=True) returns (scores, explanations).
    with patch("agents._common.score_draft",
                return_value=({"brand_voice": 0.8, "claim_support": 0.9,
                               "claim_risk": 0.85, "icp_relevance": 0.7,
                               "originality": 0.75, "conversion_intent": 0.65},
                              {"brand_voice": "concise, evidence-led"})) as m_score, \
         patch("agents._common.emit_action", return_value="act_abc") as m_emit:
        cb(ctx)

    m_score.assert_called_once()
    m_emit.assert_called_once()
    args, kwargs = m_emit.call_args
    record = args[0]
    assert record.agent == "content_agent"
    assert record.eval_scores.brand_voice == 0.8
    assert record.eval_scores.claim_risk == 0.85
    assert record.eval_scores.icp_relevance == 0.7
    assert record.model_armor.decision == "allow"
    # Outcome slots declared for linkedin drafts
    outcomes = kwargs.get("outcomes") or (args[1] if len(args) > 1 else [])
    slot_names = {s.slot_name for s in outcomes}
    assert "engagement_72h" in slot_names


def test_after_callback_swallows_errors():
    """Telemetry must NEVER fail the agent run."""
    cb = make_after_callback(
        agent_name="content_agent",
        skill_id="linkedin_post",
        action_type="draft_linkedin",
        channel="linkedin",
    )
    ctx = _fake_callback_context(state={"skill_version": "v3.txt",
                                         "draft": "Draft."})

    with patch("agents._common.score_draft", side_effect=RuntimeError("Eval exploded")), \
         patch("agents._common.emit_action", side_effect=RuntimeError("BQ exploded")):
        result = cb(ctx)  # must not raise

    assert result is None
