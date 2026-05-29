"""Tests for the paid miner + the runner orchestration end-to-end.

Uses a real Mongo (LOCAL_DEV mongo) but creates a uniquely-named
prefix in paid_variants so re-runs are idempotent. Cleanup at the end.
"""
from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest

os.environ.setdefault("LOCAL_DEV", "1")
os.environ.setdefault("MONGO_URI_DIRECT", "mongodb://localhost:27017")

from agents import self_critique_runner
from agents._miners import paid as paid_miner
from agents._schema_constants import Status
from shared import mongo_tools


@pytest.fixture
def fresh_db():
    """A scoped slate: deletes anything tagged with the test_run_id at
    the end so concurrent test runs + reruns are clean.

    Also ensures paid_thresholds has at least the ``_default`` row the
    miner needs (the schema bootstrap normally seeds this, but the
    parallel-array index issue on ``skills`` aborts ``apply()`` before
    the seed runs)."""
    db = mongo_tools.db()
    tag = f"test_paid_miner_{uuid.uuid4().hex[:8]}"
    # Idempotent self-seed for the test.
    if db.paid_thresholds.count_documents({"_id": "_default"}) == 0:
        db.paid_thresholds.insert_one({
            "_id": "_default",
            "platform": "*",
            "icp_segment": "*",
            "daily_spend_floor_usd": 100.0,
            "min_conversions_per_24h": 1,
            "min_ctr_pct": 0.5,
            "min_hours_running": 12,
        })
    yield db, tag
    # Cleanup
    db.paid_variants.delete_many({"name": {"$regex": f"^{tag}_"}})
    db.paid_actions_proposed.delete_many({"variant_id": {"$regex": f"^{tag}_"}})
    # Also clean up any self_critique_runs we wrote in this window.
    db.self_critique_runs.delete_many({
        "started_at": {"$gte": datetime.now(UTC) - timedelta(hours=1)}
    })


def _make_variant(tag, name, **kw):
    """Build a paid_variants row with the standard surface the miner
    reads. ``snapshot_at`` defaults to now (fresh)."""
    return {
        "_id":             f"{tag}_{name}",
        "name":            f"{tag}_{name}",
        "status":          "running",
        "platform":        "google_ads",
        "icp_segment":     "seg_founder_b2b",
        "experiment_id":   f"{tag}_exp1",
        "external_id":     f"ad_{name}",
        "spend_24h":       150.0,
        "conversions_24h": 0,
        "ctr_pct":         0.6,
        "hours_running":   24,
        "snapshot_at":     datetime.now(UTC),
        **kw,
    }


def test_paid_miner_pauses_underperforming_variant(fresh_db):
    db, tag = fresh_db

    # Two siblings: ``loser`` is over-spending with no conversions;
    # ``winner`` has good conversion. The miner should propose pausing
    # loser AND reallocating to winner.
    db.paid_variants.insert_many([
        _make_variant(tag, "loser",
                       spend_24h=200.0, conversions_24h=0,
                       ctr_pct=0.3, hours_running=24),
        _make_variant(tag, "winner",
                       spend_24h=100.0, conversions_24h=4,
                       ctr_pct=2.5, hours_running=24),
    ])

    proposals = paid_miner.mine(db)
    # Should propose 1 pause (loser) + 1 reallocate (to winner).
    assert len(proposals) == 2
    pause = next(p for p in proposals
                 if p["evidence"]["action_kind"] == "pause")
    realloc = next(p for p in proposals
                   if p["evidence"]["action_kind"] == "reallocate_budget")
    assert pause["target_id"] == f"{tag}_loser"
    assert realloc["target_id"] == f"{tag}_winner"
    assert realloc["evidence"]["source_variant_id"] == f"{tag}_loser"


def test_paid_miner_skips_within_threshold(fresh_db):
    db, tag = fresh_db
    # Variant is well-performing — no proposal.
    db.paid_variants.insert_one(_make_variant(
        tag, "healthy",
        spend_24h=80.0, conversions_24h=5, ctr_pct=2.0, hours_running=18,
    ))
    assert paid_miner.mine(db) == []


def test_paid_miner_ctr_secondary_trigger_alone(fresh_db):
    db, tag = fresh_db
    # Spend hasn't crossed the floor but CTR has — CTR is an
    # OR-trigger per the schema-level recipe.
    db.paid_variants.insert_one(_make_variant(
        tag, "ctr_only_failing",
        spend_24h=50.0,             # under spend floor
        conversions_24h=5,          # over conv min
        ctr_pct=0.1,                # well under CTR min (0.5)
        hours_running=20,
    ))
    proposals = paid_miner.mine(db)
    assert len(proposals) == 1
    assert proposals[0]["evidence"]["action_kind"] == "pause"
    assert any("ctr" in r.lower() for r in proposals[0]["evidence"]["reasons"])


