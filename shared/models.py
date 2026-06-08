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

Defaults are GA on Vertex AI for the AI-Studio-style ``gen-lang-client-*``
projects (gemini-3.x is not available there). A project WITH 3.x access just
sets ``MODEL_HEAVY=gemini-3.5-flash`` / ``MODEL_LIGHT=gemini-3.1-flash-lite``
in its deploy env — no code change.
"""
from __future__ import annotations

import os

HEAVY = "heavy"
LIGHT = "light"

# The only model literals in the codebase live here.
_DEFAULTS = {
    HEAVY: "gemini-2.5-flash",
    LIGHT: "gemini-2.5-flash-lite",
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
