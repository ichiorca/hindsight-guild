"""Unit tests for shared.telemetry."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

from shared.telemetry import (
    EvalScores,
    ModelArmorResult,
    OutcomeSlot,
    TelemetryRecord,
    prompt_hash,
)


def test_telemetry_record_round_trip():
    r = TelemetryRecord(
        agent="content_agent",
        skill_id="linkedin_post",
        skill_version="v3.txt",
        action_type="draft_linkedin",
        channel="linkedin",
        eval_scores=EvalScores(brand_voice=0.85, claim_support=0.9),
        model_armor=ModelArmorResult(decision="allow"),
    )
    dumped = r.model_dump_json()
    reloaded = TelemetryRecord.model_validate_json(dumped)
    assert reloaded.agent == "content_agent"
    assert reloaded.telemetry_id.startswith("act_")
    assert reloaded.eval_scores.brand_voice == 0.85
    assert reloaded.model_armor.decision == "allow"


def test_outcome_slot_serializes():
    s = OutcomeSlot(slot_name="engagement_72h", metric="engagement",
                    source="linkedin_ads",
                    expected_by=datetime.now(UTC) + timedelta(hours=72))
    payload = s.model_dump()
    assert payload["slot_name"] == "engagement_72h"
    assert payload["source"] == "linkedin_ads"


def test_prompt_hash_deterministic():
    h1 = prompt_hash("Hello world")
    h2 = prompt_hash("Hello world")
    h3 = prompt_hash("Hello world!")
    assert h1 == h2
    assert h1 != h3
    assert h1.startswith("sha256:")


def test_emit_action_calls_bigquery():
    from shared import telemetry

    mock_client = MagicMock()
    mock_client.insert_rows_json.return_value = []  # no errors

    with patch.object(telemetry, "_client", return_value=mock_client):
        record = TelemetryRecord(
            agent="content_agent",
            skill_id="linkedin_post",
            skill_version="v3.txt",
            action_type="draft_linkedin",
        )
        tid = telemetry.emit_action(record, outcomes=[
            OutcomeSlot(slot_name="engagement_72h", metric="engagement",
                        source="linkedin_ads",
                        expected_by=datetime.now(UTC) + timedelta(hours=72)),
        ])

    assert tid == record.telemetry_id
    # One call for actions, one for outcomes
    assert mock_client.insert_rows_json.call_count == 2
