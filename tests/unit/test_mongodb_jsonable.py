"""Unit tests for the MongoDB tool-result sanitizer (agents/_mongodb_tools).

A raw ObjectId anywhere in a returned doc — not just the top-level _id — makes
ADK's JSON serialization of the tool result raise PydanticSerializationError,
killing the agent run with an empty draft. (Prod hit: customer_voice.signal_id
is an ObjectId, surfaced by mongodb_vector_search on signal-triggered drafts.)
"""
from __future__ import annotations

import json
from datetime import UTC, datetime

from bson import Decimal128, ObjectId

from agents._mongodb_tools import _jsonable, _stringify_id


def test_top_level_id_stringified():
    oid = ObjectId()
    out = _stringify_id({"_id": oid, "name": "linkedin_post"})
    assert out["_id"] == str(oid)
    assert out["name"] == "linkedin_post"


def test_nested_and_non_id_objectids_are_converted():
    oid, ref = ObjectId(), ObjectId()
    doc = {
        "_id": oid,
        "signal_id": ref,                       # the prod offender
        "nested": {"ref": ref},
        "list": [ref, {"x": ref}],
    }
    out = _stringify_id(doc)
    assert out["signal_id"] == str(ref)
    assert out["nested"]["ref"] == str(ref)
    assert out["list"][0] == str(ref)
    assert out["list"][1]["x"] == str(ref)
    # The whole point: the result is now JSON-serializable.
    json.dumps(out)


def test_datetime_decimal_bytes_coerced():
    doc = {"ts": datetime(2026, 1, 2, 3, 4, tzinfo=UTC),
           "amt": Decimal128("1.50"), "blob": b"\x00\x01"}
    out = _stringify_id(doc)
    assert out["ts"].startswith("2026-01-02")
    assert out["amt"] == "1.50"
    assert out["blob"] is None
    json.dumps(out)


def test_empty_and_none_passthrough():
    assert _stringify_id(None) is None
    assert _stringify_id({}) == {}


def test_jsonable_scalars_passthrough():
    assert _jsonable("s") == "s"
    assert _jsonable(7) == 7
    assert _jsonable(None) is None


def test_evidence_result_is_fully_jsonable():
    """The 2nd prod offender: validate_claim returns customer_voice rows whose
    signal_id is an ObjectId. _result must hand back JSON-serializable docs."""
    from agents._evidence_tool import _result

    sig = ObjectId()
    voice = [{"_id": ObjectId(), "signal_id": sig, "text": "agent-ready store",
              "ts": datetime(2026, 1, 1, tzinfo=UTC)}]
    out = _result([], voice, "voice_supported", "ok")
    assert out["voice"][0]["signal_id"] == str(sig)
    json.dumps(out)  # the whole point — no PydanticSerializationError analog


def test_tool_result_sanitizer_callback_coerces_objectid():
    """The defensive after_tool_callback strips a leaked ObjectId so no tool can
    reintroduce the bug. Returns a cleaned dict only when coercion was needed."""
    from agents._common import make_tool_result_sanitizer_callback

    cb = make_tool_result_sanitizer_callback()
    oid = ObjectId()
    dirty = {"voice": [{"signal_id": oid}]}
    cleaned = cb(tool=None, args={}, tool_context=None, tool_response=dirty)
    assert cleaned is not None
    assert cleaned["voice"][0]["signal_id"] == str(oid)
    json.dumps(cleaned)
    # No-op (returns None) when already clean.
    assert cb(tool=None, args={}, tool_context=None,
              tool_response={"ok": 1}) is None
