"""Drafting pipeline — the SequentialAgent that makes the team a team.

Research → Content → CritiqueLoop(Critique→Reviser×N) → ImageBrief →
Review → Finalizer.

State flow via output_key + {template_variable}:
  research_findings → draft → critique → (reviser overwrites)draft →
  image → review → _final

The Critique + Reviser pair is the "second-thought" stage: Content writes
a first-pass draft, Critique reads it against the topic_hint + research +
generic-AI-fluff checklist and emits specific revision instructions,
Reviser applies them and OVERWRITES state["draft"] with the final version.
ImageBrief and Review only see the post-revision draft.

For high-severity critiques, the Critique+Reviser pair runs up to 2
iterations via a LoopAgent (``drafting_critique_loop``). The loop's
escalation gate short-circuits to a single pass when the first
critique reports severity="low" or "medium" — saving an LLM round-trip
on drafts that already converged.

ImageBrief sits between Content and Review so:
  - Content only worries about copy.
  - ImageBrief sees the final draft and crafts a matching visual.
  - Review evaluates both text + image as a single asset.

The Finalizer at the tail emits one JSON object containing every state slot
the UI's /api/draft needs. Without it, A2A's response surface only carries
the last sub-agent's text output (Review's JSON) — the upstream slots
(draft, research_findings, image) would be unreachable.

State invariant: ``state.telemetry_id`` is seeded before any sub-agent runs,
and ``state.channel`` / ``state.icp_segment`` are extracted from the user
message if not already set. This is what lets web_api's /api/draft kick
off a run without per-request session-state plumbing — the message text
"Draft a <channel> post targeting <icp>" is parsed here.

ADK compatibility note: ``before_agent_callback`` is inherited from
``BaseAgent`` and supported on ``SequentialAgent`` in ADK 1.x. The state
mutation pattern used here is standard but the ADK docs frame it as a
control/inspection hook, not a state-seeder — every sub-agent's
``after_agent_callback`` also calls ``ensure_telemetry_id`` defensively
(see agents/_common.py) so a missed before-callback can never leave
telemetry rows orphaned.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import uuid
from pathlib import Path

from google.adk.agents.sequential_agent import SequentialAgent

from agents._critique_loop import build_critique_loop
from agents.aeo_agent import aeo_pipeline_node
from agents.content import content_agent
from agents.critique import critique_agent
from agents.finalizer import finalizer_agent
from agents.image_brief import image_brief_agent
from agents.research import research_agent
from agents.review import review_agent
from agents.reviser import reviser_agent

log = logging.getLogger(__name__)

# lifecycle_email matched BEFORE bare "email" so the more specific channel wins.
# Same for *_ads platforms vs the generic "ads".
_CHANNEL_RE = re.compile(
    r"\b(lifecycle_email|google_ads|meta_ads|linkedin_ads|linkedin|email|blog|substack)\b",
    re.IGNORECASE,
)
_ICP_RE = re.compile(r"targeting\s+([a-z_][a-z0-9_]+)", re.IGNORECASE)
_EXP_RE = re.compile(r"experiment[_ -]?id[:=]?\s*([a-z0-9_]+)", re.IGNORECASE)
_SKILL_BY_CHANNEL = {
    "linkedin":        "linkedin_post",
    "email":           "nurture_email",
    "blog":            "blog_outline",
    "substack":        "substack_post",
    "lifecycle_email": "nurture_email_sequence",
    "google_ads":      "paid_variant_google",
    "meta_ads":        "paid_variant_meta",
    "linkedin_ads":    "paid_variant_linkedin",
}


def _ensure_session_state(callback_context):  # type: ignore[no-untyped-def]
    """Before-callback: seed telemetry_id, channel, icp_segment, skill_id.

    The web_api /api/draft path goes through A2A's JSON-RPC, which doesn't
    plumb arbitrary state into the ADK session. We extract those fields from
    the user message text here so every downstream sub-agent's after-callback
    can emit telemetry keyed on the right channel/skill.
    """
    try:
        state = callback_context.state
        # Always present so the Content instruction's {playbook_body} template
        # slot resolves even on the incumbent path (where it stays empty and
        # the agent looks the playbook up on disk exactly as before).
        state.setdefault("playbook_body", "")
        if not state.get("telemetry_id"):
            state["telemetry_id"] = f"act_{uuid.uuid4().hex[:12]}"

        # Pull the first user message text to extract hints.
        if not state.get("channel") or not state.get("icp_segment"):
            msg_text = _extract_user_message(callback_context)
            if msg_text:
                if not state.get("channel"):
                    m = _CHANNEL_RE.search(msg_text)
                    if m:
                        state["channel"] = m.group(1).lower()
                if not state.get("icp_segment"):
                    m = _ICP_RE.search(msg_text)
                    if m:
                        state["icp_segment"] = m.group(1)
                if not state.get("experiment_id"):
                    m = _EXP_RE.search(msg_text)
                    if m:
                        state["experiment_id"] = m.group(1)

        # Derive skill_id from channel if still missing.
        if not state.get("skill_id") and state.get("channel"):
            state["skill_id"] = _SKILL_BY_CHANNEL.get(state["channel"], "unknown")

        # Stamp the skill's real version so telemetry is attributable to the
        # version that produced it. WITHOUT this the after-callback defaults
        # skill_version to "v1" for every draft, which starves the promotion
        # gate: its _version_stats groups telemetry by skill_version, so the
        # incumbent (e.g. linkedin_post_v3.txt) and any candidate both show
        # n=0 and no promotion can ever fire. See agents/_common.py:make_after_callback.
        if not state.get("skill_version") and state.get("skill_id"):
            version, candidate_body = _resolve_skill_version_and_body(
                state["skill_id"], state.get("telemetry_id") or "",
            )
            if version:
                state["skill_version"] = version
            # Only a CANDIDATE injects a body. Incumbent path leaves
            # playbook_body == "" so the live drafting prompt is byte-for-byte
            # what it was before this change.
            if candidate_body:
                state["playbook_body"] = candidate_body
                state["prompt_file"] = version
    except Exception as e:
        log.warning("could not seed session state: %s", e)
    return None


def _candidate_rollout_pct() -> float:
    """Fraction of drafts routed to a skill's pending candidate version.

    0 (the default) means incumbent-only — every draft renders + is stamped
    with current_version. A value in (0, 1] splits a deterministic fraction
    of traffic onto the top candidate so the promotion gate accrues the
    candidate-vs-incumbent telemetry it needs to decide.
    """
    try:
        return max(0.0, min(1.0, float(os.environ.get("SKILL_CANDIDATE_ROLLOUT_PCT", "0"))))
    except ValueError:
        return 0.0


_PROMPTS_CONTENT_DIR = Path(__file__).resolve().parents[1] / "prompts" / "content"


def _load_version_body(doc: dict, version: str | None) -> str | None:
    """Return the renderable body for a specific skill version, or None.

    Two storage shapes are supported so BOTH playbook and agent_skill
    candidates can be A/B-rendered:
      - Mongo ``versions[<v>].body_md`` (agent_skill, or a playbook whose
        candidate body has been authored into Mongo).
      - On-disk ``prompts/content/<version>`` (file-based playbooks, where
        ``version`` is a filename like ``linkedin_post_v4_candidate.txt``).
    """
    if not version:
        return None
    versions = doc.get("versions") or {}
    body = (versions.get(version) or {}).get("body_md")
    if isinstance(body, str) and body:
        return body
    if version.endswith((".txt", ".md")):
        try:
            path = _PROMPTS_CONTENT_DIR / version
            if path.is_file():
                return path.read_text(encoding="utf-8")
        except Exception as e:
            log.debug("candidate prompt file read failed for %s: %s", version, e)
    return None


def _resolve_skill_version_and_body(
    skill_id: str, telemetry_id: str,
) -> tuple[str | None, str]:
    """Resolve (skill_version, candidate_body) for this draft.

    Default: ``(current_version, "")`` — the incumbent, with NO injected body
    (the Content agent loads the playbook itself, unchanged). When the skill
    has a RENDERABLE candidate AND ``SKILL_CANDIDATE_ROLLOUT_PCT`` > 0, a
    stable fraction of drafts (hashed on telemetry_id so retries stay in the
    same bucket) are routed to the top candidate and we return its body so the
    Content agent renders the candidate. A candidate is only ever selected
    when its body is actually renderable, so we never mislabel incumbent text
    as the candidate.

    Returns ``(None, "")`` when the skill doc can't be read — the
    after-callback's own ``"v1"`` default then applies.
    """
    try:
        from shared import mongo_tools
        doc = mongo_tools.find_one("skills", {"_id": skill_id})
    except Exception as e:
        log.debug("skill_version resolve failed for %s: %s", skill_id, e)
        return None, ""
    if not doc:
        return None, ""
    current = doc.get("current_version")

    pct = _candidate_rollout_pct()
    if pct > 0 and telemetry_id:
        for cand in (doc.get("candidates") or []):
            body = _load_version_body(doc, cand)
            if not body:
                continue
            # Stable hash → bucket in [0, 1). First renderable candidate that
            # the bucket selects wins.
            h = int(hashlib.blake2b(telemetry_id.encode(), digest_size=8).hexdigest(), 16)
            if (h % 10_000) / 10_000.0 < pct:
                return cand, body
            break  # only the top candidate participates in the split
    return current, ""


def _extract_user_message(callback_context) -> str:  # type: ignore[no-untyped-def]
    """Best-effort pull of the latest user message text from the ADK
    callback context. The exact attribute varies across ADK versions, so we
    try a couple of common shapes before giving up.
    """
    try:
        msg = getattr(callback_context, "user_message", None)
        if msg and getattr(msg, "parts", None):
            return " ".join(getattr(p, "text", "") or "" for p in msg.parts)
        session = getattr(callback_context, "session", None)
        if session and getattr(session, "messages", None):
            for m in reversed(session.messages):
                if getattr(m, "role", None) == "user":
                    return " ".join(getattr(p, "text", "") or ""
                                    for p in (m.parts or []))
    except Exception:
        pass
    return ""


drafting_critique_loop = build_critique_loop(
    name="drafting_critique_loop",
    critique_agent=critique_agent,
    reviser_agent=reviser_agent,
    critique_state_key="critique",
    max_iterations=2,
)


drafting_pipeline = SequentialAgent(
    name="drafting_pipeline",
    description=(
        "Research → Content → CritiqueLoop → AeoLoop → ImageBrief → "
        "Review → Finalizer. The CritiqueLoop runs Critique+Reviser up to "
        "2 iterations, short-circuiting when severity converges to "
        "low/medium. The AeoLoop scores and (when below threshold) "
        "restructures the draft for AI-search citability on "
        "blog/substack/linkedin channels; skips on paid + email. "
        "Produces a graded text+image asset from an ICP + channel + "
        "(optional) experiment_id."
    ),
    sub_agents=[
        research_agent,
        content_agent,
        drafting_critique_loop,
        aeo_pipeline_node(),
        image_brief_agent,
        review_agent,
        finalizer_agent,
    ],
    before_agent_callback=_ensure_session_state,
)
