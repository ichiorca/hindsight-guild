"""Approval queue + decisions + published URL lookup."""
from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from fastapi import APIRouter
from pydantic import BaseModel

from services.web_api.routers.drafting import subject_from_draft
from shared import mongo_tools

router = APIRouter()
log = logging.getLogger(__name__)


def _numeric_scores(val: object) -> dict:
    """Keep only numeric rubric scores. A populated eval_scores also carries
    provenance fields — judge_model (str) + scored_at (datetime) — which would
    fail the QueueItem.eval_scores: dict[str, float] schema and 500 the queue."""
    if not isinstance(val, dict):
        try:
            val = val.model_dump() if hasattr(val, "model_dump") else {}
        except Exception:
            val = {}
    return {k: v for k, v in val.items()
            if isinstance(v, (int, float)) and not isinstance(v, bool)}


def _as_image(val: object) -> dict | None:
    """Coerce a stored image value to a dict. The image_brief agent emits its
    result as JSON TEXT, so older/raw rows may carry the image as a JSON
    string; the QueueItem.image field + UI expect an object."""
    if isinstance(val, dict):
        return val or None
    if isinstance(val, str) and val.strip():
        try:
            parsed = json.loads(val)
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            return None
    return None


def _as_images(val: object) -> list | None:
    """Coerce a stored images value (ImageBrief emits a LIST of 1-3 visuals) to
    a list of dicts, tolerating a JSON-string list or JSON-string items."""
    if isinstance(val, str) and val.strip():
        try:
            val = json.loads(val)
        except Exception:
            return None
    if not isinstance(val, list):
        return None
    out = [d for d in (_as_image(it) for it in val) if d]
    return out or None


def _iso_utc(ts: object) -> str:
    """Serialize a Mongo datetime as a TZ-AWARE ISO string. pymongo returns
    naive (UTC) datetimes, and a naive isoformat() ('...T23:35:16') is parsed
    as LOCAL time by the browser — which made every recent draft read as 'now'.
    Stamp UTC so the frontend's relative-time is correct."""
    if hasattr(ts, "isoformat"):
        if getattr(ts, "tzinfo", None) is None:
            ts = ts.replace(tzinfo=UTC)   # type: ignore[union-attr]
        return ts.isoformat()             # type: ignore[union-attr]
    return str(ts)


# ---------------------------------------------------------------------------
# Queue
# ---------------------------------------------------------------------------

class QueueItem(BaseModel):
    telemetry_id: str
    ts: datetime
    agent: str
    channel: str | None
    skill_id: str
    skill_version: str
    subject: str | None = None   # email subject / substack headline, if any
    draft_text: str
    eval_scores: dict[str, float]
    review_flags: list[dict[str, Any]]
    customer_voice_used: list[str]
    icp_segment: str | None
    experiment_id: str | None
    image: dict[str, Any] | None = None   # {url, alt_text, aspect_ratio, mode, prompt, rationale}
    images: list[dict[str, Any]] | None = None  # ImageBrief's 1-3 visuals
    publish_state: str | None = None      # publishing | published | manual_review | failed
    publish_url: str | None = None
    publish_mode: str | None = None       # api | manual_review
    # PRD-02: populated when this draft was kicked off by signal_router.
    # The Queue card renders a "Triggered by: HN · 14h ago" chip linked
    # to triggered_by_evidence_url. ``None`` for human-initiated drafts.
    triggered_by_signal_id: str | None = None
    triggered_by_source: str | None = None
    triggered_by_evidence_url: str | None = None
    triggered_by_ts: datetime | None = None


