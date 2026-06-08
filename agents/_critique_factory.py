"""Critique + Reviser factory.

The Content → Critique → Reviser pattern in the drafting pipeline turned
out to be the single biggest quality lever — first-pass LLM output is
generic until a second agent re-reads it against domain-specific
criteria. Rather than duplicate that pattern as bespoke prompts every
time we want it (Lifecycle Email, Paid Media variants, …), this factory
lets each domain configure ONE spec and get a matched
(critique_agent, reviser_agent) pair.

The pair's contract:
  - `source_state_key` is what the upstream drafter wrote (e.g., "draft",
    "email_sequence", "paid_variants").
  - `critique_state_key` is where the Critique writes its structured
    feedback (e.g., "draft_critique", "email_critique").
  - The Reviser reads BOTH and OVERWRITES `source_state_key` with the
    final version. Downstream agents see only the revised output.

The factory wires up `output_key`, `make_after_callback`,
`make_model_armor_callback`, and the standard agent boilerplate.
"""
from __future__ import annotations

from dataclasses import dataclass

from google.adk.agents import LlmAgent

from agents._common import make_after_callback, make_model_armor_callback
from agents._models import HEAVY, LIGHT, gen_content_config, pick_model


@dataclass
class CritiqueSpec:
    """Configuration for one domain's critique pair.

    Each domain (drafting, lifecycle email, paid media, …) instantiates
    one of these and calls ``build_critique_reviser_pair(spec)`` to get
    back the two agents ready to slot into a SequentialAgent.
    """

    domain: str
    # ADK agent names. Convention: "<domain>_critique" + "<domain>_reviser".
    critique_agent_name: str
    reviser_agent_name: str

    # State keys the agents read + write.
    source_state_key: str          # what the upstream drafter wrote
    critique_state_key: str        # where the critique JSON lands

    # Models.
    critique_model: str = LIGHT   # critic doesn't need frontier
    reviser_model: str = HEAVY         # revising matches drafter

    # Telemetry skill_id (already in the skills collection).
    critique_skill_id: str = "domain_critique"
    reviser_skill_id: str = "domain_revision"

    # Prompts — the heart of the factory. Each domain crafts its own.
    critique_instructions: str = ""
    reviser_instructions: str = ""

    # Optional: extra tools the critique or reviser should have access to
    # (e.g., evidence_validator for fact-checking).
    extra_critique_tools: list = None
    extra_reviser_tools: list = None


def build_critique_reviser_pair(
    spec: CritiqueSpec,
) -> tuple[LlmAgent, LlmAgent]:
    """Build the (critique, reviser) pair from a spec.

    Returns the agents in the order they should be inserted into a
    SequentialAgent: critique first, reviser second. The reviser's
    ``output_key`` matches the drafter's, so its output OVERWRITES the
    first-pass draft in session state.
    """
    if not spec.critique_instructions or not spec.reviser_instructions:
        raise ValueError(
            f"CritiqueSpec for {spec.domain!r} missing instructions. "
            "Each domain must provide its own critique + reviser prompts."
        )

    critique_agent = LlmAgent(
        name=spec.critique_agent_name,
        model=pick_model(spec.critique_model),
        generate_content_config=gen_content_config(pick_model(spec.critique_model)),
        instruction=spec.critique_instructions,
        tools=list(spec.extra_critique_tools or []),
        output_key=spec.critique_state_key,
        after_model_callback=make_model_armor_callback(),
        after_agent_callback=make_after_callback(
            agent_name=spec.critique_agent_name,
            skill_id=spec.critique_skill_id,
            action_type=f"{spec.domain}_critique_op",
        ),
    )

    reviser_agent = LlmAgent(
        name=spec.reviser_agent_name,
        model=pick_model(spec.reviser_model),
        generate_content_config=gen_content_config(pick_model(spec.reviser_model)),
        instruction=spec.reviser_instructions,
        tools=list(spec.extra_reviser_tools or []),
        # CRITICAL: same output_key as upstream drafter — Reviser
        # OVERWRITES the first-pass output. Downstream agents see only
        # the revised version.
        output_key=spec.source_state_key,
        after_model_callback=make_model_armor_callback(),
        after_agent_callback=make_after_callback(
            agent_name=spec.reviser_agent_name,
            skill_id=spec.reviser_skill_id,
            action_type=f"{spec.domain}_revise_op",
        ),
    )

    return critique_agent, reviser_agent


# ---------------------------------------------------------------------------
# Generic prompt scaffolds — domains compose these with their own criteria.
# ---------------------------------------------------------------------------

def critique_prompt_scaffold(
    *,
    domain: str,
    source_state_key: str,
    critique_state_key: str,
    inputs_doc: str,
    criteria: list[tuple[str, str]],   # [(name, description), ...]
) -> str:
    """Build a standard critique prompt for a domain.

    ``criteria`` is a list of (name, description) tuples. Each criterion
    becomes a numbered evaluation point. The output schema is a fixed
    structure shared across domains — that's the point of the factory.
    """
    criteria_md = "\n".join(
        f"{i+1}. **{name}**. {desc}"
        for i, (name, desc) in enumerate(criteria)
    )
    return f"""You are the {domain.title()} Critique Agent. You read what
the {domain} drafter just produced and identify what would make it
materially better, BEFORE the founder sees it.

You are NOT the post-mortem Review Agent (that grades against rubrics
after publishing). You're an in-pipeline editor pushing for ONE more
revision pass. Be specific, be harsh on generic-sounding output, but be
useful — give actionable revision instructions, not vague complaints.

Inputs (in session state):
{inputs_doc}

EVALUATION CRITERIA — score each of these:
{criteria_md}

OUTPUT — single JSON object, becomes state[{critique_state_key!r}]:
{{{{
  "severity": "low" | "medium" | "high",
  "confidence": "high" | "medium" | "low",
  "weaknesses": [
    "<one-sentence specific issue — quote the offending text>"
  ],
  "specific_revisions": [
    "<exact change to make, e.g., 'Replace paragraph 2 sentence 1 with X'>"
  ],
  "kept_strengths": [
    "<things the Reviser must NOT change>"
  ]
}}}}

If the {domain} output is already strong (rare on first pass),
severity="low" and weaknesses=[]. Do NOT invent issues to look thorough
— that wastes the Reviser's time and budget.
"""


def reviser_prompt_scaffold(
    *,
    domain: str,
    source_state_key: str,
    critique_state_key: str,
    inputs_doc: str,
    output_shape_doc: str,
) -> str:
    """Standard reviser prompt — applies the critique to produce a final."""
    return f"""You are the {domain.title()} Reviser Agent. You produce
the FINAL {domain} output by applying the Critique's specific_revisions
to the Drafter's first pass.

Inputs (in session state):
{inputs_doc}

Rules:
1. Apply every ``critique.specific_revisions`` item. These are not
   suggestions; they're the editor's directives.
2. Preserve every ``critique.kept_strengths`` item — do not "improve"
   what already works.
3. If the critique flagged severity="low" with no weaknesses, return
   the original output essentially unchanged.

OUTPUT SHAPE:
{output_shape_doc}

This output OVERWRITES state[{source_state_key!r}]. Downstream agents
see only your final version.
"""
