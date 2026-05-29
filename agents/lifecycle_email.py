"""Lifecycle Email Agent — drafts nurture sequences, never autosends.

Each sequence enters email_sequences with status='draft'. The founder
activates from the UI. Approved sequences become status='approved' (Phase 2
would add a sender service that promotes 'approved' → 'sending' → 'done').

Hard governance: this agent cannot call any send tool, only mongodb writes.

Architecture: Drafter → Critique → Reviser (factory-built pair). Drafter
writes ``state['email_sequence']`` (the JSON sequence object). Critique
reads it and writes ``state['email_critique']``. Reviser reads both and
OVERWRITES ``state['email_sequence']`` with the final, edited version.
The Mongo insert happens inside the Reviser so only the final version
hits ``email_sequences``.
"""
from __future__ import annotations

from google.adk.agents import LlmAgent
from google.adk.agents.sequential_agent import SequentialAgent

from agents._common import make_after_callback, make_model_armor_callback
from agents._critique_factory import (
    CritiqueSpec,
    build_critique_reviser_pair,
    critique_prompt_scaffold,
    reviser_prompt_scaffold,
)
from agents._evidence_tool import evidence_validator_tool
from agents._mcp import mongodb_toolset
from agents._models import pick_model
from agents._prompts import LIFECYCLE_EMAIL_INSTRUCTIONS
from agents._skills_config import allowed_for, required_for
from shared.skills import make_skill_tools, with_skills

_AGENT_NAME = "lifecycle_email_agent"
_DRAFTER_NAME = "lifecycle_email_drafter"
_ALLOWED_SKILLS = allowed_for(_AGENT_NAME)


# ---------------------------------------------------------------------------
# Step 1 — Drafter (renamed; same prompt, same tools as before).
# ---------------------------------------------------------------------------

lifecycle_email_drafter = LlmAgent(
    name=_DRAFTER_NAME,
    model=pick_model("gemini-3.5-flash"),
    instruction=with_skills(LIFECYCLE_EMAIL_INSTRUCTIONS, allowed=_ALLOWED_SKILLS,
                            required=required_for(_DRAFTER_NAME)),
    tools=[
        *mongodb_toolset(mode="write", agent_name=_DRAFTER_NAME),
        *make_skill_tools(agent_name=_AGENT_NAME, allowed=_ALLOWED_SKILLS),
    ],
    output_key="email_sequence",
    after_model_callback=make_model_armor_callback(),
    after_agent_callback=make_after_callback(
        agent_name=_DRAFTER_NAME,
        skill_id="nurture_email_sequence",
        action_type="draft_email_sequence",
        channel="email",
    ),
)


# ---------------------------------------------------------------------------
# Step 2 + 3 — Critique + Reviser pair via factory.
# ---------------------------------------------------------------------------

_EMAIL_CRITIQUE_CRITERIA = [
    (
        "subject_hooks",
        "Each step's ``subject`` must be under 40 chars (mobile preview "
        "clip line). Flag every subject that exceeds. Also flag generic "
        "subjects ('Quick question', 'Following up') — they're spam-bait.",
    ),
    (
        "body_length",
        "Each step's body should be 80-140 words. Flag steps that are "
        "too long (rambling) or too short (no substance).",
    ),
    (
        "single_cta",
        "Each body must have exactly ONE call-to-action. Multiple CTAs "
        "split attention. Flag steps with 0 or 2+ CTAs.",
    ),
    (
        "icp_specificity",
        "Body language must reference the ICP's job-to-be-done, NOT a "
        "generic 'streamline your workflow' line. If you could swap "
        "ICPs without changing the body, that's a critical fail.",
    ),
    (
        "claim_evidence",
        "For every hard claim (stat, named comparison, social proof), "
        "call ``validate_claim(claim_text, icp_segment=<icp>)``. If the "
        "verdict is 'unsupported', flag 'unsourced_claim' with the exact "
        "phrase. The Reviser will either soften it or drop it.",
    ),
    (
        "spam_triggers",
        "Flag deliverability red flags in subjects and bodies: ALL CAPS, "
        "money symbols, 'free' near 'now', 'guaranteed', exclamation "
        "stacks, RE: / FWD: spoofing prefixes.",
    ),
    (
        "sequence_arc",
        "Across the 3-5 steps, there should be an arc: introduce → "
        "deepen → call-to-action. Flag a sequence where every step is "
        "the same shape (e.g., three pitches in a row).",
    ),
    (
        "delay_pacing",
        "Verify delay_days makes sense: step 1 should be 0; subsequent "
        "delays should not all be 1 (too aggressive) or all be 14+ (too "
        "passive). Flag pacing that's clearly wrong for nurture.",
    ),
]

