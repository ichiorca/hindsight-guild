"""Meta (Facebook/Instagram) Ads — create a PAUSED ad creative.

Mirrors the Google Ads adapter: when an approved draft hits the
``meta_ads`` channel, we create the creative + ad in ``PAUSED`` status
inside an existing ad set. The user manually flips it live in Ads
Manager — no surprise spend.

API reference: https://developers.facebook.com/docs/marketing-apis/
               https://developers.facebook.com/docs/marketing-api/reference/ad-creative/

We POST against the Graph API (``graph.facebook.com``) — the same
endpoint serves the Marketing API at version-pinned paths.

Credentials (set in .env or Secret Manager):
  - ``META_ACCESS_TOKEN`` — long-lived user/system-user token with
    ``ads_management`` scope. Get one via Business Manager → System
    Users → Generate New Token, selecting your ad account.
    https://business.facebook.com/settings/system-users
  - ``META_AD_ACCOUNT_ID`` — the numeric ID of your ad account,
    WITHOUT the ``act_`` prefix. e.g. ``1234567890``. We prepend
    ``act_`` on the URL ourselves.
  - ``META_PAGE_ID`` — the Facebook Page ID that will be shown as
    the ad's "actor" (advertiser).
  - ``META_DEFAULT_AD_SET_ID`` — optional default ad set to drop new
    creatives into when the caller doesn't pass one. Use a dedicated
    "drafts/holding" ad set so paused-by-us ads don't accidentally
    get bulk-enabled later.

Operational notes:
  - Headlines: 1, ≤40 chars (Meta's recommended limit; longer values
    truncate in some surfaces).
  - Primary text: 1, ≤125 chars recommended (silent truncation
    happens above 125 in feed surfaces).
  - ``image_url`` must be publicly fetchable; Meta downloads + hashes
    it server-side. For private CDN urls, upload via
    ``/{ad_account_id}/adimages`` first and pass the returned hash —
    out of scope for this v1.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

import httpx

from ._secrets import secret_env as _env

log = logging.getLogger(__name__)

# Meta promotes a new Graph API version ~every 4 months and deprecates
# each ~2 years after release. Before first real publish, verify this
# string against https://developers.facebook.com/docs/graph-api/changelog
# and bump if it has been deprecated.
_API_VERSION = "v20.0"
_GRAPH_HOST = "https://graph.facebook.com"
_DEFAULT_TIMEOUT = 20.0

_HEADLINE_MAX = 40
_PRIMARY_TEXT_MAX = 125


class MetaAdsNotConfigured(RuntimeError):
    """Raised when required env vars are missing. Callers should
    treat as "skip publish"."""


class MetaAdsError(RuntimeError):
    """Non-2xx from Graph API."""


@dataclass
class PausedMetaAd:
    ad_id: str
    creative_id: str
    ad_account_id: str
    ad_set_id: str
    page_id: str
    headline: str
    primary_text: str
    image_url: str | None
    preview_url: str         # Best-effort Ads Manager deep link


def is_configured() -> bool:
    required = ("META_ACCESS_TOKEN", "META_AD_ACCOUNT_ID", "META_PAGE_ID")
    return all(_env(k) for k in required)


def _normalize(text: str, cap: int) -> str:
    s = (text or "").strip()
    s = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", s)
    s = re.sub(r"[*_`]+", "", s)
    s = re.sub(r"\s+", " ", s)
    if len(s) > cap:
        s = s[: cap - 1].rstrip() + "…"
    return s


def create_paused_creative(
    *,
    headline: str,
    primary_text: str,
    image_url: str | None = None,
    link_url: str = "",
    ad_set_id: str | None = None,
    name: str | None = None,
) -> PausedMetaAd:
    """Create a paused link-ad creative and bind it to an ad in the
    configured ad set. Raises ``MetaAdsNotConfigured`` when creds are
    missing.

    Two Graph API calls in sequence:
      1) POST /act_{account}/adcreatives    → creative_id
      2) POST /act_{account}/ads            → ad_id (status=PAUSED)

    On step-2 failure we don't roll back the orphan creative —
    they're cheap, and Meta's UI surfaces them under "Library →
    Creatives" so the user can clean up if needed.
    """
    if not is_configured():
        raise MetaAdsNotConfigured(
            "Missing one of META_ACCESS_TOKEN / META_AD_ACCOUNT_ID / META_PAGE_ID"
        )

    account_id = _env("META_AD_ACCOUNT_ID")
    page_id = _env("META_PAGE_ID")
    token = _env("META_ACCESS_TOKEN")
    ad_set_id = ad_set_id or _env("META_DEFAULT_AD_SET_ID")

    if not ad_set_id:
        raise MetaAdsError(
            "ad_set_id required (pass explicitly or set META_DEFAULT_AD_SET_ID)"
        )

    headline = _normalize(headline, _HEADLINE_MAX)
    primary_text = _normalize(primary_text, _PRIMARY_TEXT_MAX)
    name = name or f"draft: {headline[:30]}"

    # Step 1 — create the creative. Use ``link_data`` for a single-
    # image link ad. If no image is provided, fall back to a text-only
    # creative which Meta accepts for placements that allow it.
    object_story = {"page_id": page_id, "link_data": {
        "link": link_url or f"https://www.facebook.com/{page_id}",
        "message": primary_text,
        "name": headline,
    }}
    if image_url:
        object_story["link_data"]["picture"] = image_url

    creative_url = (
        f"{_GRAPH_HOST}/{_API_VERSION}/act_{account_id}/adcreatives"
    )
    try:
        r1 = httpx.post(
            creative_url,
            data={
                "name": name,
                "object_story_spec": _json_compact(object_story),
                "access_token": token,
            },
            timeout=_DEFAULT_TIMEOUT,
        )
    except httpx.HTTPError as e:
        raise MetaAdsError(f"Meta adcreatives request failed: {e}") from e
    if r1.status_code != 200:
        raise MetaAdsError(
            f"Meta adcreatives HTTP {r1.status_code}: {r1.text[:400]}"
        )
    creative_id = r1.json().get("id", "")
    if not creative_id:
        raise MetaAdsError(f"Meta adcreatives 200 but no id: {r1.text[:300]}")

    # Step 2 — bind the creative to a paused ad in the target ad set.
    ad_url = f"{_GRAPH_HOST}/{_API_VERSION}/act_{account_id}/ads"
    try:
        r2 = httpx.post(
            ad_url,
            data={
                "name": name,
                "adset_id": ad_set_id,
                "creative": _json_compact({"creative_id": creative_id}),
                "status": "PAUSED",
                "access_token": token,
            },
            timeout=_DEFAULT_TIMEOUT,
        )
    except httpx.HTTPError as e:
        raise MetaAdsError(f"Meta ads request failed: {e}") from e
    if r2.status_code != 200:
        raise MetaAdsError(f"Meta ads HTTP {r2.status_code}: {r2.text[:400]}")
    ad_id = r2.json().get("id", "")
    if not ad_id:
        raise MetaAdsError(f"Meta ads 200 but no id: {r2.text[:300]}")

    preview = (
        f"https://www.facebook.com/adsmanager/manage/ads"
        f"?act={account_id}&selected_ad_ids={ad_id}"
    )

    return PausedMetaAd(
        ad_id=ad_id,
        creative_id=creative_id,
        ad_account_id=account_id,
        ad_set_id=ad_set_id,
        page_id=page_id,
        headline=headline,
        primary_text=primary_text,
        image_url=image_url,
        preview_url=preview,
    )


def pause_existing_ad(ad_id: str) -> dict:
    """Flip an existing Meta Ad to ``status: PAUSED`` via the Graph API.

    Used by ``shared/apply_paid_action.py`` when the founder approves a
    paid-pause proposal. Graph API takes a POST to the ad's node URL
    with ``status=PAUSED`` as a form field.

    Returns ``{"applied": True, "ad_id": str}`` on success.
    Raises ``MetaAdsNotConfigured`` / ``MetaAdsError`` on the same
    paths as ``create_paused_creative``.
    """
    if not is_configured():
        raise MetaAdsNotConfigured(
            "Missing one of META_ACCESS_TOKEN / META_AD_ACCOUNT_ID / META_PAGE_ID"
        )
    if not (ad_id or "").strip():
        raise MetaAdsError("ad_id required")

    token = _env("META_ACCESS_TOKEN")
    url = f"{_GRAPH_HOST}/{_API_VERSION}/{ad_id}"
    try:
        r = httpx.post(
            url,
            data={"status": "PAUSED", "access_token": token},
            timeout=_DEFAULT_TIMEOUT,
        )
    except httpx.HTTPError as e:
        raise MetaAdsError(f"Meta ads pause request failed: {e}") from e
    if r.status_code != 200:
        raise MetaAdsError(f"Meta ads pause HTTP {r.status_code}: {r.text[:400]}")

    return {"applied": True, "ad_id": ad_id}


def _json_compact(obj) -> str:
    """Graph API takes JSON-encoded nested fields as form values —
    not as a JSON body. ``json.dumps`` defaults work; this wrapper
    just keeps the call sites readable."""
    return json.dumps(obj, separators=(",", ":"))
