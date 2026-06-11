"""Edit capture handler — receives Sheet row syncs from Apps Script.

Writes to:
  - training.edits (any 'edit' or 'reject' decision)
  - mongodb.negative_examples (rejects only)
  - mongodb.approvals (every decision)

Edit classification: a LIGHT-tier (shared.models) structured-output Gemini call.
Cheaper than running a full ADK agent, richer than regex.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import UTC, datetime

from flask import Flask, jsonify, request
from google import genai

from mongo.history import update_with_history
from shared import mongo_tools
from shared.clients import bigquery_client
from shared.models import light

mongo_tools.use_secret("mongo_uri_writer")

app = Flask(__name__)
PROJECT_ID = os.environ["PROJECT_ID"]
REGION = os.environ.get("REGION", "us-central1")
BQ = bigquery_client()
# Honor the deploy's backend choice (Gemini 3 models are Developer-API-only
# on this project; with vertexai=False the client uses GOOGLE_API_KEY).
_USE_VERTEX = os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "true").lower() in ("1", "true", "yes")
_genai = (genai.Client(vertexai=True, project=PROJECT_ID, location=REGION)
          if _USE_VERTEX else genai.Client())
log = logging.getLogger(__name__)


EDIT_CLASSIFIER_PROMPT = """You are classifying a founder's edit on a marketing draft.

ORIGINAL DRAFT:
{before}

EDITED DRAFT:
{after}

REJECTION REASON (may be empty):
{reason}

Classify this edit. Possible categories (multiple allowed):
- softened_tone:    absolute language → directional
- trimmed_length:   significant shortening
- added_evidence:   added specific numbers / citations
- voice_corrected:  fixed brand-voice issues
- claim_removed:    dropped an unsupportable claim
- icp_adjusted:     re-targeted to the right persona
- cta_changed:      modified or replaced the call-to-action
- restructured:     reorganized structure without changing substance

Also categorize the REJECTION REASON (if any) into ONE of:
claim_risk | tone | originality | icp_relevance | conversion_intent

