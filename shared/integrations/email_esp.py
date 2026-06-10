"""Email ESP integration — HubSpot Marketing Email DRAFTS.

Closes the lifecycle-email dead end: approving an email/lifecycle_email
draft persists the sequence to ``email_sequences`` (queue.py owns that
write) and, when HubSpot is configured, stages step 1 as a **DRAFT
marketing email** in HubSpot — never sent. Same governance contract as
the ads adapters (paused RSAs/creatives): the system stages, the founder
pulls the trigger inside the ESP's own UI.

Wiring:
  - Set ``HUBSPOT_API_TOKEN`` (private-app token with
    ``content``/``marketing-email`` scopes). The Secret Manager secret
    ``hubspot_api_token`` is mounted onto web-api; a missing/placeholder
    value (e.g. the literal "PENDING" stub) means not-configured and the
    publish step is skipped — the Mongo persistence still happens.

API: POST https://api.hubapi.com/marketing/v3/emails  (state DRAFT)
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

import httpx

log = logging.getLogger(__name__)

_HUBSPOT_BASE = "https://api.hubapi.com"
_DEFAULT_TIMEOUT = 20.0

# Values that mean "secret exists but was never filled in".
_PLACEHOLDERS = ("", "pending", "todo", "changeme", "<unset>")


class EmailEspNotConfigured(RuntimeError):
    """HUBSPOT_API_TOKEN unset/placeholder. Callers skip ESP staging."""


class EmailEspError(RuntimeError):
    """HubSpot returned a non-2xx. Wrapped so callers don't need httpx."""


@dataclass
class StagedEmail:
    id: str
    url: str          # deep link into the HubSpot email editor
    name: str
    state: str        # always "DRAFT" on this path


def _token() -> str:
    tok = (os.environ.get("HUBSPOT_API_TOKEN") or "").strip()
    if tok.lower() in _PLACEHOLDERS:
        raise EmailEspNotConfigured(
            "HUBSPOT_API_TOKEN unset (or placeholder) — staging skipped")
    return tok


def is_configured() -> bool:
    try:
        _token()
        return True
    except EmailEspNotConfigured:
        return False


def stage_draft_email(*, name: str, subject: str, body_html: str) -> StagedEmail:
    """Create a DRAFT marketing email in HubSpot and return its handle.

    Never sends. The founder reviews/sends from HubSpot's editor — the
    returned ``url`` is the queue badge's deep link.
    """
    tok = _token()
    payload = {
        "name": name[:120],
        "subject": subject[:200],
        "content": {"flexAreas": {}, "plainTextVersion": ""},
        "state": "DRAFT",
    }
    # The v3 create accepts custom HTML via content widgets only on some
    # plan tiers; plainTextVersion is universally accepted, so ship the
    # body there too — the founder polishes layout in the editor.
    payload["content"]["plainTextVersion"] = body_html
    try:
        r = httpx.post(
            f"{_HUBSPOT_BASE}/marketing/v3/emails",
            headers={"Authorization": f"Bearer {tok}"},
            json=payload,
            timeout=_DEFAULT_TIMEOUT,
        )
    except httpx.HTTPError as e:
        raise EmailEspError(f"HubSpot request failed: {e}") from e
    if r.status_code >= 300:
        raise EmailEspError(
            f"HubSpot create-email returned {r.status_code}: {r.text[:300]}")
    doc = r.json()
    email_id = str(doc.get("id", ""))
    return StagedEmail(
        id=email_id,
        url=f"https://app.hubspot.com/email/{email_id}/edit",
        name=payload["name"],
        state="DRAFT",
    )
