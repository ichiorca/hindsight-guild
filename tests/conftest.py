"""Shared pytest fixtures + bootstrap.

Sets TELEMETRY_DISABLED=1 so callbacks don't try to write to BigQuery during
unit tests. Sets LOCAL_DEV=1 so shared.clients accessors return None / raise
instead of trying to construct GCP clients against missing ADC credentials.
Sets a sentinel PROJECT_ID. Override per-test if needed.
"""
import os

os.environ.setdefault("PROJECT_ID", "test-project")
os.environ.setdefault("REGION", "us-central1")
os.environ.setdefault("TELEMETRY_DISABLED", "1")
os.environ.setdefault("LOCAL_DEV", "1")

import pytest


@pytest.fixture(autouse=True)
def _isolate_gcp_client_cache():
    """Drop the shared.clients lru_cache between tests.

    ``bigquery_client``/``secret_manager_client``/``secret_value`` cache on
    first call keyed on the LOCAL_DEV/PROJECT_ID seen then. A test that
    monkeypatches those env vars would otherwise inherit a stale cached
    client (the BigQuery/Secret-Manager analog of the mongo_tools._CLIENTS
    leak). Resetting around each test keeps the cache from bleeding across
    cases. Lazy import so collection doesn't require google.cloud.
    """
    from shared.clients import _reset_cached_clients
    _reset_cached_clients()
    yield
    _reset_cached_clients()
