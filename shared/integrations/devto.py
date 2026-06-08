"""Dev.to (Forem) publishing integration.

Why Dev.to:
  - 100% free, no app review, no Partner Program. Personal API keys
    from https://dev.to/settings/extensions just work.
  - Markdown-native — the draft body lands verbatim.
  - Supports drafts vs published — matches the founder's approval flow.
  - Tagged + canonical-url support so SEO + cross-posting are clean.

Wiring:
  - Set ``DEVTO_API_KEY`` in .env (or Secret Manager secret name
    ``devto_api_key``). When unset, ``publish_article`` raises
    ``DevToNotConfigured`` so callers can skip publishing without crashing.
  - Default tags / canonical URL come from the queue item's metadata
    (channel + icp_segment + topic_hint).

API reference: https://developers.forem.com/api/v1#tag/articles
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import httpx

log = logging.getLogger(__name__)

_DEVTO_BASE = "https://dev.to/api"
_DEFAULT_TIMEOUT = 15.0


class DevToNotConfigured(RuntimeError):
    """Raised when DEVTO_API_KEY isn't set. Callers should treat this
    as a no-op (publish is optional), not a hard failure."""


class DevToError(RuntimeError):
    """Raised when Dev.to returned a non-2xx status. Wrapped so callers
    don't need to know httpx exists."""


@dataclass
class PublishedArticle:
    id: int
    url: str
    title: str
    published: bool
    canonical_url: str | None = None


def _api_key() -> str:
    """Resolve the Dev.to API key. Env var wins; Secret Manager is the
    cloud fallback (skipped under LOCAL_DEV=1)."""
    from ._secrets import secret_env
    return secret_env("DEVTO_API_KEY", secret_name="devto_api_key")


def is_configured() -> bool:
    """Cheap check the UI / decision handler can call before attempting
    publish. Doesn't make any network calls."""
    return bool(_api_key())


def derive_title(body_markdown: str, fallback: str = "Untitled draft") -> str:
    """Pull a sensible Dev.to title out of the draft. Strategy:
    1. First H1 in the markdown — that's the author's title.
    2. First non-empty line, truncated to 80 chars.
    3. Fallback string.
    """
    # Strip code fences first to avoid matching # inside them.
    cleaned = re.sub(r"```[\s\S]*?```", "", body_markdown or "")
    m = re.search(r"^\s*#\s+(.+?)\s*$", cleaned, flags=re.MULTILINE)
    if m:
        return m.group(1).strip()[:120]
    for line in (cleaned or "").splitlines():
        line = line.strip()
        if not line:
            continue
        # Skip subtitle-style "> ..." quotes
        if line.startswith(">"):
            continue
        return line[:80] + ("…" if len(line) > 80 else "")
    return fallback


def derive_tags(icp_segment: str | None, topic_hint: str | None) -> list[str]:
    """Pick 2-4 Dev.to tags from the queue item's metadata. Dev.to caps
    tags at 4 and they must match the platform's lowercase alphanum
    convention. We map our ICP slugs to category-shaped tags."""
    tags: list[str] = ["agenticai"]   # always present — this is what the post is FROM
    # Map ICP → audience tag
    if icp_segment:
        icp_tag = {
            "seg_merchant_dtc":     "ecommerce",
            "seg_ecom_leader":      "ecommerce",
            "seg_payments_network": "fintech",
            "seg_agent_platform":   "ai",
        }.get(icp_segment)
        if icp_tag and icp_tag not in tags:
            tags.append(icp_tag)
    # Pull a topic word — only if it looks like a sensible tag.
    if topic_hint:
        m = re.search(r"[a-z]{4,15}", topic_hint.lower())
        if m and m.group(0) not in tags:
            tags.append(m.group(0))
    return tags[:4]


def publish_article(
    body_markdown: str,
    *,
    title: str | None = None,
    tags: list[str] | None = None,
    publish: bool = True,
    canonical_url: str | None = None,
    series: str | None = None,
) -> PublishedArticle:
    """Post an article to Dev.to. Raises ``DevToNotConfigured`` when
    the API key is missing — callers should treat that as "skip publish,
    just record the approval".

    ``publish=False`` creates a draft on Dev.to (visible only to the
    author) — useful for the founder's pre-approval flow.
    """
    key = _api_key()
    if not key:
        raise DevToNotConfigured(
            "DEVTO_API_KEY not set — skipping Dev.to publish"
        )

    final_title = (title or derive_title(body_markdown)).strip()
    payload = {
        "article": {
            "title": final_title,
            "body_markdown": body_markdown,
            "published": bool(publish),
            "tags": tags or [],
        }
    }
    if canonical_url:
        payload["article"]["canonical_url"] = canonical_url
    if series:
        payload["article"]["series"] = series

    try:
        r = httpx.post(
            f"{_DEVTO_BASE}/articles",
            headers={"api-key": key, "Content-Type": "application/json"},
            json=payload,
            timeout=_DEFAULT_TIMEOUT,
        )
    except httpx.HTTPError as e:
        raise DevToError(f"Dev.to publish failed: {e}") from e

    if r.status_code not in (200, 201):
        raise DevToError(
            f"Dev.to returned HTTP {r.status_code}: {r.text[:300]}"
        )
    body = r.json()
    return PublishedArticle(
        id=int(body.get("id", 0)),
        url=body.get("url", ""),
        title=body.get("title", final_title),
        published=bool(body.get("published", publish)),
        canonical_url=body.get("canonical_url"),
    )
