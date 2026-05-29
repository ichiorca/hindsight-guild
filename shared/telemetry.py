"""Telemetry record schema + emitter. Imported by every agent callback.

Dual-write: every action lands in BOTH BigQuery (production analytics
warehouse) AND Mongo (so local-dev runs without GCP creds are still
visible in the UI). Either path failing is non-fatal — telemetry must
NEVER block an agent run.

  - BigQuery write skipped when no client can be constructed (LOCAL_DEV).
  - Mongo write always attempted; the Queue endpoint falls back to it
    when BQ is unavailable.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from google.cloud import bigquery  # noqa: F401


def _bq_module():
    """Lazy import of google.cloud.bigquery — keeps local-dev callers from
    needing google-cloud-bigquery installed."""
    try:
        from google.cloud import bigquery
        return bigquery
    except Exception as e:
        log.debug("google.cloud.bigquery not importable: %s", e)
        return None

PROJECT_ID = os.environ.get("PROJECT_ID", "agentic-marketing-mvp")
BQ_DATASET = os.environ.get("BQ_DATASET", "telemetry")

_bq: bigquery.Client | None = None
_bq_init_attempted = False
log = logging.getLogger(__name__)


def _client():
    """Lazy BigQuery client. Returns None if GCP credentials or the
    google-cloud-bigquery package aren't available — local-dev callers
    fall through to the Mongo mirror that emit_action also writes."""
    global _bq, _bq_init_attempted
    if _bq is not None or _bq_init_attempted:
        return _bq
    _bq_init_attempted = True
    bq = _bq_module()
    if bq is None:
        return None
    try:
        _bq = bq.Client(project=PROJECT_ID)
    except Exception as e:
        log.info("BigQuery client unavailable (%s) — telemetry will write "
                  "to Mongo only.", e.__class__.__name__)
        _bq = None
    return _bq


class OutcomeSlot(BaseModel):
    slot_name: str
    metric: str
    source: str
    expected_by: datetime


class EvalScores(BaseModel):
    brand_voice: float | None = None
    claim_support: float | None = None
    claim_risk: float | None = None
    icp_relevance: float | None = None
    originality: float | None = None
    conversion_intent: float | None = None
    # Provenance — which judge produced these scores and when. Lets the
    # drift detector compare scores across judge versions and makes a
    # promotion decision reproducible from the data that justified it.
    # (Judges drift + the nightly harness re-grades in place, so a bare
    # score with no judge tag can't be trusted over time.)
    judge_model: str | None = None
    scored_at: datetime | None = None


class ModelArmorResult(BaseModel):
    decision: Literal["allow", "block"]
    categories: list[str] = Field(default_factory=list)
    threat_score: float | None = None


class EditSummary(BaseModel):
    """Populated asynchronously by the edit-capture handler, not at action emit time.

    Lives on the action row so dashboards can JOIN edits to draft scores cheaply.
    """
    diff_text: str | None = None
    categories: list[str] = Field(default_factory=list)
    edit_distance: int | None = None
    rejection_reason: str | None = None


class TelemetryRecord(BaseModel):
    telemetry_id: str = Field(default_factory=lambda: f"act_{uuid.uuid4().hex[:12]}")
    ts: datetime = Field(default_factory=lambda: datetime.now(UTC))
    agent: str
    # skill_id is the *playbook* (e.g., linkedin_post). skills_loaded is the
    # set of Agent Skills (e.g., house-style, copywriting) the agent loaded
    # via read_skill / read_skill_reference during this action. Together
    # they let every Skill — playbook or library — get credit/blame for the
    # rubric outcome, which is what makes Skill-level self-learning possible.
    skill_id: str
    skills_loaded: list[str] = Field(default_factory=list)
    skill_version: str
    action_type: str
    channel: str | None = None
    experiment_id: str | None = None
    variant_id: str | None = None
    prompt_file: str | None = None
    approval_id: str | None = None
    eval_scores: EvalScores | None = None
    edit_summary: EditSummary | None = None
    model_armor: ModelArmorResult | None = None
    trace_id: str | None = None
    raw: dict | None = None


def emit_action(record: TelemetryRecord, outcomes: list[OutcomeSlot] | None = None) -> str:
    """Dual-write: BigQuery (if available) + Mongo (always).

    Returns the telemetry_id even if both writes fail. Telemetry must never
    block the agent run.

    Mongo collection ``actions`` (and ``outcomes``) mirror the BQ shape so
    the web_api Queue endpoint can fall back to Mongo in LOCAL_DEV mode
    without changing the row schema.
    """
    # --- Mongo write (always tried first, since it's the local fallback) ---
    # The pipeline's sub-agents (research/content/image_brief/review) all
    # SHARE one telemetry_id by design, but each emits its own action row
    # with a different action_type. Don't key Mongo on telemetry_id — use
    # (telemetry_id, agent, action_type) as the dedup key so every
    # sub-agent's row survives. Mongo auto-generates the actual _id.
    try:
        from shared import mongo_tools  # lazy: avoids cycle on import
        doc = record.model_dump()
        # model_dump leaves datetimes native; BSON encodes them fine.
        mongo_tools.db()["actions"].update_one(
            {
                "telemetry_id": record.telemetry_id,
                "agent": record.agent,
                "action_type": record.action_type,
            },
            {"$set": doc},
            upsert=True,
        )
    except Exception as e:
        log.warning("mongo action write failed for %s/%s: %s",
                     record.telemetry_id, record.agent, e)

    if outcomes:
        try:
            from shared import mongo_tools
            outcome_docs = [{
                "telemetry_id": record.telemetry_id,
                "slot_name": s.slot_name,
                "metric": s.metric,
                "source": s.source,
                "expected_by": s.expected_by,
                "status": "pending",
            } for s in outcomes]
            mongo_tools.db()["outcomes"].insert_many(outcome_docs)
        except Exception as e:
            log.warning("mongo outcomes write failed for %s: %s",
                         record.telemetry_id, e)

    # --- BigQuery write (production analytics) — best-effort -----------------
    bq = _client()
    if bq is not None:
        actions_table = f"{PROJECT_ID}.{BQ_DATASET}.actions"
        try:
            row = json.loads(record.model_dump_json())
            errors = bq.insert_rows_json(actions_table, [row])
            if errors:
                log.warning("BQ insert failed for telemetry.actions: %s", errors)
        except Exception as e:
            log.warning("BQ insert raised for telemetry.actions: %s", e)

        if outcomes:
            outcomes_table = f"{PROJECT_ID}.{BQ_DATASET}.outcomes"
            try:
                outcome_rows = [{
                    "telemetry_id": record.telemetry_id,
                    "slot_name": s.slot_name,
                    "metric": s.metric,
                    "source": s.source,
                    "expected_by": s.expected_by.isoformat(),
                    "status": "pending",
                } for s in outcomes]
                errors = bq.insert_rows_json(outcomes_table, outcome_rows)
                if errors:
                    log.warning("BQ insert failed for telemetry.outcomes: %s", errors)
            except Exception as e:
                log.warning("BQ insert raised for telemetry.outcomes: %s", e)

    return record.telemetry_id


def prompt_hash(prompt: str) -> str:
    return f"sha256:{hashlib.sha256(prompt.encode()).hexdigest()[:16]}"
