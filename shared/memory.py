"""Agent memory — institutional lessons stored in MongoDB.

Replaces the Vertex AI Memory Bank (no AGENT_ENGINE_ID / Agent Engine needed).
The Finalizer writes a one-sentence "what worked for this ICP" lesson after
each draft (``remember_lesson``); the Research agent recalls recent lessons for
the same scope before drafting (``recall``). Lessons live in the Mongo
collection ``agent_lessons``.

Scopes (the ``scope`` string namespace):
  - icp_segment:<id>   — what worked for whom
  - channel:<name>     — what worked where
  - campaign:<id>      — per-campaign history
  - skill:<id>         — playbook track-record narrative

Both functions are best-effort: memory must never fail a pipeline run.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

log = logging.getLogger(__name__)

_COLL = "agent_lessons"


async def remember_lesson(scope: str, lesson: str,
                           metadata: dict | None = None) -> None:
    """Persist a one-sentence lesson under a scoped key. Never raises."""
    if not scope or not lesson:
        return
    try:
        from shared import mongo_tools
        mongo_tools.db()[_COLL].insert_one({
            "scope": scope,
            "lesson": lesson,
            "metadata": metadata or {},
            "ts": datetime.now(UTC),
        })
    except Exception as e:  # noqa: BLE001 — memory writes must not block a run
        log.warning("remember_lesson (mongo) failed for %s: %s", scope, e)


async def recall(scope: str, query: str = "", top_k: int = 5) -> list[dict]:
    """Return lessons for a scope as ``[{content, score}]``.

    Pulls the most recent lessons for the scope; when ``query`` is given,
    lessons sharing keywords with it rank first (a cheap relevance nudge over
    pure recency). No vector index required — recent-per-scope is enough for
    cross-draft continuity, and the Research agent only needs a handful.
    """
    try:
        from shared import mongo_tools
        rows = list(
            mongo_tools.db()[_COLL]
            .find({"scope": scope})
            .sort("ts", -1)
            .limit(max(int(top_k or 5) * 3, 15))
        )
    except Exception as e:  # noqa: BLE001
        log.warning("recall (mongo) failed for %s: %s", scope, e)
        return []

    terms = {t for t in (query or "").lower().split() if len(t) > 3}

    def _kw_score(text: str) -> float:
        if not terms:
            return 0.0
        low = text.lower()
        return float(sum(1 for t in terms if t in low))

    scored = [
        {"content": r.get("lesson", ""), "score": _kw_score(r.get("lesson", ""))}
        for r in rows
    ]
    # Stable sort by keyword score keeps recency order within equal scores.
    scored.sort(key=lambda d: d["score"], reverse=True)
    return scored[: int(top_k or 5)]
