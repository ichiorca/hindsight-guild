"""Central provider for GCP service clients.

Why: 10+ files in this codebase each re-implement the "lazy-init a Google
client and cache it module-locally" pattern. Some use ``@lru_cache``,
some use a ``global _X`` mutation, some eagerly construct at import. The
result is hard-to-mock and slow to cold-start (every callsite pays its
own import cost).

This module centralizes the pattern. Callers do:

    from shared.clients import bigquery_client, secret_manager_client, secret_value

    bq = bigquery_client()
    sm = secret_manager_client()
    val = secret_value("mongo_uri")

Each accessor is:
  - lazy (no client built until first call)
  - cached (subsequent calls return the same instance)
  - importless until needed (the heavy ``google.cloud.*`` modules don't
    load until the first accessor call)

Switching the import-time behavior of every service depends on having
ONE place to change. Cold-start tuning, ADC mocking, regional client
selection, retry-policy injection — all happen here.

Local-dev fallback: when ``LOCAL_DEV=1`` is set, the BigQuery client is
NOT constructed (we'd just no-op every query anyway via shared.bigquery_helper).
The accessor returns None; callers that already check ``BQ is None``
keep working without change.
"""
from __future__ import annotations

import logging
import os
from functools import lru_cache

log = logging.getLogger(__name__)


def _local_dev() -> bool:
    return os.environ.get("LOCAL_DEV", "").lower() in ("1", "true", "yes")


def _project_id() -> str:
    return os.environ.get("PROJECT_ID", "agentic-marketing-mvp")


# ---------------------------------------------------------------------------
# BigQuery
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def bigquery_client():
    """Return a cached BigQuery client. None in LOCAL_DEV mode.

    Callers that branch on the None return for local-dev behavior keep
    working — same convention every service already uses for
    ``BQ = None``.
    """
    if _local_dev():
        log.info("bigquery_client(): LOCAL_DEV=1; returning None")
        return None
    from google.cloud import bigquery
    return bigquery.Client(project=_project_id())


# ---------------------------------------------------------------------------
# Secret Manager
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def secret_manager_client():
    """Return a cached Secret Manager client. Raises in LOCAL_DEV if
    accessed — local-dev callers should use MONGO_URI_DIRECT / per-secret
    env-var overrides instead of fetching from Secret Manager.
    """
    if _local_dev():
        # In local-dev there's no ADC; constructing this client would
        # raise from inside google.auth. Surface a clearer error.
        raise RuntimeError(
            "secret_manager_client() called in LOCAL_DEV mode. Use an "
            "env-var override (e.g. MONGO_URI_DIRECT, GOOGLE_API_KEY) "
            "instead of fetching from Secret Manager."
        )
    from google.cloud import secretmanager
    return secretmanager.SecretManagerServiceClient()


@lru_cache(maxsize=64)
def secret_value(name: str) -> str:
    """Fetch a Secret Manager secret value, cached per name. Returns the
    decoded UTF-8 string of the secret's ``latest`` version.

    Raises in LOCAL_DEV (the secret value should come from an env-var
    override; callers should detect this and prefer the env path).
    """
    if _local_dev():
        # Allow callers to detect missing-secret as opposed to crashing,
        # via the env-var pattern they already use.
        raise RuntimeError(
            f"secret_value({name!r}) called in LOCAL_DEV mode. Set the "
            f"value via env var or use MONGO_URI_DIRECT-style override."
        )
    sm = secret_manager_client()
    path = f"projects/{_project_id()}/secrets/{name}/versions/latest"
    return sm.access_secret_version(name=path).payload.data.decode()


# ---------------------------------------------------------------------------
# Test hooks
# ---------------------------------------------------------------------------

def _reset_cached_clients() -> None:
    """Drop every cached client. Used by tests that want a clean slate
    between cases. Not part of the public API."""
    bigquery_client.cache_clear()
    secret_manager_client.cache_clear()
    secret_value.cache_clear()