@router.get("/api/queue", response_model=list[QueueItem])
def get_queue(channel: str | None = None, limit: int = 50):
    """Pending drafts the founder needs to decide on.

    Pulls drafting-class actions from the last 7 days that don't yet have
    an approval record. The UI shows the queue grouped by channel.

    Source: Mongo (`actions`, populated by shared/telemetry.py's dual-write)
    is the operational store and the default read path — it's where the
    pipeline's after-callbacks reliably land every draft. BigQuery is wired
    for future GA4/analytics work but is read here only when explicitly
    opted in via QUEUE_USE_BQ=1 (and a client is available), so the inbox
    never goes empty just because the BQ streaming path is unpopulated.
    """
    from services.web_api.main import BQ, PROJECT_ID, bigquery
    if BQ is None or os.environ.get("QUEUE_USE_BQ", "").lower() not in ("1", "true", "yes"):
        return _queue_from_mongo(channel=channel, limit=limit)
    chan_filter = "AND channel = @ch" if channel else ""
    sql = f"""
    SELECT a.telemetry_id, a.ts, a.agent, a.channel, a.skill_id, a.skill_version,
           a.eval_scores, a.raw, a.experiment_id
    FROM `{PROJECT_ID}.telemetry.actions` a
    LEFT JOIN (
      SELECT telemetry_id FROM `{PROJECT_ID}.training.edits`
    ) e USING(telemetry_id)
    WHERE a.action_type LIKE 'draft_%'
      AND a.ts >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 7 DAY)
      AND e.telemetry_id IS NULL
      {chan_filter}
    ORDER BY a.ts DESC
    LIMIT @lim
    """
    params = [bigquery.ScalarQueryParameter("lim", "INT64", limit)]
    if channel:
        params.append(bigquery.ScalarQueryParameter("ch", "STRING", channel))

    rows = BQ.query(sql, job_config=bigquery.QueryJobConfig(query_parameters=params)).result()

    # Cross-reference approvals from Mongo. Keep rows where the publish is
    # in flight or stuck so the founder sees state moving.
    approvals_map = {a["telemetry_id"]: a for a in mongo_tools.find(
        "approvals", {}, limit=500)}

    items: list[QueueItem] = []
    for r in rows:
        appr = approvals_map.get(r.telemetry_id)
        if appr:
            decision = appr.get("decision")
            publish_state = appr.get("publish_state")
            if decision == "reject":
                continue
            if decision in ("approve", "edit") and publish_state == "published":
                continue
            # else: keep — UI surfaces the publish_state badge
        raw = r.raw or {}
        if isinstance(raw, str):
            import json
            try:
                raw = json.loads(raw)
            except Exception:
                raw = {}
        # Substack drafts arrive as a structured dict ({headline, subtitle,
        # body_markdown}). Flatten to a single preview string for the Queue
        # card; the publish flow reads the structured fields separately.
        draft_blob = raw.get("draft") or raw.get("output_text") or ""
        if isinstance(draft_blob, dict):
            headline = draft_blob.get("headline") or ""
            body = draft_blob.get("body_markdown") or draft_blob.get("body") or ""
            draft_text = f"# {headline}\n\n{body}" if headline else body
        else:
            draft_text = draft_blob
        items.append(QueueItem(
            telemetry_id=r.telemetry_id,
            ts=r.ts,
            agent=r.agent,
            channel=r.channel,
            skill_id=r.skill_id,
            skill_version=r.skill_version,
            subject=subject_from_draft(draft_blob),
            draft_text=draft_text,
            eval_scores=_numeric_scores(r.eval_scores),
            review_flags=raw.get("review_flags") or [],
            customer_voice_used=raw.get("customer_voice_used") or [],
            icp_segment=raw.get("icp_segment"),
            experiment_id=r.experiment_id,
            image=_as_image(raw.get("image")),
            images=_as_images(raw.get("images")),
            publish_state=(appr or {}).get("publish_state"),
            publish_url=(appr or {}).get("external_url"),
            publish_mode=(appr or {}).get("publish_mode"),
        ))
    return items


