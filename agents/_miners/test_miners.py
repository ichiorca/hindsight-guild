"""Unit tests for the PRD-03 miners.

Uses an in-process fake Mongo (just a dict-of-lists) so tests don't
depend on a running Mongo. Covers the contract from PRD-03 §6:
  - Voice miner triggers only when freq>=3 AND drafts>=3.
  - Negative miner triggers only when category count>=3.
  - claim_risk emits the extra review_agent rule proposal.
  - Stop-words filtered out as noise.
  - Empty inputs produce empty proposals (no exceptions).
"""
from __future__ import annotations

from datetime import UTC, datetime

from agents._miners import negative as negative_miner
from agents._miners import voice as voice_miner

# ---------------------------------------------------------------------------
# Tiny fake DB
# ---------------------------------------------------------------------------

class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def limit(self, _n):
        return self

    def __iter__(self):
        return iter(self._rows)


class _FakeColl:
    def __init__(self, rows):
        self._rows = rows

    def find(self, query, projection=None):
        out = []
        for r in self._rows:
            if _matches(r, query):
                if projection:
                    proj = {k: r[k] for k in projection if k in r}
                    out.append(proj)
                else:
                    out.append(r)
        return _FakeCursor(out)


def _matches(row: dict, query: dict) -> bool:
    for k, v in query.items():
        if isinstance(v, dict):
            for op, opv in v.items():
                if op == "$gte" and not row.get(k, datetime.min) >= opv:
                    return False
                if op == "$in" and row.get(k) not in opv:
                    return False
        else:
            if row.get(k) != v:
                return False
    return True


class _FakeDB:
    def __init__(self, **collections):
        self._coll = {name: _FakeColl(rows) for name, rows in collections.items()}

    def __getitem__(self, name):
        return self._coll.get(name, _FakeColl([]))


# ---------------------------------------------------------------------------
# Voice miner
# ---------------------------------------------------------------------------

def _edit(tid, decided_at, before, after):
    return ({"telemetry_id": tid, "decided_at": decided_at,
             "decision": "edit", "approved_text": after},
            {"telemetry_id": tid, "ts": decided_at, "channel": "blog",
             "skill_id": "blog_outline", "raw": {"draft": before}})


def test_voice_no_edits_returns_empty():
    db = _FakeDB(approvals=[], actions=[])
    assert voice_miner.mine(db, lookback_days=7) == []


def test_voice_below_threshold_returns_empty():
    # 2 edits with one shared n-gram → below the freq>=3 floor.
    now = datetime.now(UTC)
    a1, b1 = _edit("act_1", now, "we leverage synergy daily", "we use synergy daily")
    a2, b2 = _edit("act_2", now, "we leverage automation broadly",
                    "we use automation broadly")
    db = _FakeDB(approvals=[a1, a2], actions=[b1, b2])
    assert voice_miner.mine(db, lookback_days=7) == []


def test_voice_meets_threshold_emits_proposal():
    # 3 distinct edits, each removing "leverage" → freq>=3 + drafts>=3.
    now = datetime.now(UTC)
    fixtures = [
        _edit("act_1", now, "we leverage automation tools", "we use automation tools"),
        _edit("act_2", now, "we leverage analytics dashboards", "we use analytics dashboards"),
        _edit("act_3", now, "we leverage outbound emails", "we use outbound emails"),
    ]
    approvals = [a for a, _ in fixtures]
    actions = [b for _, b in fixtures]
    db = _FakeDB(approvals=approvals, actions=actions)
    proposals = voice_miner.mine(db, lookback_days=7,
                                  min_frequency=3, min_drafts=3)
    assert len(proposals) >= 1
    p = proposals[0]
    assert p["kind"] == "voice"
    assert p["target_kind"] == "skill"
    assert p["target_id"] == "house-style"
    assert "leverage" in p["issue"]
    assert p["evidence"]["distinct_drafts"] >= 3
    assert p["evidence"]["frequency"] >= 3


