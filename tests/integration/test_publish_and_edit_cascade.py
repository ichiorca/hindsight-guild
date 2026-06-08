"""Genuine e2e for the two outward-facing decision cascades, driven through the
REAL web_api decision endpoint (services.web_api.routers.queue.submit_decision):

  1. Dev.to publish path — approving a blog draft runs the real _publish_devto
     → shared.integrations.devto.publish_article → attribution_map write. Only
     the Forem HTTP POST is mocked (no real external post); everything else is
     the production code path. Closes the "publishing path is untested" gap.

  2. Edit → learning-loop input — an 'edit' decision writes an approvals row
     (with approved_text) through the real handler; the voice miner then
     clusters the before→after and proposes a house-style revision. Closes the
     "approval-decision cascade is unit-only" gap (the INPUT side of PRD-03 —
     the complement of e2e_skill_evolution Phase 12, which seeds approvals
     directly instead of producing them from a real decision).

Opt-in: needs a local Mongo. Auto-skips when one isn't reachable.

    LOCAL_DEV=1 MONGO_URI_DIRECT="mongodb://localhost:27017" \
      python -m pytest tests/integration/test_publish_and_edit_cascade.py -q
"""
from __future__ import annotations

import uuid

import pytest

# Loads .env + LOCAL_DEV defaults before any web_api/mongo import.
from scripts._test_bootstrap import REPO_ROOT  # noqa: E402,F401
from shared import mongo_tools  # noqa: E402

# A consistent absolute→softened edit so the voice miner clusters a clean
# remove/swap pattern across drafts.
_ABSOLUTE = ("Our platform guarantees 100% delivery and completely eliminates "
             "manual review for every merchant, always.")
_SOFTENED = ("Our platform typically delivers reliably and reduces manual "
             "review meaningfully for most merchants.")


def _mongo_or_skip():
    try:
        db = mongo_tools.db()
        db.command("ping")
        return db
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"local Mongo not reachable: {e}")


def test_devto_publish_path(monkeypatch):
    """approve(channel=blog) → real _publish_devto → publish_article (mocked
    Forem edge) → attribution_map persisted with the external handle."""
    db = _mongo_or_skip()
    from services.web_api.routers import queue as q
    from shared.integrations import devto

    # Configure the key (secret_env reads the env var first) + mock ONLY the
    # outbound Forem HTTP call. publish_article builds the payload for real.
    monkeypatch.setenv("DEVTO_API_KEY", "test-key-e2e")
    captured: dict = {}

    class _Resp:
        status_code = 201
        text = ""

        def json(self):
            return {"id": 778899, "url": "https://dev.to/e2e/agent-ready-xyz",
                    "title": "Make your store agent-ready", "published": True}

    def _fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers or {}
        captured["payload"] = json
        return _Resp()

    monkeypatch.setattr(devto.httpx, "post", _fake_post)

    tid = f"act_pub_{uuid.uuid4().hex[:8]}"
    db["actions"].insert_one({
        "telemetry_id": tid, "icp_segment": "seg_merchant_dtc",
        "channel": "blog", "raw": {"topic_hint": "agent checkout readiness"}})
    db["attribution_map"].delete_many({"telemetry_id": tid})

    try:
        resp = q.submit_decision(q.Decision(
            telemetry_id=tid, decision="approve", original_draft="(n/a)",
            approved_text=f"# Make your store agent-ready\n\n{_SOFTENED}",
            channel="blog", decided_by="founder"))

        pub = resp.get("publish") or {}
        assert pub.get("status") == "published", f"publish not done: {pub}"
        assert pub.get("platform") == "devto"

        # The real publish_article assembled the Forem payload + sent the key.
        assert captured["headers"].get("api-key") == "test-key-e2e"
        article = captured["payload"]["article"]
        assert article["published"] is True
        assert _SOFTENED.split()[0] in article["body_markdown"]
        assert article["title"], "title should be derived from the H1"
        assert article.get("tags"), "tags should be derived from icp/topic"

        # attribution_map is the canonical 'where did it publish' store.
        am = db["attribution_map"].find_one({"telemetry_id": tid}) or {}
        assert am.get("platform") == "devto"
        assert am.get("external_id") == 778899
        assert "dev.to" in (am.get("external_url") or "")
    finally:
        db["actions"].delete_many({"telemetry_id": tid})
        db["attribution_map"].delete_many({"telemetry_id": tid})
        db["approvals"].delete_many({"telemetry_id": tid})
        db["history.approvals"].delete_many({"document_id": tid})


def test_devto_publish_skips_without_key(monkeypatch):
    """No DEVTO_API_KEY → publish is reported 'skipped', not 'failed', and the
    approval still lands (founder decisions never block on a missing key)."""
    db = _mongo_or_skip()
    from services.web_api.routers import queue as q

    monkeypatch.delenv("DEVTO_API_KEY", raising=False)
    tid = f"act_pub_nokey_{uuid.uuid4().hex[:8]}"
    db["actions"].insert_one({"telemetry_id": tid, "channel": "blog",
                              "icp_segment": "seg_merchant_dtc", "raw": {}})
    try:
        resp = q.submit_decision(q.Decision(
            telemetry_id=tid, decision="approve", original_draft="(n/a)",
            approved_text="# Title\n\nbody", channel="blog"))
        pub = resp.get("publish") or {}
        assert pub.get("status") == "skipped", f"expected skipped, got {pub}"
        assert db["approvals"].find_one({"telemetry_id": tid}) is not None
    finally:
        db["actions"].delete_many({"telemetry_id": tid})
        db["approvals"].delete_many({"telemetry_id": tid})
        db["history.approvals"].delete_many({"document_id": tid})
        db["attribution_map"].delete_many({"telemetry_id": tid})


