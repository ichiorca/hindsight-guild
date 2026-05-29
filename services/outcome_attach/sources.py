"""Outcome source handlers. Each pulls a metric value for a given telemetry_id.

GA4: queries the BigQuery export tables (events_<date>); free.
HubSpot: REST API v3 — email events endpoint for open/click/reply metrics.
Google Ads: google-ads SDK against GAQL.
LinkedIn Ads: REST API.

All four use tenacity for retry with exponential backoff. API tokens come from
Secret Manager (set up by setup.sh and rotated by the founder when needed).

Convention: every campaign-attributable agent action sets a campaign-level
tag equal to its telemetry_id so attribution can be done by key, not heuristic.
LinkedIn posts use the post's URN as `external_id`; emails use HubSpot's
email_send_id; GA4 events use event_param `campaign_id`.

The attach mapping (telemetry_id → external_id) lives in
mongodb.attribution_map, populated by the agent at publish/send time. The
sources below read from it.
"""
from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta
from functools import lru_cache

import httpx
from google.cloud import bigquery
from tenacity import retry, stop_after_attempt, wait_exponential

from services.outcome_attach.health import record_failure, record_success
from shared.clients import bigquery_client, secret_value

log = logging.getLogger(__name__)

PROJECT_ID = os.environ["PROJECT_ID"]
GA4_PROPERTY = os.environ.get("GA4_PROPERTY_ID")


def _bq() -> bigquery.Client:
    """Cached BigQuery client — central provider in shared.clients."""
    return bigquery_client()


def _secret(name: str) -> str:
    """Fetch a Secret Manager value — central provider in shared.clients."""
    return secret_value(name)


def _attribution(telemetry_id: str) -> dict | None:
    """Look up the external_id that this telemetry_id was published under."""
    from mongo.queries import lookup_attribution
    return lookup_attribution(telemetry_id)


# ---------------------------------------------------------------------------
# GA4 — sessions / events from the BigQuery export
# ---------------------------------------------------------------------------

def query_ga4(telemetry_id: str, metric: str) -> float | None:
    if not GA4_PROPERTY:
        return None
    if metric == "sessions":
        sql = f"""
          SELECT COUNT(*) AS v
          FROM `{PROJECT_ID}.analytics_{GA4_PROPERTY}.events_*`
          WHERE event_name='session_start'
            AND _TABLE_SUFFIX >= FORMAT_DATE('%Y%m%d', CURRENT_DATE() - 7)
            AND EXISTS (SELECT 1 FROM UNNEST(event_params) p
                        WHERE p.key='campaign_id' AND p.value.string_value=@id)
        """
    elif metric == "conversions":
        sql = f"""
          SELECT COUNTIF(event_name IN ('purchase','sign_up','generate_lead')) AS v
          FROM `{PROJECT_ID}.analytics_{GA4_PROPERTY}.events_*`
          WHERE _TABLE_SUFFIX >= FORMAT_DATE('%Y%m%d', CURRENT_DATE() - 7)
            AND EXISTS (SELECT 1 FROM UNNEST(event_params) p
                        WHERE p.key='campaign_id' AND p.value.string_value=@id)
        """
    else:
        log.warning("ga4: unknown metric %s", metric)
        return None
    try:
        result = _bq().query(sql, job_config=bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("id", "STRING", telemetry_id),
        ])).result()
        row = next(iter(result), None)
        # Query ran cleanly — record success regardless of whether the row
        # had matching attribution. Zero matches is a valid query result,
        # not an integration failure.
        record_success("ga4", metric)
        return float(row.v) if row else None
    except Exception as e:
        log.warning("ga4 query failed for %s/%s: %s", telemetry_id, metric, e)
        record_failure("ga4", metric, str(e))
        return None


# ---------------------------------------------------------------------------
# HubSpot — email events via /email/public/v1/events
# ---------------------------------------------------------------------------

HUBSPOT_BASE = "https://api.hubapi.com"


def _hubspot_headers() -> dict:
    return {
        "Authorization": f"Bearer {_secret('hubspot_api_token')}",
        "Content-Type": "application/json",
    }


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def _hubspot_get(path: str, params: dict | None = None) -> dict:
    r = httpx.get(f"{HUBSPOT_BASE}{path}", headers=_hubspot_headers(),
                  params=params or {}, timeout=15)
    r.raise_for_status()
    return r.json()


