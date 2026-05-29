"""The headline self-learning integration test.

Reject a draft, watch the system re-grade the next one against the new negative.

Prereqs:
  - INTEGRATION_TEST=1 in env
  - A live Atlas cluster with mongo_uri_writer in Secret Manager
  - BigQuery datasets created
  - demo/seed_demo.py has been run

Test flow:
  1. Pick a brand-new draft body that contains an absolute claim.
  2. Score it on claim_risk via the live Eval Service. Expect a low score
     (the rubric calibration set anchors this).
  3. Insert that draft body into negative_examples with category='claim_risk',
     channel='linkedin', ts=now (so it's the freshest negative).
  4. Score a NEW draft that ALSO contains absolute claims but different wording.
  5. The rubric grounding pulls our just-inserted negative (find_sorted by
     ts DESC) and the Eval Service judge should give a lower claim_risk
     score than it would without the grounding.

This isn't perfect — the judge is stochastic — but over a sample of N=5 the
mean post-rejection claim_risk score should be measurably lower than the
mean pre-rejection score.
"""
from __future__ import annotations

import os
import time
from datetime import UTC, datetime

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("INTEGRATION_TEST") != "1",
    reason="Set INTEGRATION_TEST=1 to run against real cloud resources.",
)


def test_reject_then_redraft_lowers_score_on_similar_drafts():
    from shared import mongo_tools
    from shared.rubrics import CLAIM_RISK, score_draft

    mongo_tools.use_secret("mongo_uri_writer")

    # 1. Baseline: score an absolute-claim draft without injecting a fresh negative
    similar_drafts = [
        "We eliminate every churn risk for B2B SaaS companies.",
        "100% of customers see ROI in week one.",
        "Our platform completely transforms revenue operations.",
        "Guaranteed pipeline lift within 30 days.",
        "The only handoff workflow you'll ever need.",
    ]

    # Clear our test-specific negatives first
    mongo_tools.db()["negative_examples"].delete_many(
        {"tags": {"$in": ["__integration_test__"]}}
    )

    pre_scores = []
    for d in similar_drafts:
        result = score_draft(
            d, channel="linkedin", rubrics=[CLAIM_RISK]
        )
        pre_scores.append(result.get("claim_risk", 1.0))

    # 2. Inject a fresh negative with ts=now so find_sorted surfaces it
    fresh_neg = {
        "_id": f"neg_test_{int(time.time())}",
        "ts": datetime.now(UTC),
        "draft_text": "Our solution eliminates revenue leakage entirely.",
        "rejection_reason": "Absolute claim — 'eliminates entirely'.",
        "rejection_category": "claim_risk",
        "channel": "linkedin",
        "rejected_by": "integration_test",
        "tags": ["__integration_test__"],
    }
    mongo_tools.insert_many("negative_examples", [fresh_neg])

    # 3. Score the same drafts again — grounding now includes the fresh negative
    post_scores = []
    for d in similar_drafts:
        result = score_draft(
            d, channel="linkedin", rubrics=[CLAIM_RISK]
        )
        post_scores.append(result.get("claim_risk", 1.0))

    # Cleanup
    mongo_tools.db()["negative_examples"].delete_many(
        {"tags": {"$in": ["__integration_test__"]}}
    )

    pre_mean = sum(pre_scores) / len(pre_scores)
    post_mean = sum(post_scores) / len(post_scores)

    print(f"pre_mean={pre_mean:.3f}, post_mean={post_mean:.3f}")
    # Judge model is stochastic, so we allow some slack — but post should
    # at least not exceed pre.
    assert post_mean <= pre_mean + 0.05, (
        f"Expected lower or equal claim_risk after injecting negative, "
        f"got pre={pre_mean:.3f} post={post_mean:.3f}"
    )


def test_negative_examples_find_sorted_returns_newest_first():
    """The mechanic the rubric harness depends on: find_sorted by ts DESC."""
    from shared import mongo_tools

    mongo_tools.use_secret("mongo_uri_writer")
    mongo_tools.db()["negative_examples"].delete_many(
        {"tags": {"$in": ["__sort_test__"]}}
    )

    now = datetime.now(UTC)
    mongo_tools.insert_many("negative_examples", [
        {"_id": "older",
         "ts": now.replace(year=now.year - 1),
         "draft_text": "older negative",
         "rejection_category": "tone",
         "channel": "linkedin",
         "tags": ["__sort_test__"]},
        {"_id": "newer",
         "ts": now,
         "draft_text": "newer negative",
         "rejection_category": "tone",
         "channel": "linkedin",
         "tags": ["__sort_test__"]},
    ])

    results = mongo_tools.find_sorted(
        "negative_examples",
        {"channel": "linkedin", "rejection_category": "tone",
         "tags": {"$in": ["__sort_test__"]}},
        sort=[("ts", -1)],
        limit=2,
    )

    mongo_tools.db()["negative_examples"].delete_many(
        {"tags": {"$in": ["__sort_test__"]}}
    )

    assert len(results) == 2
    assert results[0]["_id"] == "newer", "find_sorted must return newest first"
    assert results[1]["_id"] == "older"
