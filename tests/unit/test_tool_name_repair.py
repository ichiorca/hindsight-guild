"""Unit tests for tool-name repair (agents/_common).

Gemini intermittently calls a tool by a near-miss name (dropping the MCP
``mongodb_`` prefix, swapping hyphen for underscore). ADK hard-errors on
unknown tool names and the error leaks out as the draft — these tests lock in
the canonical remap that prevents that.
"""
from __future__ import annotations

import types

from agents._common import (
    make_tool_name_repair_callback,
    repair_tool_name,
)


def test_hallucinated_names_remap_to_real_mongodb_tools():
    assert repair_tool_name("list_collections") == "mongodb_list-collections"
    assert repair_tool_name("listCollections") == "mongodb_list-collections"
    assert repair_tool_name("collection_schema") == "mongodb_collection-schema"
    assert repair_tool_name("find") == "mongodb_find"
    assert repair_tool_name("aggregate") == "mongodb_aggregate"
    assert repair_tool_name("mongodb_list_collections") == "mongodb_list-collections"


def test_exact_names_pass_through():
    for n in ("mongodb_find", "mongodb_list-collections", "mongodb_update_one"):
        assert repair_tool_name(n) == n


def test_non_mongodb_tools_untouched():
    # Skill tools / web_search / anything else must never be remapped.
    for n in ("read_skill", "read_skill_reference", "web_search",
              "search_past_lessons", "propose_skill_revision", ""):
        assert repair_tool_name(n) == n


def test_callback_rewrites_function_call_name_in_response():
    cb = make_tool_name_repair_callback()
    fc = types.SimpleNamespace(name="list_collections", args={})
    part = types.SimpleNamespace(function_call=fc, text=None)
    resp = types.SimpleNamespace(content=types.SimpleNamespace(parts=[part]))

    out = cb(callback_context=types.SimpleNamespace(state={}), llm_response=resp)

    assert fc.name == "mongodb_list-collections"   # rewritten in place
    assert out is resp                              # modified response returned


def test_callback_noop_when_names_valid():
    cb = make_tool_name_repair_callback()
    fc = types.SimpleNamespace(name="mongodb_find", args={})
    part = types.SimpleNamespace(function_call=fc, text=None)
    resp = types.SimpleNamespace(content=types.SimpleNamespace(parts=[part]))

    out = cb(callback_context=types.SimpleNamespace(state={}), llm_response=resp)

    assert fc.name == "mongodb_find"
    assert out is None   # nothing changed → no override
