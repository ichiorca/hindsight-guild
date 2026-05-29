"""bigquery_query FunctionTool. Attached to the CMO Planner in Agent Designer
via 'Add tool → Function'. If the playbook is later ported to ADK, add this
tool to the agent's tools=[...] list directly.

Read-only: only SELECT / WITH statements are accepted.
"""
from __future__ import annotations

import os

from google.adk.tools import FunctionTool
from google.cloud import bigquery

_bq: bigquery.Client | None = None


def _client() -> bigquery.Client:
    global _bq
    if _bq is None:
        _bq = bigquery.Client(project=os.environ["PROJECT_ID"])
    return _bq


def bigquery_query(query: str, max_rows: int = 100) -> list[dict]:
    """Read-only SELECT against analytics.*, monitoring.*, telemetry.*."""
    q = query.strip().upper()
    if not (q.startswith("SELECT") or q.startswith("WITH")):
        raise ValueError("Only SELECT/WITH queries are allowed.")
    rows = _client().query(query).result(max_results=max_rows)
    return [dict(r) for r in rows]


bigquery_query_tool = FunctionTool(func=bigquery_query)