def test_paid_miner_stale_snapshot_skipped(fresh_db):
    db, tag = fresh_db
    db.paid_variants.insert_one(_make_variant(
        tag, "stale",
        spend_24h=200.0, conversions_24h=0,
        snapshot_at=datetime.now(UTC) - timedelta(hours=24),
    ))
    assert paid_miner.mine(db) == []


# ---------------------------------------------------------------------------
# Runner integration — persistence flow end-to-end.
# ---------------------------------------------------------------------------

def test_runner_persists_paid_proposal(fresh_db):
    db, tag = fresh_db
    db.paid_variants.insert_one(_make_variant(
        tag, "failing", spend_24h=200.0, conversions_24h=0, ctr_pct=0.2,
    ))

    result = self_critique_runner.run_once(
        db=db, miners=("paid",), lookback_days=14,
    )

    assert result["status"] == "ok"
    paid_count = result["miners"]["paid"]["proposals"]
    assert paid_count >= 1

    # Verify the proposal landed in paid_actions_proposed with the
    # expected envelope.
    row = db.paid_actions_proposed.find_one({
        "variant_id": f"{tag}_failing",
        "status": Status.AWAITING_HUMAN_REVIEW,
    })
    assert row is not None
    assert row["miner"] == "paid"
    assert row["kind"] == "pause"


def test_runner_dedupes_repeated_paid_proposal(fresh_db):
    """Running the miner twice on the same data should NOT create two
    pending pause proposals on the same variant."""
    db, tag = fresh_db
    db.paid_variants.insert_one(_make_variant(
        tag, "failing2", spend_24h=200.0, conversions_24h=0,
    ))

    self_critique_runner.run_once(db=db, miners=("paid",))
    self_critique_runner.run_once(db=db, miners=("paid",))

    n_pending = db.paid_actions_proposed.count_documents({
        "variant_id": f"{tag}_failing2",
        "status": Status.AWAITING_HUMAN_REVIEW,
    })
    assert n_pending == 1


def test_runner_dismissed_paid_proposal_does_not_recur(fresh_db):
    """A3: once the founder dismisses a pause proposal, the miner must not
    re-surface it on the next tick while the dismissal is within cooldown —
    even though the variant still breaches stop-loss."""
    db, tag = fresh_db
    db.paid_variants.insert_one(_make_variant(
        tag, "dismissed1", spend_24h=200.0, conversions_24h=0, ctr_pct=0.2,
    ))

    # First tick emits a pending pause.
    self_critique_runner.run_once(db=db, miners=("paid",))
    row = db.paid_actions_proposed.find_one({
        "variant_id": f"{tag}_dismissed1",
        "status": Status.AWAITING_HUMAN_REVIEW,
    })
    assert row is not None

    # Founder dismisses it.
    db.paid_actions_proposed.update_one(
        {"_id": row["_id"]},
        {"$set": {"status": "dismissed",
                  "decided_at": datetime.now(UTC)}},
    )

    # Next tick must NOT re-create a pending proposal for the same variant.
    result = self_critique_runner.run_once(db=db, miners=("paid",))
    n_pending = db.paid_actions_proposed.count_documents({
        "variant_id": f"{tag}_dismissed1",
        "status": Status.AWAITING_HUMAN_REVIEW,
    })
    assert n_pending == 0
    # And the run reports it as skipped, not as a fresh proposal.
    assert result["miners"]["paid"]["proposals"] == 0
    assert result["miners"]["paid"]["skipped"] >= 1


def test_runner_run_doc_records_per_miner_counts(fresh_db):
    db, tag = fresh_db
    db.paid_variants.insert_one(_make_variant(
        tag, "fail3", spend_24h=200.0, conversions_24h=0,
    ))
    result = self_critique_runner.run_once(db=db, miners=("paid",))
    # self_critique_runs row was updated with completed_at + status.
    assert result["miners"]["paid"]["proposals"] >= 1
    assert result["status"] == "ok"
    assert "completed_at" in result
    assert isinstance(result["completed_at"], datetime)


def test_runner_disabled_miners_env(fresh_db, monkeypatch):
    db, tag = fresh_db
    monkeypatch.setenv("SELF_CRITIQUE_DISABLED_MINERS", "paid")
    db.paid_variants.insert_one(_make_variant(
        tag, "fail4", spend_24h=200.0, conversions_24h=0,
    ))
    result = self_critique_runner.run_once(db=db, miners=("paid",))
    assert result["miners"]["paid"]["disabled"] is True
    assert result["miners"]["paid"]["proposals"] == 0


def test_runner_kill_switch_short_circuits(monkeypatch):
    monkeypatch.setenv("SELF_CRITIQUE_DISABLED", "1")
    result = self_critique_runner.run_once(db=mongo_tools.db(),
                                            miners=("paid",))
    assert result["status"] == "disabled"
