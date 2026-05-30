"""AEO sub-pipeline — scores + (optionally) restructures a draft for
citation by AI search engines (ChatGPT, Perplexity, Google AI Overviews,
Claude web search).

Pipeline placement::

    research → content → critique_loop → AEO_LOOP → image_brief → review → finalizer

The AEO_LOOP is a ``LoopAgent`` over (AeoScorer → AeoReviser →
AeoEscalationGate). Same structural pattern as ``drafting_critique_loop``
in ``agents/_critique_loop.py``. Max 2 iterations.

Termination:
  - AeoScorer emits ``answer_extractability >= 0.7`` → gate escalates → exit.
  - AeoScorer emits ``answer_extractability is None`` (skip — channel
    isn't blog/substack/linkedin OR draft too short) → gate escalates → exit.
  - max_iterations reached → exit anyway. The downstream Review agent will
    surface the persistent low score to the founder if it still matters.

Why a peer agent and not a tool inside Review:
  Review runs in a single LLM pass and grades the existing 6 rubrics.
  AEO needs *structural rewrites* (reorder paragraphs, insert question-form
  H2s, consolidate into 134-167-word blocks). That's drafting work — it
  belongs in a peer scorer+reviser pair, not bolted onto a grader.

Telemetry shape:
  - The AeoScorer's after_agent_callback emits one telemetry row per pass
    (skill_id="aeo_score", action_type="aeo_score").
  - It ALSO writes the resulting ``answer_extractability`` score onto the
    content_agent's existing ``actions`` row's ``eval_scores`` dict so the
    queue / rubric trend / weekly review surfaces pick it up without any
    schema change downstream. See ``_inject_aeo_into_eval_scores`` below.
  - When the AeoReviser actually rewrote the draft, it appends an
    ``aeo_audits`` row that the PRD-03 ``aeo_miner`` will read.
"""
from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import AsyncGenerator
from datetime import UTC, datetime

from google.adk.agents import BaseAgent, LlmAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.agents.loop_agent import LoopAgent
from google.adk.events.event import Event
from google.adk.events.event_actions import EventActions
from google.adk.tools.function_tool import FunctionTool

from agents._common import make_after_callback, make_model_armor_callback
from agents._models import pick_model
from agents._prompts import AEO_REVISER_INSTRUCTIONS, AEO_SCORER_INSTRUCTIONS
from scripts.aeo import content_quality as _cq
from scripts.aeo import passage_blocks as _pb
from shared import mongo_tools

log = logging.getLogger(__name__)

# Channels where AEO actually applies. Paid + email have no answer-engine
# surface; for those we skip cleanly and surface ``None`` on the rubric card.
AEO_CHANNELS = {"blog", "substack", "linkedin"}

# Composite weights — must match AEO_SCORER_INSTRUCTIONS + skills/aeo/SKILL.md.
SUB_SIGNAL_WEIGHTS = {
    "answer_first":          0.25,
    "question_form_h2s":     0.15,
    "self_contained_blocks": 0.20,
    "specific_stats":        0.15,
    "definition_patterns":   0.10,
    "entity_grounding":      0.15,
}

# Exit threshold — matches the SKILL.md ship-as-is bar.
EXIT_THRESHOLD = 0.7

# Kill switch — set AEO_SKIP=1 to make the LoopAgent pass-through. Useful for
# debugging the rest of the pipeline without paying the AEO latency.
def _kill_switch() -> bool:
    return os.environ.get("AEO_SKIP", "").lower() in ("1", "true", "yes")


# ---------------------------------------------------------------------------
# Tools — thin wrappers around scripts/aeo/* so they're callable from ADK.
# ---------------------------------------------------------------------------

def aeo_content_quality(draft_text: str) -> dict:
    """Score a draft against QRG quality heuristics (filler, AI-pattern,
    info density, repetition). Returns the dict from
    ``scripts.aeo.content_quality.analyse``. Use the ``flags`` list as
    evidence for AEO sub-signals — low-density text generally scores
    poorly on specific_stats and definition_patterns."""
    return _cq.analyse(draft_text or "")