_EMAIL_INPUTS_DOC = """
- state['email_sequence'] — the Drafter's first-pass sequence (a JSON
  object with keys ``_id``, ``sequence_name``, ``icp_segment``, ``steps``).
  ``steps`` is a list of {step_num, subject, body, cta, delay_days}.
- state['icp_segment'] — the ICP segment ID (use as the ``icp_segment``
  arg to ``validate_claim``).
"""

_EMAIL_OUTPUT_SHAPE = """
A single JSON object with the SAME shape as state['email_sequence']:
{
  "_id": "<slug>",
  "sequence_name": "<name>",
  "icp_segment": "<id>",
  "steps": [
    {"step_num": 1, "subject": "<under 40 chars>", "body": "<80-140 words>",
     "cta": "<single CTA url or label>", "delay_days": 0},
    ...
  ],
  "status": "draft",
  "drafted_by": "lifecycle_email_agent",
  "confidence": "high" | "medium" | "low"
}

Apply every ``email_critique.specific_revisions`` item. If the critique
flagged ``unsourced_claim`` for a specific phrase, either remove the
phrase or soften it to language that doesn't make a hard factual
assertion ('teams often see...' rather than 'teams see 2.3x more...').

Do NOT call mongodb.insert-one — the SequentialAgent's downstream layer
handles persistence after the founder's approval flow.
"""

_email_critique_agent, _email_reviser_agent = build_critique_reviser_pair(
    CritiqueSpec(
        domain="lifecycle_email",
        critique_agent_name="lifecycle_email_critique",
        reviser_agent_name="lifecycle_email_reviser",
        source_state_key="email_sequence",
        critique_state_key="email_critique",
        critique_skill_id="email_critique",
        reviser_skill_id="email_revision",
        critique_instructions=critique_prompt_scaffold(
            domain="lifecycle_email",
            source_state_key="email_sequence",
            critique_state_key="email_critique",
            inputs_doc=_EMAIL_INPUTS_DOC,
            criteria=_EMAIL_CRITIQUE_CRITERIA,
        ),
        reviser_instructions=reviser_prompt_scaffold(
            domain="lifecycle_email",
            source_state_key="email_sequence",
            critique_state_key="email_critique",
            inputs_doc=_EMAIL_INPUTS_DOC,
            output_shape_doc=_EMAIL_OUTPUT_SHAPE,
        ),
        extra_critique_tools=[evidence_validator_tool],
        extra_reviser_tools=[],
    )
)


# ---------------------------------------------------------------------------
# Composed Sequential pipeline — exported under the original symbol name so
# downstream (a2a_server, registry) doesn't need to change.
# ---------------------------------------------------------------------------

lifecycle_email_agent = SequentialAgent(
    name=_AGENT_NAME,
    description=(
        "Lifecycle Email pipeline: Drafter → Critique → Reviser. Produces "
        "a nurture sequence object in state['email_sequence']. Reviser "
        "overwrites the Drafter's first pass; downstream sees only the "
        "final, edited sequence."
    ),
    sub_agents=[
        lifecycle_email_drafter,
        _email_critique_agent,
        _email_reviser_agent,
    ],
)
