"""Shared credential resolution for outbound integrations.

Every adapter (devto, linkedin, google_ads, meta_ads) gates on a few
env-var creds. When the env var is unset we *also* try Secret Manager
so the same code path works in Cloud Run.

The catch: Secret Manager's Python client blocks on ADC discovery
when no Google credentials are present — ~3s per missing key.
``is_configured()`` checks multiple keys, and the dispatcher's
``/api/integrations/status`` endpoint calls ``is_configured()`` for
four adapters. Cumulative wait was ~15s, which broke Playwright's
``waitForLoadState("networkidle")`` on every page load.

Fix: when ``LOCAL_DEV=1``, short-circuit the Secret Manager fallback.
The cloud path is unchanged — Secret Manager runs in Cloud Run where
ADC is wired up.
"""
from __future__ import annotations

import logging
import os
from functools import lru_cache

log = logging.getLogger(__name__)


def _local_dev() -> bool:
    return os.environ.get("LOCAL_DEV", "").strip() in ("1", "true", "True")


def secret_env(key: str, *, secret_name: str | None = None) -> str:
    """Resolve a credential value.

    Order:
      1. Env var ``key`` (always checked).
      2. Secret Manager secret named ``secret_name`` (defaults to
         ``key.lower()``). Skipped when ``LOCAL_DEV=1`` to keep local
         dev fast.
    Returns empty string when neither source has a value.
    """
    val = os.environ.get(key, "").strip()
    if val:
        return val
    if _local_dev():
        return ""
    return _fetch_secret(secret_name or key.lower())


@lru_cache(maxsize=64)
def _fetch_secret(secret_id: str) -> str:
    """Pull a secret from Secret Manager, cached per-process. We cache
    on success AND on failure (empty string) so a missing secret only
    blocks the first call — subsequent ``is_configured()`` checks
    return instantly even when creds are absent in the cloud."""
    try:
        from google.cloud import secretmanager
        project = os.environ.get("PROJECT_ID", "hindsight-guild-mvp")
        client = secretmanager.SecretManagerServiceClient()
        name = f"projects/{project}/secrets/{secret_id}/versions/latest"
        return client.access_secret_version(name=name).payload.data.decode().strip()
    except Exception as e:
        log.debug("%s not in Secret Manager: %s", secret_id, e)
        return ""
