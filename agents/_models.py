"""Per-process model selection.

Production runs use gemini-3.5-flash for higher-quality drafting (Content,
CMO Planner, Lifecycle Email, Positioning, Paid Media, Self-Critique) and
gemini-3.1-flash-lite for the lighter agents (Research, Review, ImageBrief,
Analytics, Ops/QA, Customer Voice). Both are GA on Vertex AI as of
2026-Q2; gemini-3-pro-preview was discontinued 2026-03-26 so we route
heavy-lift slots through gemini-3.5-flash (frontier intelligence at
Flash latency).

Local-dev runs use the Gemini API direct endpoint (free tier from Google
AI Studio). When the local key hits quota or paid-only models, set:

    LOCAL_OVERRIDE_MODEL=gemini-3.1-flash-lite

in .env, and every agent's model resolves through ``pick_model()`` to
that override. Production deployments leave the env var unset and the
original model strings win.
"""
from __future__ import annotations

import os


def pick_model(default: str) -> str:
    """Return the runtime model name for an agent.

    ``default`` is what each agent was constructed with (typically
    "gemini-3.5-flash" or "gemini-3.1-flash-lite"). If
    ``LOCAL_OVERRIDE_MODEL`` is set, that string wins for every agent —
    useful when the local key can't access the frontier model but can call
    the lite variant.

    Future: per-agent overrides via env vars like ``MODEL_CONTENT_AGENT``.
    """
    override = os.environ.get("LOCAL_OVERRIDE_MODEL")
    if override:
        return override
    return default