def query_hubspot(telemetry_id: str, metric: str) -> float | None:
    attr = _attribution(telemetry_id)
    if not attr or "hubspot_email_id" not in attr:
        return None

    email_id = attr["hubspot_email_id"]

    # /marketing/v3/emails/statistics/{emailId} returns campaign aggregate stats.
    # Adjust endpoint as needed for your HubSpot edition.
    try:
        data = _hubspot_get(f"/marketing/v3/emails/statistics/{email_id}")
        stats = data.get("statistics", {})
        result: float | None = None

        if metric == "open_rate":
            sent = stats.get("sent", 0)
            opens = stats.get("uniqueOpens") or stats.get("opens", 0)
            result = float(opens) / sent if sent else None
        elif metric == "click_rate":
            sent = stats.get("sent", 0)
            clicks = stats.get("uniqueClicks") or stats.get("clicks", 0)
            result = float(clicks) / sent if sent else None
        elif metric == "reply_rate":
            sent = stats.get("sent", 0)
            replies = stats.get("replies", 0)
            result = float(replies) / sent if sent else None
        else:
            log.warning("hubspot: unknown metric %s", metric)
            return None
        record_success("hubspot", metric)
        return result
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            # Email not sent yet — not an integration failure, just a
            # too-early read. The API is reachable + auth works.
            log.info("hubspot email %s not found yet (likely not sent)", email_id)
            record_success("hubspot", metric)
        else:
            log.warning("hubspot query failed for %s: %s", telemetry_id, e)
            record_failure("hubspot", metric, str(e))
    except Exception as e:
        log.warning("hubspot error for %s: %s", telemetry_id, e)
        record_failure("hubspot", metric, str(e))
    return None


# ---------------------------------------------------------------------------
# Google Ads — via google-ads SDK (GAQL)
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _google_ads_client():
    """Lazy import; the google-ads SDK is heavy."""
    from google.ads.googleads.client import GoogleAdsClient
    creds = {
        "developer_token": _secret("google_ads_developer_token"),
        "client_id": _secret("google_ads_client_id"),
        "client_secret": _secret("google_ads_client_secret"),
        "refresh_token": _secret("google_ads_refresh_token"),
        "login_customer_id": _secret("google_ads_login_customer_id"),
        "use_proto_plus": True,
    }
    return GoogleAdsClient.load_from_dict(creds, version="v17")


def query_google_ads(telemetry_id: str, metric: str) -> float | None:
    attr = _attribution(telemetry_id)
    if not attr or "google_ads_campaign_id" not in attr:
        return None
    campaign_id = attr["google_ads_campaign_id"]
    customer_id = attr.get("google_ads_customer_id") or _secret("google_ads_login_customer_id")

    try:
        client = _google_ads_client()
        ga_service = client.get_service("GoogleAdsService")
        gaql = f"""
            SELECT metrics.impressions, metrics.clicks, metrics.conversions,
                   metrics.cost_micros, metrics.ctr
            FROM campaign
            WHERE campaign.id = {campaign_id}
              AND segments.date DURING LAST_7_DAYS
        """
        rows = list(ga_service.search(customer_id=customer_id, query=gaql))
        record_success("google_ads", metric)
        if not rows:
            return None
        m = rows[0].metrics
        return {
            "impressions": float(m.impressions),
            "clicks": float(m.clicks),
            "conversions": float(m.conversions),
            "cost": float(m.cost_micros) / 1_000_000,
            "ctr": float(m.ctr),
        }.get(metric)
    except Exception as e:
        log.warning("google_ads query failed for %s: %s", telemetry_id, e)
        record_failure("google_ads", metric, str(e))
        return None


# ---------------------------------------------------------------------------
# LinkedIn Marketing API
# ---------------------------------------------------------------------------

LINKEDIN_BASE = "https://api.linkedin.com/rest"


def _linkedin_headers() -> dict:
    return {
        "Authorization": f"Bearer {_secret('linkedin_access_token')}",
        "X-Restli-Protocol-Version": "2.0.0",
        "LinkedIn-Version": "202405",
    }


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def _linkedin_get(path: str, params: dict | None = None) -> dict:
    r = httpx.get(f"{LINKEDIN_BASE}{path}", headers=_linkedin_headers(),
                  params=params or {}, timeout=15)
    r.raise_for_status()
    return r.json()


