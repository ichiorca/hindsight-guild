"""Positioning Agent — maintains ICP definitions and messaging library.

Writes proposals (never auto-applies). The founder reviews them in the UI
and approves/rejects. Approved proposals get promoted into messaging_library
with status='approved'.
"""
from __future__ import annotations

from agents._evidence_tool import evidence_validator_tool
from agents._factory import make_llm_agent
from agents._models import HEAVY
from agents._prompts import POSITIONING_INSTRUCTIONS
from agents.web_search import web_search_tool

# mongo_tools defaults to mongo_uri_writer; explicit setter removed (see C5).

positioning_agent = make_llm_agent(
    name="positioning_agent",
    instructions=POSITIONING_INSTRUCTIONS,
    model=HEAVY,
    mode="write",
    output_key="positioning_proposals",
    skill_id="positioning_maintenance",
    action_type="positioning_op",
    extra_tools=[
        evidence_validator_tool,   # validate proposed claims before promoting
        web_search_tool,           # source new positioning angles + competitor moves
    ],
)
