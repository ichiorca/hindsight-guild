"""A2A client helpers for cross-agent calls from non-agent code.

Cloud Run jobs (outcome_attach, drift_detect, self_critique, promotion_gate)
and external triggers call the live agents via A2A rather than importing
them in-process. This is what makes the system a real team rather than a
shared codebase — same protocol, same surface, whether the caller is another
agent, a Cloud Run job, or an external SaaS.

URL resolution order (first hit wins):
  1. Env var ``A2A_URL_<NAME>`` (e.g., ``A2A_URL_RESEARCH``). Set this for
     local dev or when the caller's container is wired with the URLs as env.
  2. Secret Manager secret ``a2a_url_<name>`` (lowercase). This is what
     ``deploy.sh`` writes after each Cloud Run service comes up, so cron jobs
     resolve URLs without needing per-job env wiring.

The Secret Manager lookup is cached per process so the API is cheap to call.
"""
from __future__ import annotations

import logging
import os
from typing import Any

import httpx

log = logging.getLogger(__name__)

PROJECT_ID = os.environ.get("PROJECT_ID", "hindsight-guild-mvp")

_URL_CACHE: dict[str, str] = {}
_CARD_CACHE: dict[str, dict] = {}
_sm = None  # SecretManagerServiceClient — lazy import to keep dev imports cheap


def _from_secret_manager(secret_name: str) -> str | None:
    global _sm
    if _sm is None:
        try:
            from google.cloud import secretmanager
            _sm = secretmanager.SecretManagerServiceClient()
        except Exception as e:
            log.warning("secret manager client unavailable: %s", e)
            return None
    try:
        path = f"projects/{PROJECT_ID}/secrets/{secret_name}/versions/latest"
        return _sm.access_secret_version(name=path).payload.data.decode()
    except Exception as e:
        log.debug("secret %s not found: %s", secret_name, e)
        return None


def _agent_url(agent_name: str) -> str:
    """Resolve the Cloud Run URL for the named agent's A2A endpoint.

    Falls through env -> Secret Manager. Caches the answer per process.
    """
    if agent_name in _URL_CACHE:
        return _URL_CACHE[agent_name]
    env_key = f"A2A_URL_{agent_name.upper()}"
    url = os.environ.get(env_key)
    if not url:
        url = _from_secret_manager(f"a2a_url_{agent_name}")
    if not url:
        raise RuntimeError(
            f"A2A URL not set: tried env {env_key} and secret a2a_url_{agent_name}"
        )
    resolved = url.rstrip("/")
    _URL_CACHE[agent_name] = resolved
    return resolved


def _auth_headers(target_url: str) -> dict:
    """Authorization header to invoke a PRIVATE Cloud Run A2A service.

    Cloud Run service-to-service auth needs a Google-signed ID token whose
    audience is the receiving service's URL, minted from the metadata server
    for the running service account (sa-agents). Without it a private service
    returns 403. No-op in LOCAL_DEV where A2A servers are plain processes.
    """
    if os.environ.get("LOCAL_DEV"):
        return {}
    try:
        from urllib.parse import urlsplit

        import google.auth.transport.requests
        import google.oauth2.id_token

        parts = urlsplit(target_url)
        audience = f"{parts.scheme}://{parts.netloc}"
        token = google.oauth2.id_token.fetch_id_token(
            google.auth.transport.requests.Request(), audience)
        return {"Authorization": f"Bearer {token}"}
    except Exception as e:  # noqa: BLE001 — best-effort; a 403 surfaces if it fails
        log.warning("could not mint ID token for %s: %s", target_url, e)
        return {}


def get_agent_card(agent_name: str) -> dict:
    """Fetch (and cache) the agent card — A2A capability discovery.

    The card at ``/.well-known/agent-card.json`` advertises the agent's
    JSON-RPC endpoint (``url``) + skills + capabilities. ``call_agent`` uses
    it to resolve the RPC endpoint rather than assuming it equals the base
    service URL.
    """
    if agent_name in _CARD_CACHE:
        return _CARD_CACHE[agent_name]
    url = f"{_agent_url(agent_name)}/.well-known/agent-card.json"
    r = httpx.get(url, timeout=10, headers=_auth_headers(url))
    r.raise_for_status()
    card = r.json()
    _CARD_CACHE[agent_name] = card
    return card


def _rpc_endpoint(agent_name: str) -> str:
    """Resolve the JSON-RPC endpoint via A2A agent-card discovery, falling
    back to the base service URL if the card is unreachable or omits ``url``."""
    try:
        card = get_agent_card(agent_name)
        advertised = (card.get("url") or "").rstrip("/")
        if advertised:
            return advertised
    except Exception as e:
        log.warning("agent-card discovery failed for %s (%s); using base URL",
                    agent_name, e)
    return _agent_url(agent_name)


def call_agent(agent_name: str, message: str | dict,
                session_id: str | None = None,
                timeout: float = 60.0) -> dict[str, Any]:
    """Send a message to a deployed A2A agent and return its response.

    Resolves the JSON-RPC endpoint via agent-card discovery (per the A2A
    protocol), then posts the task. The agent runs the request and returns a
    structured task result. Sessions are stateful across calls when
    session_id is reused.
    """
    url = _rpc_endpoint(agent_name)
    payload = {
        "jsonrpc": "2.0",
        "id": session_id or "1",
        "method": "tasks/send",
        "params": {
            "id": session_id or "task-1",
            "message": {
                "role": "user",
                "parts": [{"type": "text", "text": message if isinstance(message, str) else str(message)}],
            },
        },
    }
    r = httpx.post(url, json=payload, timeout=timeout, headers=_auth_headers(url))
    r.raise_for_status()
    return r.json()


def call_drafting_pipeline(icp_segment: str, channel: str,
                            experiment_id: str | None = None,
                            session_id: str | None = None) -> dict:
    """Convenience: invoke the Research→Content→Review pipeline via A2A."""
    msg = (
        f"Run the drafting pipeline for ICP={icp_segment}, channel={channel}, "
        f"experiment_id={experiment_id or 'none'}."
    )
    return call_agent("pipeline", msg, session_id=session_id)