def _queue_from_mongo(channel: str | None, limit: int) -> list[QueueItem]:
    """LOCAL_DEV queue source. Reads the same shape from the Mongo
    `actions` collection that shared/telemetry.py dual-writes to."""
    cutoff = datetime.now(UTC) - timedelta(days=7)
    db = mongo_tools.db()
    filt: dict[str, Any] = {
        "action_type": {"$regex": "^draft_"},
        "ts": {"$gte": cutoff},
    }
    if channel:
        filt["channel"] = channel
    rows = list(db["actions"].find(filt).sort("ts", -1).limit(limit))

    # Mirror the BQ path's approval cross-reference for publish-state display.
    approvals_map = {a["telemetry_id"]: a for a in mongo_tools.find(
        "approvals", {}, limit=500)}

    # PRD-02: pre-join signals so the queue card can render the
    # "Triggered by" chip. We pull ONE find for the whole batch keyed
    # on triggered_telemetry_id; the inverse-index by telemetry_id is
    # tiny so this is a single round-trip.
    telem_ids = [r["telemetry_id"] for r in rows]
    signal_map: dict[str, dict] = {}
    if telem_ids:
        try:
            for s in db["signals"].find(
                {"triggered_telemetry_id": {"$in": telem_ids}},
                {"_id": 1, "source": 1, "evidence_url": 1, "ts": 1,
                 "triggered_telemetry_id": 1},
            ):
                signal_map[s["triggered_telemetry_id"]] = s
        except Exception:
            # Bare collection / no signals yet — nothing to surface.
            pass

    items: list[QueueItem] = []
    for r in rows:
        appr = approvals_map.get(r["telemetry_id"])
        if appr and appr.get("publish_state") not in ("publishing", "failed",
                                                      "manual_review"):
            # Already decided + (published or n/a) — drop from queue. Keep
            # ones still moving (in flight or stuck).
            continue
        raw = r.get("raw") or {}
        # Substack drafts arrive as a structured dict; flatten for preview.
        draft_blob = raw.get("draft") or raw.get("output_text") or ""
        if isinstance(draft_blob, dict):
            head = draft_blob.get("headline") or ""
            body = draft_blob.get("body_markdown") or draft_blob.get("body") or ""
            draft_text = f"# {head}\n\n{body}" if head else body
        else:
            draft_text = draft_blob
        sig = signal_map.get(r["telemetry_id"])
        items.append(QueueItem(
            telemetry_id=r["telemetry_id"],
            ts=_iso_utc(r.get("ts")),
            agent=r.get("agent") or "",
            channel=r.get("channel"),
            # `or ""` (not a .get default): these land as explicit None on
            # real pipeline rows, which would 500 the required-str fields.
            skill_id=r.get("skill_id") or "",
            skill_version=r.get("skill_version") or "",
            subject=subject_from_draft(draft_blob),
            draft_text=draft_text or "",
            eval_scores=_numeric_scores(r.get("eval_scores")),
            review_flags=raw.get("review_flags") or [],
            customer_voice_used=raw.get("customer_voice_used") or [],
            icp_segment=raw.get("icp_segment"),
            experiment_id=r.get("experiment_id"),
            image=_as_image(raw.get("image")),
            images=_as_images(raw.get("images")),
            publish_state=(appr or {}).get("publish_state"),
            publish_url=(appr or {}).get("external_url"),
            publish_mode=(appr or {}).get("publish_mode"),
            triggered_by_signal_id=str(sig["_id"]) if sig else None,
            triggered_by_source=sig.get("source") if sig else None,
            triggered_by_evidence_url=sig.get("evidence_url") if sig else None,
            triggered_by_ts=sig.get("ts") if sig else None,
        ))
    return items


# ---------------------------------------------------------------------------
# Decisions (approve / edit / reject) — same path as the Sheet
# ---------------------------------------------------------------------------

class Decision(BaseModel):
    telemetry_id: str
    decision: str  # approve | edit | reject
    original_draft: str
    approved_text: str | None = ""
    rejection_reason: str | None = ""
    channel: str | None = None
    decided_by: str = "founder"


