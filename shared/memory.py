"""Vertex AI Memory Bank integration for the agent team.

Uses the real google.adk.memory.VertexAiMemoryBankService — verified against
ADK docs: https://adk.dev/sessions/memory/

Memory Bank extracts facts/preferences asynchronously from completed sessions
and lets agents recall them across conversations. We use it as the substrate
for cross-channel transfer and institutional continuity. Pricing as of Jan 28,
2026: $0.25 per 1,000 events or memories stored.

Memory scopes used in this build (encoded as user_id namespaces because the
Memory Bank service keys by (app_name, user_id)):
  - icp_segment:<id>   — what worked for whom
  - channel:<name>     — what worked where
  - campaign:<id>      — per-campaign history
  - skill:<id>         — playbook track-record narrative
"""
from __future__ import annotations

import logging
import os
from functools import lru_cache

from google.adk.memory import VertexAiMemoryBankService

log = logging.getLogger(__name__)

PROJECT_ID = os.environ.get("PROJECT_ID", "agentic-marketing-mvp")
LOCATION = os.environ.get("REGION", "us-central1")
APP_NAME = os.environ.get("AGENT_APP_NAME", "agentic-marketing")

# Agent Engine ID is the trailing segment of the agent engine resource name,
# e.g. projects/123/locations/us-central1/reasoningEngines/4567890 → "4567890".
# Populated by setup.sh into Secret Manager / env after the agent engine is
# created.
AGENT_ENGINE_ID = os.environ.get("AGENT_ENGINE_ID", "")


@lru_cache(maxsize=1)
def memory_service() -> VertexAiMemoryBankService:
    if not AGENT_ENGINE_ID:
        raise RuntimeError(
            "AGENT_ENGINE_ID env var not set. Run `gcloud ai reasoning-engines "
            "list` to find it after the Agent Engine is created, then export "
            "it into the agent's runtime."
        )
    return VertexAiMemoryBankService(
        project=PROJECT_ID,
        location=LOCATION,
        agent_engine_id=AGENT_ENGINE_ID,
    )


async def remember_lesson(scope: str, lesson: str,
                           metadata: dict | None = None) -> None:
    """Persist a one-sentence lesson under a scoped key (e.g. campaign:<id>).

    Wraps the lesson into a one-event session for Memory Bank to extract.
    Memory extraction runs asynchronously on the service side; expect a few
    seconds of latency before the memory is queryable.
    """
    from google.adk.sessions import InMemorySessionService

    sess = InMemorySessionService()
    session = await sess.create_session(
        app_name=APP_NAME, user_id=scope,
        state={"lesson": lesson, "metadata": metadata or {}},
    )
    # Append a single event so Memory Bank has something to extract from
    await sess.append_event(session, _LessonEvent(lesson))
    completed = await sess.get_session(
        app_name=APP_NAME, user_id=scope, session_id=session.id
    )
    await memory_service().add_session_to_memory(completed)


async def recall(scope: str, query: str, top_k: int = 5) -> list[dict]:
    """Semantic search Memory Bank for a scope. Returns list of {content, score}."""
    results = await memory_service().search_memory(
        app_name=APP_NAME, user_id=scope, query=query
    )
    return [{"content": m.content, "score": getattr(m, "score", None)}
            for m in results.memories[:top_k]]


class _LessonEvent:
    """Minimal event shape Memory Bank expects when adding a session.

    Recent ADK versions reach for ``event.partial`` during session
    persistence, so we expose that attribute too (False = full event,
    not a streamed chunk). Without it, ``add_session_to_memory`` raises
    ``AttributeError: '_LessonEvent' object has no attribute 'partial'``
    on every Finalizer run — non-blocking but noisy.
    """

    def __init__(self, lesson: str):
        from google.genai.types import Content, Part
        # Part.from_text() switched from positional to kw-only in newer
        # google.genai builds (>=1.x). Use the kw form — the old
        # positional call now raises TypeError.
        self.content = Content(parts=[Part.from_text(text=lesson)], role="user")
        self.author = "system"
        self.invocation_id = "lesson_event"
        # ADK ≥1.x checks `event.partial` during memory persistence.
        self.partial = False
        # Defensive: other attributes ADK occasionally introspects.
        self.actions = None
        self.error_code = None
        self.error_message = None
