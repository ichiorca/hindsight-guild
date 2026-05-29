"""A4: multiple proposals coexist per skill (array) + the singleton mirror
stays in sync; A2: accepting a specific proposal mints a candidate version.

Uses real LOCAL_DEV Mongo with a uniquely-tagged skill doc, cleaned up at the
end so reruns are idempotent."""
from __future__ import annotations

import os
import uuid

import pytest

os.environ.setdefault("LOCAL_DEV", "1")
os.environ.setdefault("MONGO_URI_DIRECT", "mongodb://localhost:27017")

from agents import self_critique_runner as runner
from agents._schema_constants import Status
from services.web_api.routers import self_critique as sc
from shared import mongo_tools


@pytest.fixture
def skill_doc():
    db = mongo_tools.db()
    sid = f"test_sc_skill_{uuid.uuid4().hex[:8]}"
    db.skills.insert_one({
        "_id": sid,
        "skill_kind": "agent_skill",
        "current_version": "v1",
        "candidates": [],
        "history": ["v1"],
        "versions": {"v1": {"body_md": "# House style\n\nBe concise."}},
    })
    yield db, sid
    db.skills.delete_one({"_id": sid})


def _envelope(skill_id, ngram, miner="voice"):
    raw = {
        "kind": miner,
        "target_kind": "skill",
        "target_id": skill_id,
        "issue": f"Founder removed {ngram!r}.",
        "proposed_change": f'Add to "never use" list: "{ngram}"',
        "confidence": "high",
        "evidence_count": 4,
        "evidence": {"ngram": ngram, "pattern_kind": "remove"},
    }
    return runner._proposal_envelope(raw, miner=miner, run_id=None)


def test_two_proposals_coexist_and_mirror_tracks_primary(skill_doc):
    db, sid = skill_doc

    o1 = runner._persist_slot_proposal(db, "skills", {"_id": sid}, _envelope(sid, "leverage"))
    o2 = runner._persist_slot_proposal(db, "skills", {"_id": sid}, _envelope(sid, "synergy"))
    assert o1 == "persisted" and o2 == "persisted"

    doc = db.skills.find_one({"_id": sid})
    # Both proposals live in the canonical array — no clobber.
    assert len(doc["self_critique_proposals"]) == 2
    # The singleton mirror still holds a pending proposal for legacy readers.
    assert doc["self_critique_proposal"]["status"] == Status.AWAITING_HUMAN_REVIEW

    # Re-running the SAME finding refreshes in place (no duplicate).
    o3 = runner._persist_slot_proposal(db, "skills", {"_id": sid}, _envelope(sid, "leverage"))
    assert o3 == "updated"
    doc = db.skills.find_one({"_id": sid})
    assert len(doc["self_critique_proposals"]) == 2


def test_unified_list_emits_one_row_per_proposal_with_subids(skill_doc):
    db, sid = skill_doc
    runner._persist_slot_proposal(db, "skills", {"_id": sid}, _envelope(sid, "leverage"))
    runner._persist_slot_proposal(db, "skills", {"_id": sid}, _envelope(sid, "synergy"))

    rows = sc.list_unified_proposals(status=None, limit=200)
    mine = [r for r in rows if r["target_id"] == sid]
    assert len(mine) == 2
    # Each id is addressable + distinct (skill:{id}::{subid}).
    ids = {r["id"] for r in mine}
    assert len(ids) == 2
    for r in mine:
        assert r["id"].startswith(f"skill:{sid}::")


def test_accept_specific_proposal_mints_candidate(skill_doc):
    db, sid = skill_doc
    runner._persist_slot_proposal(db, "skills", {"_id": sid}, _envelope(sid, "leverage"))
    runner._persist_slot_proposal(db, "skills", {"_id": sid}, _envelope(sid, "synergy"))

    rows = [r for r in sc.list_unified_proposals(status=None, limit=200) if r["target_id"] == sid]
    target = rows[0]

    res = sc.approve_proposal(target["id"])
    assert res["applied"] is True
    candidate = res["candidate_added"]

    doc = db.skills.find_one({"_id": sid})
    # A real candidate version was authored with the rule appended.
    assert candidate in doc["candidates"]
    assert candidate in doc["versions"]
    assert "Learned rules" in doc["versions"][candidate]["body_md"]
    # The accepted entry is recorded; the OTHER proposal is still pending and
    # is now the mirror primary.
    statuses = sorted(p["status"] for p in doc["self_critique_proposals"])
    assert statuses == [Status.ACCEPTED, Status.AWAITING_HUMAN_REVIEW]
    assert doc["self_critique_proposal"]["status"] == Status.AWAITING_HUMAN_REVIEW


def test_dismiss_last_pending_clears_mirror(skill_doc):
    db, sid = skill_doc
    runner._persist_slot_proposal(db, "skills", {"_id": sid}, _envelope(sid, "leverage"))
    rows = [r for r in sc.list_unified_proposals(status=None, limit=200) if r["target_id"] == sid]
    sc.dismiss_proposal(rows[0]["id"])

    doc = db.skills.find_one({"_id": sid})
    # Entry preserved as audit row; mirror unset (nothing pending).
    assert doc["self_critique_proposals"][0]["status"] == "dismissed"
    assert doc.get("self_critique_proposal") is None


def test_dismissed_proposal_does_not_recur_within_cooldown(skill_doc):
    db, sid = skill_doc
    runner._persist_slot_proposal(db, "skills", {"_id": sid}, _envelope(sid, "leverage"))
    rows = [r for r in sc.list_unified_proposals(status=None, limit=200) if r["target_id"] == sid]
    sc.dismiss_proposal(rows[0]["id"])

    # Miner re-finds the same pattern → must be suppressed (A3 for skills).
    outcome = runner._persist_slot_proposal(db, "skills", {"_id": sid}, _envelope(sid, "leverage"))
    assert outcome == "skipped_recent_decision"
    doc = db.skills.find_one({"_id": sid})
    pending = [p for p in doc["self_critique_proposals"]
               if p["status"] == Status.AWAITING_HUMAN_REVIEW]
    assert pending == []