def aeo_passage_blocks(draft_text: str, page_type_hint: str = "") -> dict:
    """Segment the draft into paragraph blocks and score each for
    self-containment + the 134-167-word target (80-120 for listicle).
    Returns the dict from ``scripts.aeo.passage_blocks.detect_blocks``.
    The ``self_contained_blocks_signal`` field IS the AEO sub-signal
    score for that dimension — use it verbatim, don't re-derive."""
    hint = (page_type_hint or "").strip() or None
    return _pb.detect_blocks(draft_text or "", page_type_hint=hint)


# ---------------------------------------------------------------------------
# Composite + injection helpers
# ---------------------------------------------------------------------------

def _composite(sub_signals: dict) -> float | None:
    """Compute answer_extractability from sub_signals. Returns None when
    any required key is missing — the Scorer is responsible for emitting
    a complete sub_signals dict, but we defend against partial emit."""
    try:
        return round(sum(
            float(sub_signals[k]) * w for k, w in SUB_SIGNAL_WEIGHTS.items()
        ), 3)
    except (KeyError, TypeError, ValueError):
        return None


def _parse_aeo_score(raw_output: object) -> dict | None:
    """Parse the Scorer's emit into a dict. ADK delivers structured LLM
    output as either a dict or a JSON string (sometimes fenced).
    Returns ``None`` if parsing fails — caller treats as skip."""
    if isinstance(raw_output, dict):
        return raw_output
    if not isinstance(raw_output, str):
        return None
    s = raw_output.strip()
    m = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", s)
    if m:
        s = m.group(1)
    if not s.startswith("{"):
        return None
    try:
        return json.loads(s)
    except Exception:
        return None


def _inject_aeo_into_eval_scores(telemetry_id: str, score: float) -> None:
    """Set ``actions.eval_scores.answer_extractability`` on the
    content_agent's draft row for this telemetry_id. Done as a direct
    Mongo update so the queue / rubric trend / weekly review surfaces
    pick it up without schema changes.

    The row may be written by content_agent OR by reviser_agent (which
    overwrites the draft); we update WHICHEVER row has the matching
    telemetry_id + a non-null draft. In practice both rows exist after
    the critique loop runs; updating both is safe (idempotent set).
    """
    try:
        db = mongo_tools.db()
        # Pipeline update so a NULL eval_scores (now common — eval scoring is
        # sampled via EVAL_SAMPLE_RATE, so most rows have eval_scores=null) is
        # turned into {} before adding answer_extractability. A plain
        # `$set: {"eval_scores.answer_extractability": ...}` fails on a null
        # parent with "Cannot create field ... in element {eval_scores: null}".
        db["actions"].update_many(
            {"telemetry_id": telemetry_id},
            [{"$set": {"eval_scores": {"$mergeObjects": [
                {"$ifNull": ["$eval_scores", {}]},
                {"answer_extractability": float(score)},
            ]}}}],
        )
    except Exception as e:
        # Telemetry must NEVER block the agent run.
        log.warning("AEO score injection failed for %s: %s", telemetry_id, e)


def _write_aeo_audit(state) -> None:
    """If the Reviser actually applied rewrites, persist an
    ``aeo_audits`` row for PRD-03's aeo_miner to consume later. Skipped
    silently when nothing changed."""
    try:
        score = state.get("aeo_score") or {}
        if not isinstance(score, dict):
            return
        rewrites = score.get("rewrites") or []
        if not rewrites:
            return
        db = mongo_tools.db()
        db["aeo_audits"].insert_one({
            "telemetry_id":  state.get("telemetry_id"),
            "ts":            datetime.now(UTC),
            "channel":       state.get("channel"),
            "score_before":  score.get("score_before"),
            "score_after":   score.get("answer_extractability"),
            "rewrites":      rewrites,
        })
    except Exception as e:
        log.warning("aeo_audits write failed: %s", e)


