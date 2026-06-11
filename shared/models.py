"""Central, env-driven model registry — the ONE place model names live.

No agent or service hardcodes a Gemini model string. Code references a
semantic TIER; the concrete model for each tier is resolved here, from the
environment, so a deployment can target whatever its project/region actually
serves without touching code.

Tiers
-----
  HEAVY  — frontier drafting / reasoning (Content, Reviser, CMO, Positioning,
           Paid Media, Lifecycle Email, Self-Critique, AEO reviser).
  LIGHT  — cheap, near-deterministic work (Research, Review, ImageBrief,
           Analytics, Ops/QA, Customer Voice, Critique, edit classification,
           the eval judge).

Resolution order for a tier
---------------------------
  1. ``LOCAL_OVERRIDE_MODEL`` — if set, forces BOTH tiers to that one model
     (handy when a single key/project can only reach one model).
  2. ``MODEL_HEAVY`` / ``MODEL_LIGHT`` — per-tier override.
  3. Built-in default (below).

Defaults are the Gemini 3 generation — GA on both Vertex AI and the Gemini
Developer API. The deploy (``deploy/env.sh``) sets both tiers explicitly; a
project that needs different models just sets ``MODEL_HEAVY``/``MODEL_LIGHT``
in its deploy env — no code change.
"""
from __future__ import annotations

import os

HEAVY = "heavy"
LIGHT = "light"

# The only model literals in the codebase live here.
_DEFAULTS = {
    HEAVY: "gemini-3.5-flash",
    LIGHT: "gemini-3.1-flash-lite",
}
_ENV_VAR = {HEAVY: "MODEL_HEAVY", LIGHT: "MODEL_LIGHT"}

# Map any legacy literal model name a caller might still pass to its tier, so
# resolution stays centralized even if a literal sneaks back in.
_LEGACY = {
    "gemini-3.5-flash": HEAVY,
    "gemini-2.5-flash": HEAVY,
    "gemini-3.1-flash-lite": LIGHT,
    "gemini-2.5-flash-lite": LIGHT,
}


def model_for(tier: str) -> str:
    """Resolve a TIER (``HEAVY``/``LIGHT``) — or a legacy literal — to the
    concrete model name for this environment."""
    override = os.environ.get("LOCAL_OVERRIDE_MODEL")
    if override:
        return override
    tier = _LEGACY.get(tier, tier)
    if tier in _DEFAULTS:
        return os.environ.get(_ENV_VAR[tier], _DEFAULTS[tier])
    # Unknown value (not a tier, not a known literal) → pass through so a
    # caller can still force an exact model name if it ever needs to.
    return tier


def heavy() -> str:
    return model_for(HEAVY)


def light() -> str:
    return model_for(LIGHT)


def pick_model(default: str) -> str:
    """Back-compat entry point. Accepts a tier constant or a legacy literal;
    both resolve through :func:`model_for`."""
    return model_for(default)


def gen_content_config(resolved_model: str, *, has_tools: bool = True):
    """A ``GenerateContentConfig`` that makes ADK function calling robust across
    Gemini model families. ``resolved_model`` is the concrete model name (post
    :func:`model_for`).

    - ``tool_config`` → AUTO: ask for explicit, structured function calls.
    - gemini-2.5* emit *compositional* tool calls as code (``print(read_skill(
      ...))``) plus ``<ctrl>`` thinking tokens when thinking is on (the default).
      ADK 1.x can't parse those → ``MALFORMED_FUNCTION_CALL`` → dropped call →
      empty draft. So for the 2.5 family we constrain thinking:
        * gemini-2.5-flash / -flash-lite → thinking_budget=0 (fully off).
        * gemini-2.5-pro → thinking_budget=128 (the minimum; pro REJECTS 0 with
          HTTP 400 "does not support setting thinking_budget to 0").
      gemini-3.x parses tool calls fine and keeps its native thinking, so we
      leave its config alone.

    Returns a config safe to attach to any agent regardless of which model the
    environment resolves to.
    """
    from google.genai import types as t

    # Only attach a function-calling config when the agent actually HAS
    # tools. Vertex tolerates tool_config without function_declarations;
    # the Gemini Developer API rejects it with 400 INVALID_ARGUMENT
    # ("Function calling config is set without function_declarations"),
    # which crashed the tool-less Reviser/AEO-Reviser stages mid-pipeline.
    cfg = t.GenerateContentConfig()
    if has_tools:
        cfg.tool_config = t.ToolConfig(
            function_calling_config=t.FunctionCallingConfig(
                mode=t.FunctionCallingConfigMode.AUTO,
            ),
        )
    m = str(resolved_model)
    if m.startswith("gemini-2.5"):
        # pro can't disable thinking; flash/-lite can.
        budget = 128 if "pro" in m else 0
        cfg.thinking_config = t.ThinkingConfig(thinking_budget=budget)
    return cfg
