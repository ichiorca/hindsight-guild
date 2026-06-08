"""Review Agent — pipeline node #4 (after Research, Content, ImageBrief).

Reads {draft}, {research_findings}, {image} from state. Qualitative flagging
only — numeric rubric scores come from the after_agent_callback via
Vertex AI Eval Service.
"""
from __future__ import annotations

from agents._evidence_tool import evidence_validator_tool
from agents._factory import make_llm_agent
from agents._models import LIGHT
from agents._prompts import REVIEW_INSTRUCTIONS
from agents.web_search import web_search_tool

# Read-only intent is enforced per-call inside shared/rubrics.py via
# secret_name="mongo_uri_readonly" (see C5 in the codebase review).

review_agent = make_llm_agent(
    name="review_agent",
    instructions=REVIEW_INSTRUCTIONS,
    model=LIGHT,
    mode="read",
    output_key="review",
    skill_id="rubric_review",
    action_type="review_op",
    extra_tools=[
        evidence_validator_tool,   # check claims across approved + voice + ICP
        web_search_tool,           # independent verification for unsourced facts
    ],
)
