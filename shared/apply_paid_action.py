"""Apply an approved paid-action proposal to the platform.

Called by the ``/api/self-critique/proposals/{id}/approve`` handler when
the proposal's ``target_kind`` is ``paid_action``. The handler flips the
``paid_actions_proposed`` row's ``status`` to ``accepted`` BEFORE this
runs — the founder approval is the source-of-truth event. This helper's
job is the **best-effort downstream apply** against the right platform
adapter.

Three kinds (one per proposal flavor the paid miner can emit):

  - ``pause`` -> calls ``google_ads.pause_existing_ad`` or
    ``meta_ads.pause_existing_ad`` depending on platform.
  - ``reallocate_budget`` -> NOT WIRED in v1. The action records as
    ``status: "deferred"`` with a rationale so the founder sees it
    didn't actually move money. Reallocation requires reading + writing
    budget caps which the v1 adapters don't expose.
  - anything else -> ``status: "unknown_kind"``.

Outcome shape (always returned, never raises) so the caller can update
``paid_actions_proposed.applied_at`` / ``apply_result`` without a
try/except dance::

    {"status": "applied" | "skipped" | "failed" | "deferred" | "unknown_kind",
     "platform": str | None,
     "reason": str,
     "details": dict}

When credentials aren't configured for the target platform, the
outcome is ``skipped`` (not ``failed``) — same posture as the outbound
publish integrations: a missing key is not a bug.
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


def apply_paid_action(proposal: dict) -> dict:
    """Dispatch one paid_actions_proposed row to its platform adapter.

    Args:
        proposal: A document from ``paid_actions_proposed`` — must have
            ``kind`` (pause/reallocate_budget), ``platform``
            (google_ads/meta_ads), ``external_id`` (platform-side ad
            id), and ideally ``evidence.source_variant_id`` /
            ``evidence.variant_name`` for the rationale.

    Returns:
        Dict matching the module docstring's shape. Caller is expected
        to merge ``{"applied_at": now, "apply_result": <return>}`` onto
        the Mongo row.
    """
    kind = (proposal.get("kind") or "").lower()
    platform = (proposal.get("platform") or "").lower()
    external_id = proposal.get("external_id") or ""
    evidence = proposal.get("evidence") or {}

    if kind == "pause":
        return _dispatch_pause(platform, external_id, evidence)

    if kind == "reallocate_budget":
        # v1 NOT WIRED — reallocation requires budget read/write surface
        # the adapters don't expose. Record as deferred so the UI shows
        # honest status; founder can do the move in the platform UI.
        return {
            "status":   "deferred",
            "platform": platform or None,
            "reason":   "reallocate_budget not yet wired to adapters (v2)",
            "details": {
                "source_variant_id":   evidence.get("source_variant_id"),
                "source_variant_name": evidence.get("source_variant_name"),
                "winner_variant_name": evidence.get("winner_variant_name"),
            },
        }

    return {
        "status":   "unknown_kind",
        "platform": platform or None,
        "reason":   f"unknown paid-action kind {kind!r}",
        "details":  {},
    }


# ---------------------------------------------------------------------------
# Per-platform dispatch
# ---------------------------------------------------------------------------

def _dispatch_pause(platform: str, external_id: str, evidence: dict) -> dict:
    """Route a pause to the right adapter."""
    if platform == "google_ads":
        return _pause_google_ads(external_id, evidence)
    if platform == "meta_ads":
        return _pause_meta_ads(external_id)
    return {
        "status":   "unknown_kind",
        "platform": platform or None,
        "reason":   f"no pause adapter for platform {platform!r}",
        "details":  {},
    }


def _pause_google_ads(external_id: str, evidence: dict) -> dict:
    """Google Ads pause needs both ad_group_id + ad_id to construct the
    resource_name. The miner records ad_group_id under
    ``evidence.ad_group_id`` when it can derive it; if absent, fall back
    to the GOOGLE_ADS_DEFAULT_AD_GROUP_ID env var (same pattern as the
    publish path)."""
    try:
        from shared.integrations import google_ads
    except Exception as e:
        return _fail(google_ads_unconfigured_msg(e))

    if not google_ads.is_configured():
        return {
            "status":   "skipped",
            "platform": "google_ads",
            "reason":   "Google Ads credentials not set — proposal accepted "
                         "but no platform-side change applied",
            "details":  {},
        }

    ad_group_id = (evidence.get("ad_group_id") or "").strip()
    if not ad_group_id:
        import os
        ad_group_id = os.environ.get("GOOGLE_ADS_DEFAULT_AD_GROUP_ID", "").strip()
    if not ad_group_id:
        return {
            "status":   "skipped",
            "platform": "google_ads",
            "reason":   "ad_group_id unavailable (neither evidence nor "
                         "GOOGLE_ADS_DEFAULT_AD_GROUP_ID set)",
            "details":  {"external_id": external_id},
        }

    if not external_id:
        return {
            "status":   "skipped",
            "platform": "google_ads",
            "reason":   "external_id (ad_id) missing on proposal — nothing to pause",
            "details":  {},
        }

    try:
        result = google_ads.pause_existing_ad(
            ad_group_id=ad_group_id, ad_id=external_id,
        )
        return {
            "status":   "applied",
            "platform": "google_ads",
            "reason":   "PAUSED via Google Ads REST",
            "details":  result,
        }
    except google_ads.GoogleAdsNotConfigured as e:
        return {"status": "skipped", "platform": "google_ads",
                "reason": str(e), "details": {}}
    except Exception as e:
        log.warning("google_ads pause failed for ad_id=%s: %s", external_id, e)
        return {"status": "failed", "platform": "google_ads",
                "reason": str(e)[:200], "details": {"ad_id": external_id}}


def _pause_meta_ads(external_id: str) -> dict:
    try:
        from shared.integrations import meta_ads
    except Exception as e:
        return _fail(f"meta_ads import failed: {e}")

    if not meta_ads.is_configured():
        return {
            "status":   "skipped",
            "platform": "meta_ads",
            "reason":   "Meta Ads credentials not set — proposal accepted "
                         "but no platform-side change applied",
            "details":  {},
        }

    if not external_id:
        return {
            "status":   "skipped",
            "platform": "meta_ads",
            "reason":   "external_id (ad_id) missing on proposal — nothing to pause",
            "details":  {},
        }

    try:
        result = meta_ads.pause_existing_ad(external_id)
        return {
            "status":   "applied",
            "platform": "meta_ads",
            "reason":   "PAUSED via Meta Graph API",
            "details":  result,
        }
    except meta_ads.MetaAdsNotConfigured as e:
        return {"status": "skipped", "platform": "meta_ads",
                "reason": str(e), "details": {}}
    except Exception as e:
        log.warning("meta_ads pause failed for ad_id=%s: %s", external_id, e)
        return {"status": "failed", "platform": "meta_ads",
                "reason": str(e)[:200], "details": {"ad_id": external_id}}


def _fail(msg: str) -> dict:
    return {"status": "failed", "platform": None, "reason": msg, "details": {}}


def google_ads_unconfigured_msg(e: Any) -> str:
    return f"google_ads import failed: {e}"