def test_edit_decision_feeds_voice_miner():
    """edit decisions through the real handler write approvals(approved_text);
    the voice miner picks them up and proposes a house-style revision."""
    db = _mongo_or_skip()
    from agents._miners import voice as voice_miner
    from services.web_api.routers import queue as q

    pfx = f"act_edit_{uuid.uuid4().hex[:6]}_"
    tids = [f"{pfx}{i}" for i in range(4)]   # >= voice freq/drafts threshold (3)
    try:
        for tid in tids:
            # The pre-edit draft body (before-text) lives on the action.
            db["actions"].insert_one({
                "telemetry_id": tid, "channel": "email",
                "skills_loaded": ["house-style"],
                "raw": {"draft": _ABSOLUTE}})
            resp = q.submit_decision(q.Decision(
                telemetry_id=tid, decision="edit", original_draft=_ABSOLUTE,
                approved_text=_SOFTENED, channel="email", decided_by="founder"))
            assert resp.get("ok"), f"decision not recorded: {resp}"

        # The real handler wrote an approvals row carrying approved_text.
        appr = db["approvals"].find_one({"telemetry_id": tids[0]}) or {}
        assert appr.get("decision") == "edit"
        assert appr.get("approved_text") == _SOFTENED

        # The voice miner (pure read) clusters the before→after across the 4
        # drafts and proposes a house-style revision.
        props = voice_miner.mine(db, lookback_days=14)
        house = [p for p in props if p.get("target_kind") == "skill"
                 and p.get("target_id") == "house-style"]
        assert house, (
            "voice miner produced no house-style proposal from real edit "
            f"decisions (got {len(props)} proposals)")
        # The proposal evidence reflects the softening pattern we edited in.
        ngrams = {p["evidence"].get("ngram") for p in house}
        assert ngrams & {"guarantees", "completely", "eliminates", "always"}, \
            f"expected a softened absolute term in the proposal; got {ngrams}"
    finally:
        db["actions"].delete_many({"telemetry_id": {"$regex": f"^{pfx}"}})
        db["approvals"].delete_many({"telemetry_id": {"$regex": f"^{pfx}"}})
        db["history.approvals"].delete_many({"document_id": {"$regex": f"^{pfx}"}})


def test_edit_capture_handler_feeds_voice_miner():
    """The CLOUD-handler path (services/edit_capture_handler) must feed the
    voice miner too — it previously wrote approvals WITHOUT approved_text, so
    with the handler active the miner was starved. Drive the real handler and
    assert it now writes approved_text + mirrors training.edits to Mongo, and
    the voice miner picks the pattern up."""
    db = _mongo_or_skip()
    from agents._miners import voice as voice_miner
    from services.edit_capture_handler.main import app as capture_app

    client = capture_app.test_client()
    pfx = f"act_capture_{uuid.uuid4().hex[:6]}_"
    tids = [f"{pfx}{i}" for i in range(4)]
    try:
        for tid in tids:
            db["actions"].insert_one({
                "telemetry_id": tid, "channel": "email",
                "skills_loaded": ["house-style"], "raw": {"draft": _ABSOLUTE}})
            # Real handler; in LOCAL_DEV the Gemini classify falls back to regex
            # and the BQ training.edits write is mirrored to Mongo.
            r = client.post("/handle", json={
                "telemetry_id": tid, "decision": "edit",
                "original_draft": _ABSOLUTE, "approved_text": _SOFTENED,
                "rejection_reason": "", "decided_by": "founder",
                "channel": "email"})
            assert r.status_code == 200, r.data

        appr = db["approvals"].find_one({"telemetry_id": tids[0]}) or {}
        assert appr.get("decision") == "edit"
        assert appr.get("approved_text") == _SOFTENED, \
            "handler must write approved_text so the voice miner can read it"
        # training.edits mirrored into Mongo (LOCAL_DEV-safe, no BQ).
        assert db["training.edits"].count_documents(
            {"telemetry_id": tids[0]}) >= 1

        props = voice_miner.mine(db, lookback_days=14)
        house = [p for p in props if p.get("target_kind") == "skill"
                 and p.get("target_id") == "house-style"]
        assert house, ("voice miner produced no house-style proposal from "
                       "cloud-handler edit decisions")
    finally:
        db["actions"].delete_many({"telemetry_id": {"$regex": f"^{pfx}"}})
        db["approvals"].delete_many({"telemetry_id": {"$regex": f"^{pfx}"}})
        db["history.approvals"].delete_many({"document_id": {"$regex": f"^{pfx}"}})
        db["training.edits"].delete_many({"telemetry_id": {"$regex": f"^{pfx}"}})
