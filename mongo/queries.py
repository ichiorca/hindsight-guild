"""Reference queries used across the codebase.

These are the canonical shape of the queries that:
  - the CMO Planner expects when it calls mongodb_mcp.find()
  - the workers (outcome_attach, promotion_gate, etc.) use directly
  - the founder runs from `python -m mongo.cli`

Putting them in one place means the agent prompts can reference them by name
and the schema-level index design (mongo/schema.py) can be reviewed against
real query patterns.
"""
from __future__ import annotations

from agents._schema_constants import Coll, Status
from shared import mongo_tools

# ---------------------------------------------------------------------------
# experiments
# ---------------------------------------------------------------------------

def running_experiments(channel: str | None = None,
                         icp_segment: str | None = None) -> list[dict]:
    """Hot path for the CMO Planner. Index: (state, created_at)."""
    q: dict = {"state": "running"}
    if channel:
        q["channel"] = channel
    if icp_segment:
        q["icp_segment"] = icp_segment
    return mongo_tools.find_sorted(Coll.EXPERIMENTS, q,
                                    sort=[("created_at", -1)], limit=50)


def recent_decisions(limit: int = 10) -> list[dict]:
    """For the weekly memo's 'what worked / didn't' sections."""
    return mongo_tools.find_sorted(Coll.EXPERIMENTS, {"state": "decided"},
                                    sort=[("decided_at", -1)], limit=limit)


def drift_investigations() -> list[dict]:
    """Investigations auto-opened by services/drift_detect."""
    return mongo_tools.find(Coll.EXPERIMENTS,
                             {"tags": "drift", "state": "running"}, limit=50)


# ---------------------------------------------------------------------------
# skills
# ---------------------------------------------------------------------------

def skill(skill_id: str) -> dict | None:
    return mongo_tools.find_one(Coll.SKILLS, {"_id": skill_id})


def skills_with_self_critique_proposal() -> list[dict]:
    return mongo_tools.find(Coll.SKILLS,
                             {"self_critique_proposal.status": Status.AWAITING_HUMAN_REVIEW},
                             limit=20)


def skills_with_promotion_request() -> list[dict]:
    return mongo_tools.find(Coll.SKILLS,
                             {"promotion_request.status": Status.AWAITING_APPROVAL},
                             limit=20)


# ---------------------------------------------------------------------------
# negative_examples (rubric grounding)
# ---------------------------------------------------------------------------

def recent_negatives(channel: str, category: str, limit: int = 3) -> list[dict]:
    """The hot path. shared/rubrics.py calls this every draft.

    Index: (rejection_category, channel) + (ts DESC) — see mongo/schema.py.
    """
    return mongo_tools.find_sorted(
        Coll.NEGATIVE_EXAMPLES,
        {"channel": channel, "rejection_category": category},
        sort=[("ts", -1)],
        limit=limit,
    )


# ---------------------------------------------------------------------------
# messaging_library
# ---------------------------------------------------------------------------

def approved_claims_for(icp_segment: str) -> list[dict]:
    """Used by Content Agent at draft time and Review Agent at validate time."""
    return mongo_tools.find(
        Coll.MESSAGING_LIBRARY,
        {"applies_to_icp": icp_segment, "status": Status.APPROVED},
        limit=50,
    )


# ---------------------------------------------------------------------------
# attribution_map
# ---------------------------------------------------------------------------

def lookup_attribution(telemetry_id: str) -> dict | None:
    """outcome_attach uses this to find the external_id for a published action."""
    return mongo_tools.find_one(Coll.ATTRIBUTION_MAP, {"telemetry_id": telemetry_id})


def record_attribution(telemetry_id: str, mapping: dict) -> None:
    """Agents call this after publish/send to record where the action landed."""
    mongo_tools.upsert(Coll.ATTRIBUTION_MAP, {"telemetry_id": telemetry_id},
                        {"telemetry_id": telemetry_id, **mapping})
