"""Web-search sub-agent.

ADK constraint: ``google_search`` (Gemini's grounded-search built-in) can't
coexist with custom FunctionTools in the same LlmAgent — the model can be
in tool-calling mode OR grounded-search mode, not both. The standard
workaround is to put ``google_search`` inside its own LlmAgent and expose
that agent to the calling agent via ``AgentTool``.

This module wires that micro-agent. Research uses it as a normal tool
(``web_search(query=...)``) and gets back a factual summary with sources.
"""
from __future__ import annotations

from google.adk.agents import LlmAgent
from google.adk.tools import google_search
from google.adk.tools.agent_tool import AgentTool

from agents._models import LIGHT, gen_content_config, pick_model

_SEARCH_INSTRUCTIONS = """You are the team's web-search specialist. Given a
query, return the most useful, RECENT, and factually anchored information
the web has on it — not a generic summary.

How to respond:
  1. Use ``google_search`` to pull live results. Prefer 2025–2026 sources
     over older posts. Skip listicles and SEO spam; weight blog posts from
     known operators, primary docs, vendor announcements, recent academic
     papers, and reputable news.
  2. Return a STRUCTURED summary:
       FINDINGS:
         - <fact 1> [source: <url>]
         - <fact 2> [source: <url>]
         - ...
       RECENT DEVELOPMENTS (last 6 months):
         - <event> [source: <url>]
       KEY PLAYERS / NAMED EXAMPLES:
         - <name>: <one-line context>
       UNCERTAINTIES:
         - <thing the agent should NOT claim without further evidence>
  3. Keep facts that are dated, named, and citable. Avoid platitudes,
     framework summaries, or generic explanations.
  4. If the search returns nothing useful, say so explicitly — do not
     invent.

You are read-only. Your job is information surface area for the caller,
not opinion.
"""

_web_search_agent = LlmAgent(
    name="web_search",
    model=pick_model(LIGHT),
    generate_content_config=gen_content_config(pick_model(LIGHT)),
    description=(
        "Web-search specialist. Call this when you need recent / current "
        "information that wouldn't be in our local data (latest "
        "advancements, news, vendor announcements, named examples)."
    ),
    instruction=_SEARCH_INSTRUCTIONS,
    tools=[google_search],
)


web_search_tool = AgentTool(agent=_web_search_agent)
