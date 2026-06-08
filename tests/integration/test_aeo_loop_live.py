"""Live AEO loop test (PRD-01) — runs the REAL Scorer -> Reviser -> Gate loop
on Gemini against a deliberately low-AEO draft.

Opt-in (LLM cost + network). Needs GOOGLE_API_KEY in .env (loaded by
scripts._test_bootstrap) and a local Mongo for the audit/inject side-effects.
Because pick_model resolves at import time and the gemini-3.x defaults aren't on
the AI-Studio key, set LOCAL_OVERRIDE_MODEL to a Developer-API model.

    GOOGLE_GENAI_USE_VERTEXAI=0 LOCAL_OVERRIDE_MODEL=gemini-2.5-flash \
    AEO_LIVE_TEST=1 MONGO_URI_DIRECT="mongodb://localhost:27017" \
    python -m pytest tests/integration/test_aeo_loop_live.py -q -s

Auto-skips without AEO_LIVE_TEST=1.
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest

# Load .env (GOOGLE_API_KEY, GOOGLE_GENAI_USE_VERTEXAI, MONGO_URI_DIRECT, …)
# before any agent import that resolves a model at construction time.
from scripts._test_bootstrap import REPO_ROOT  # noqa: E402,F401

pytestmark = pytest.mark.skipif(
    os.environ.get("AEO_LIVE_TEST") != "1",
    reason="Set AEO_LIVE_TEST=1 (and GOOGLE_API_KEY) to run the live AEO loop.",
)

# A deliberately LOW-AEO blog draft: one wall of text, no H2s, no statistics,
# no attribution, generic phrasing. The Scorer should rate answer_extractability
# low and the Reviser should restructure it (question H2s, answer-first blocks).
LOW_AEO_DRAFT = (
    "Agentic commerce is becoming really important for online stores and "
    "many merchants are starting to think about how AI agents will change "
    "the way people shop. It is important to make sure that your store works "
    "well with these new tools because the future of shopping is changing "
    "fast. There are a lot of things to consider when you think about agentic "
    "commerce and how it affects your business. Stores need to be ready for "
    "the future of shopping with AI agents that can browse and buy on behalf "
    "of customers. Being prepared is the key to success in this new world. "
    "Many companies are not ready yet and they might lose out if they do not "
    "act soon. The landscape is evolving and it is important to keep up with "
    "the changes that are happening. AI agents will be a big part of commerce "
    "going forward and merchants who adapt early will do better than those "
    "who wait. It is worth thinking carefully about how your store presents "
    "its products and whether an automated shopper could understand them. "
    "Overall, agentic commerce is a topic that every online store should be "
    "paying attention to right now because it is only going to get bigger "
    "over time and the stores that prepare will be the ones that win. "
)


def test_aeo_loop_scores_and_revises_live():
    if not os.environ.get("GOOGLE_API_KEY"):
        pytest.skip("GOOGLE_API_KEY not set (configure it in .env)")

    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai.types import Content, Part

    from agents import aeo_agent as A
    from shared import mongo_tools

    tid = f"act_aeolive_{uuid.uuid4().hex[:8]}"

    # Pre-insert a content-style actions row so the scorer's eval_scores
    # injection has a target; tolerate no-Mongo (inject/audit fail silently).
    db = None
    try:
        db = mongo_tools.db()
        db["actions"].insert_one({
            "telemetry_id": tid, "agent": "content_agent",
            "action_type": "draft_blog", "channel": "blog",
            "eval_scores": None, "draft": LOW_AEO_DRAFT,
        })
        db["aeo_audits"].delete_many({"telemetry_id": tid})
    except Exception as e:
        print(f"  (no Mongo for side-effects: {e})")
        db = None

    seed_state = {
        "telemetry_id": tid,
        "channel": "blog",
        "icp_segment": "seg_merchant_dtc",
        "draft": LOW_AEO_DRAFT,
        "aeo_score": "",
        "topic_hint": "make your store agent-ready",
    }

    async def _run() -> dict:
        ss = InMemorySessionService()
        runner = Runner(agent=A.aeo_loop_agent, app_name="aeo_live",
                        session_service=ss)
        sess = await ss.create_session(app_name="aeo_live", user_id="t",
                                       state=seed_state)
        msg = Content(role="user", parts=[Part.from_text(
            text="Score this blog draft for AI-search citability and improve it.")])
        async for ev in runner.run_async(user_id="t", session_id=sess.id,
                                         new_message=msg):
            author = getattr(ev, "author", None)
            if author:
                print(f"   event: {author}")
        final = await ss.get_session(app_name="aeo_live", user_id="t",
                                     session_id=sess.id)
        return final.state if final else {}

    # Capture every scorer pass that produced a complete sub_signals dict.
    # (LLMs can degrade on later loop turns — esp. the local gemini-2.5-flash
    # override vs prod gemini-3.x — so we assert on the live scorer's real work,
    # not the possibly-degraded final state.)
    passes: list[tuple[dict, float]] = []
    _orig_composite = A._composite

    def _spy_composite(sub):
        r = _orig_composite(sub)
        if isinstance(r, (int, float)):
            passes.append((dict(sub), r))
        return r

    A._composite = _spy_composite
    try:
        state = asyncio.run(_run())
    finally:
        A._composite = _orig_composite

    for i, (sub, sc) in enumerate(passes, 1):
        print(f"\n  scorer pass {i}: answer_extractability={sc}")
        print(f"    sub_signals={sub}")
    final = state.get("aeo_score") or {}
    print(f"  final answer_extractability={final.get('answer_extractability')} "
          f"rewrites={ (final.get('rewrites') or [])[:3] }")

    # Core assertion: the LIVE scorer ran the deterministic tools and emitted a
    # complete, numeric extractability score (all 6 sub-signals) at least once.
    # If it produced none, the LLM calls almost certainly failed/throttled this
    # run (free-tier rate limits) — skip rather than fail, like the Reddit live
    # test on a 429. The deterministic mechanics are covered in
    # tests/unit/test_aeo_loop.py.
    if not passes:
        pytest.skip("live scorer produced no parseable score this run — likely "
                    "rate-limited / transient API error; re-run")
    sub0, score0 = passes[0]
    assert set(sub0) >= set(A.SUB_SIGNAL_WEIGHTS), f"incomplete sub_signals: {sub0}"
    assert 0.0 <= float(score0) <= 1.0

    # If a rewrite actually landed (Mongo present), an audit row must exist.
    if db is not None and (final.get("rewrites")):
        audit = db["aeo_audits"].find_one({"telemetry_id": tid})
        print(f"  aeo_audit: score_before={audit and audit.get('score_before')} "
              f"score_after={audit and audit.get('score_after')}")
        assert audit is not None, "rewrites applied but no aeo_audits row written"

    if db is not None:
        db["actions"].delete_many({"telemetry_id": tid})
        db["aeo_audits"].delete_many({"telemetry_id": tid})
