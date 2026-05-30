"""Finalizer — the pipeline's tail node.

ADK's ``to_a2a()`` exposes the *last* sub-agent's output as the A2A response.
That means whatever Research, Content, ImageBrief wrote into state is
unreachable from a JSON-RPC caller — only Review's text comes back.

The Finalizer's job is to emit one JSON object with every state slot the
UI's /api/draft (and any other A2A caller) needs. We implement it as a
non-LLM BaseAgent — no model call, no creativity, no chance of the
LLM emitting malformed JSON. Just dump selected state.

Why not an LlmAgent: template substitution on dict-valued state slots
(research_findings, image, review) renders Python ``repr()`` (e.g.,
``{'flags': [...]}``), not JSON. A non-LLM agent serializes state as
proper JSON deterministically.

Side effect: the Finalizer also pushes a short lesson into Vertex AI
Memory Bank, scoped by ICP, so future Research-agent sessions can recall
"what worked for this ICP" via the search_memory tool. Best-effort —
Memory Bank failures must never abort the pipeline.
"""
from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator
from datetime import datetime

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event
from google.genai.types import Content, Part  # ADK re-exports Content/Part

# from google.genai.types; both paths work in 1.x. Stick with genai to keep
# imports consistent with how the rest of the codebase references them.

log = logging.getLogger(__name__)

# State slots the finalizer surfaces to A2A callers. Add new slots here
# when the pipeline gains a new output_key the UI needs.
_FINALIZER_KEYS = (
    "telemetry_id",
    "channel",
    "icp_segment",
    "draft",
    "research_findings",
    "image",
    "images",
    "review",
)


def _json_default(o):
    """Tolerate datetime + other non-JSON-native types in state."""
    if isinstance(o, datetime):
        return o.isoformat()
    return str(o)


class _StateEnvelopeAgent(BaseAgent):
    """Emit a JSON envelope assembled from selected session-state slots,
    and push a Memory Bank lesson scoped to this run's ICP segment."""

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        state = ctx.session.state if ctx.session else {}

        # 1. Best-effort Memory Bank write — fire and forget; never blocks
        #    the envelope emission below.
        await _persist_lesson(state)

        # 2. Assemble + emit the envelope.
        try:
            payload = {k: state.get(k) for k in _FINALIZER_KEYS}
            # ImageBrief emits a LIST into state["images"]. Surface the first as
            # `image` so single-image consumers (older UI, telemetry) still work.
            imgs = payload.get("images")
            if isinstance(imgs, list) and imgs and not payload.get("image"):
                payload["image"] = imgs[0]
            text = json.dumps(payload, default=_json_default)
        except Exception as e:
            log.exception("finalizer failed to assemble envelope: %s", e)
            text = json.dumps({"error": "finalizer_failed", "detail": str(e)})

        yield Event(
            author=self.name,
            content=Content(parts=[Part(text=text)]),
        )


async def _persist_lesson(state: dict) -> None:
    """Push a one-line lesson into Memory Bank under icp_segment:<id>.

    The lesson is a compact "what worked": draft snippet + which approved
    claim it leaned on + the review recommendation. Memory Bank does the
    async fact extraction on its side; the Research agent's search_memory
    tool later surfaces relevant prior runs.
    """
    icp = state.get("icp_segment")
    draft = state.get("draft")
    if not icp or not draft:
        return
    try:
        from shared import memory as memory_mod
    except Exception as e:
        log.debug("memory module unavailable; skipping persist: %s", e)
        return
    try:
        # Flatten substack dict drafts to a single string for the lesson.
        if isinstance(draft, dict):
            draft_text = (
                draft.get("body_markdown")
                or draft.get("body")
                or draft.get("text")
                or json.dumps(draft, default=_json_default)
            )
        else:
            draft_text = str(draft)
        snippet = draft_text[:240].rsplit(" ", 1)[0]

        review = state.get("review") or {}
        recommendation = review.get("recommendation") if isinstance(review, dict) else None

        rf = state.get("research_findings") or {}
        approved_claims = rf.get("approved_claims") if isinstance(rf, dict) else None
        claim_anchor = ""
        if approved_claims:
            first = approved_claims[0]
            claim_anchor = first.get("claim_text") if isinstance(first, dict) else str(first)

        lesson = (
            f"Channel={state.get('channel')}, recommendation={recommendation}. "
            f"Draft opened: \"{snippet}\". "
            + (f"Anchored on claim: \"{claim_anchor[:160]}\"." if claim_anchor else "")
        )
        await memory_mod.remember_lesson(
            scope=f"icp_segment:{icp}",
            lesson=lesson,
            metadata={
                "telemetry_id": state.get("telemetry_id"),
                "channel": state.get("channel"),
                "recommendation": recommendation,
            },
        )
    except Exception as e:
        # Memory Bank may be unavailable (AGENT_ENGINE_ID unset in dev) or
        # rate-limited — never block the pipeline on it.
        log.warning("memory persist failed: %s", e)


finalizer_agent = _StateEnvelopeAgent(
    name="finalizer",
    description=(
        "Tail node of the drafting pipeline. Emits one JSON envelope with "
        "telemetry_id + channel + icp_segment + draft + research_findings + "
        "image + review so A2A callers get the full pipeline output instead "
        "of just the last sub-agent's text."
    ),
)