@router.post("/api/decisions")
def submit_decision(d: Decision):
    """Forwards to the edit_capture_handler so the learning loop fires
    identically whether decisions come from the Sheet or the UI.

    In LOCAL_DEV (no Cloud Run handler), writes the approval row directly
    to Mongo so the UI updates immediately and the founder's decision is
    durable. The cloud handler does extra work — classifying edits as
    tone-vs-fact-vs-cta, inserting into ``negative_examples`` on reject —
    but the core write (an ``approvals`` row + queue refresh) happens
    locally too, so the UI flow isn't broken without GCP creds.
    """
    from services.web_api.main import _secret_optional
    url = _secret_optional("edit_capture_handler_url")
    if url:
        # The handler does EXTRA work (classifying edits tone/fact/cta), but a
        # founder's approve/reject must NEVER fail just because that secondary
        # service is unavailable. On any handler error, fall through to the
        # durable Mongo write below (which also captures negative_examples on
        # reject + publishes on approve) so the decision still lands.
        try:
            r = httpx.post(f"{url}/handle", json=d.model_dump(), timeout=20)
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001 — degrade to the durable path
            log.warning("edit_capture_handler unavailable (%s); writing the "
                        "decision directly to Mongo instead", e)

    # Durable path (also the LOCAL_DEV path) — write the decision to Mongo
    # through the history-
    # aware helpers so every write captures a pre-image in history.<coll>
    # and lands with a _provenance block. Founder approvals are
    # authoritative source-of-truth, so trust_tier="verified".
    from mongo.history import (
        DocumentNotFound,
        default_provenance_block,
        insert_with_provenance,
        update_with_history,
    )

    now = datetime.now(UTC)
    actor_id = d.decided_by or "founder"

    record = {
        "telemetry_id": d.telemetry_id,
        "decision": d.decision,
        "original_draft": d.original_draft,
        "approved_text": d.approved_text,
        "rejection_reason": d.rejection_reason,
        "channel": d.channel,
        "decided_by": d.decided_by,
        "decided_at": now,
    }
    # The approvals collection has a unique index on telemetry_id. The
    # founder changing their mind on a draft is an UPDATE (history-
    # tracked); the first decision is an INSERT (history "create" row).
    try:
        update_with_history(
            "approvals", {"telemetry_id": d.telemetry_id},
            {"$set": record},
            actor_id=actor_id,
            change_kind=f"approval_{d.decision}",
        )
    except DocumentNotFound:
        block = default_provenance_block(
            actor_id=actor_id, kind="human",
            trust_tier="verified", confidence=1.0,
            source_kind="founder_decision",
        )
        insert_with_provenance(
            "approvals", {**block, **record},
            actor_id=actor_id,
            change_kind=f"approval_{d.decision}",
        )

    # On reject, mirror the cloud handler's negative_examples insert so
    # the Review agent can ground future drafts against rejected patterns.
    # We previously required ``original_draft`` to be truthy — but synthetic
    # queue items occasionally have empty draft_text, and we still want
    # the rejection reason captured for learning. Always insert on reject.
    if d.decision == "reject":
        neg_block = default_provenance_block(
            actor_id=actor_id, kind="human",
            trust_tier="verified", confidence=1.0,
            source_kind="founder_rejection",
        )
        insert_with_provenance("negative_examples", {
            **neg_block,
            "channel": d.channel,
            "rejection_category": _infer_reject_category(d.rejection_reason or ""),
            "rejected_phrase": (d.original_draft or "")[:500],
            "reason": d.rejection_reason or "",
            "tags": [],
            "ts": now,
        }, actor_id=actor_id, change_kind="reject_capture")

    # On approve, dispatch the draft to whichever external platform
    # owns this channel (Dev.to → blog/substack, LinkedIn → linkedin,
    # Google Ads / Meta Ads → their respective paid channels). Failure
    # is non-fatal: the approval row is already saved; we just record
    # the publish-failed state so the UI can surface it.
    # ``attribution_map`` is the canonical store for "what external
    # URL/ID did this draft end up at" across all channels.
    publish_result: dict | None = None
    if d.decision == "approve" and (d.approved_text or "").strip():
        publish_result = _try_publish_for_channel(d, actor_id, now)

    log.info("decision %s recorded for %s (LOCAL_DEV path)",
             d.decision, d.telemetry_id)
    resp = {"ok": True, "mode": "local"}
    if publish_result:
        resp["publish"] = publish_result
    return resp


@router.get("/api/published/{telemetry_id}")
def get_published_url(telemetry_id: str) -> dict:
    """Return the external publish URL for a queue item if it was
    published to a real platform (Dev.to today; LinkedIn / Bluesky /
    Hashnode would land here too once their integrations exist).

    Empty dict when the draft was approved but not published (no
    integration configured, or rejected, or still pending)."""
    try:
        row = mongo_tools.db()["attribution_map"].find_one(
            {"telemetry_id": telemetry_id},
        )
    except Exception:
        return {}
    if not row:
        return {}
    return {
        "platform":     row.get("platform"),
        "external_url": row.get("external_url"),
        "external_id":  row.get("external_id"),
        "title":        row.get("title"),
        "published_at": row.get("published_at"),
    }