# ---------------------------------------------------------------------------
# Sub-agents
# ---------------------------------------------------------------------------

def _aeo_scorer_after_callback(callback_context):  # type: ignore[no-untyped-def]
    """After the Scorer runs: parse its output, compute the composite
    (defensively — Scorer's instruction tells it to compute, but we
    re-compute from sub_signals as the source of truth), inject onto
    the content_agent row's eval_scores, and emit the per-agent
    telemetry row via the standard ``make_after_callback`` machinery.
    """
    try:
        state = callback_context.state
        raw = state.get("aeo_score") or state.get("_last_output")
        parsed = _parse_aeo_score(raw) or {}

        # Channel-skip path — emit a null score, don't inject anything.
        channel = (state.get("channel") or "").lower()
        if channel not in AEO_CHANNELS:
            state["aeo_score"] = {
                "answer_extractability": None,
                "sub_signals": {},
                "rationale": f"skipped: channel={channel!r} has no answer-engine surface",
                "rewrites": [],
            }
            return None

        sub = parsed.get("sub_signals") or {}
        # Trust the Scorer's emit if it computed answer_extractability,
        # but always re-derive from sub_signals when possible — the math
        # must match SUB_SIGNAL_WEIGHTS exactly (the LLM can drift).
        if sub:
            score = _composite(sub)
        else:
            try:
                score = float(parsed.get("answer_extractability"))
            except (TypeError, ValueError):
                score = None

        normalized = {
            "answer_extractability": score,
            "sub_signals": sub,
            "rationale": parsed.get("rationale") or "",
            "rewrites": parsed.get("rewrites") or [],
        }
        state["aeo_score"] = normalized

        if score is not None:
            tid = state.get("telemetry_id")
            if tid:
                _inject_aeo_into_eval_scores(tid, score)
    except Exception as e:
        log.warning("AEO scorer post-processing failed: %s", e)

    # Chain through to the standard telemetry callback so the per-agent
    # row still gets written.
    return _scorer_telemetry_cb(callback_context)


_scorer_telemetry_cb = make_after_callback(
    agent_name="aeo_scorer",
    skill_id="aeo_score",
    action_type="aeo_score",
)


aeo_scorer_agent = LlmAgent(
    name="aeo_scorer",
    model=pick_model("gemini-3.1-flash-lite"),  # cheap — deterministic-ish work
    instruction=AEO_SCORER_INSTRUCTIONS,
    tools=[
        FunctionTool(aeo_content_quality),
        FunctionTool(aeo_passage_blocks),
    ],
    output_key="aeo_score",
    after_model_callback=make_model_armor_callback(),
    after_agent_callback=_aeo_scorer_after_callback,
)


_AEO_REWRITES_LINE_RE = re.compile(r"AEO_REWRITES:\s*(\{[\s\S]*?\})\s*$")


def _aeo_reviser_after_callback(callback_context):  # type: ignore[no-untyped-def]
    """After the Reviser runs: split the response into draft body +
    AEO_REWRITES block; rewrite ``state["draft"]`` with just the body;
    merge the rewrites object into ``state["aeo_score"]``.

    The Reviser's output is shaped as::

        <draft body, possibly substack JSON>
        AEO_REWRITES: {"applied": [...], "skipped": [...]}

    We parse the AEO_REWRITES line off the tail and treat the rest as
    the draft body (matching content/reviser agent shape).

    When the Reviser actually applied a rewrite, this callback writes
    an ``aeo_audits`` row that PRD-03's aeo_miner reads later.
    """
    try:
        state = callback_context.state
        raw = state.get("draft") or state.get("_last_output") or ""
        if not isinstance(raw, str):
            # Substack output already parsed into dict — Scorer's JSON
            # shape; nothing to split off.
            return _reviser_telemetry_cb(callback_context)

        m = _AEO_REWRITES_LINE_RE.search(raw)
        if m:
            try:
                rewrites_block = json.loads(m.group(1))
            except Exception:
                rewrites_block = {"applied": [], "skipped": []}
            # Strip the AEO_REWRITES line from the draft body.
            body = raw[: m.start()].rstrip()
            state["draft"] = body

            score = state.get("aeo_score") or {}
            if isinstance(score, dict):
                applied = rewrites_block.get("applied") or []
                # Preserve any prior rewrites; append new applied ones.
                existing = score.get("rewrites") or []
                if not isinstance(existing, list):
                    existing = []
                score["rewrites"] = existing + applied
                score["score_before"] = score.get("answer_extractability")
                # Re-score happens on the next loop iteration; the
                # post-rewrite answer_extractability is recomputed there.
                state["aeo_score"] = score

            # Persist an audit row if any rewrite landed.
            if rewrites_block.get("applied"):
                _write_aeo_audit(state)
    except Exception as e:
        log.warning("AEO reviser post-processing failed: %s", e)

    return _reviser_telemetry_cb(callback_context)


