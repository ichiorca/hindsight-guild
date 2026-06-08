"""Paid Media Analyst Agent — reads ad performance, drafts paused variants,
recommends stop-loss pauses.

Cannot spend, cannot unpause, cannot change budgets. New creative variants
land in paid_variants with status='paused'. Stop-loss recommendations
become ops_incidents.

Architecture: Drafter → Critique → Reviser (factory-built pair).
  - Drafter: pulls ad performance + customer voice + approved claims;
    assembles a structured action plan in state['paid_media_action'] —
    does NOT persist yet.
  - Critique: reads the plan + evaluates variants against ICP fit, claim
    evidence (via validate_claim), platform format rules, and stop-loss
    rationale. Writes state['paid_critique'].
  - Reviser: applies the critique, then PERSISTS final variants via
    ``mongodb.insert-one`` into paid_variants (status=paused) and stop-loss
    incidents into ops_incidents.

This split exists because Paid Media variants used to be inserted on first
pass — generic / unsourced ad copy was hitting the queue before any second
look. Now the founder only sees critiqued, revised variants.
"""
from __future__ import annotations

import os

from google.adk.agents import LlmAgent
from google.adk.agents.sequential_agent import SequentialAgent
from google.adk.tools import FunctionTool

from agents._common import make_after_callback, make_model_armor_callback
from agents._critique_factory import (
    CritiqueSpec,
    build_critique_reviser_pair,
    critique_prompt_scaffold,
    reviser_prompt_scaffold,
)
from agents._evidence_tool import evidence_validator_tool
from agents._mcp import mongodb_toolset
from agents._models import HEAVY, pick_model
from agents._prompts import PAID_MEDIA_INSTRUCTIONS
from agents._skills_config import allowed_for, required_for
from shared.skills import make_skill_tools, with_skills

_AGENT_NAME = "paid_media_agent"
_DRAFTER_NAME = "paid_media_drafter"
_ALLOWED_SKILLS = allowed_for(_AGENT_NAME)

PROJECT_ID = os.environ.get("PROJECT_ID", "hindsight-guild-mvp")

# Shared helper degrades gracefully in LOCAL_DEV (returns [] instead of
# raising). Previously the per-agent bigquery_query raised hard on
# missing ADC, taking out paid_media + self_critique + cmo_planner in
# the e2e run. See shared/bigquery_helper.py.
from shared.bigquery_helper import bigquery_query  # noqa: E402

bigquery_query_tool = FunctionTool(func=bigquery_query)


# ---------------------------------------------------------------------------
# Step 1 — Drafter. Same tools as before; prompt updated in _prompts.py to
# direct the agent to assemble a structured plan in state instead of
# inserting to Mongo on first pass.
# ---------------------------------------------------------------------------

paid_media_drafter = LlmAgent(
    name=_DRAFTER_NAME,
    model=pick_model(HEAVY),
    instruction=with_skills(PAID_MEDIA_INSTRUCTIONS, allowed=_ALLOWED_SKILLS,
                            required=required_for(_DRAFTER_NAME)),
    tools=[
        # Read-only mongo for the drafter (no inserts in first pass; the
        # reviser owns persistence after critique).
        *mongodb_toolset(mode="read", agent_name=_AGENT_NAME),
        bigquery_query_tool,
        *make_skill_tools(agent_name=_AGENT_NAME, allowed=_ALLOWED_SKILLS),
    ],
    output_key="paid_media_action",
    after_model_callback=make_model_armor_callback(),
    after_agent_callback=make_after_callback(
        agent_name=_DRAFTER_NAME,
        skill_id="paid_media_review",
        action_type="paid_media_op",
    ),
)


# ---------------------------------------------------------------------------
# Step 2 + 3 — Critique + Reviser pair.
# ---------------------------------------------------------------------------

