"""LinkedIn UGC (User Generated Content) post integration.

Posts text content as either a personal share or a company page share
via the LinkedIn UGC API (``POST /v2/ugcPosts``).

API reference: https://learn.microsoft.com/en-us/linkedin/marketing/integrations/community-management/shares/ugc-post-api

Credentials (set in .env or Secret Manager):
  - ``LINKEDIN_ACCESS_TOKEN`` — OAuth 2.0 token with ``w_member_social``
    scope (personal) or ``w_organization_social`` (company page).
    Free to obtain via https://www.linkedin.com/developers — create an
    app, request ``Share on LinkedIn`` product, generate a personal
    access token. Production-grade access requires LinkedIn Marketing
    Developer Platform approval (1-4 week review), but personal-app
    tokens work end-to-end without that.
  - ``LINKEDIN_AUTHOR_URN`` — identifies who posts. Either
    ``urn:li:person:<id>`` (your own user ID) or
    ``urn:li:organization:<id>`` (a company page you admin).
    Get your person URN via ``GET https://api.linkedin.com/v2/me``
    with the access token — the ``id`` field is your URN suffix.

Operational notes:
  - LinkedIn's UGC payload caps body text at 3000 chars. Drafts longer
    than that get truncated with a "[continued in original …]" tail.
  - We post as ``visibility: PUBLIC`` and ``lifecycleState: PUBLISHED``.
    LinkedIn doesn't support "draft" posts via API — anything sent is
    immediately live on the author's profile / page.
  - No image / multi-asset support in this v1 — text-only posts. Image
    attach uses a separate two-step asset upload flow.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import httpx

from ._secrets import secret_env

log = logging.getLogger(__name__)

_LI_BASE = "https://api.linkedin.com"
_DEFAULT_TIMEOUT = 15.0
_MAX_BODY_CHARS = 3000

# We post to the LEGACY unversioned API at ``/v2/ugcPosts``. That
# endpoint accepts a stable payload shape and does NOT require the
# ``LinkedIn-Version`` header (which is only meaningful on the
# Versioned API at ``/rest/posts``). When LinkedIn finally sunsets
# ``/v2/`` — they keep moving the deadline — switch to ``/rest/posts``
# and add ``LinkedIn-Version: YYYYMM`` to the header dict below.


class LinkedInNotConfigured(RuntimeError):
    """Raised when LINKEDIN_ACCESS_TOKEN or LINKEDIN_AUTHOR_URN are
    missing. Callers should treat as "skip publish"."""


class LinkedInError(RuntimeError):
    """Non-2xx response from LinkedIn."""


@dataclass
class PublishedPost:
    post_urn: str            # urn:li:share:1234567890 — LinkedIn's stable ID
    url: str                 # https://www.linkedin.com/feed/update/<urn>/
    author_urn: str
    text_length: int


def _access_token() -> str:
    return secret_env("LINKEDIN_ACCESS_TOKEN")


def _author_urn() -> str:
    return secret_env("LINKEDIN_AUTHOR_URN")


def is_configured() -> bool:
    """Cheap check; no network calls. The UI gates the 'Will publish to
    LinkedIn' badge on this."""
    return bool(_access_token() and _author_urn())


def _strip_markdown(body: str) -> str:
    """LinkedIn UGC API doesn't render markdown. Strip the common
    markers so the post looks reasonable as plain text:
      - Headings: drop the leading ``#``s
      - Bold/italic: drop the asterisks/underscores
      - Inline code: drop the backticks
      - Links: convert ``[text](url)`` to ``text (url)``
      - Blockquotes: drop leading ``> ``
    """
    s = body or ""
    # Links first (they may contain other markers inside)
    s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", s)
    s = re.sub(r"^#{1,6}\s+", "", s, flags=re.MULTILINE)
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)
    s = re.sub(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)", r"\1", s)
    s = re.sub(r"__(.+?)__", r"\1", s)
    s = re.sub(r"`+", "", s)
    s = re.sub(r"^>\s?", "", s, flags=re.MULTILINE)
    return s.strip()


def publish_post(
    body: str,
    *,
    visibility: str = "PUBLIC",
) -> PublishedPost:
    """Create a UGC post on the configured author's feed. Raises
    ``LinkedInNotConfigured`` when creds are missing.

    ``visibility`` is ``PUBLIC`` (everyone) or ``CONNECTIONS`` (1st
    degree only). LinkedIn doesn't support per-post audience targeting
    beyond that — for narrower targeting use Sponsored Content via the
    Marketing API.
    """
    token = _access_token()
    author = _author_urn()
    if not token or not author:
        raise LinkedInNotConfigured(
            "LINKEDIN_ACCESS_TOKEN or LINKEDIN_AUTHOR_URN not set"
        )

    plain = _strip_markdown(body)
    if len(plain) > _MAX_BODY_CHARS:
        plain = plain[: _MAX_BODY_CHARS - 32] + "… [continued in original]"

    payload = {
        "author": author,
        "lifecycleState": "PUBLISHED",
        "specificContent": {
            "com.linkedin.ugc.ShareContent": {
                "shareCommentary": {"text": plain},
                "shareMediaCategory": "NONE",
            }
        },
        "visibility": {
            "com.linkedin.ugc.MemberNetworkVisibility": visibility,
        },
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-Restli-Protocol-Version": "2.0.0",
    }

    try:
        r = httpx.post(
            f"{_LI_BASE}/v2/ugcPosts",
            headers=headers, json=payload, timeout=_DEFAULT_TIMEOUT,
        )
    except httpx.HTTPError as e:
        raise LinkedInError(f"LinkedIn post failed: {e}") from e

    if r.status_code not in (200, 201):
        raise LinkedInError(
            f"LinkedIn returned HTTP {r.status_code}: {r.text[:300]}"
        )

    # The response includes the new post URN in the ``x-restli-id``
    # header (preferred) or the body's ``id`` field.
    post_urn = r.headers.get("x-restli-id") or r.json().get("id", "")
    if not post_urn:
        raise LinkedInError(f"LinkedIn 2xx but no post URN: {r.text[:300]}")

    # Build the public URL. LinkedIn's URN format is ``urn:li:share:NNN``
    # and the public URL is ``https://www.linkedin.com/feed/update/urn:li:share:NNN/``.
    public_url = f"https://www.linkedin.com/feed/update/{post_urn}/"

    return PublishedPost(
        post_urn=post_urn,
        url=public_url,
        author_urn=author,
        text_length=len(plain),
    )
