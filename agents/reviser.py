"""Reviser sub-agent — applies the critique to produce a final draft.

Reads {draft, critique, research_findings, topic_hint, icp_segment} and
overwrites state["draft"] with a revised version. This is the only
sub-agent in the pipeline that shares an output_key with another agent —
Content drafts state["draft"], Reviser overwrites it. Downstream
(ImageBrief, Review, Finalizer) sees only the final revised draft.

Output shape matches Content's:
  - linkedin / email / blog: plain body text
  - substack: {headline, subtitle, body_markdown} JSON object
"""
from __future__ import annotations

from google.adk.agents import LlmAgent

from agents._common import make_after_callback, make_model_armor_callback
from agents._models import HEAVY, pick_model
from agents._prompts import REVISER_INSTRUCTIONS

reviser_agent = LlmAgent(
    name="reviser_agent",
    model=pick_model(HEAVY),   # heavy lift — same model as Content
    instruction=REVISER_INSTRUCTIONS,
    tools=[],
    output_key="draft",   # OVERWRITES Content's first-pass draft
    after_model_callback=make_model_armor_callback(),
    after_agent_callback=make_after_callback(
        agent_name="reviser_agent",
        skill_id="draft_revision",
        action_type="revise_op",
    ),
)
