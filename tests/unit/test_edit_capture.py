"""Unit tests for the Gemini-based edit_capture_handler."""
from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

os.environ.setdefault("PROJECT_ID", "test-project")

from services.edit_capture_handler.main import (  # noqa: E402
    _classify_regex,
    _classify_with_gemini,
    app,
)


def _client():
    return app.test_client()


def test_classify_regex_detects_softened_tone():
    out = _classify_regex(
        "Our platform eliminates churn entirely.",
        "Customers report meaningful reductions in churn.",
        "Absolute claim",
    )
    assert "softened_tone" in out["edit_categories"]
    assert out["rejection_category"] == "claim_risk"


def test_classify_with_gemini_uses_structured_output():
    """Verify the structured-output config is requested."""
    mock_genai = MagicMock()
    mock_resp = MagicMock()
    mock_resp.text = json.dumps({
        "edit_categories": ["softened_tone", "added_evidence"],
        "rejection_category": "claim_risk",
    })
    mock_genai.models.generate_content.return_value = mock_resp

    with patch("services.edit_capture_handler.main._genai", mock_genai):
        result = _classify_with_gemini(
            "We eliminate churn.",
            "Customers report 23% fewer churns.",
            "Absolute claim",
        )
    mock_genai.models.generate_content.assert_called_once()
    call_kwargs = mock_genai.models.generate_content.call_args.kwargs
    assert call_kwargs["config"]["response_mime_type"] == "application/json"
    assert "softened_tone" in result["edit_categories"]
    assert result["rejection_category"] == "claim_risk"


def test_reject_writes_to_both_bq_and_mongo():
    """Reject path: BQ edit row + Mongo negative_examples + approvals upsert.
    Reject does NOT seed an attribution_map row (only approve/edit does).
    """
    fake_classification = {
        "edit_categories": ["softened_tone", "claim_removed"],
        "rejection_category": "claim_risk",
    }
    with patch("services.edit_capture_handler.main.BQ") as m_bq, \
         patch("services.edit_capture_handler.main.mongo_tools") as m_mongo, \
         patch("services.edit_capture_handler.main.update_with_history") as m_uwh, \
         patch("services.edit_capture_handler.main._classify_with_gemini",
               return_value=fake_classification):
        m_bq.insert_rows_json.return_value = []
        # First-time write path — find_one returns None so we hit upsert,
        # not update_with_history.
        m_mongo.find_one.return_value = None
        resp = _client().post("/handle", json={
            "telemetry_id": "act_test_001",
            "original_draft": "Our platform eliminates churn.",
            "approved_text": "",
            "decision": "reject",
            "rejection_reason": "Absolute claim",
            "decided_by": "rohit",
            "channel": "linkedin",
        })
    assert resp.status_code == 200
    m_bq.insert_rows_json.assert_called_once()
    m_mongo.insert_many.assert_called_once()
    inserted = m_mongo.insert_many.call_args[0][1][0]
    assert inserted["_id"] == "neg_act_test_001"
    assert inserted["rejection_category"] == "claim_risk"
    assert inserted["channel"] == "linkedin"
    # Reject writes only the approval doc — no attribution stub on reject.
    m_mongo.upsert.assert_called_once()
    assert m_mongo.upsert.call_args[0][0] == "approvals"
    m_uwh.assert_not_called()


def test_approve_seeds_attribution_stub():
    """Approve path: approvals upsert + attribution_map stub (two upserts)
    plus no BQ edit row and no negative_examples insert.
    """
    with patch("services.edit_capture_handler.main.BQ") as m_bq, \
         patch("services.edit_capture_handler.main.mongo_tools") as m_mongo, \
         patch("services.edit_capture_handler.main.update_with_history") as m_uwh, \
         patch("services.edit_capture_handler.main._classify_with_gemini",
               return_value={"edit_categories": [], "rejection_category": None}):
        m_bq.insert_rows_json.return_value = []
        m_mongo.find_one.return_value = None  # first-time approval
        resp = _client().post("/handle", json={
            "telemetry_id": "act_test_002",
            "original_draft": "Good draft.",
            "approved_text": "Good draft.",
            "decision": "approve",
            "rejection_reason": "",
            "decided_by": "rohit",
            "channel": "linkedin",
        })
    assert resp.status_code == 200
    m_mongo.insert_many.assert_not_called()
    m_bq.insert_rows_json.assert_not_called()
    # Two upserts: the approval doc + the attribution_map stub.
    assert m_mongo.upsert.call_count == 2
    upsert_targets = {c.args[0] for c in m_mongo.upsert.call_args_list}
    assert upsert_targets == {"approvals", "attribution_map"}
    # First-time approval, so we don't take the history path.
    m_uwh.assert_not_called()


def test_re_decision_goes_through_history():
    """When an approval already exists for the telemetry_id, a second
    decision flips it via update_with_history so the old decision survives
    in history.approvals.
    """
    with patch("services.edit_capture_handler.main.BQ") as m_bq, \
         patch("services.edit_capture_handler.main.mongo_tools") as m_mongo, \
         patch("services.edit_capture_handler.main.update_with_history") as m_uwh, \
         patch("services.edit_capture_handler.main._classify_with_gemini",
               return_value={"edit_categories": [], "rejection_category": None}):
        m_bq.insert_rows_json.return_value = []
        m_mongo.find_one.return_value = {"telemetry_id": "act_test_003",
                                          "decision": "reject"}
        resp = _client().post("/handle", json={
            "telemetry_id": "act_test_003",
            "original_draft": "Drafted.",
            "approved_text": "Drafted.",
            "decision": "approve",
            "decided_by": "rohit",
            "channel": "linkedin",
        })
    assert resp.status_code == 200
    m_uwh.assert_called_once()
    args, kwargs = m_uwh.call_args
    assert args[0] == "approvals"
    assert kwargs["change_kind"] == "decision_approve"
    # Attribution stub still fires on approve, even on re-decision.
    upsert_targets = {c.args[0] for c in m_mongo.upsert.call_args_list}
    assert "attribution_map" in upsert_targets