# ---------------------------------------------------------------------------
# Publishing helpers — used by /api/decisions on approve.
# ---------------------------------------------------------------------------

def _try_publish_for_channel(d: Decision, actor_id: str, now: datetime) -> dict:
    """Dispatch an approved draft to the integration that owns its
    channel. Returns a dict describing what happened
    (``status`` ∈ {published, skipped, failed, no_route}).

    The CHANNEL_ROUTES table in ``shared.integrations`` decides which
    adapter handles each channel. Branches below adapt the draft's
    ``approved_text`` (which may be markdown, plain text, or a JSON
    payload) into the per-adapter argument shape.

    On success, records an ``attribution_map`` row so the queue's
    "Published on X" badge can deep-link to the live URL / Ads Manager
    surface.
    """
    from shared.integrations import CHANNEL_ROUTES

    if not d.approved_text:
        return {"status": "skipped", "reason": "no approved text"}

    channel = (d.channel or "").lower()
    if channel not in CHANNEL_ROUTES:
        return {"status": "no_route", "channel": channel}

    # Pull icp_segment + topic_hint off the actions row — needed by
    # Dev.to for tag derivation and by Google/Meta for ad naming.
    try:
        db_h = mongo_tools.db()
        act = db_h["actions"].find_one({"telemetry_id": d.telemetry_id}) or {}
    except Exception:
        act = {}
    icp_segment = act.get("icp_segment") or ""
    topic_hint = ""
    raw = act.get("raw") or {}
    if isinstance(raw, dict):
        topic_hint = raw.get("topic_hint") or ""

    try:
        if channel in ("blog", "substack"):
            result = _publish_devto(d, icp_segment, topic_hint)
        elif channel == "linkedin":
            result = _publish_linkedin(d)
        elif channel == "google_ads":
            result = _publish_google_ads(d)
        elif channel == "meta_ads":
            result = _publish_meta_ads(d, icp_segment, topic_hint)
        else:
            return {"status": "no_route", "channel": channel}
    except _PublishSkipped as e:
        return {"status": "skipped", "platform": e.platform, "reason": e.reason}
    except _PublishFailed as e:
        log.warning("%s publish failed for %s: %s", e.platform, d.telemetry_id, e.reason)
        return {"status": "failed", "platform": e.platform, "error": e.reason[:200]}

    # Persist the external handle in attribution_map. ``external_url``
    # is the deep link the UI surfaces; for paid adapters this is the
    # Ads Manager URL since there's no public landing surface.
    try:
        mongo_tools.db()["attribution_map"].update_one(
            {"telemetry_id": d.telemetry_id},
            {"$set": {
                "telemetry_id":  d.telemetry_id,
                "channel":       d.channel,
                "published_at":  now,
                "platform":      result["platform"],
                "external_url":  result.get("url", ""),
                "external_id":   result.get("external_id", ""),
                "title":         result.get("title", ""),
                "actor_id":      actor_id,
            }},
            upsert=True,
        )
    except Exception as e:
        log.warning("attribution_map write failed: %s", e)

    return {"status": "published", **result}


class _PublishSkipped(Exception):
    """Internal — integration not configured. Caught by dispatcher."""
    def __init__(self, platform: str, reason: str):
        self.platform = platform
        self.reason = reason
        super().__init__(reason)


class _PublishFailed(Exception):
    """Internal — integration call failed. Caught by dispatcher."""
    def __init__(self, platform: str, reason: str):
        self.platform = platform
        self.reason = reason
        super().__init__(reason)


