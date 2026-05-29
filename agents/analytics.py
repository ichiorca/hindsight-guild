"""Analytics Agent — used as a tool by the CMO Planner.

Reads BigQuery for the weekly performance snapshot, drift signals, and
candidate playbook tracking. Returns JSON the CMO Planner splices into the
weekly memo.
"""
from __future__ import annotations

import os

from google.adk.tools import FunctionTool

from agents._factory import make_llm_agent
from agents._prompts import ANALYTICS_INSTRUCTIONS
from shared.bigquery_helper import bigquery_query

PROJECT_ID = os.environ.get("PROJECT_ID", "agentic-marketing-mvp")

# Shared BQ helper — degrades to [] in LOCAL_DEV instead of raising.
bigquery_query_tool = FunctionTool(func=bigquery_query)

# Analytics doesn't touch Mongo directly — BigQuery is its data plane.
# Pass mode=None to skip the mongodb_toolset wiring.
analytics_agent = make_llm_agent(
    name="analytics_agent",
    instructions=ANALYTICS_INSTRUCTIONS,
    model="gemini-3.1-flash-lite",
    mode=None,
    output_key="analytics_snapshot",
    skill_id="weekly_snapshot",
    action_type="analytics_op",
    extra_tools=[bigquery_query_tool],
)
