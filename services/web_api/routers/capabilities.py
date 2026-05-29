"""Capabilities — Agent Skills catalog + usage observability.

Surfaces the open Agent Skills standard data in our system:
  - the filesystem catalog (skills/*/SKILL.md), one row per installed skill
  - per-agent allowlists from agents._skills_config
  - rollups over state.skill_usage telemetry (Tier 2 + Tier 3 loads)

One composite endpoint because the UI consumes it as one page.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter

from shared import mongo_tools

router = APIRouter()


@router.get("/api/capabilities")
def capabilities(days: int = 7):
    """Catalog + usage rollups for the Capabilities page.

    days controls the time window for "loads" and "tokens" aggregations.
    Compare loads to the equivalent previous period for delta arrows.
    """
    from agents._skills_config import SKILLS_BY_AGENT
    from shared.skills import registry

    reg = registry()
    now = datetime.now(UTC)
    period_start = now - timedelta(days=days)
    prev_period_start = now - timedelta(days=days * 2)

    # Catalog from the filesystem registry
    catalog = []
    for name, skill in reg.skills.items():
        fm = skill.frontmatter
        # Reverse the SKILLS_BY_AGENT map to find who's allowed to load this
        allowed_for = sorted(
            agent for agent, allowed in SKILLS_BY_AGENT.items()
            if name in allowed
        )
        # Count references on disk
        ref_count = sum(
            1 for p in skill.skill_dir.glob("references/*.md")
            if p.is_file()
        )
        catalog.append({
            "name": fm.name,
            "description": fm.description,
            "version": fm.version,
            "reference_count": ref_count,
            "allowed_for": allowed_for,
        })
    catalog.sort(key=lambda x: x["name"])

    # Usage rollups from state.skill_usage
    db = mongo_tools.db()
    coll = db["skill_usage"]

    # by_skill: loads this period + previous period + last_used + top agents
    by_skill = _rollup_by_skill(coll, period_start, prev_period_start)

    # by_agent: total loads + skill distribution per agent. Augment with
    # *implicit Tier 1 loads* derived from the actions collection — every
    # time an agent runs, its system prompt includes its allowed skills'
    # metadata, which is a Tier 1 load even though it's never explicitly
    # written to skill_usage. Without this augmentation the heatmap is
    # all-grey for agents that don't make ``read_skill`` tool calls
    # (which is most of them in practice — Tier 2/3 loads only fire when
    # the agent wants the full body / a specific reference).
    by_agent = _rollup_by_agent_augmented(coll, db, SKILLS_BY_AGENT, period_start)

    # Daily timeseries split by tier
    timeseries = _rollup_timeseries(coll, days)

    # Recent activity feed — project only the fields the UI's RecentSkillLoad
    # type declares; the full skill_usage doc carries a ~200-byte _provenance
    # block per row that the page never renders.
    recent = list(coll.find(
        {"ts": {"$gte": period_start}},
        {
            "_id": 0,
            "ts": 1,
            "agent_name": 1,
            "skill_name": 1,
            "tier": 1,
            "reference_path": 1,
            "tokens_estimated": 1,
        },
    ).sort([("ts", -1)]).limit(50))

    # Summary
    total_loads_period = sum(s["loads"] for s in by_skill)
    total_loads_prev = sum(s["loads_prev"] for s in by_skill)
    total_tokens = sum(s["tokens_estimated"] for s in by_skill)
    used_names = {s["skill_name"] for s in by_skill if s["loads"] > 0}
    installed_names = {c["name"] for c in catalog}
    dead_weight = sorted(installed_names - used_names)

    return {
        "window_days": days,
        "catalog": catalog,
        "summary": {
            "installed_skills": len(catalog),
            "loads_period": total_loads_period,
            "loads_prev_period": total_loads_prev,
            "loads_delta_pct": _safe_delta_pct(total_loads_period, total_loads_prev),
            "tokens_estimated": total_tokens,
            "unique_skills_used": len(used_names),
            "dead_weight_count": len(dead_weight),
            "dead_weight": dead_weight[:20],  # cap for payload size
        },
        "by_skill": by_skill,
        "by_agent": by_agent,
        "timeseries": timeseries,
        "recent": recent,
    }


def _safe_delta_pct(curr: float, prev: float) -> float | None:
    if not prev:
        return None
    return ((curr - prev) / prev) * 100.0


def _rollup_by_skill(coll, period_start, prev_period_start):
    """Returns a list ordered by loads desc. Each entry:
       { skill_name, loads, loads_prev, tokens_estimated, last_used_at,
         top_agents: [{agent_name, count}, ...] }
    """
    # Group within the current period
    current_pipeline = [
        {"$match": {"ts": {"$gte": period_start}}},
        {"$group": {
            "_id": "$skill_name",
            "loads": {"$sum": 1},
            "tokens_estimated": {"$sum": {"$ifNull": ["$tokens_estimated", 0]}},
            "last_used_at": {"$max": "$ts"},
            "agents": {"$push": "$agent_name"},
        }},
    ]
    current = {r["_id"]: r for r in coll.aggregate(current_pipeline)}

    # Previous-period totals only (for delta)
    prev_pipeline = [
        {"$match": {"ts": {"$gte": prev_period_start, "$lt": period_start}}},
        {"$group": {"_id": "$skill_name", "loads": {"$sum": 1}}},
    ]
    prev = {r["_id"]: r["loads"] for r in coll.aggregate(prev_pipeline)}

    out = []
    for skill_name, doc in current.items():
        # Top agents = the agents responsible for the most loads of this skill
        from collections import Counter
        agent_counts = Counter(doc["agents"]).most_common(3)
        out.append({
            "skill_name": skill_name,
            "loads": doc["loads"],
            "loads_prev": prev.get(skill_name, 0),
            "tokens_estimated": doc["tokens_estimated"],
            "last_used_at": doc["last_used_at"],
            "top_agents": [
                {"agent_name": a, "count": c} for a, c in agent_counts
            ],
        })
    out.sort(key=lambda x: x["loads"], reverse=True)
    return out


def _rollup_by_agent_augmented(coll, db, skills_by_agent: dict, period_start):
    """The agent × skill matrix combines TWO sources:

      - **Reference data**: ``skills_by_agent`` — the static allowlist
        from agents/_skills_config.py. Every (agent, skill) pair in this
        map is a structurally-valid cell, even with zero loads. The
        matrix should always render every allowed cell so the UI shows
        the team's CAPABILITY (which agent CAN load which skill).

      - **Transactional weighting**: actions + skill_usage from Mongo
        over the time window. Each ``actions`` row credits one Tier 1
        load to every skill in the agent's allowlist (because Tier 1
        skills ship as metadata in the agent's system prompt at boot,
        regardless of whether the LLM explicitly read them). Each
        ``skill_usage`` row credits one Tier 2/3 load (explicit
        ``read_skill`` / ``read_skill_reference`` tool calls).

    Returned shape:
      [{ agent_name, total_loads, skill_loads: [{skill_name, count}] }]
    — total_loads can be 0 for agents that haven't run yet, but
    skill_loads still lists their entire allowlist with count=0 so the
    heatmap renders structural reference data.
    """
    # Step 1 — explicit Tier 2/3 loads from skill_usage
    explicit = _rollup_by_agent(coll, period_start)
    explicit_by_agent: dict[str, dict] = {
        a["agent_name"]: a for a in explicit
    }

    # Step 2 — count actions per agent in window. Use the same
    # sub-agent-aliasing as /api/agents so the parent name accrues the
    # work its sub-agents did (lifecycle_email_drafter → lifecycle_email_agent).
    SUB_AGENT_TO_PARENT = {
        "lifecycle_email_drafter":  "lifecycle_email_agent",
        "lifecycle_email_critique": "lifecycle_email_agent",
        "lifecycle_email_reviser":  "lifecycle_email_agent",
        "paid_media_drafter":       "paid_media_agent",
        "paid_media_critique":      "paid_media_agent",
        "paid_media_reviser":       "paid_media_agent",
    }
    actions_by_agent: dict[str, int] = {}
    for r in db["actions"].aggregate([
        {"$match": {"ts": {"$gte": period_start}}},
        {"$group": {"_id": "$agent", "n": {"$sum": 1}}},
    ]):
        canonical = SUB_AGENT_TO_PARENT.get(r["_id"], r["_id"])
        actions_by_agent[canonical] = actions_by_agent.get(canonical, 0) + r["n"]

    # Step 3 — build the structural matrix: every agent × every skill
    # in its allowlist is a cell, even when count=0. Then layer on
    # actions-derived weights + explicit skill_usage counts.
    out: list[dict] = []
    for agent_id, allowed_skills in skills_by_agent.items():
        n_actions = actions_by_agent.get(agent_id, 0)
        # Start with the structural allowlist; every cell starts at the
        # action count (Tier 1 weighting). Zero actions = zero-weight
        # cells but they STILL appear in the heatmap as structural data.
        skill_counts: dict[str, int] = {s: n_actions for s in allowed_skills}

        # Layer on explicit Tier 2/3 reads from skill_usage. These can
        # surface skills NOT in the static allowlist (e.g., if an agent
        # explicitly read a skill outside its allowlist for some
        # one-off reason). Add them as additional cells.
        explicit_slot = explicit_by_agent.get(agent_id)
        if explicit_slot:
            for sl in explicit_slot["skill_loads"]:
                skill_counts[sl["skill_name"]] = (
                    skill_counts.get(sl["skill_name"], 0) + sl["count"]
                )

        out.append({
            "agent_name": agent_id,
            "total_loads": sum(skill_counts.values()),
            "skill_loads": sorted(
                [{"skill_name": k, "count": v} for k, v in skill_counts.items()],
                key=lambda x: x["count"], reverse=True,
            ),
        })

    out.sort(key=lambda x: x["total_loads"], reverse=True)
    return out


def _rollup_by_agent(coll, period_start):
    """Returns: [{ agent_name, total_loads, skill_loads: [{skill, count}] }]"""
    pipeline = [
        {"$match": {"ts": {"$gte": period_start}}},
        {"$group": {
            "_id": {"agent": "$agent_name", "skill": "$skill_name"},
            "count": {"$sum": 1},
        }},
    ]
    raw = list(coll.aggregate(pipeline))
    by_agent: dict[str, dict] = {}
    for r in raw:
        agent = r["_id"]["agent"]
        skill = r["_id"]["skill"]
        slot = by_agent.setdefault(agent, {"agent_name": agent, "total_loads": 0, "skill_loads": []})
        slot["total_loads"] += r["count"]
        slot["skill_loads"].append({"skill_name": skill, "count": r["count"]})

    out = list(by_agent.values())
    for a in out:
        a["skill_loads"].sort(key=lambda x: x["count"], reverse=True)
    out.sort(key=lambda x: x["total_loads"], reverse=True)
    return out


def _rollup_timeseries(coll, days):
    """Daily loads split by tier (2 + 3) over the period."""
    start = datetime.now(UTC) - timedelta(days=days)
    pipeline = [
        {"$match": {"ts": {"$gte": start}}},
        {"$group": {
            "_id": {
                "day": {"$dateToString": {"format": "%Y-%m-%d", "date": "$ts"}},
                "tier": "$tier",
            },
            "loads": {"$sum": 1},
        }},
    ]
    raw = list(coll.aggregate(pipeline))
    by_day: dict[str, dict] = {}
    for r in raw:
        day = r["_id"]["day"]
        tier = r["_id"]["tier"]
        slot = by_day.setdefault(day, {"day": day, "tier_2": 0, "tier_3": 0})
        if tier == 2:
            slot["tier_2"] = r["loads"]
        elif tier == 3:
            slot["tier_3"] = r["loads"]
    return sorted(by_day.values(), key=lambda x: x["day"])