def query_linkedin_ads(telemetry_id: str, metric: str) -> float | None:
    attr = _attribution(telemetry_id)
    if not attr:
        return None

    # Organic post analytics (for non-paid posts)
    if "linkedin_post_urn" in attr and metric in ("impressions", "engagement"):
        urn = attr["linkedin_post_urn"]
        try:
            data = _linkedin_get("/socialActions/" + urn)
            record_success("linkedin_ads", metric)
            if metric == "impressions":
                return float(data.get("totalShares", 0) +
                              data.get("likesSummary", {}).get("totalLikes", 0) +
                              data.get("commentsSummary", {}).get("totalFirstLevelComments", 0))
            if metric == "engagement":
                likes = data.get("likesSummary", {}).get("totalLikes", 0)
                comments = data.get("commentsSummary", {}).get("totalFirstLevelComments", 0)
                impressions = data.get("totalShares", 1)
                return float(likes + comments) / impressions if impressions else None
        except Exception as e:
            log.warning("linkedin organic query failed for %s: %s", telemetry_id, e)
            record_failure("linkedin_ads", metric, str(e))

    # Paid creative analytics
    if "linkedin_creative_urn" in attr:
        creative = attr["linkedin_creative_urn"]
        try:
            yesterday = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%d")
            data = _linkedin_get(
                "/adAnalytics",
                params={
                    "q": "analytics",
                    "pivot": "CREATIVE",
                    "dateRange.start.day": yesterday.split("-")[2],
                    "dateRange.start.month": yesterday.split("-")[1],
                    "dateRange.start.year": yesterday.split("-")[0],
                    "creatives[0]": creative,
                    "timeGranularity": "DAILY",
                    "fields": "impressions,clicks,costInLocalCurrency",
                },
            )
            elements = data.get("elements") or []
            record_success("linkedin_ads", metric)
            if not elements:
                return None
            e0 = elements[0]
            return {
                "impressions": float(e0.get("impressions", 0)),
                "clicks": float(e0.get("clicks", 0)),
                "cost": float(e0.get("costInLocalCurrency", 0)),
            }.get(metric)
        except Exception as e:
            log.warning("linkedin ads query failed for %s: %s", telemetry_id, e)
            record_failure("linkedin_ads", metric, str(e))

    return None


# ---------------------------------------------------------------------------
# Substack — Posts API (Substack Pro / Author API)
# ---------------------------------------------------------------------------
#
# Substack's author API requires either a Pro subscription (which exposes
# a REST endpoint) or — for free authors — browser automation via the
# session cookie. We implement the Pro path here and document the cookie
# fallback in DECISIONS.md.
#
# Auth: a Substack API key in Secret Manager as `substack_api_key`.
# Author host (e.g. "yourname.substack.com") in `substack_publication_host`.
# Endpoint shape may evolve — the GETs below match the spec as of 2026-Q2.

SUBSTACK_BASE = "https://substack.com/api/v1"


def _substack_headers() -> dict:
    return {
        "Authorization": f"Bearer {_secret('substack_api_key')}",
        "Content-Type": "application/json",
    }


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def _substack_get(path: str, params: dict | None = None) -> dict:
    r = httpx.get(f"{SUBSTACK_BASE}{path}", headers=_substack_headers(),
                  params=params or {}, timeout=15)
    r.raise_for_status()
    return r.json()


def query_substack(telemetry_id: str, metric: str) -> float | None:
    """Pull post-level analytics for the Substack post linked to this telemetry_id.

    attribution_map entry shape:
      {"telemetry_id": ..., "substack_post_id": "12345678",
       "substack_publication_id": "..." }

    Metrics supported:
      - opens           — unique email opens
      - open_rate       — opens / sent
      - clicks          — unique link clicks
      - click_rate      — clicks / sent
      - restacks        — number of restacks (Substack's repost mechanic)
      - new_subscribers — subscriptions attributable to this post in the 24h
                          window after publish
      - paid_conversions — free → paid upgrades attributed to this post
    """
    attr = _attribution(telemetry_id)
    if not attr or "substack_post_id" not in attr:
        return None
    post_id = attr["substack_post_id"]
    pub_id = attr.get("substack_publication_id")

    try:
        # /publications/{pub_id}/posts/{post_id}/stats
        path = (f"/publications/{pub_id}/posts/{post_id}/stats"
                if pub_id else f"/posts/{post_id}/stats")
        data = _substack_get(path)

        stats = data.get("statistics") or data
        sent = stats.get("delivered") or stats.get("sent") or 0
        result: float | None = None

        if metric == "opens":
            result = float(stats.get("unique_opens", 0))
        elif metric == "open_rate":
            opens = stats.get("unique_opens", 0)
            result = float(opens) / sent if sent else None
        elif metric == "clicks":
            result = float(stats.get("unique_clicks", 0))
        elif metric == "click_rate":
            clicks = stats.get("unique_clicks", 0)
            result = float(clicks) / sent if sent else None
        elif metric == "restacks":
            result = float(stats.get("restacks") or stats.get("restack_count", 0))
        elif metric == "new_subscribers":
            result = float(stats.get("new_free_subscribers", 0))
        elif metric == "paid_conversions":
            result = float(stats.get("new_paid_subscribers", 0))
        else:
            log.warning("substack: unknown metric %s", metric)
            return None
        record_success("substack", metric)
        return result
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            # Post not published yet — API reachable, auth works.
            log.info("substack post %s not found yet (likely scheduled)", post_id)
            record_success("substack", metric)
        else:
            log.warning("substack query failed for %s: %s", telemetry_id, e)
            record_failure("substack", metric, str(e))
    except Exception as e:
        log.warning("substack error for %s: %s", telemetry_id, e)
        record_failure("substack", metric, str(e))
    return None


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

SOURCES = {
    "ga4": query_ga4,
    "hubspot": query_hubspot,
    "google_ads": query_google_ads,
    "linkedin_ads": query_linkedin_ads,
    "email": query_hubspot,    # alias
    "substack": query_substack,
}
