"""Live golden-set eval — the judge-drift regression gate.

Runs the REAL Vertex AI Eval Service over the curated golden corpus and asserts
the live judge still lands each draft inside its expected band. This is the gate
the harness-contract unit test (``tests/unit/test_eval_golden.py``) can't be:
it actually catches a rubric-prompt edit or a JUDGE_MODEL bump that regresses
scoring quality.

Requires:
  - INTEGRATION_TEST=1
  - Vertex AI creds (PROJECT_ID / REGION) and a reachable Mongo for grounding.

Run:
    INTEGRATION_TEST=1 pytest tests/integration/test_eval_golden_live.py -v

Note: this spends Vertex Eval quota — ~6 judge calls per draft. Keep the corpus
small. It is intentionally excluded from the cloud-free CI run.
"""
from __future__ import annotations

import os

import pytest

from shared import rubrics
from tests.golden.golden_set import ALL_GOLDEN, GoldenDraft

pytestmark = pytest.mark.skipif(
    os.environ.get("INTEGRATION_TEST") != "1",
    reason="Set INTEGRATION_TEST=1 to run against the real Vertex Eval Service.",
)


@pytest.mark.parametrize("g", ALL_GOLDEN, ids=[g.id for g in ALL_GOLDEN])
def test_live_judge_lands_in_band(g: GoldenDraft):
    scores = rubrics.score_draft(
        candidate=g.text,
        channel=g.channel,
        icp_description=g.icp_description,
    )
    failures = []
    for name, (lo, hi) in g.expect.items():
        got = scores.get(name)
        if got is None:
            failures.append(f"{name}: no score returned")
        elif not (lo <= got <= hi):
            failures.append(f"{name}={got:.3f} outside [{lo}, {hi}]")
    assert not failures, f"{g.id} regressed: " + "; ".join(failures)

    # And the ship/hold verdict must match human judgment.
    assert rubrics.passes_quality_floor(scores) is g.should_pass_floor, (
        f"{g.id}: live floor verdict disagrees with golden label "
        f"(scores={scores})"
    )
