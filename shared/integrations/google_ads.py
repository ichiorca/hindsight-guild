"""Google Ads — create a PAUSED Responsive Search Ad (RSA) draft.

Why "paused": we never want an approved-but-not-launched draft to start
spending real budget. The integration creates the creative in
``status: PAUSED`` inside an existing ad group. Flipping it live is a
manual step inside Google Ads — that's intentional.

API reference: https://developers.google.com/google-ads/api/rest/overview
                https://developers.google.com/google-ads/api/docs/ads/responsive-search-ads

This adapter speaks the REST gateway (``googleads.googleapis.com``)
directly via httpx to avoid the heavy ``google-ads`` SDK dependency.

Credentials (set in .env or Secret Manager):
  - ``GOOGLE_ADS_DEVELOPER_TOKEN`` — issued by Google when your MCC
    account is approved for API access (~1 day, free).
    https://ads.google.com/aw/apicenter
  - ``GOOGLE_ADS_CUSTOMER_ID`` — the 10-digit account ID *without*
    dashes that owns the ad group. e.g. ``1234567890``.
  - ``GOOGLE_ADS_LOGIN_CUSTOMER_ID`` — optional; the manager (MCC)
    account ID when accessing client accounts. Omit for direct
    self-managed accounts.
  - ``GOOGLE_ADS_REFRESH_TOKEN`` + ``GOOGLE_ADS_CLIENT_ID`` +
    ``GOOGLE_ADS_CLIENT_SECRET`` — OAuth 2.0 desktop-app credentials
    from a Cloud Console project with the Ads API enabled.

Operational notes:
  - Headlines: 3-15 entries, ≤30 chars each. The API rejects fewer
    than 3. We auto-truncate per-headline and back-fill from the
    primary text if the caller provided fewer than 3.
  - Descriptions: 2-4 entries, ≤90 chars each. Same backfill rule.
  - ``final_url`` must include the protocol (https://...).
  - Access tokens last 1 hour; we mint a new one per call rather than
    caching, since this is a low-frequency operation (one publish per
    approval) and caching adds failure modes (clock skew on lambdas,
    container restarts, etc.).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import httpx

from ._secrets import secret_env as _env

log = logging.getLogger(__name__)

# Google Ads ships a new REST API version roughly every 4 months and
# sunsets each ~12 months after release. Before first real publish,
# verify this string against
# https://developers.google.com/google-ads/api/docs/release-notes
# and bump if it has been deprecated.
_API_VERSION = "v18"
_API_HOST = "https://googleads.googleapis.com"
_OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
_DEFAULT_TIMEOUT = 20.0

_HEADLINE_MAX = 30
_DESCRIPTION_MAX = 90
_MIN_HEADLINES = 3
_MIN_DESCRIPTIONS = 2


class GoogleAdsNotConfigured(RuntimeError):
    """Raised when required env vars are missing. Callers should
    treat as "skip publish"."""


class GoogleAdsError(RuntimeError):
    """Non-2xx from Google Ads REST gateway, or OAuth refresh failure."""


@dataclass
class PausedRSA:
    resource_name: str       # customers/123/ads/456 — Google's stable handle
    ad_id: str               # the trailing numeric ID for convenience
    ad_group_id: str
    customer_id: str
    headlines: list[str]
    descriptions: list[str]
    final_url: str
    preview_url: str         # Best-effort deep link into the Google Ads UI


def is_configured() -> bool:
    """All required creds present? UI gates the publish badge on this."""
    required = (
        "GOOGLE_ADS_DEVELOPER_TOKEN",
        "GOOGLE_ADS_CUSTOMER_ID",
        "GOOGLE_ADS_REFRESH_TOKEN",
        "GOOGLE_ADS_CLIENT_ID",
        "GOOGLE_ADS_CLIENT_SECRET",
    )
    return all(_env(k) for k in required)


def _mint_access_token() -> str:
    """Exchange refresh token for a short-lived access token via Google
    OAuth. Raises ``GoogleAdsError`` on failure."""
    payload = {
        "client_id": _env("GOOGLE_ADS_CLIENT_ID"),
        "client_secret": _env("GOOGLE_ADS_CLIENT_SECRET"),
        "refresh_token": _env("GOOGLE_ADS_REFRESH_TOKEN"),
        "grant_type": "refresh_token",
    }
    try:
        r = httpx.post(_OAUTH_TOKEN_URL, data=payload, timeout=_DEFAULT_TIMEOUT)
    except httpx.HTTPError as e:
        raise GoogleAdsError(f"OAuth refresh failed: {e}") from e
    if r.status_code != 200:
        raise GoogleAdsError(
            f"OAuth refresh HTTP {r.status_code}: {r.text[:300]}"
        )
    token = r.json().get("access_token", "")
    if not token:
        raise GoogleAdsError(f"OAuth response missing access_token: {r.text[:300]}")
    return token


def _normalize(text: str, cap: int) -> str:
    """Strip markdown noise + trim to Google's character cap.

    Headlines/descriptions are rendered as plain text in Google Ads.
    Common markdown artifacts (``**bold**``, ``[link](url)``,
    backticks) get stripped so they don't end up visible in the ad
    surface. Cap is applied AFTER stripping so we don't waste budget
    on chopped-up markers.
    """
    s = (text or "").strip()
    s = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", s)
    s = re.sub(r"[*_`]+", "", s)
    s = re.sub(r"\s+", " ", s)
    if len(s) > cap:
        s = s[: cap - 1].rstrip() + "…"
    return s


def _backfill(items: list[str], primary_text: str, cap: int, minimum: int) -> list[str]:
    """If the caller passed fewer than ``minimum`` items, mine the
    primary text for sentence-length chunks to top up the list.

    Google Ads rejects RSA payloads with too few assets, so the
    integration handles backfill rather than surfacing a 400 to the
    caller. The mined chunks are split on sentence boundaries and
    de-duplicated against existing items.
    """
    out = [_normalize(x, cap) for x in items if x]
    if len(out) >= minimum:
        return out
    chunks = re.split(r"(?<=[.!?])\s+", primary_text or "")
    seen = {x.lower() for x in out}
    for chunk in chunks:
        candidate = _normalize(chunk, cap)
        if candidate and candidate.lower() not in seen:
            out.append(candidate)
            seen.add(candidate.lower())
        if len(out) >= minimum:
            break
    return out


def create_paused_rsa(
    *,
    ad_group_id: str,
    final_url: str,
    headlines: list[str],
    descriptions: list[str],
    primary_text: str = "",
) -> PausedRSA:
    """Create a paused Responsive Search Ad. Raises
    ``GoogleAdsNotConfigured`` when creds are missing.

    ``ad_group_id`` is the 11-12 digit numeric ID of an existing ad
    group in the configured customer account. Use a dedicated
    "drafts/holding" ad group so paused-by-us ads don't accidentally
    get bulk-enabled later.
    """
    if not is_configured():
        raise GoogleAdsNotConfigured(
            "Missing one of GOOGLE_ADS_DEVELOPER_TOKEN / CUSTOMER_ID / "
            "REFRESH_TOKEN / CLIENT_ID / CLIENT_SECRET"
        )
    if not (ad_group_id or "").strip():
        # Caller is expected to pass one explicitly OR set
        # GOOGLE_ADS_DEFAULT_AD_GROUP_ID upstream. Surface a clean
        # error so the dispatcher can map it to a friendly skip reason.
        raise GoogleAdsError("ad_group_id required")

    customer_id = _env("GOOGLE_ADS_CUSTOMER_ID")
    dev_token = _env("GOOGLE_ADS_DEVELOPER_TOKEN")
    login_cid = _env("GOOGLE_ADS_LOGIN_CUSTOMER_ID")

    headlines = _backfill(headlines, primary_text, _HEADLINE_MAX, _MIN_HEADLINES)
    descriptions = _backfill(descriptions, primary_text, _DESCRIPTION_MAX, _MIN_DESCRIPTIONS)

    if len(headlines) < _MIN_HEADLINES:
        raise GoogleAdsError(
            f"need at least {_MIN_HEADLINES} headlines after backfill, "
            f"got {len(headlines)}"
        )
    if len(descriptions) < _MIN_DESCRIPTIONS:
        raise GoogleAdsError(
            f"need at least {_MIN_DESCRIPTIONS} descriptions after backfill, "
            f"got {len(descriptions)}"
        )

    access_token = _mint_access_token()

    # Google Ads "AdGroupAdService.mutate" — one operation creating
    # a new RSA in PAUSED state. Resource paths take the form
    # ``customers/{cid}/adGroups/{agid}``.
    ad_group_rn = f"customers/{customer_id}/adGroups/{ad_group_id}"
    operation = {
        "create": {
            "adGroup": ad_group_rn,
            "status": "PAUSED",
            "ad": {
                "finalUrls": [final_url],
                "responsiveSearchAd": {
                    "headlines": [{"text": h} for h in headlines[:15]],
                    "descriptions": [{"text": d} for d in descriptions[:4]],
                },
            },
        }
    }

    url = (
        f"{_API_HOST}/{_API_VERSION}/customers/{customer_id}"
        f"/adGroupAds:mutate"
    )
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "developer-token": dev_token,
    }
    if login_cid:
        headers["login-customer-id"] = login_cid

    try:
        r = httpx.post(
            url, headers=headers,
            json={"operations": [operation], "partialFailure": False},
            timeout=_DEFAULT_TIMEOUT,
        )
    except httpx.HTTPError as e:
        raise GoogleAdsError(f"Google Ads mutate failed: {e}") from e

    if r.status_code != 200:
        raise GoogleAdsError(
            f"Google Ads HTTP {r.status_code}: {r.text[:400]}"
        )

    body = r.json()
    results = body.get("results", [])
    if not results:
        raise GoogleAdsError(f"Google Ads 200 but no results: {body}")

    resource_name = results[0].get("resourceName", "")
    # resourceName looks like: customers/123/adGroupAds/456~789
    # The ad ID is the segment after the tilde.
    ad_id = resource_name.split("~")[-1] if "~" in resource_name else ""
    preview = (
        f"https://ads.google.com/aw/ads?ocid={customer_id}"
        f"&adGroupId={ad_group_id}"
    )

    return PausedRSA(
        resource_name=resource_name,
        ad_id=ad_id,
        ad_group_id=ad_group_id,
        customer_id=customer_id,
        headlines=headlines,
        descriptions=descriptions,
        final_url=final_url,
        preview_url=preview,
    )


def pause_existing_ad(ad_group_id: str, ad_id: str) -> dict:
    """Flip an existing live ad to ``status: PAUSED``.

    Used by ``shared/apply_paid_action.py`` when the founder approves a
    ``self_critique`` paid-pause proposal. The resource name format is
    ``customers/{cid}/adGroupAds/{ad_group_id}~{ad_id}`` per the
    Google Ads REST surface.

    Returns ``{"applied": True, "resource_name": str}`` on success.
    Raises ``GoogleAdsNotConfigured`` / ``GoogleAdsError`` on the same
    paths as ``create_paused_rsa``.
    """
    if not is_configured():
        raise GoogleAdsNotConfigured(
            "Missing one of GOOGLE_ADS_DEVELOPER_TOKEN / CUSTOMER_ID / "
            "REFRESH_TOKEN / CLIENT_ID / CLIENT_SECRET"
        )
    if not (ad_group_id or "").strip() or not (ad_id or "").strip():
        raise GoogleAdsError("ad_group_id + ad_id required")

    customer_id = _env("GOOGLE_ADS_CUSTOMER_ID")
    dev_token = _env("GOOGLE_ADS_DEVELOPER_TOKEN")
    login_cid = _env("GOOGLE_ADS_LOGIN_CUSTOMER_ID")
    access_token = _mint_access_token()

    resource_name = (
        f"customers/{customer_id}/adGroupAds/{ad_group_id}~{ad_id}"
    )
    # ``update_mask`` tells Google to only touch ``status``; without it
    # the API expects every field on the row.
    operation = {
        "update": {
            "resourceName": resource_name,
            "status": "PAUSED",
        },
        "updateMask": "status",
    }

    url = (
        f"{_API_HOST}/{_API_VERSION}/customers/{customer_id}"
        f"/adGroupAds:mutate"
    )
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "developer-token": dev_token,
    }
    if login_cid:
        headers["login-customer-id"] = login_cid

    try:
        r = httpx.post(
            url, headers=headers,
            json={"operations": [operation], "partialFailure": False},
            timeout=_DEFAULT_TIMEOUT,
        )
    except httpx.HTTPError as e:
        raise GoogleAdsError(f"Google Ads pause request failed: {e}") from e

    if r.status_code != 200:
        raise GoogleAdsError(
            f"Google Ads pause HTTP {r.status_code}: {r.text[:400]}"
        )

    return {"applied": True, "resource_name": resource_name}
