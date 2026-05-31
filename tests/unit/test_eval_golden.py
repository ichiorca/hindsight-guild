"""Golden-set regression test (cloud-free) — the CI eval gate.

Runs with the Vertex AI Eval Service mocked, so it spends no quota and runs on
every PR. It guards the *harness contract*, not the live judge:

  1. The golden corpus is well-formed and every rubric it names is a real,
     live rubric (catches a renamed/dropped rubric).
  2. ``score_draft`` wires the dataset columns and normalizes the judge's raw
     1-5 verdict into 0..1 correctly for the FULL rubric set (catches a broken
     column name or a normalization regression).
  3. ``passes_quality_floor`` holds the deliberately-bad drafts and ships the
     good ones (catches a regression in the ship/hold gate).

The live judge-drift gate lives in ``tests/integration/test_eval_golden_live.py``
(gated on INTEGRATION_TEST=1), which runs this same corpus through real Vertex.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from shared import rubrics
from tests.golden import golden_set
from tests.golden.golden_set import ALL_GOLDEN, GoldenDraft

# ---------------------------------------------------------------------------
# 1. Corpus integrity + rubric coverage
# ---------------------------------------------------------------------------

def test_corpus_is_well_formed():
    assert ALL_GOLDEN, "golden corpus is empty"
    ids = [g.id for g in ALL_GOLDEN]
    assert len(ids) == len(set(ids)), "duplicate golden ids"
    for g in ALL_GOLDEN:
        assert g.text.strip(), f"{g.id}: empty text"
        assert g.channel, f"{g.id}: missing channel"
        assert g.expect, f"{g.id}: no rubric expectations"
        for name, band in g.expect.items():
            lo, hi = band
            assert 0.0 <= lo <= hi <= 1.0, f"{g.id}/{name}: bad band {band}"


def test_every_golden_rubric_is_a_live_rubric():
    """A renamed/removed rubric must break this test, not silently un-cover."""
    live = {r.name for r in rubrics.ALL_RUBRICS}
    referenced = golden_set.rubric_names_referenced()
    missing = referenced - live
    assert not missing, f"golden set references unknown rubrics: {missing}"


def test_floor_rubrics_are_live():
    live = {r.name for r in rubrics.ALL_RUBRICS}
    assert set(rubrics.FLOOR_RUBRICS) <= live


def test_corpus_has_both_pass_and_hold_cases():
    """The gate is only meaningful if the corpus exercises both verdicts."""
    verdicts = {g.should_pass_floor for g in ALL_GOLDEN}
    assert verdicts == {True, False}, "need both ship and hold golden cases"


# ---------------------------------------------------------------------------
# 2 + 3. Harness wiring / normalization / floor, per golden draft
# ---------------------------------------------------------------------------

def _fake_table_for(g: GoldenDraft) -> pd.DataFrame:
    """A metrics_table as Vertex would return it for ``g``: raw 1-5 scores at
    the midpoint of each expected band, neutral (3) elsewhere. After the
    harness divides by 5, expected rubrics land exactly inside their band."""
    row: dict[str, object] = {"candidate": g.text, "response": g.text}
    for r in rubrics.ALL_RUBRICS:
        if r.name in g.expect:
            lo, hi = g.expect[r.name]
            row[f"{r.name}/score"] = ((lo + hi) / 2.0) * 5.0
        else:
            row[f"{r.name}/score"] = 3.0
    return pd.DataFrame([row])


def _score_with_mock(g: GoldenDraft) -> dict[str, float]:
    mock_task = MagicMock()
    mock_task.evaluate.return_value = MagicMock(metrics_table=_fake_table_for(g))
    # Grounding returns nothing — the harness must still score cleanly.
    with patch.object(rubrics.mongo_tools, "find_sorted", return_value=[]), \
         patch.object(rubrics, "EvalTask", return_value=mock_task):
        return rubrics.score_draft(
            candidate=g.text,
            channel=g.channel,
            icp_description=g.icp_description,
        )


@pytest.mark.parametrize("g", ALL_GOLDEN, ids=[g.id for g in ALL_GOLDEN])
def test_harness_normalizes_into_band(g: GoldenDraft):
    scores = _score_with_mock(g)
    for name, (lo, hi) in g.expect.items():
        assert name in scores, f"{g.id}: harness dropped rubric {name}"
        assert lo <= scores[name] <= hi, (
            f"{g.id}: {name}={scores[name]:.3f} outside band [{lo}, {hi}] — "
            "harness column-wiring or 1-5→0..1 normalization regressed"
        )


@pytest.mark.parametrize("g", ALL_GOLDEN, ids=[g.id for g in ALL_GOLDEN])
def test_quality_floor_matches_expectation(g: GoldenDraft):
    scores = _score_with_mock(g)
    assert rubrics.passes_quality_floor(scores) is g.should_pass_floor, (
        f"{g.id}: floor verdict {not g.should_pass_floor} != expected "
        f"{g.should_pass_floor} (scores={scores})"
    )


# ---------------------------------------------------------------------------
# passes_quality_floor unit behavior (edge cases)
# ---------------------------------------------------------------------------

def test_floor_ignores_missing_scores():
    """An unsampled draft (no scores) must not be held by the floor."""
    assert rubrics.passes_quality_floor({}) is True


def test_floor_holds_on_a_single_failing_required_rubric():
    scores = {r: 0.9 for r in rubrics.FLOOR_RUBRICS}
    scores[rubrics.FLOOR_RUBRICS[0]] = 0.3
    assert rubrics.passes_quality_floor(scores) is False


def test_floor_ignores_non_required_rubrics():
    """A low non-gating rubric (e.g. originality) alone does not hold a draft."""
    scores = {r: 0.9 for r in rubrics.FLOOR_RUBRICS}
    scores["originality"] = 0.1
    assert rubrics.passes_quality_floor(scores) is True


def test_floor_respects_explicit_floor_arg():
    scores = {r: 0.6 for r in rubrics.FLOOR_RUBRICS}
    assert rubrics.passes_quality_floor(scores, floor=0.5) is True
    assert rubrics.passes_quality_floor(scores, floor=0.7) is False
