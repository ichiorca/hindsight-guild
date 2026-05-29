"""Unit tests for shared.rubrics — Vertex AI Eval Service path.

These mock vertexai.evaluation.EvalTask so no real Eval Service call happens.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd

from shared import rubrics


def test_all_rubrics_defined():
    """Six rubrics live, all PointwiseMetric-shaped."""
    assert len(rubrics.ALL_RUBRICS) == 6
    names = {r.name for r in rubrics.ALL_RUBRICS}
    assert names == {
        "brand_voice", "claim_support", "claim_risk",
        "icp_relevance", "originality", "conversion_intent",
    }


def test_grounded_rubrics_have_category():
    """Every grounded rubric maps to a rejection_category for Mongo lookup."""
    grounded = [r for r in rubrics.ALL_RUBRICS if r.needs_grounding]
    for r in grounded:
        assert r.rejection_category, f"{r.name} is grounded but has no rejection_category"


def test_score_draft_calls_eval_task_with_expected_input():
    """End-to-end: rubric harness builds a DataFrame with negative grounding
    pulled from Mongo, calls EvalTask, normalizes 1-5 → 0..1."""
    fake_negatives = [{"draft_text": "We eliminate churn entirely."}]

    fake_metrics_table = pd.DataFrame([{
        "candidate": "A test draft",
        "brand_voice/score": 4,
        "brand_voice/explanation": "Strong",
        "claim_support/score": 3,
        "claim_support/explanation": "Mostly grounded",
    }])

    mock_task = MagicMock()
    mock_task.evaluate.return_value = MagicMock(metrics_table=fake_metrics_table)

    with patch.object(rubrics.mongo_tools, "find_sorted",
                       return_value=fake_negatives) as m_find, \
         patch.object(rubrics, "EvalTask", return_value=mock_task):
        result = rubrics.score_draft(
            candidate="A test draft",
            channel="linkedin",
            rubrics=[rubrics.BRAND_VOICE, rubrics.CLAIM_SUPPORT],
        )

    # Mongo grounding was queried with ts DESC
    m_find.assert_called()
    kwargs_or_args = m_find.call_args
    sort_arg = kwargs_or_args.kwargs.get("sort") or kwargs_or_args.args[2]
    assert sort_arg == [("ts", -1)], "grounding lookup must sort by ts DESC"

    # Scores normalized 1-5 → 0..1
    assert result["brand_voice"] == 0.8
    assert result["claim_support"] == 0.6


def test_score_draft_skips_grounding_when_channel_missing():
    """If channel isn't provided, grounding lookup shouldn't fire."""
    fake_metrics_table = pd.DataFrame([{
        "candidate": "X",
        "brand_voice/score": 5,
    }])
    mock_task = MagicMock()
    mock_task.evaluate.return_value = MagicMock(metrics_table=fake_metrics_table)

    with patch.object(rubrics.mongo_tools, "find_sorted") as m_find, \
         patch.object(rubrics, "EvalTask", return_value=mock_task):
        rubrics.score_draft("X", rubrics=[rubrics.BRAND_VOICE])

    m_find.assert_not_called()


def test_evaluate_batch_handles_per_row_grounding():
    """Batch eval enriches each row with its own grounding negatives."""
    rows = [
        {"candidate": "draft 1", "channel": "linkedin"},
        {"candidate": "draft 2", "channel": "email"},
    ]
    fake_table = pd.DataFrame([
        {"candidate": "draft 1", "brand_voice/score": 4},
        {"candidate": "draft 2", "brand_voice/score": 5},
    ])
    mock_task = MagicMock()
    mock_task.evaluate.return_value = MagicMock(metrics_table=fake_table)

    with patch.object(rubrics.mongo_tools, "find_sorted", return_value=[]), \
         patch.object(rubrics, "EvalTask", return_value=mock_task):
        table = rubrics.evaluate_batch(rows, rubrics=[rubrics.BRAND_VOICE])

    assert len(table) == 2
    assert "brand_voice/score" in table.columns
