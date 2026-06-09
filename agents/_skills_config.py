"""Per-agent skill allowlists.

Each entry is the set of skills surfaced to that agent at Tier 1 (system
prompt metadata) AND permitted at Tier 2/3 (read_skill / read_skill_reference
FunctionTool calls).

Allowlists serve three purposes:
  1. Keep Tier 1 metadata blocks lean per agent (~80 tokens × N skills).
  2. Govern which agent can invoke which knowledge.
  3. Make the agent's specialty legible — looking at this file tells you
     what each agent "knows".

`house-style` is on every agent's list because it's our voice and applies
to anything customer-facing.

Library is sized to match what's in skills/ — bringing in additional skills
requires both copying the SKILL.md and adding the name here.
"""
from __future__ import annotations

import time

SKILLS_BY_AGENT: dict[str, list[str]] = {
    "research_agent": [
        "house-style",
        "customer-research",
        "competitor-profiling",
    ],
    "content_agent": [
        "house-style",
        "copywriting",
        "copy-editing",         # editing as drafting feedback loop
        "cro",
        "ab-testing",
        "pricing",              # pricing-page copy
        "marketing-psychology", # persuasion principles for emotional pull
    ],
    "review_agent": [
        "house-style",
        "copywriting",
        "copy-editing",         # the editing rules ARE the review rubric
        "cro",
        "marketing-psychology", # diagnose flatness, not just spec compliance
    ],
    "analytics_agent": [
        "house-style",
        "ab-testing",
        "ads-attribution",
        "ads-math",
    ],
    "cmo_planner": [
        "house-style",
        "ab-testing",
        "cro",
        "customer-research",
        "thinking-framework",   # meta-cognitive discipline for the weekly memo
        "content-strategy",     # editorial planning + content pillars
        "launch",               # product-launch playbooks
        "product-marketing",    # master ICP / positioning context
    ],
    "positioning_agent": [
        "house-style",
        "customer-research",
        "competitor-profiling",
        "product-marketing",    # this is THE positioning context skill
        "pricing",              # pricing is positioning made tangible
        "marketing-psychology", # framing, anchoring, narrative architecture
    ],
    "customer_voice_agent": [
        "house-style",
        "customer-research",
    ],
    "lifecycle_email_agent": [
        "house-style",
        "copywriting",
        "emails",               # nurture sequences, drip campaigns, lifecycle flows
        "onboarding",           # welcome / activation sequences
    ],
    "paid_media_agent": [
        "house-style",
        "ads-meta",
        "ads-google",           # #1 paid channel for B2B SaaS
        "ads-creative",
        "ads-attribution",
        "ads-budget",
        "ads-math",
        "ads-audit",            # weighted-scoring account-health audit (0-100)
        "ab-testing",
        "competitor-profiling",
    ],
    "ops_qa_agent": [
        "house-style",
        "ads-attribution",
    ],
    "self_critique_agent": [
        "house-style",
        "ab-testing",
        "customer-research",
        "thinking-framework",   # the meta-discipline IS the self-critique
    ],
    "image_brief_agent": [
        "house-style",
        "ads-creative",
    ],
    "aeo_agent": [
        "house-style",          # voice preservation is non-negotiable during rewrites
        "aeo",                  # the AEO playbook itself
        "copy-editing",         # rewriter needs editing discipline
    ],
}


# ---------------------------------------------------------------------------
# Founder overrides — the Agents page can edit an agent's skill loadout. Edits
# are persisted to the Mongo `agent_skill_overrides` collection and MERGED over
# the code defaults below. We cache them briefly and fail safe to the code
# defaults so a Mongo hiccup never strips an agent of its skills, and so local
# dev / tests without Mongo behave exactly as the static config.
#
# NOTE: allowed_for/required_for run at AGENT CONSTRUCTION (a2a_server import),
# so a saved override takes effect on the next agent (cold) start, not mid-run.
# The roster endpoint reads these fresh, so the UI reflects edits immediately.
# ---------------------------------------------------------------------------
_OVERRIDE_TTL = 30.0
_override_cache: dict[str, dict] = {}
_override_ts = 0.0


def _overrides() -> dict[str, dict]:
    global _override_ts, _override_cache
    now = time.monotonic()
    if _override_cache and (now - _override_ts) < _OVERRIDE_TTL:
        return _override_cache
    try:
        from shared import mongo_tools
        rows = mongo_tools.find("agent_skill_overrides", {}, limit=100)
        _override_cache = {r["_id"]: r for r in rows if r.get("_id")}
        _override_ts = now
    except Exception:
        # Mongo unavailable (local/tests) or any error → defaults win.
        if not _override_cache:
            _override_cache = {}
    return _override_cache


def invalidate_override_cache() -> None:
    """Drop the cache so a just-saved override is reflected immediately."""
    global _override_ts
    _override_ts = 0.0


def available_skills() -> list[str]:
    """Every skill assignable to an agent (the union of all defaults) — the
    catalog the Agents-page skill picker chooses from."""
    catalog: set[str] = set()
    for skills in SKILLS_BY_AGENT.values():
        catalog.update(skills)
    return sorted(catalog)


def allowed_for(agent_name: str) -> list[str]:
    """Return the allowlist for an agent. Empty list = no skills surfaced.
    A founder override (agent_skill_overrides) wins over the code default."""
    ov = _overrides().get(agent_name)
    if ov and isinstance(ov.get("skills_allowed"), list):
        return list(ov["skills_allowed"])
    return SKILLS_BY_AGENT.get(agent_name, ["house-style"])


# Skills the agent MUST read_skill() before producing output. These get
# emitted as a "REQUIRED first steps" block at the top of the agent's
# system prompt via shared.skills.with_skills(..., required=...).
#
# Mandating skills costs token budget (each invocation pulls the body into
# context — ~1-3 KB per skill) and latency (extra LLM round-trip per call),
# so we limit to ~2 per agent: the universal house-style, plus the one
# skill most central to the agent's job. Optional skills in the allowlist
# stay invoke-on-relevance.
REQUIRED_SKILLS_BY_AGENT: dict[str, list[str]] = {
    "research_agent":        ["house-style", "customer-research"],
    "content_agent":         ["house-style", "copywriting"],
    "review_agent":          ["house-style", "copy-editing"],
    "analytics_agent":       ["house-style"],
    "cmo_planner":           ["house-style", "thinking-framework"],
    "positioning_agent":     ["house-style", "product-marketing"],
    "customer_voice_agent":  ["house-style", "customer-research"],
    "lifecycle_email_agent": ["house-style", "emails"],
    "lifecycle_email_drafter": ["house-style", "emails"],
    "paid_media_agent":      ["house-style", "ads-creative"],
    "paid_media_drafter":    ["house-style", "ads-creative"],
    "ops_qa_agent":          ["house-style"],
    "self_critique_agent":   ["house-style", "thinking-framework"],
    "image_brief_agent":     ["house-style", "ads-creative"],
    "aeo_agent":             ["house-style", "aeo"],
}


def required_for(agent_name: str) -> list[str]:
    """Return the must-read_skill set for an agent. Empty list = no mandatory
    invocations (the allowlist is still surfaced at Tier 1). A founder override
    wins over the code default."""
    ov = _overrides().get(agent_name)
    if ov and isinstance(ov.get("skills_required"), list):
        return list(ov["skills_required"])
    return REQUIRED_SKILLS_BY_AGENT.get(agent_name, ["house-style"])
