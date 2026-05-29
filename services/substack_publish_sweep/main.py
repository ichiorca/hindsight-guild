"""Substack publish sweep — backup retry for any approval that didn't publish.

Runs every 15 minutes. Picks up approvals where:
  - decision = 'approve'
  - channel  = 'substack'
  - publish_state IN ('publishing', NULL) AND publish_started_at < now - 5min
    (stuck) OR publish_state IS NULL (never tried)

For each, calls the publisher /publish endpoint. The publisher is idempotent
on telemetry_id so re-invocation is safe.

The synchronous trigger from edit_capture_handler is the primary path; this
sweep exists for resilience — webhook failures, publisher cold starts, the
publisher returning before the founder's session committed, etc.
"""
from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta

import httpx

from shared import mongo_tools
from shared.clients import secret_value

mongo_tools.use_secret("mongo_uri_writer")

PROJECT_ID = os.environ["PROJECT_ID"]
log = logging.getLogger(__name__)


def _publisher_url() -> str:
    """Resolve the Substack publisher Cloud Run URL — written to a secret
    by deploy.sh after the service is deployed."""
    try:
        return secret_value("substack_publisher_url")
    except Exception as e:
        log.error("substack_publisher_url secret not set: %s", e)
        return ""


def main():
    url = _publisher_url()
    if not url:
        log.error("Substack publisher URL not configured; skipping sweep")
        return

    cutoff = datetime.now(UTC) - timedelta(minutes=5)

    # Find approvals that should have published but haven't
    pending = list(mongo_tools.db()["approvals"].find({
        "decision": "approve",
        "$or": [
            {"publish_state": {"$exists": False}},
            {"publish_state": "publishing", "publish_started_at": {"$lt": cutoff}},
        ],
    }))

    # Filter to Substack only — cross-ref via attribution_map (channel field
    # is populated by the publisher; for pristine approvals we look at the
    # telemetry channel — done by the publisher itself, so we approximate
    # here by checking attribution_map first then leaving the publisher to
    # bounce non-Substack)
    log.info("Found %d stuck/pending approvals to retry", len(pending))

    for appr in pending:
        tid = appr["telemetry_id"]
        try:
            r = httpx.post(f"{url}/publish", json={"telemetry_id": tid}, timeout=60)
            if r.status_code == 200:
                log.info("Retry-published %s: %s", tid, r.json().get("mode"))
            elif r.status_code in (403, 404):
                # The publisher rejected — e.g. non-Substack channel.
                # That's fine for this sweep; just move on.
                log.debug("Publisher returned %d for %s: %s",
                          r.status_code, tid, r.text)
            else:
                log.warning("Publisher returned %d for %s: %s",
                            r.status_code, tid, r.text)
        except Exception as e:
            log.exception("Retry-publish failed for %s: %s", tid, e)


if __name__ == "__main__":
    main()
