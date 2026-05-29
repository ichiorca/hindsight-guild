"""/api/agents — the team roster + per-agent inbox + capabilities.

The Capabilities page is matrix-focused (skills × agents). This endpoint is
agent-focused: one card per agent showing:
  - identity + role
  - what skills it can load (from _skills_config)
  - what tools it carries (we keep a hand-curated map below — pulling from
    the live agent objects requires importing the whole agent graph, which
    pulls Vertex AI + Mongo at import time and is too heavy for an HTTP
    handler that just wants names)
  - inbox count + first 5 items (per-agent semantic differs — see below)
  - recent_actions: last 5 telemetry rows authored by this agent

Inbox semantics per agent — what the FOUNDER needs to action:
  positioning_agent       → positioning_proposals where status=='proposed'
  lifecycle_email_agent   → email_sequences      where status=='draft'
  paid_media_agent        → paid_variants        where status=='paused'
                             + ops_incidents     where source=='paid_media'
  ops_qa_agent            → ops_incidents        where status=='open'
  self_critique_agent     → skills               where self_critique_proposal
                             .status=='awaiting_human_review'
  cmo_planner             → approvals where action_type contains 'weekly_memo'
                             and decision is null
  customer_voice_agent    → customer_voice rows inserted in last 7d
  image_brief_agent       → recent drafts with image.mode=='stub'
  research/content/review/analytics — produce within the drafting pipeline;
      no standalone queue. Show "Recent contributions" instead.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter

from shared import mongo_tools

router = APIRouter()
log = logging.getLogger(__name__)


# Tools each agent carries. Kept here (not derived live) so the endpoint
# doesn't pay the agent-graph import cost on every request. Update when
# tools change in agents/<agent>.py. The Capabilities matrix already covers
# skill loads — this list documents tool surface.
_AGENT_TOOLS: dict[str, list[str]] = {
    "research_agent":        ["mongodb (read)", "search_past_lessons", "web_search", "skill_tools"],
    "content_agent":         ["mongodb (read)", "skill_tools"],
    "critique_agent":        ["skill_tools"],
    "reviser_agent":         ["skill_tools"],
    "review_agent":          ["mongodb (read)", "validate_claim", "web_search", "skill_tools"],
    "image_brief_agent":     ["check_image_safety", "imagen_generate", "skill_tools"],
    "finalizer":             [],
    "analytics_agent":       ["mongodb (read)", "bigquery_query", "skill_tools"],
    "cmo_planner":           ["AgentTool(research)", "AgentTool(analytics)", "mongodb (write)",
                              "validate_claim", "web_search", "slack_approval", "skill_tools"],
    "positioning_agent":     ["mongodb (write)", "validate_claim", "web_search", "skill_tools"],
    "customer_voice_agent":  ["mongodb (write)", "skill_tools"],
    "lifecycle_email_agent": ["mongodb (write)", "skill_tools"],
    "paid_media_agent":      ["mongodb (read+write via reviser)", "bigquery_query",
                              "validate_claim", "skill_tools"],
    "ops_qa_agent":          ["mongodb (write)", "http_health_check", "skill_tools"],
    "self_critique_agent":   ["mongodb (read)", "bigquery_query", "skill_tools"],
}


@router.get("/api/agents")
def list_agents(inbox_limit: int = 5):
    """Per-agent capabilities + inbox + recent actions.

    The UI's Agents page reads this once and renders a card grid. Each
    card needs both static identity (skills/tools) and a live snapshot
    of what's waiting on the founder per agent.
    """
    from agents._skills_config import SKILLS_BY_AGENT

    # The A2A port map is owned by the drafting router (it routes /api/draft
    # single-agent calls). Import lazily here to keep the agents card aware
    # of which agent has a live A2A port without duplicating the map.
    from services.web_api.routers.drafting import _AGENT_A2A_PORTS

    db = mongo_tools.db()
    now = datetime.now(UTC)
    week_ago = now - timedelta(days=7)

    def _strip_ids(rows: list[dict]) -> list[dict]:
        # Mongo ObjectIds aren't JSON-serializable in the FastAPI path.
        # The /skills, /experiments etc. paths use queries.py helpers that
        # don't return raw ObjectId fields; raw .find() returns can.
        out = []
        for r in rows or []:
            r = dict(r)
            if "_id" in r and not isinstance(r["_id"], str):
                r["_id"] = str(r["_id"])
            out.append(r)
        return out

    def _summarize_item(agent_id: str, item: dict) -> dict:
        """Compute a human-readable task summary for an inbox item. The
        UI used to fall back to ``_id`` when no title-shaped field existed,
        which surfaced opaque hex strings. Here we hand-craft per-collection
        summaries so each inbox row reads like a task you can act on."""
        out = dict(item)
        summary = ""
        detail = ""

        if agent_id == "positioning_agent":
            kind = item.get("kind", "proposal")
            claim = (item.get("claim_text") or "").strip()
            icps = item.get("applies_to_icp") or []
            icp_label = ", ".join(icps) if isinstance(icps, list) else str(icps)
            summary = f"{kind}: \"{claim[:80]}\"" + ("…" if len(claim) > 80 else "")
            detail = f"applies to {icp_label}" if icp_label else ""

        elif agent_id == "lifecycle_email_agent":
            name = item.get("sequence_name") or item.get("_id") or "(unnamed)"
            steps = item.get("steps") or []
            icp = item.get("icp_segment") or "—"
            summary = f"Sequence \"{name}\" — {len(steps)} step{'' if len(steps) == 1 else 's'}"
            detail = f"targets {icp}"

        elif agent_id == "paid_media_agent":
            # Could be a paid_variant OR an ops_incident (paid stop-loss).
            if "headline" in item or "headlines" in item:  # paid variant
                # Google Ads RSA stores `headlines` as a list (3x 30-char).
                # Meta / LinkedIn store `headline` as a single string. Handle
                # both — pick whichever exists, flatten list to first entry.
                raw_hl = item.get("headlines") or item.get("headline") or ""
                if isinstance(raw_hl, list):
                    hl = (raw_hl[0] if raw_hl else "")
                    if not isinstance(hl, str):
                        hl = str(hl)
                else:
                    hl = str(raw_hl)
                hl = hl.strip()
                platform = item.get("platform") or "?"
                axis = item.get("test_axis") or ""
                summary = f"{platform} variant: \"{hl[:60]}\"" + ("…" if len(hl) > 60 else "")
                detail = f"tests {axis}" if axis else ""
            else:  # ops_incident
                cat = item.get("category") or "incident"
                msg = (item.get("message") or item.get("rationale") or "").strip()
                summary = f"{cat}: {msg[:80]}" + ("…" if len(msg) > 80 else "")
                detail = f"severity {item.get('severity', '?')}"

        elif agent_id == "customer_voice_agent":
            # Schema drift in the field — different agent runs wrote
            # ``text``, ``raw_quote``, or ``raw_text``. Accept any.
            text = (item.get("text") or item.get("raw_quote")
                    or item.get("raw_text") or "").strip()
            theme = item.get("theme") or ""
            persona = item.get("persona") or ""
            if text:
                summary = f"\"{text[:90]}\"" + ("…" if len(text) > 90 else "")
            else:
                # Row carries no extractable quote text — show the
                # source instead so the row isn't blank.
                source = item.get("source_id") or item.get("source_kind") or "?"
                summary = f"(no quote text — source: {source})"
            detail_parts = [p for p in [theme, persona] if p]
            detail = " · ".join(detail_parts)

        elif agent_id == "image_brief_agent":
            ch = item.get("channel") or "?"
            tid = item.get("telemetry_id") or item.get("_id") or "?"
            # Detect mode from the raw envelope. Stub = needs upload.
            raw = item.get("raw") or {}
            img = raw.get("image") or {}
            if isinstance(img, str):
                # raw may carry the image dict as a JSON string in some paths
                mode = "stub" if '"mode": "stub"' in img else "api"
            else:
                mode = img.get("mode") or "api"
            if mode == "stub":
                summary = f"Manual hero image needed — {ch} draft"
            else:
                summary = f"Hero image generated for {ch} draft"
            detail = f"telemetry_id {str(tid)[:18]}"

        elif agent_id == "ops_qa_agent":
            # Items may be either ops_incidents or ops_targets fallback.
            if "url" in item:  # ops_target
                summary = f"Monitoring {item.get('url', '?')}"
                detail = f"kind={item.get('kind', '?')}"
            else:  # ops_incident
                cat = item.get("category") or "incident"
                msg = (item.get("message") or "").strip()
                summary = f"{cat}: {msg[:80]}" + ("…" if len(msg) > 80 else "")
                detail = f"severity {item.get('severity', '?')}"

        elif agent_id == "cmo_planner":
            # Items may be either approvals (pending memos) or actions
            # rows (recent CMO planning runs).
            if "memo_markdown" in item:
                summary = "Weekly memo awaiting approval"
                detail = ""
            elif item.get("action_type") == "cmo_plan_op":
                summary = "CMO planning run completed"
                detail = f"channel={item.get('channel', 'n/a')}"
            else:
                summary = f"Approval pending: {item.get('action_type', '?')}"
                detail = ""

        elif agent_id == "self_critique_agent":
            # Items may be skills (with self_critique_proposal) or
            # actions (recent sweeps).
            if "self_critique_proposal" in item:
                sc = item["self_critique_proposal"] or {}
                issue = (sc.get("issue") or "").strip()
                skill = item.get("_id") or "unknown skill"
                summary = f"On \"{skill}\": {issue[:80]}" + ("…" if len(issue) > 80 else "")
                detail = f"confidence: {sc.get('confidence', '?')}"
            elif item.get("action_type") == "self_critique_op":
                summary = "Self-critique sweep — no patterns this week"
                detail = ""
            else:
                summary = f"Sweep: {item.get('action_type', '?')}"
                detail = ""

        else:
            # Pipeline-only agents — derive from the actions row.
            at = item.get("action_type") or "(action)"
            ch = item.get("channel") or ""
            sk = item.get("skill_id") or ""
            summary = f"{at}" + (f" · {ch}" if ch else "")
            detail = f"skill {sk}" if sk else ""

        out["_summary"] = summary or "(no summary)"
        out["_detail"] = detail
        return out

    # Per-agent inbox queries. Each returns (count, sample_items).
    def _inbox_for(agent_id: str) -> tuple[int, list[dict], str]:
        """Returns (count, sample_items, semantic_label)."""
        if agent_id == "positioning_agent":
            q = {"status": "proposed"}
            items = list(db["positioning_proposals"].find(q).limit(inbox_limit))
            return (
                db["positioning_proposals"].count_documents(q),
                _strip_ids(items),
                "Proposals awaiting your decision",
            )
        if agent_id == "lifecycle_email_agent":
            q = {"status": "draft"}
            items = list(db["email_sequences"].find(q).limit(inbox_limit))
            return (
                db["email_sequences"].count_documents(q),
                _strip_ids(items),
                "Nurture sequences awaiting approval (never auto-sent)",
            )
        if agent_id == "paid_media_agent":
            qv = {"status": "paused"}
            variants = list(db["paid_variants"].find(qv).limit(inbox_limit))
            qi = {"status": "open", "source": {"$in": ["paid_media", "paid_media_agent"]}}
            incidents = list(db["ops_incidents"].find(qi).limit(inbox_limit))
            return (
                db["paid_variants"].count_documents(qv) +
                db["ops_incidents"].count_documents(qi),
                _strip_ids(variants[:inbox_limit] + incidents[:inbox_limit])[:inbox_limit],
                "Paused variants + stop-loss incidents",
            )
        if agent_id == "ops_qa_agent":
            # Prefer open incidents (decisions to action). Fall back to
            # ALL recent incidents + the ops_targets watchlist so the
            # founder sees what's under monitoring even when nothing
            # is broken right now.
            open_q = {"status": "open"}
            n_open = db["ops_incidents"].count_documents(open_q)
            if n_open > 0:
                items = list(db["ops_incidents"].find(open_q).limit(inbox_limit))
                return (n_open, _strip_ids(items),
                        "Open ops incidents (uptime, UTM, pixel, form)")
            # Fall back to ops_targets — the watchlist itself is the
            # ongoing "work" the agent owns.
            targets = list(db["ops_targets"].find({}).limit(inbox_limit))
            n_targets = db["ops_targets"].count_documents({})
            if n_targets > 0:
                return (n_targets, _strip_ids(targets),
                        "Targets under continuous monitoring (no open incidents)")
            return (0, [], "Open ops incidents (uptime, UTM, pixel, form)")
        if agent_id == "self_critique_agent":
            # Prefer awaiting-review proposals. Fall back to recent
            # critique runs (actions) so the founder sees the agent
            # ran on its weekly cadence even when no pattern emerged.
            review_q = {"self_critique_proposal.status": "awaiting_human_review"}
            n_review = db["skills"].count_documents(review_q)
            if n_review > 0:
                items = list(db["skills"].find(review_q).limit(inbox_limit))
                return (n_review, _strip_ids(items),
                        "Playbook improvements proposed by the system")
            # Fall back: recent sweeps. Use ObjectId-time for actions in
            # case ts isn't indexed.
            from bson import ObjectId
            cutoff_oid = ObjectId.from_datetime(week_ago)
            sweeps_q = {
                "agent": "self_critique_agent",
                "_id": {"$gte": cutoff_oid},
            }
            n_sweeps = db["actions"].count_documents(sweeps_q)
            items = list(db["actions"].find(sweeps_q)
                          .sort([("_id", -1)]).limit(inbox_limit))
            return (n_sweeps, _strip_ids(items),
                    "Recent self-critique sweeps (no patterns flagged this week)")
        if agent_id == "cmo_planner":
            # Prefer pending memos. Fall back to recent CMO planning
            # runs so the agent isn't blank when slack_approval no-ops
            # in LOCAL_DEV (no webhook configured).
            pending_q = {
                "action_type": {"$regex": "weekly_memo"},
                "$or": [{"decision": None}, {"decision": {"$exists": False}}],
            }
            n_pending = db["approvals"].count_documents(pending_q)
            if n_pending > 0:
                items = list(db["approvals"].find(pending_q).limit(inbox_limit))
                return (n_pending, _strip_ids(items),
                        "Weekly memos awaiting approval")
            from bson import ObjectId
            cutoff_oid = ObjectId.from_datetime(week_ago)
            runs_q = {
                "agent": "cmo_planner",
                "_id": {"$gte": cutoff_oid},
            }
            n_runs = db["actions"].count_documents(runs_q)
            items = list(db["actions"].find(runs_q)
                          .sort([("_id", -1)]).limit(inbox_limit))
            return (n_runs, _strip_ids(items),
                    "Recent CMO planning runs (no memo pending approval)")
        if agent_id == "customer_voice_agent":
            # customer_voice rows inserted by the agent don't set a ``ts``
            # field — they only carry source_id + source_kind. Use the
            # ObjectId's embedded creation timestamp instead, which works
            # for every Mongo insert regardless of explicit ts column.
            from bson import ObjectId
            cutoff_oid = ObjectId.from_datetime(week_ago)
            q = {"_id": {"$gte": cutoff_oid}}
            items = list(
                db["customer_voice"].find(q).sort([("_id", -1)]).limit(inbox_limit)
            )
            return (
                db["customer_voice"].count_documents(q),
                _strip_ids(items),
                "Voice quotes ingested in the last 7 days",
            )
        if agent_id == "image_brief_agent":
            # Prefer stub-mode images (drafts that NEED a manual upload).
            # Fall back to recent image briefs so the agent shows the
            # visuals it has shipped this week when nothing failed.
            stub_q = {
                "agent": agent_id,
                "ts": {"$gte": week_ago},
                "$or": [
                    {"raw.image.mode": "stub"},
                    {"raw.image": {"$regex": '"mode":\\s*"stub"'}},
                ],
            }
            n_stub = db["actions"].count_documents(stub_q)
            if n_stub > 0:
                items = list(db["actions"].find(stub_q)
                              .sort([("ts", -1)]).limit(inbox_limit))
                return (n_stub, _strip_ids(items),
                        "Drafts needing a manual hero image upload")
            # Fall back: recent image briefs (any mode). Use ObjectId-time.
            from bson import ObjectId
            cutoff_oid = ObjectId.from_datetime(week_ago)
            recent_q = {
                "agent": agent_id,
                "_id": {"$gte": cutoff_oid},
            }
            n_recent = db["actions"].count_documents(recent_q)
            items = list(db["actions"].find(recent_q)
                          .sort([("_id", -1)]).limit(inbox_limit))
            return (n_recent, _strip_ids(items),
                    "Image briefs generated this week (no stubs to upload)")

        # Pipeline agents — no standing queue; show recent contributions.
        q = {"agent": agent_id, "ts": {"$gte": week_ago}}
        items = list(db["actions"].find(q).sort([("ts", -1)]).limit(inbox_limit))
        return (
            db["actions"].count_documents(q),
            _strip_ids(items),
            "Recent contributions to the drafting pipeline",
        )

    # Sub-agent → parent mapping. Several "top-level" agents are
    # actually SequentialAgents whose sub-agents emit telemetry under
    # their own names (e.g. lifecycle_email_drafter / _critique /
    # _reviser all write to the actions table separately). When the UI
    # asks for ``recent_actions`` of ``lifecycle_email_agent``, we want
    # all three. Same for paid_media and the drafting pipeline.
    SUB_AGENT_ALIASES: dict[str, list[str]] = {
        "lifecycle_email_agent": [
            "lifecycle_email_agent", "lifecycle_email_drafter",
            "lifecycle_email_critique", "lifecycle_email_reviser",
        ],
        "paid_media_agent": [
            "paid_media_agent", "paid_media_drafter",
            "paid_media_critique", "paid_media_reviser",
        ],
        # The drafting pipeline writes under each sub-agent's name; the
        # UI exposes each as its own card so we DON'T alias them under
        # a single parent — leave research/content/review as-is.
    }

    # Recent activity per agent — last 5 telemetry rows. Useful for
    # pipeline agents (research/content/review/etc.) where the inbox
    # concept doesn't apply but you still want to see what they've done.
    def _recent_for(agent_id: str) -> list[dict]:
        # If this agent is a parent of sub-agents, search under all
        # known names. Otherwise just look for exact match.
        names = SUB_AGENT_ALIASES.get(agent_id, [agent_id])
        agent_filter = {"$in": names} if len(names) > 1 else names[0]
        try:
            rows = list(
                db["actions"]
                .find({"agent": agent_filter, "ts": {"$gte": week_ago}})
                .sort([("ts", -1)])
                .limit(5)
            )
        except Exception as e:
            log.warning("recent_for(%s) failed: %s", agent_id, e)
            return []
        out_rows: list[dict] = []
        for r in rows:
            r = dict(r)
            if "_id" in r and not isinstance(r["_id"], str):
                r["_id"] = str(r["_id"])
            out_rows.append({
                "ts": r.get("ts").isoformat() if r.get("ts") else None,
                "action_type": r.get("action_type"),
                "channel": r.get("channel"),
                "skill_id": r.get("skill_id"),
                "telemetry_id": r.get("telemetry_id"),
            })
        return out_rows

    out: list[dict] = []
    for agent_id, skills in SKILLS_BY_AGENT.items():
        count, items, label = _inbox_for(agent_id)
        # Decorate each sample item with a human-readable summary + detail
        # so the UI never falls back to opaque ObjectIds for the title.
        decorated = [_summarize_item(agent_id, it) for it in items]
        out.append({
            "agent_id": agent_id,
            "skills_allowed": skills,
            "tools": _AGENT_TOOLS.get(agent_id, []),
            "a2a_port": _AGENT_A2A_PORTS.get(agent_id),
            "inbox": {
                "count": count,
                "label": label,
                "sample": decorated,
            },
            "recent_actions": _recent_for(agent_id),
        })

    return {"agents": out, "as_of": now.isoformat()}
