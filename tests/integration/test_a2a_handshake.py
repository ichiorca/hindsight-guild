"""A2A handshake: verify one agent can call another over the protocol.

Prereqs:
  - INTEGRATION_TEST=1
  - At least the Research and Analytics agents deployed and reachable
  - Env vars A2A_URL_RESEARCH and A2A_URL_ANALYTICS set
"""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("INTEGRATION_TEST") != "1",
    reason="Set INTEGRATION_TEST=1 to run against real cloud resources.",
)


def test_agent_card_endpoint_returns_valid_card():
    """Every to_a2a-wrapped agent exposes /.well-known/agent-card.json."""
    import httpx
    url = f"{os.environ['A2A_URL_RESEARCH']}/.well-known/agent-card.json"
    r = httpx.get(url, timeout=10)
    r.raise_for_status()
    card = r.json()
    assert "name" in card
    assert "skills" in card
    assert "capabilities" in card


def test_call_research_agent_via_a2a():
    """The drift detector / outcome attach pattern: call an agent over A2A."""
    from agents.a2a_client import call_agent

    result = call_agent(
        "research",
        "List the top 3 customer voice themes for ICP seg_ecom_leader.",
    )

    # JSON-RPC envelope; the actual task result is in result['result']
    assert "result" in result or "jsonrpc" in result
