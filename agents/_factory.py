"""LlmAgent factory — collapses the ~30-50 LOC of construction boilerplate
every agent in this codebase repeats verbatim.

Why this exists: every agent in ``agents/`` was hand-rolling the same
``LlmAgent(...)`` block:

  - resolve ``allowed_for(name)`` / ``required_for(name)`` and wrap the
    instruction via ``with_skills(...)``
  - spread ``mongodb_toolset(mode=..., agent_name=name)``
  - spread ``make_skill_tools(agent_name=name, allowed=...)``
  - wire ``after_model_callback=make_model_armor_callback()``
  - wire ``after_agent_callback=make_after_callback(agent_name=name, ...)``

Repeating that across 13+ files made it easy for ``agent_name=`` arguments
to drift out of sync with the agent's actual name (we hit exactly this
bug in lifecycle_email, where the toolset was attributed to
``_AGENT_NAME`` instead of ``_DRAFTER_NAME``). Centralizing the wiring
means ``name`` is threaded through every call site exactly once.
"""
from __future__ import annotations

from typing import Literal

from google.adk.agents import LlmAgent

from agents._common import make_after_callback, make_model_armor_callback
from agents._mcp import mongodb_toolset
from agents._models import pick_model
from agents._skills_config import allowed_for, required_for
from shared.skills import make_skill_tools, with_skills


def make_llm_agent(
    *,
    name: str,
    instructions: str,
    model: str,
    mode: Literal["read", "write"] | None,
    output_key: str,
    skill_id: str,
    action_type: str,
    channel: str | None = None,
    extra_tools: list | None = None,
    unrestricted_skill_reads: bool = False,
) -> LlmAgent:
    """Construct a standard LlmAgent.

    Wraps the common boilerplate every agent in this codebase needs:
      - ``name`` becomes the agent's identity (used for telemetry +
        provenance attribution; threaded into every helper so the audit
        trail and the agent name never drift apart)
      - ``instructions`` is wrapped via
        ``with_skills(allowed=allowed_for(name), required=required_for(name))``
      - ``tools`` is auto-composed:
        ``mongodb_toolset(mode=mode, agent_name=name)`` (omitted when
        ``mode`` is ``None`` — image_brief / analytics don't talk to
        Mongo directly) + ``make_skill_tools(agent_name=name, allowed=...)``
        + ``extra_tools``
      - ``unrestricted_skill_reads`` decouples the read_skill gate from the
        Tier-1 metadata allowlist. Tier-1 surfacing stays lean
        (``allowed_for(name)``), but read_skill/read_skill_reference are
        permitted for *any* skill. This is for meta-agents like
        self_critique that reason over arbitrary skills they don't load —
        otherwise a read_skill on the skill under review is refused, and the
        model can fixate on the denial instead of falling back to Mongo.
      - ``after_model_callback = make_model_armor_callback()``
      - ``after_agent_callback = make_after_callback(agent_name=name,
        skill_id=skill_id, action_type=action_type, channel=channel)``

    Returns the ``LlmAgent`` ready to bind as a module-level constant.
    """
    allowed = allowed_for(name)
    # Tier-1 metadata stays lean (allowed); the read_skill gate may be wider.
    read_gate = None if unrestricted_skill_reads else allowed

    tools: list = []
    if mode is not None:
        tools.extend(mongodb_toolset(mode=mode, agent_name=name))
    if extra_tools:
        tools.extend(extra_tools)
    tools.extend(make_skill_tools(agent_name=name, allowed=read_gate))

    return LlmAgent(
        name=name,
        model=pick_model(model),
        instruction=with_skills(
            instructions,
            allowed=allowed,
            required=required_for(name),
        ),
        tools=tools,
        output_key=output_key,
        after_model_callback=make_model_armor_callback(),
        after_agent_callback=make_after_callback(
            agent_name=name,
            skill_id=skill_id,
            action_type=action_type,
            channel=channel,
        ),
    )