Return JSON only:
{{
  "edit_categories": [<list of categories>],
  "rejection_category": "<one of the above or null>"
}}
"""


def _classify_with_gemini(before: str, after: str, reason: str) -> dict:
    """Use Gemini Flash to classify the edit. Falls back to regex on failure."""
    try:
        prompt = EDIT_CLASSIFIER_PROMPT.format(
            before=before or "(empty)",
            after=after or "(empty)",
            reason=reason or "(empty)",
        )
        resp = _genai.models.generate_content(
            model=light(),
            contents=prompt,
            config={"response_mime_type": "application/json"},
        )
        return json.loads(resp.text)
    except Exception as e:
        log.warning("Gemini edit classifier failed; falling back to regex: %s", e)
        return _classify_regex(before, after, reason)


def _classify_regex(before: str, after: str, reason: str) -> dict:
    """Heuristic fallback when Gemini is unavailable."""
    cats = []
    if before and after and len(after) < len(before) * 0.85:
        cats.append("trimmed_length")
    absolutes = ("eliminates", "100%", "completely", "guaranteed")
    if any(w in (before or "").lower() for w in absolutes):
        if not any(w in (after or "").lower() for w in absolutes):
            cats.append("softened_tone")
    r = (reason or "").lower()
    rej = None
    if any(w in r for w in ("overclaim", "exaggerat", "absolute", "100%", "guarantee")):
        rej = "claim_risk"
    elif any(w in r for w in ("tone", "aggressive", "off-brand")):
        rej = "tone"
    elif any(w in r for w in ("copied", "competitor", "echo")):
        rej = "originality"
    elif any(w in r for w in ("icp", "persona", "wrong audience")):
        rej = "icp_relevance"
    elif any(w in r for w in ("cta", "conversion")):
        rej = "conversion_intent"
    return {"edit_categories": cats or ["other"], "rejection_category": rej}


@app.post("/handle")
def handle():
    p = request.get_json(force=True)
    tid = p["telemetry_id"]
    decision = p["decision"]
    now = datetime.now(UTC).isoformat()

    classification = (_classify_with_gemini(
        p.get("original_draft", ""),
        p.get("approved_text", ""),
        p.get("rejection_reason", ""),
    ) if decision in ("edit", "reject") else {"edit_categories": [], "rejection_category": None})

    if decision in ("edit", "reject"):
        edit_id = f"edit_{hashlib.sha256(f'{tid}{now}'.encode()).hexdigest()[:12]}"
        edit_row = {
            "edit_id": edit_id,
            "telemetry_id": tid,
            "ts": now,
            "before_text": p.get("original_draft", ""),
            "after_text": p.get("approved_text", ""),
            "edit_categories": classification.get("edit_categories", []),
            "rejection_reason": p.get("rejection_reason"),
        }
        # BigQuery is the system of record for training.edits; mirror to Mongo
        # so the row survives and is readable in LOCAL_DEV (no BQ client).
        if BQ is not None:
            BQ.insert_rows_json(f"{PROJECT_ID}.training.edits", [edit_row])
        try:
            mongo_tools.db()["training.edits"].update_one(
                {"edit_id": edit_id}, {"$set": edit_row}, upsert=True)
        except Exception as e:  # noqa: BLE001 — telemetry mirror is best-effort
            log.warning("training.edits mongo mirror failed for %s: %s", tid, e)

    if decision == "reject":
        rejection_category = classification.get("rejection_category") or "tone"
        mongo_tools.insert_many("negative_examples", [{
            "_id": f"neg_{tid}",
            "ts": datetime.now(UTC),
            "draft_text": p.get("original_draft", ""),
            "rejection_reason": p.get("rejection_reason"),
            "rejection_category": rejection_category,
            "rejected_by": p.get("decided_by"),
            "channel": p.get("channel"),  # carried through from the Sheet row
        }])

    # Write the approval doc through update_with_history so the
    # history.approvals collection captures the pre-image of every decision
    # flip (e.g., approve → reject correction). update_with_history doesn't
    # upsert on its own, so for the first write we fall back to a plain
    # upsert; subsequent edits get historized.
    approval_filter = {"telemetry_id": tid}
    approval_doc = {
        "telemetry_id": tid,
        "decision": decision,
        # approved_text + original_draft must be on the approvals row: the
        # PRD-03 voice miner reads approvals.approved_text (vs actions.raw for
        # the before-text). Without these the cloud-handler path starved the
        # voice miner while the durable web_api path fed it — now both write
        # the same fields.
        "original_draft": p.get("original_draft", ""),
        "approved_text": p.get("approved_text", ""),
        "rejection_reason": p.get("rejection_reason"),
        "decided_by": p.get("decided_by"),
        "decided_at": datetime.now(UTC),
        "channel": p.get("channel"),
        "edit_categories": classification.get("edit_categories", []),
    }
    existing_approval = mongo_tools.find_one("approvals", approval_filter)
    if existing_approval:
        update_with_history(
            "approvals",
            approval_filter,
            {"$set": approval_doc},
            actor_id=p.get("decided_by") or "founder",
            change_kind=f"decision_{decision}",
        )
    else:
        mongo_tools.upsert("approvals", approval_filter, approval_doc)

    # Seed an attribution stub on approve so outcome_attach can find the row
    # by telemetry_id regardless of channel. For Substack the publisher fills
    # in the external_id (post_id); for LinkedIn/email the field stays empty
    # until those publishers are wired — but the row exists, so the lookup
    # path doesn't bail out at None.
    if decision in ("approve", "edit"):
        mongo_tools.upsert(
            "attribution_map",
            {"telemetry_id": tid},
            {
                "telemetry_id": tid,
                "channel": p.get("channel"),
                "approved_at": datetime.now(UTC),
                "external_id": None,
                "external_url": None,
            },
        )

    # NOTE: Publishing is owned by web-api's /api/decisions for EVERY channel
    # (Dev.to/blog, Substack, LinkedIn, ads) — it calls _try_publish_for_channel
    # after this handler returns. This handler used to also trigger Substack
    # here, which (now that web-api publishes Substack too) would double-fire
    # the publisher. The publisher is idempotent, but we drop the trigger to
    # keep a single publish owner. The 15-min substack_publish_sweep cron is
    # still the safety net for anything that slips through.
    return jsonify({
        "ok": True,
        "classification": classification,
    }), 200


# ---------------------------------------------------------------------------
# Downstream publish triggers — fire on approve
# ---------------------------------------------------------------------------

def _trigger_substack_publish(telemetry_id: str) -> dict:
    """POST to the Substack publisher Cloud Run service."""
    import httpx
    from google.cloud import secretmanager

    sm = secretmanager.SecretManagerServiceClient()
    try:
        name = f"projects/{PROJECT_ID}/secrets/substack_publisher_url/versions/latest"
        url = sm.access_secret_version(name=name).payload.data.decode()
    except Exception:
        log.warning("substack_publisher_url secret not set; relying on sweep cron")
        return {"triggered": False, "reason": "publisher_url_unset"}

    try:
        r = httpx.post(f"{url}/publish", json={"telemetry_id": telemetry_id}, timeout=30)
        return {
            "triggered": True,
            "status_code": r.status_code,
            "response": (r.json() if r.headers.get("content-type", "").startswith("application/json") else None),
        }
    except Exception as e:
        log.warning("Substack publish trigger failed for %s: %s", telemetry_id, e)
        return {"triggered": False, "error": str(e)}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
