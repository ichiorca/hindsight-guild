"""Research Agent — pipeline node #1.

Writes findings to state['research_findings']. Has access to:
  - the customer-research skill via Tier 1/2/3 progressive disclosure
  - the mongodb tool surface (find, vector_search) for customer_voice +
    messaging_library + negative_examples
  - search_past_lessons: a FunctionTool over Vertex AI Memory Bank that
    surfaces "what worked for this ICP before" across prior drafting runs
  - **web_search** (AgentTool wrapping google_search) — pulls live,
    dated, citable facts for current / "latest" topics where our local
    data can't help.

The Finalizer at the pipeline tail writes one lesson per completed session
into Memory Bank scoped to icp_segment:<id>. Research recalls those here
so the team's "institutional continuity" claim is real, not aspirational.
"""
from __future__ import annotations

import logging

from google.adk.tools import FunctionTool

from agents._factory import make_llm_agent
from agents._models import LIGHT
from agents._prompts import RESEARCH_INSTRUCTIONS
from agents.web_search import web_search_tool

# mongo_tools defaults to mongo_uri_writer; explicit setter removed (see C5).

log = logging.getLogger(__name__)


async def search_past_lessons(icp_segment: str, query: str,
                               top_k: int = 5) -> list[dict]:
    """Search Memory Bank for past lessons under this ICP.

    Each lesson was persisted by the Finalizer at the end of a prior
    drafting run: it captures what the team produced for this ICP and what
    Review recommended. Use it to avoid repeating patterns that didn't land.

    Returns: list of {content, score} dicts. Empty list if Memory Bank
    isn't configured (AGENT_ENGINE_ID unset) — never raises.
    """
    try:
        from shared import memory as memory_mod
    except Exception as e:
        log.debug("memory module unavailable: %s", e)
        return []
    try:
        return await memory_mod.recall(
            scope=f"icp_segment:{icp_segment}",
            query=query,
            top_k=top_k,
        )
    except Exception as e:
        log.warning("search_past_lessons failed: %s", e)
        return []


search_past_lessons_tool = FunctionTool(func=search_past_lessons)


research_agent = make_llm_agent(
    name="research_agent",
    instructions=RESEARCH_INSTRUCTIONS,
    model=LIGHT,
    mode="write",
    output_key="research_findings",
    skill_id="research_scan",
    action_type="research_op",
    extra_tools=[search_past_lessons_tool, web_search_tool],
)
