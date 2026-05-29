"""Self-Critique Agent — promoted from cron-script to real ADK agent.

The cron job in services/self_critique/main.py now invokes this agent via
A2A on a weekly schedule. The agent is what reasons over telemetry + edits
and proposes playbook revisions; the cron job just kicks it off.

Why promote: the script-style Gemini call worked but couldn't be invoked
ad hoc from the UI ("Run self-critique now") and couldn't be composed with
other agents (e.g. via AgentTool). As an LlmAgent it's first-class.

Persistence: the agent_skill branch persists its proposal via the dedicated
``propose_skill_revision`` tool rather than hand-built ``mongodb_update_one``
calls. Letting the model construct dotted ``$set`` paths for the
two-key (versions.<id> + self_critique_proposal) write was unreliable — it
mangled keys (quote/bracket/backtick wrapping), invented ``$unset``
pseudo-operators, and occasionally crashed on path conflicts. The typed
tool takes plain scalars and builds the correct provenance-stamped write
internally, so persistence is deterministic.
"""
from __future__ import annotations

import logging
import os
from datetime import UTC, datetime

from google.adk.tools import FunctionTool

from agents._factory import make_llm_agent
from agents._prompts import SELF_CRITIQUE_INSTRUCTIONS
from agents._schema_constants import Coll, Status
from shared.bigquery_helper import bigquery_query

log = logging.getLogger(__name__)

PROJECT_ID = os.environ.get("PROJECT_ID", "agentic-marketing-mvp")
_AGENT_ID = "self_critique_agent"
_VALID_CONFIDENCE = ("high", "medium", "low")

# Shared BQ helper — degrades to [] in LOCAL_DEV instead of raising.
bigquery_query_tool = FunctionTool(func=bigquery_query)


def propose_skill_revision(
    skill_id: str,
    candidate_id: str,
    body_md: str,
    issue: str,
    confidence: str,
    evidence_count: int,
    channels_affected: list[str],
) -> dict:
    """Persist an agent_skill revision proposal in one safe, atomic write.

    Use this for the agent_skill branch instead of hand-writing a Mongo
    update. It writes BOTH the candidate version body and the
    ``self_critique_proposal`` onto the skill document together, with the
    correct dotted-path keys, provenance, and history capture — so you never
    construct ``$set`` operators or dotted paths yourself.

    Args:
        skill_id: The agent_skill ``_id`` (e.g. "house-style").
        candidate_id: New version id, e.g. "v2" or "v2_critique". Must not
            collide with the current version.
        body_md: The COMPLETE new SKILL.md body (YAML frontmatter + content,
            version bumped, additive changes preferred). Must be non-empty.
        issue: One-sentence description of the systematic pattern.
        confidence: "high", "medium", or "low".
        evidence_count: Number of supporting edits / low-scoring drafts.
        channels_affected: Channels where the pattern appears. MUST list >= 2
            distinct channels — a single-channel issue belongs in that
            channel's playbook, not a cross-channel agent_skill.

    Returns:
        ``{"ok": True, "skill_id", "candidate_id"}`` on success, or
        ``{"ok": False, "error": "<reason>"}`` if a guardrail rejects the
        input — read the error and either fix the inputs or stay silent.
    """
    body = (body_md or "").strip()
    if not skill_id or not candidate_id:
        return {"ok": False, "error": "skill_id and candidate_id are required."}
    if not body:
        return {"ok": False,
                "error": "body_md is empty; write the COMPLETE new SKILL.md body."}
    # The candidate body must be a valid SKILL.md — the on-disk reconcile in
    # shared.skills.read_body re-parses it on the next read_skill() and raises
    # if the frontmatter is missing. Validate here (same parser) so a
    # malformed body is rejected at write time with a clear instruction,
    # rather than crashing a downstream reader after it's been persisted.
    from shared.skills import _parse_skill_md
    try:
        _parse_skill_md(body)
    except Exception as e:  # noqa: BLE001 — surface the parse error to the LLM
        return {"ok": False,
                "error": (f"body_md is not a valid SKILL.md ({e}). It must start "
                          "with the version-bumped YAML frontmatter (a '---' line, "
                          "the YAML, then a closing '---' line), followed by the "
                          "full body. Include the complete frontmatter, do not omit it.")}
    if confidence not in _VALID_CONFIDENCE:
        return {"ok": False,
                "error": f"confidence must be one of {_VALID_CONFIDENCE}."}
    channels = sorted({c for c in (channels_affected or []) if c})
    if len(channels) < 2:
        return {"ok": False,
                "error": "channels_affected needs >= 2 distinct channels for an "
                         "agent_skill change; a single-channel issue belongs in "
                         "that channel's playbook."}

    # Lazy import — keeps module load cheap and free of history side effects.
    from mongo.history import DocumentNotFound, update_with_history

    now = datetime.now(UTC)
    update = {"$set": {
        f"versions.{candidate_id}": {
            "body_md": body,
            "proposed_at": now,
            "source": "self_critique",
        },
        "self_critique_proposal": {
            "candidate_id": candidate_id,
            "issue": issue,
            "confidence": confidence,
            "evidence_count": int(evidence_count or 0),
            "channels_affected": channels,
            "proposed_at": now,
            "status": Status.AWAITING_HUMAN_REVIEW,
        },
    }}
    try:
        update_with_history(
            Coll.SKILLS, {"_id": skill_id}, update,
            actor_id=_AGENT_ID, change_kind="self_critique_proposal",
            secret_name="mongo_uri_writer",
        )
    except DocumentNotFound:
        return {"ok": False, "error": f"no skill with _id={skill_id!r}."}
    except Exception as e:  # noqa: BLE001 — surface the failure to the LLM
        log.exception("propose_skill_revision failed for %s", skill_id)
        return {"ok": False, "error": f"write failed: {e}"}
    return {"ok": True, "skill_id": skill_id, "candidate_id": candidate_id}


propose_skill_revision_tool = FunctionTool(func=propose_skill_revision)

self_critique_agent = make_llm_agent(
    name="self_critique_agent",
    instructions=SELF_CRITIQUE_INSTRUCTIONS,
    model="gemini-3.5-flash",
    mode="write",
    output_key="self_critique",
    skill_id="self_critique_weekly",
    action_type="self_critique_op",
    extra_tools=[bigquery_query_tool, propose_skill_revision_tool],
    # Meta-agent: it reviews arbitrary agent_skills it doesn't load, so it
    # must be able to read_skill any of them (Tier-1 metadata stays lean).
    unrestricted_skill_reads=True,
)