def _publish_devto(d: Decision, icp_segment: str, topic_hint: str) -> dict:
    """Dev.to (Forem) blog publish. Accepts markdown body or a substack
    JSON envelope ({headline, subtitle, body_markdown})."""
    from shared.integrations.devto import (
        DevToError,
        DevToNotConfigured,
        derive_tags,
        derive_title,
        publish_article,
    )
    body_md = d.approved_text or ""
    if body_md.lstrip().startswith("{"):
        try:
            obj = json.loads(body_md)
            if isinstance(obj, dict):
                head = obj.get("headline") or ""
                sub = obj.get("subtitle") or ""
                body = obj.get("body_markdown") or obj.get("body") or ""
                body_md = (f"# {head}\n\n" if head else "") \
                          + (f"> {sub}\n\n" if sub else "") \
                          + body
        except Exception:
            pass

    title = derive_title(body_md)
    tags = derive_tags(icp_segment, topic_hint)

    try:
        article = publish_article(body_md, title=title, tags=tags, publish=True)
    except DevToNotConfigured:
        raise _PublishSkipped("devto", "DEVTO_API_KEY not set") from None
    except DevToError as e:
        raise _PublishFailed("devto", str(e)) from e

    return {
        "platform": "devto",
        "url": article.url,
        "external_id": article.id,
        "title": article.title,
    }


def _publish_linkedin(d: Decision) -> dict:
    """LinkedIn UGC personal/org share. Accepts markdown or plain text;
    the adapter strips markdown markers since LinkedIn renders plain
    text only."""
    from shared.integrations.linkedin import (
        LinkedInError,
        LinkedInNotConfigured,
        publish_post,
    )
    body = d.approved_text or ""
    # Substack-shaped JSON envelopes occasionally arrive on the
    # linkedin channel when the founder repurposes a blog draft —
    # flatten so we don't post raw JSON.
    if body.lstrip().startswith("{"):
        try:
            obj = json.loads(body)
            if isinstance(obj, dict):
                head = obj.get("headline") or ""
                sub = obj.get("subtitle") or ""
                inner = obj.get("body_markdown") or obj.get("body") or ""
                body = "\n\n".join(x for x in (head, sub, inner) if x)
        except Exception:
            pass

    try:
        post = publish_post(body)
    except LinkedInNotConfigured:
        raise _PublishSkipped("linkedin", "LINKEDIN_ACCESS_TOKEN/AUTHOR_URN not set") from None
    except LinkedInError as e:
        raise _PublishFailed("linkedin", str(e)) from e

    return {
        "platform": "linkedin",
        "url": post.url,
        "external_id": post.post_urn,
        "title": (body[:60] + "…") if len(body) > 60 else body,
    }


def _publish_google_ads(d: Decision) -> dict:
    """Google Ads — create a PAUSED Responsive Search Ad in the
    configured ad group. ``approved_text`` is expected to be a JSON
    payload of the form ``{headlines: [...], descriptions: [...],
    final_url, ad_group_id?}`` from the paid_media_agent. Falls back
    to single-headline mode when the agent emitted a flat string.

    Note: Google Ads identifies RSAs by their internal resource name —
    there's no first-class ad-name field, so icp_segment / topic_hint
    aren't threaded through here (unlike Meta, which has ``name``).
    """
    from shared.integrations.google_ads import (
        GoogleAdsError,
        GoogleAdsNotConfigured,
        create_paused_rsa,
    )
    headlines, descriptions, final_url, ad_group_id = [], [], "", ""
    primary_text = d.approved_text or ""

    text = primary_text.strip()
    if text.startswith("{") or text.startswith("["):
        try:
            obj = json.loads(text)
            # Two valid envelopes: an RSA object directly, OR a list
            # of variant objects (paid_media_agent's typical shape).
            if isinstance(obj, list) and obj:
                obj = obj[0]
            if isinstance(obj, dict):
                headlines = list(obj.get("headlines") or [])
                if not headlines and obj.get("headline"):
                    headlines = [obj["headline"]]
                descriptions = list(obj.get("descriptions") or [])
                if not descriptions and obj.get("body"):
                    descriptions = [obj["body"]]
                final_url = obj.get("final_url") or obj.get("cta_url") or ""
                ad_group_id = obj.get("ad_group_id") or ""
                primary_text = obj.get("body") or obj.get("primary_text") or primary_text
        except Exception:
            pass

    if not ad_group_id:
        ad_group_id = os.environ.get("GOOGLE_ADS_DEFAULT_AD_GROUP_ID", "").strip()
    if not final_url:
        final_url = os.environ.get("GOOGLE_ADS_DEFAULT_FINAL_URL", "https://example.com").strip()

    try:
        rsa = create_paused_rsa(
            ad_group_id=ad_group_id,
            final_url=final_url,
            headlines=headlines,
            descriptions=descriptions,
            primary_text=primary_text,
        )
    except GoogleAdsNotConfigured:
        raise _PublishSkipped("google_ads", "Google Ads credentials not set") from None
    except GoogleAdsError as e:
        # Differentiate "no ad group" from a genuine API error so the
        # UI can surface a more actionable skip reason.
        if "ad_group_id required" in str(e):
            raise _PublishSkipped(
                "google_ads",
                "GOOGLE_ADS_DEFAULT_AD_GROUP_ID not set (and draft did not specify one)",
            ) from None
        raise _PublishFailed("google_ads", str(e)) from e

    return {
        "platform": "google_ads",
        "url": rsa.preview_url,
        "external_id": rsa.ad_id or rsa.resource_name,
        "title": rsa.headlines[0] if rsa.headlines else "",
    }