def test_voice_stopwords_filtered():
    # 3 edits where the only "shared removed token" is the stopword "the".
    now = datetime.now(UTC)
    fixtures = [
        _edit("act_1", now, "the team works fast", "team works fast"),
        _edit("act_2", now, "the dashboard updates daily", "dashboard updates daily"),
        _edit("act_3", now, "the report is delayed", "report is delayed"),
    ]
    approvals = [a for a, _ in fixtures]
    actions = [b for _, b in fixtures]
    db = _FakeDB(approvals=approvals, actions=actions)
    proposals = voice_miner.mine(db, lookback_days=7,
                                  min_frequency=3, min_drafts=3)
    # "the" must not surface as a proposal — stop-word filter.
    for p in proposals:
        assert "the" != p["evidence"]["ngram"]


def test_voice_max_proposals_cap():
    # Many distinct removed n-grams, each over threshold; result capped.
    now = datetime.now(UTC)
    bad_words = ["synergy", "leverage", "unlock", "robust", "holistic",
                 "paradigm", "ecosystem"]
    fixtures = []
    for word in bad_words:
        for i in range(3):
            tid = f"act_{word}_{i}"
            fixtures.append(_edit(tid, now,
                                   f"we use {word} for our analysis",
                                   "we measure for our analysis"))
    approvals = [a for a, _ in fixtures]
    actions = [b for _, b in fixtures]
    db = _FakeDB(approvals=approvals, actions=actions)
    proposals = voice_miner.mine(db, lookback_days=7, max_proposals=5)
    assert len(proposals) <= 5


# ---------------------------------------------------------------------------
# Negative miner
# ---------------------------------------------------------------------------

def _reject(tid, category, body):
    return ({"telemetry_id": tid, "decision": "reject",
             "decided_at": datetime.now(UTC),
             "rejection_category": category},
            {"telemetry_id": tid, "ts": datetime.now(UTC),
             "channel": "linkedin", "raw": {"draft": body}})


def test_negative_no_rejects_empty():
    db = _FakeDB(approvals=[], actions=[])
    assert negative_miner.mine(db, lookback_days=7) == []


def test_negative_below_min_category_count_skipped():
    # 2 rejects in a category, below default min_category_count=3.
    fixtures = [
        _reject("act_1", "tone", "we revolutionize the customer experience always"),
        _reject("act_2", "tone", "we revolutionize the customer experience always"),
    ]
    approvals = [a for a, _ in fixtures]
    actions = [b for _, b in fixtures]
    db = _FakeDB(approvals=approvals, actions=actions)
    assert negative_miner.mine(db, lookback_days=7) == []


def test_negative_meets_threshold_emits_proposal():
    body = "we revolutionize the customer experience always promising more"
    fixtures = [_reject(f"act_{i}", "tone", body) for i in range(4)]
    approvals = [a for a, _ in fixtures]
    actions = [b for _, b in fixtures]
    db = _FakeDB(approvals=approvals, actions=actions)
    proposals = negative_miner.mine(db, lookback_days=7)
    assert len(proposals) >= 1
    tone_props = [p for p in proposals if p["evidence"].get("category") == "tone"]
    assert len(tone_props) == 1
    p = tone_props[0]
    assert p["kind"] == "negative"
    assert p["target_kind"] == "negative_example"
    assert p["target_id"] == "tone"
    assert p["evidence_count"] == 4


def test_negative_claim_risk_emits_extra_review_proposal():
    body = "guaranteed to triple your pipeline within 30 days no exceptions"
    fixtures = [_reject(f"act_{i}", "claim_risk", body) for i in range(3)]
    approvals = [a for a, _ in fixtures]
    actions = [b for _, b in fixtures]
    db = _FakeDB(approvals=approvals, actions=actions)
    proposals = negative_miner.mine(db, lookback_days=7)
    kinds = [(p["target_kind"], p["target_id"]) for p in proposals]
    assert ("negative_example", "claim_risk") in kinds
    assert ("skill", "review_agent") in kinds


def test_negative_max_proposals_cap():
    # Many categories each over threshold; result capped.
    cats = ["tone", "originality", "icp_relevance", "conversion_intent",
            "off_brand", "other"]
    fixtures = []
    for cat in cats:
        for i in range(3):
            fixtures.append(_reject(f"act_{cat}_{i}", cat,
                                     "phrase " * 10))   # 20 tokens of "phrase"
    approvals = [a for a, _ in fixtures]
    actions = [b for _, b in fixtures]
    db = _FakeDB(approvals=approvals, actions=actions)
    proposals = negative_miner.mine(db, lookback_days=7, max_proposals=3)
    assert len(proposals) <= 3
