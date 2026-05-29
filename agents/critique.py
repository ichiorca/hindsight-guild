"""Critique sub-agent — pre-draft-finalization reviewer.

Runs AFTER Content drafts and BEFORE ImageBrief / Review. Reads the draft
+ research_findings + topic_hint + icp_segment and produces a structured
critique that the Reviser sub-agent uses to refine the draft.

This is a different role from the post-draft Review agent: Review is the
final-shipped grader (rubric scores + recommendation), Critique is the
in-pipeline editor pushing for one more revision. The hand-off:

    Content writes  →  state["draft"]            (first pass)
    Critique reads draft + findings, writes  →  state["critique"]
    Reviser reads draft + critique, OVERWRITES  →  state["draft"]   (final)
    ImageBrief / Review see only the final draft.

Without this step, every draft is one-pass LLM output — first thought is
final thought. Adding a critique → revise round catches the easy wins
(generic framing, missing concrete numbers, weak CTA) before they ship.
"""
from __future__ import annotations

from google.adk.agents import LlmAgent

from agents._common import make_after_callback, make_model_armor_callback
from agents._models import pick_model
from agents._prompts import CRITIQUE_INSTRUCTIONS

critique_agent = LlmAgent(
    name="critique_agent",
    model=pick_model("gemini-3.1-flash-lite"),
    instruction=CRITIQUE_INSTRUCTIONS,
    tools=[],   # pure reading / writing of state; no external lookups
    output_key="critique",
    after_model_callback=make_model_armor_callback(),
    after_agent_callback=make_after_callback(
        agent_name="critique_agent",
        skill_id="draft_critique",
        action_type="critique_op",
    ),
)
