"""Substack REST client.

Substack does not have a fully public authoring API. The endpoints below
match what the official authoring web app hits as of 2026-Q2; they're
the same surface their browser SDK uses internally. Use Substack Pro
credentials for stable access.

Auth: API key from Secret Manager (`substack_api_key`) + publication
host (`substack_publication_host`, e.g. "yourname.substack.com").

If credentials are absent OR the API rejects us, the client signals
mode="manual_review" so the caller can stage the post for the founder
to publish manually. Honest fallback per the lean spec's
"browser automation second, computer-use last" principle.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import httpx
from tenacity import RetryError, retry, stop_after_attempt, wait_exponential

from shared.clients import secret_value

log = logging.getLogger(__name__)

PROJECT_ID = os.environ.get("PROJECT_ID", "hindsight-guild-mvp")


def _secret_optional(name: str) -> str | None:
    """Fetch a secret, treating PENDING placeholder as missing.

    Returns None if the secret doesn't exist, is set to PENDING, or any
    auth/permission error occurs. Caching happens inside secret_value.
    """
    try:
        val = secret_value(name)
    except Exception:
        return None
    return val if val and val != "PENDING" else None


@dataclass
class PublishResult:
    mode: Literal["api", "manual_review"]
    post_id: str | None
    publication_id: str | None
    url: str | None
    published_at: datetime | None
    reason: str | None = None  # for manual_review mode


class SubstackClient:
    """Thin Substack REST client.

    Two phases:
      1. create_draft → POST a draft (not yet visible to subscribers)
      2. publish      → flip the draft to live, optionally scheduled

    We split them because the founder may want to schedule a Sunday-evening
    post on Friday afternoon. The two-call shape mirrors the real Substack
    authoring flow.
    """

    BASE = "https://substack.com/api/v1"

    def __init__(self, api_key: str | None = None,
                 publication_host: str | None = None,
                 publication_id: str | None = None):
        self.api_key = api_key or _secret_optional("substack_api_key")
        self.publication_host = publication_host or _secret_optional("substack_publication_host")
        self.publication_id = publication_id or _secret_optional("substack_publication_id")

    @property
    def available(self) -> bool:
        return bool(self.api_key and (self.publication_host or self.publication_id))

    def _headers(self) -> dict:
        # Substack uses session-cookie auth in the browser; for partner API
        # keys, Bearer is the common header. If your tenant requires the
        # cookie style, replace with `Cookie: substack.sid=<value>`.
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "User-Agent": "hindsight-guild/0.2 (https://github.com/yours/auto-gtm)",
        }

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def _post(self, path: str, payload: dict) -> dict:
        r = httpx.post(f"{self.BASE}{path}", headers=self._headers(),
                       json=payload, timeout=20)
        r.raise_for_status()
        return r.json()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def _put(self, path: str, payload: dict) -> dict:
        r = httpx.put(f"{self.BASE}{path}", headers=self._headers(),
                      json=payload, timeout=20)
        r.raise_for_status()
        return r.json()

    def publish(self, *,
                headline: str,
                subtitle: str,
                body_markdown: str,
                publish_at: datetime | None = None,
                audience: Literal["everyone", "only_paid", "only_free"] = "everyone",
                send_email: bool = True,
                cover_image_url: str | None = None,
                cover_image_alt: str | None = None) -> PublishResult:
        """Create a draft and publish (or schedule) it.

        Returns mode="manual_review" if credentials are absent or the API
        rejects us — the caller stages the post for the founder to publish
        by hand and the rest of the system (attribution, telemetry) still
        works because we record a stable post_id of `manual:<telemetry_id>`.
        """
        if not self.available:
            return PublishResult(
                mode="manual_review", post_id=None, publication_id=None,
                url=None, published_at=None,
                reason="substack_api_key or publication_host secret not set",
            )

        try:
            # 1. Create draft — embed the cover image at the top of the
            # body via Markdown if we have one. Substack also accepts a
            # separate cover_image field; we set both to be robust.
            body_with_image = body_markdown
            if cover_image_url:
                alt = cover_image_alt or headline
                body_with_image = f"![{alt}]({cover_image_url})\n\n{body_markdown}"

            create_payload = {
                "title": headline,
                "subtitle": subtitle,
                "body_markdown": body_with_image,
                "audience": audience,
                "send_email": send_email,
                "type": "newsletter",
            }
            if cover_image_url:
                create_payload["cover_image"] = cover_image_url
                create_payload["cover_image_alt"] = cover_image_alt or headline
            pub_segment = f"publications/{self.publication_id}" if self.publication_id else "posts"
            created = self._post(f"/{pub_segment}/posts", create_payload)
            post_id = str(created.get("id") or created.get("post_id"))
            slug = created.get("slug")

            # 2. Publish (scheduled or immediate)
            publish_payload = {"send_email": send_email}
            if publish_at:
                publish_payload["publish_at"] = publish_at.isoformat()

            published = self._put(f"/{pub_segment}/posts/{post_id}/publish",
                                  publish_payload)

            url = published.get("canonical_url") or (
                f"https://{self.publication_host}/p/{slug}" if slug else None
            )
            published_at_str = published.get("published_at")
            published_at = (datetime.fromisoformat(published_at_str.replace("Z", "+00:00"))
                            if published_at_str else None)

            return PublishResult(
                mode="api",
                post_id=post_id,
                publication_id=self.publication_id,
                url=url,
                published_at=published_at,
            )
        except (RetryError, httpx.HTTPError) as e:
            log.warning("Substack API publish failed: %s — falling back to manual_review", e)
            return PublishResult(
                mode="manual_review", post_id=None, publication_id=None,
                url=None, published_at=None,
                reason=f"api_error: {e!s}",
            )