_reviser_telemetry_cb = make_after_callback(
    agent_name="aeo_reviser",
    skill_id="aeo_revision",
    action_type="aeo_revise",
)


aeo_reviser_agent = LlmAgent(
    name="aeo_reviser",
    model=pick_model("gemini-3.5-flash"),   # same model as content/reviser — restructuring
    instruction=AEO_REVISER_INSTRUCTIONS,
    tools=[],
    output_key="draft",   # OVERWRITES content/reviser's draft
    after_model_callback=make_model_armor_callback(),
    after_agent_callback=_aeo_reviser_after_callback,
)


# ---------------------------------------------------------------------------
# Escalation gate — exits the loop early when the score is good enough OR
# when AEO is skip-eligible (null score).
# ---------------------------------------------------------------------------

class AeoEscalationGate(BaseAgent):
    """Deterministic gate. Reads ``state["aeo_score"]["answer_extractability"]``.
    Escalates (exits the loop) when score is None (skipped) or
    >= EXIT_THRESHOLD. Otherwise stays in the loop so the Reviser gets
    another pass."""

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        score_obj = ctx.session.state.get("aeo_score") or {}
        score = None
        if isinstance(score_obj, dict):
            score = score_obj.get("answer_extractability")

        should_escalate = score is None or (
            isinstance(score, (int, float)) and float(score) >= EXIT_THRESHOLD
        )
        yield Event(
            author=self.name,
            invocation_id=ctx.invocation_id,
            actions=EventActions(escalate=should_escalate),
        )


# ---------------------------------------------------------------------------
# Public composite: the AEO loop agent.
# ---------------------------------------------------------------------------

aeo_loop_agent = LoopAgent(
    name="aeo_loop",
    description=(
        "Score → Rewrite → Gate. Up to 2 iterations. Exits early when "
        f"answer_extractability >= {EXIT_THRESHOLD} or when the channel "
        "has no answer-engine surface (null score)."
    ),
    sub_agents=[
        aeo_scorer_agent,
        aeo_reviser_agent,
        AeoEscalationGate(name="aeo_gate"),
    ],
    max_iterations=2,
)


# ---------------------------------------------------------------------------
# Pass-through wrapper when AEO_SKIP=1
# ---------------------------------------------------------------------------

class _AeoPassThrough(BaseAgent):
    """No-op stand-in for the AEO loop when the kill switch is on. Always
    escalates immediately. Lets us toggle AEO off without unwiring the
    pipeline."""

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        yield Event(
            author=self.name,
            invocation_id=ctx.invocation_id,
            actions=EventActions(escalate=True),
        )


def aeo_pipeline_node() -> BaseAgent:
    """Return the right node to slot into the drafting pipeline. Honors
    AEO_SKIP=1 by returning the pass-through stub."""
    if _kill_switch():
        log.info("AEO_SKIP=1 — AEO loop replaced with pass-through")
        return _AeoPassThrough(name="aeo_skipped")
    return aeo_loop_agent
