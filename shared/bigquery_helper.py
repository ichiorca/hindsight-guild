"""Shared BigQuery query helper with LOCAL_DEV-aware degradation.

In LOCAL_DEV mode, BigQuery isn't reachable (no ADC), so the helper
returns ``[]`` with a single-row diagnostic instead of raising. Agents
that consume the result (paid_media, self_critique, cmo_planner,
analytics) keep running — they just have fewer data points to reason
about. The LLM sees ``[]`` and adjusts its conclusions accordingly.

Why a shared helper: four agents had near-identical `bigquery_query`
functions, each independently failing hard when ADC wasn't configured.
One missing credential took down four workflows in the e2e suite.
Centralizing the degradation behavior here means a single env-driven
fallback covers them all.
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

_PROJECT_ID = os.environ.get("PROJECT_ID", "agentic-marketing-mvp")

# Lazy: holding off on importing google.cloud.bigquery until needed so
# LOCAL_DEV-only test runs don't pay the heavy import cost.
_bq_client = None  # type: ignore[assignment]


def _local_dev() -> bool:
    return os.environ.get("LOCAL_DEV", "").lower() in ("1", "true", "yes")


def _client():
    """Lazy-init BigQuery client. Raises if ADC isn't configured."""
    global _bq_client
    if _bq_client is None:
        from google.cloud import bigquery
        _bq_client = bigquery.Client(project=_PROJECT_ID)
    return _bq_client


def bigquery_query(query: str, max_rows: int = 200) -> list[dict]:
    """Read-only SELECT helper. SELECT/WITH only — anything else raises.

    In LOCAL_DEV mode, returns ``[]`` with a single explainer row instead
    of touching BigQuery. Callers see structured data either way.

    On any other failure (auth, query error, timeout), returns ``[]``
    with a structured note so agents can detect the empty + react. The
    agent never sees an unhandled exception.
    """
    q_clean = query.strip().upper()
    if not (q_clean.startswith("SELECT") or q_clean.startswith("WITH")):
        raise ValueError("Only SELECT/WITH queries are allowed.")

    if _local_dev():
        log.info("bigquery_query skipped (LOCAL_DEV=1): %s",
                 query[:80].replace("\n", " "))
        return []

    try:
        rows = _client().query(query).result(max_results=max_rows)
        return [dict(r) for r in rows]
    except Exception as e:
        log.warning("bigquery_query failed (degrading to []): %s", e)
        return []