_PAID_CRITIQUE_CRITERIA = [
    (
        "icp_fit",
        "For each variant, the headline + body must speak to the ICP's "
        "named role and named pain. Flag generic appeals ('grow faster', "
        "'optimize your funnel') with no role-specific signal.",
    ),
    (
        "claim_evidence",
        "For every hard claim in headline or body (stats, named "
        "outcomes, named comparisons), call "
        "``validate_claim(claim_text, icp_segment=<icp>)``. Verdict "
        "'unsupported' = flag 'unsourced_claim'. Paid copy hits a lot "
        "of eyeballs — unsourced claims are the highest-risk failure mode.",
    ),
    (
        "platform_format_rules",
        "Per-platform constraints:\n"
        "  - LinkedIn single-image: headline ≤ 70 chars, body ≤ 150 chars\n"
        "  - Meta single-image: headline ≤ 40 chars, primary text ≤ 125 chars\n"
        "  - Google search: 30 / 30 / 30 for headlines, 90 for descriptions\n"
        "Flag any variant exceeding its platform's caps.",
    ),
    (
        "stop_loss_rationale",
        "For each stop_loss_incident, the rationale MUST cite the actual "
        "metric values (spend, ctr, conversions, hours-running). Flag any "
        "incident that says 'underperforming' without numbers — the founder "
        "needs the data to action it.",
    ),
    (
        "variant_diversity",
        "If drafter proposed 2-3 variants per ad set, they should test "
        "DIFFERENT axes (different angle, different proof, different CTA "
        "format) — not three near-duplicates with comma tweaks. Flag "
        "lazy variants that don't actually create a test.",
    ),
    (
        "compliance_red_flags",
        "Flag any variant making medical / financial / legal outcome "
        "claims, ALL CAPS shouting, 'guaranteed' wording, or implied "
        "endorsements from public figures or brands not in our messaging "
        "library.",
    ),
]

_PAID_INPUTS_DOC = """
- state['paid_media_action'] — the Drafter's proposed action plan, a
  JSON object with keys:
    {
      "variants_proposed": [
        {"platform": "...", "ad_set_id": "...", "headline": "...",
         "body": "...", "cta": "...", "rationale": "...", "test_axis": "..."}
      ],
      "stop_loss_incidents": [
        {"campaign_id": "...", "platform": "...", "severity": "high|medium",
         "rationale": "<must cite metric values>"}
      ],
      "campaigns_reviewed": <int>
    }
- state['icp_segment'] — the target ICP id (use with ``validate_claim``).
"""

_PAID_OUTPUT_SHAPE = """
A single JSON object — same shape as state['paid_media_action'] above —
PLUS a confidence field. After producing the JSON, call
``mongodb.insert-one`` once per FINAL variant into ``paid_variants``
with ``status='paused'`` and once per final incident into
``ops_incidents`` with ``status='open'``. Variants the Critique flagged
as ``compliance_red_flags`` MUST be dropped entirely, not softened.

{
  "variants_proposed": [ /* final, revised list — drop the bad ones */ ],
  "stop_loss_incidents": [ /* final list with rationale fixed */ ],
  "campaigns_reviewed": <int>,
  "variants_dropped": <int>,    // how many compliance-flagged variants you removed
  "confidence": "high" | "medium" | "low"
}
"""

_paid_critique_agent, _paid_reviser_agent = build_critique_reviser_pair(
    CritiqueSpec(
        domain="paid_media",
        critique_agent_name="paid_media_critique",
        reviser_agent_name="paid_media_reviser",
        source_state_key="paid_media_action",
        critique_state_key="paid_critique",
        critique_skill_id="paid_critique",
        reviser_skill_id="paid_revision",
        critique_instructions=critique_prompt_scaffold(
            domain="paid_media",
            source_state_key="paid_media_action",
            critique_state_key="paid_critique",
            inputs_doc=_PAID_INPUTS_DOC,
            criteria=_PAID_CRITIQUE_CRITERIA,
        ),
        reviser_instructions=reviser_prompt_scaffold(
            domain="paid_media",
            source_state_key="paid_media_action",
            critique_state_key="paid_critique",
            inputs_doc=_PAID_INPUTS_DOC,
            output_shape_doc=_PAID_OUTPUT_SHAPE,
        ),
        extra_critique_tools=[evidence_validator_tool],
        # Reviser needs write access — it persists the final variants +
        # incidents to Mongo. (Drafter is read-only by design.)
        extra_reviser_tools=[*mongodb_toolset(mode="write", agent_name=_AGENT_NAME)],
    )
)


# ---------------------------------------------------------------------------
# Composed Sequential pipeline. Exported under the original symbol name so
# the A2A server + registry don't need to change.
# ---------------------------------------------------------------------------

paid_media_agent = SequentialAgent(
    name=_AGENT_NAME,
    description=(
        "Paid Media pipeline: Drafter → Critique → Reviser. Drafter "
        "assembles a structured plan (variants + stop-loss incidents) in "
        "state['paid_media_action'] without persisting. Critique evaluates "
        "ICP fit, claim evidence, platform format rules, and compliance. "
        "Reviser applies fixes and ONLY THEN persists final variants to "
        "paid_variants (status=paused) and incidents to ops_incidents."
    ),
    sub_agents=[
        paid_media_drafter,
        _paid_critique_agent,
        _paid_reviser_agent,
    ],
)
