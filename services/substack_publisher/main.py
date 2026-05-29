"""Substack publisher — the gate between an approved draft and a live post.

Flow:
  1. edit_capture_handler approves a Substack draft → POSTs here.
  2. We validate the approval record exists in MongoDB (defense in depth).
  3. We check idempotency: was this telemetry_id already published?
  4. We read the original draft from telemetry.actions.raw (Content Agent
     stores the JSON shape there).
  5. We call Substack via SubstackClient.publish().
  6. We write to attribution_map so outcome_attach can read post analytics.
  7. We emit a `publish_substack` telemetry action so the publish event
     itself is in the event log (with declared outcome slots).
  8. We update the approvals record with publish_state + external_id + url.

If anything fails — invalid approval, double-publish attempt, Substack API
error — we open an ops_incident and return a 4xx/5xx with the reason.
Publishes never happen without a valid approval.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime

from flask import Flask, jsonify, request
from google.cloud import bigquery
from pydantic import BaseModel

from services.substack_publisher.substack_client import (
    PublishResult,
    SubstackClient,
)
from shared import mongo_tools
from shared.clients import bigquery_client
from shared.telemetry import (
    ModelArmorResult,
    TelemetryRecord,
    emit_action,
)

mongo_tools.use_secret("mongo_uri_writer")

app = Flask(__name__)
PROJECT_ID = os.environ["PROJECT_ID"]
BQ = bigquery_client()
log = logging.getLogger(__name__)


class PublishRequest(BaseModel):
    telemetry_id: str
    publish_at: datetime | None = None
    audience: str = "everyone"
    send_email: bool = True


@app.post("/publish")
def publish():
    """Approve → publish flow. Always idempotent on telemetry_id."""
    body = PublishRequest.model_validate(request.get_json(force=True))
    tid = body.telemetry_id

    # 1. Validate approval — the publisher MUST NOT act without one
    approval = mongo_tools.find_one("approvals", {"telemetry_id": tid})
    if not approval:
        _open_incident("substack_publish_no_approval", "critical", tid,
                       f"Publisher invoked without approval record for {tid}")
        return jsonify({"ok": False,
                        "error": "no_approval_record",
                        "telemetry_id": tid}), 403

    if approval.get("decision") != "approve":
        _open_incident("substack_publish_wrong_decision", "high", tid,
                       f"Approval decision is {approval.get('decision')}, not approve")
        return jsonify({"ok": False,
                        "error": f"approval_decision_is_{approval.get('decision')}",
                        "telemetry_id": tid}), 403

    # 2. Idempotency — bail if already published
    existing = mongo_tools.find_one("attribution_map", {"telemetry_id": tid})
    if existing and existing.get("substack_post_id"):
        return jsonify({
            "ok": True, "already_published": True,
            "post_id": existing["substack_post_id"],
            "url": existing.get("substack_url"),
        }), 200

    # 3. Read draft from telemetry — Content Agent stores the JSON shape in raw
    draft = _read_draft(tid)
    if not draft:
        return jsonify({"ok": False,
                        "error": "draft_not_found",
                        "telemetry_id": tid}), 404

    headline = draft.get("headline") or "(missing headline)"
    subtitle = draft.get("subtitle") or ""
    body_markdown = draft.get("body_markdown") or draft.get("draft") or ""

    if not body_markdown:
        return jsonify({"ok": False, "error": "empty_body"}), 400

    # 4. Mark publish_state=publishing on the approval record so the UI
    # can show "Publishing..." between the approve click and API success.
    mongo_tools.upsert("approvals", {"telemetry_id": tid}, {
        "publish_state": "publishing",
        "publish_started_at": datetime.now(UTC),
    })

    # 5. Pull the image (if ImageBrief produced one) and call Substack
    image = _read_image(tid)
    cover_image_url = (image or {}).get("url") if image else None
    cover_image_alt = (image or {}).get("alt_text") if image else None

    client = SubstackClient()
    result: PublishResult = client.publish(
        headline=headline,
        subtitle=subtitle,
        body_markdown=body_markdown,
        publish_at=body.publish_at,
        audience=body.audience,
        send_email=body.send_email,
        cover_image_url=cover_image_url,
        cover_image_alt=cover_image_alt,
    )

    # 6. Write attribution + update approval — same shape whether real or manual
    post_id = result.post_id or f"manual:{tid}"
    publication_id = result.publication_id
    url = result.url
    now = datetime.now(UTC)

    from mongo.queries import record_attribution
    record_attribution(tid, {
        "channel": "substack",
        "substack_post_id": post_id,
        "substack_publication_id": publication_id,
        "substack_url": url,
        "publish_mode": result.mode,
        "published_at": result.published_at or now,
    })

    mongo_tools.upsert("approvals", {"telemetry_id": tid}, {
        "publish_state": "published" if result.mode == "api" else "manual_review",
        "publish_mode": result.mode,
        "publish_completed_at": now,
        "external_id": post_id,
        "external_url": url,
        "publish_reason": result.reason,
    })

    # 7. Emit a publish_substack telemetry action — drives outcome slots
    _emit_publish_telemetry(tid, post_id, url, result.mode, body.publish_at)

    # 8. If we fell back to manual_review, open a low-severity incident so
    # the founder knows to publish by hand.
    if result.mode == "manual_review":
        _open_incident(
            "substack_manual_review", "low", tid,
            f"Substack API unavailable ({result.reason}); "
            f"draft staged for manual publish by founder. "
            f"Headline: {headline!r}"
        )

    return jsonify({
        "ok": True,
        "mode": result.mode,
        "post_id": post_id,
        "url": url,
        "published_at": (result.published_at or now).isoformat(),
        "reason": result.reason,
    }), 200


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_draft(telemetry_id: str) -> dict | None:
    """The Content Agent emits a Substack draft as JSON in telemetry.actions.raw."""
    sql = f"""
    SELECT raw FROM `{PROJECT_ID}.telemetry.actions`
    WHERE telemetry_id = @id AND channel = 'substack'
    LIMIT 1
    """
    rows = BQ.query(sql, job_config=bigquery.QueryJobConfig(query_parameters=[
        bigquery.ScalarQueryParameter("id", "STRING", telemetry_id),
    ])).result()
    row = next(iter(rows), None)
    if not row or not row.raw:
        return None
    raw = row.raw
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return {"body_markdown": raw}
    # Content Agent's draft JSON may be nested under "draft" or top-level
    if isinstance(raw, dict):
        if "headline" in raw or "body_markdown" in raw:
            return raw
        inner = raw.get("draft")
        if isinstance(inner, str):
            try:
                return json.loads(inner)
            except Exception:
                return {"body_markdown": inner}
        if isinstance(inner, dict):
            return inner
    return None


def _read_image(telemetry_id: str) -> dict | None:
    """ImageBrief's output lives in telemetry.actions.raw.image OR on a
    separate image_brief_op row keyed by the same parent telemetry_id."""
    sql = f"""
    SELECT raw FROM `{PROJECT_ID}.telemetry.actions`
    WHERE telemetry_id = @id AND channel = 'substack'
    LIMIT 1
    """
    rows = BQ.query(sql, job_config=bigquery.QueryJobConfig(query_parameters=[
        bigquery.ScalarQueryParameter("id", "STRING", telemetry_id),
    ])).result()
    row = next(iter(rows), None)
    if not row or not row.raw:
        return None
    raw = row.raw
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return None
    return raw.get("image") if isinstance(raw, dict) else None


def _emit_publish_telemetry(telemetry_id: str, post_id: str, url: str | None,
                             mode: str, publish_at: datetime | None) -> None:
    """Emit a publish_substack action. Outcome slots are declared in
    agents/_common._declared_outcomes for action_type='publish_substack' —
    open_rate_24h, click_rate_72h, restacks_7d, new_subscribers_7d,
    paid_conversions_30d."""
    from agents._common import _declared_outcomes  # local import to avoid cycle

    record = TelemetryRecord(
        agent="substack_publisher",
        skill_id="substack_post",
        skill_version="publish_v1",
        action_type="publish_substack",
        channel="substack",
        approval_id=telemetry_id,  # the approval lives keyed by telemetry_id
        model_armor=ModelArmorResult(decision="allow"),
        raw={
            "parent_telemetry_id": telemetry_id,
            "substack_post_id": post_id,
            "substack_url": url,
            "publish_mode": mode,
            "publish_at": publish_at.isoformat() if publish_at else None,
        },
    )
    try:
        emit_action(record, outcomes=_declared_outcomes("publish_substack"))
    except Exception as e:
        log.exception("publish telemetry emit failed: %s", e)


def _open_incident(category: str, severity: str, target: str, message: str) -> None:
    mongo_tools.insert_many("ops_incidents", [{
        "_id": f"incident_{category}_{target}_{int(datetime.now().timestamp())}",
        "category": category,
        "severity": severity,
        "target": target,
        "message": message,
        "status": "open",
        "opened_at": datetime.now(UTC),
    }])


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
