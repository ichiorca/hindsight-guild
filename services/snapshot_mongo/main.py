"""Hourly Mongo snapshot to GCS.

M0 doesn't include Atlas managed backups, so we dump every state.* collection
as JSON to gs://${PROJECT_ID}-snapshots/mongo/<yyyymmdd-HH>/. Retains 30
hourly snapshots + 7 daily snapshots (Sunday-aligned).

When you upgrade Atlas to M10+, continuous backup + PITR becomes the primary
recovery mechanism and this job is the redundant safety net.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime, timedelta

from bson import json_util
from google.cloud import storage

from shared import mongo_tools

mongo_tools.use_secret("mongo_uri_writer")

PROJECT_ID = os.environ["PROJECT_ID"]
BUCKET_NAME = os.environ.get("SNAPSHOT_BUCKET", f"{PROJECT_ID}-snapshots")

# Only dump state.* and the legacy collections we haven't migrated yet.
COLLECTIONS_TO_SNAPSHOT = [
    "experiments", "skills", "approvals",
    "negative_examples", "customer_voice", "messaging_library",
    "attribution_map", "email_sequences", "paid_variants",
    "ops_incidents", "ops_targets", "positioning_proposals",
]

log = logging.getLogger(__name__)


def main():
    gcs = storage.Client(project=PROJECT_ID)
    bucket = gcs.bucket(BUCKET_NAME)
    if not bucket.exists():
        bucket.create(location=os.environ.get("REGION", "us-central1"))

    now = datetime.now(UTC)
    stamp = now.strftime("%Y%m%d-%H%M")
    prefix = f"mongo/{stamp}"

    db = mongo_tools.db()
    summary: dict[str, int] = {}

    for coll in COLLECTIONS_TO_SNAPSHOT:
        try:
            docs = list(db[coll].find({}))
            payload = json.dumps(docs, default=json_util.default, indent=2)
            blob = bucket.blob(f"{prefix}/{coll}.json")
            blob.upload_from_string(payload, content_type="application/json")
            summary[coll] = len(docs)
            log.info("snapshot %s: %d docs", coll, len(docs))
        except Exception as e:
            log.exception("snapshot failed for %s: %s", coll, e)
            summary[coll] = -1

    # Manifest
    manifest = {
        "snapshot_id": stamp,
        "taken_at": now.isoformat(),
        "collections": summary,
        "total_docs": sum(n for n in summary.values() if n >= 0),
    }
    bucket.blob(f"{prefix}/_manifest.json").upload_from_string(
        json.dumps(manifest, indent=2), content_type="application/json",
    )

    # Retention
    _prune_old_snapshots(bucket)


def _prune_old_snapshots(bucket: storage.Bucket):
    """Keep 30 most-recent hourly snapshots + 7 most-recent daily snapshots."""
    now = datetime.now(UTC)
    keep_hourly_after = now - timedelta(hours=30)
    keep_daily_after = now - timedelta(days=7)

    by_stamp: dict[str, list[str]] = {}
    for blob in bucket.list_blobs(prefix="mongo/"):
        stamp = blob.name.split("/")[1] if "/" in blob.name else None
        if not stamp:
            continue
        by_stamp.setdefault(stamp, []).append(blob.name)

    for stamp, blob_names in by_stamp.items():
        try:
            taken = datetime.strptime(stamp, "%Y%m%d-%H%M").replace(tzinfo=UTC)
        except ValueError:
            continue

        is_daily = stamp.endswith("-0000")  # midnight snapshots are "daily"
        if is_daily:
            keep = taken >= keep_daily_after
        else:
            keep = taken >= keep_hourly_after

        if not keep:
            for name in blob_names:
                try:
                    bucket.blob(name).delete()
                except Exception:
                    pass


if __name__ == "__main__":
    main()