def _publish_meta_ads(d: Decision, icp_segment: str, topic_hint: str) -> dict:
    """Meta Ads — create a PAUSED creative + ad in the configured ad
    set. Same JSON-envelope expectations as Google Ads, but with a
    single headline + primary_text instead of multi-asset RSA."""
    from shared.integrations.meta_ads import (
        MetaAdsError,
        MetaAdsNotConfigured,
        create_paused_creative,
    )
    headline = ""
    primary_text = d.approved_text or ""
    image_url = None
    link_url = ""
    ad_set_id = None

    text = primary_text.strip()
    if text.startswith("{") or text.startswith("["):
        try:
            obj = json.loads(text)
            if isinstance(obj, list) and obj:
                obj = obj[0]
            if isinstance(obj, dict):
                headline = obj.get("headline") or ""
                primary_text = (
                    obj.get("body") or obj.get("primary_text") or primary_text
                )
                image_url = obj.get("image_url") or obj.get("image")
                link_url = obj.get("link") or obj.get("cta_url") or ""
                ad_set_id = obj.get("ad_set_id") or None
        except Exception:
            pass
    if not headline:
        # Best-effort: first non-blank line is the headline.
        for line in primary_text.splitlines():
            if line.strip():
                headline = line.strip()
                break

    # Surface icp + topic in the ad name so the user can scan Ads
    # Manager and immediately tell which draft this is. Skipped when
    # both are blank — falls back to the headline-based default.
    name_parts = [p for p in (icp_segment, topic_hint) if p]
    ad_name = ("draft: " + " · ".join(name_parts)) if name_parts else None

    try:
        ad = create_paused_creative(
            headline=headline or "Draft",
            primary_text=primary_text,
            image_url=image_url,
            link_url=link_url,
            ad_set_id=ad_set_id,
            name=ad_name,
        )
    except MetaAdsNotConfigured:
        raise _PublishSkipped("meta_ads", "Meta Ads credentials not set") from None
    except MetaAdsError as e:
        raise _PublishFailed("meta_ads", str(e)) from e

    return {
        "platform": "meta_ads",
        "url": ad.preview_url,
        "external_id": ad.ad_id,
        "title": ad.headline,
    }


_REJECT_CATEGORY_HINTS = {
    "claim_risk": ["claim", "unsupported", "evidence", "absolute", "100%", "guaranteed"],
    "tone": ["tone", "off-brand", "voice", "salesy", "aggressive"],
    "originality": ["copy", "competitor", "echoes", "generic", "boilerplate"],
    "icp_relevance": ["icp", "audience", "wrong segment", "doesn't fit"],
    "conversion_intent": ["cta", "hard sell", "multi-cta", "pushy"],
}


def _infer_reject_category(reason: str) -> str:
    """Best-effort category inference from a free-text rejection reason.
    The cloud handler does this via an LLM classifier; locally we use a
    keyword match so something useful gets recorded."""
    r = (reason or "").lower()
    for cat, keywords in _REJECT_CATEGORY_HINTS.items():
        if any(kw in r for kw in keywords):
            return cat
    return "other"
