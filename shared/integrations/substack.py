"""Substack publishing integration (routing/status shim).

Unlike the other adapters in this package, the actual Substack publish runs
in a dedicated Cloud Run service (``services/substack_publisher``) because it
needs BigQuery draft reads, idempotency, and incident-opening that don't belong
in the web-api request path. This module exists so the ``CHANNEL_ROUTES`` table
and the ``/api/integrations/status`` endpoint can represent Substack as a
first-class destination — with its own platform name and a real
``is_configured()`` check — instead of mislabeling the ``substack`` channel as
Dev.to (which it used to be routed to).

The web-api decision handler triggers the publisher service over HTTP (see
``services/web_api/routers/queue._publish_substack``); the Substack REST calls
themselves live in ``services/substack_publisher/substack_client``.
"""
from __future__ import annotations

from ._secrets import secret_env


def is_configured() -> bool:
    """True when the Substack publisher has the creds it needs: an API key
    plus a publication target (host or id). Mirrors ``SubstackClient.available``.
    No network calls — safe for the status endpoint to call per request."""
    api_key = secret_env("SUBSTACK_API_KEY", secret_name="substack_api_key")
    host = secret_env("SUBSTACK_PUBLICATION_HOST",
                      secret_name="substack_publication_host")
    pub_id = secret_env("SUBSTACK_PUBLICATION_ID",
                        secret_name="substack_publication_id")
    return bool(api_key and (host or pub_id))
