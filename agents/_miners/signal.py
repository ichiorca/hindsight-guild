"""Signal miner — PRD-03 §6.5 (depends on PRD-02).

Reads ``signals`` rows from the lookback window, joins to ``approvals``
via the ``triggered_telemetry_id`` back-reference written by the
drafting pipeline, and computes per-source precision (approve-rate).

Proposals:
  - Source with approve_rate >= 0.7 AND n >= 10 -> propose lowering
    its ``score_floor`` by 0.05 (catch more good signals).
  - Source with approve_rate <= 0.1 AND n >= 10 -> propose disabling
    the source entirely.

Both target ``signal_sources.{name}.self_critique_proposal`` so the
existing /api/self-critique unified accept/dismiss path applies them.

Gated on the ``signals`` collection existing AND having processed
rows; degrades to ``[]`` when PRD-02 isn't deployed.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import UTC, datetime, timedelta

log = logging.getLogger(__name__)

DEFAULT_LOOKBACK_DAYS = 14
MIN_SAMPLE = 10
PRECISION_LOWER = 0.7
PRECISION_KILL = 0.1
SCORE_FLOOR_DELTA = 0.05
DEFAULT_MAX_PROPOSALS = 3


def mine(db, *, lookback_days: int = DEFAULT_LOOKBACK_DAYS,
         max_proposals: int = DEFAULT_MAX_PROPOSALS) -> list[dict]:
    """Signal miner entry point. See module docstring."""
    try:
        cutoff = datetime.now(UTC) - timedelta(days=lookback_days)
        signals = list(db["signals"].find(
            {
                "ts": {"$gte": cutoff},
                "triggered_telemetry_id": {"$ne": None},
            },
            {"_id": 1, "source": 1, "icp_segment": 1,
             "triggered_telemetry_id": 1, "score": 1},
        ).limit(5000))
    except Exception as e:
        log.warning("signal miner signals query failed: %s", e)
        return []

    if not signals:
        return []

    # Group by (source kind, icp_segment) → list of (telemetry_id, score).
    tids: list[str] = []
    for s in signals:
        tid = s.get("triggered_telemetry_id")
        if tid:
            tids.append(tid)

    if not tids:
        return []

    # Fetch matching approvals in one batch.
    try:
        approvals = {
            a["telemetry_id"]: a.get("decision") for a in db["approvals"].find(
                {"telemetry_id": {"$in": tids}},
                {"telemetry_id": 1, "decision": 1},
            )
        }
    except Exception as e:
        log.warning("signal miner approvals batch failed: %s", e)
        approvals = {}

    # Resolve source kind → source_name. We need the signal_sources doc
    # to know the human-facing name AND the current score_floor.
    sources_by_kind_icp: dict[tuple[str, str], dict] = {}
    try:
        for src in db["signal_sources"].find({}):
            key = (src.get("source") or "", src.get("icp_segment") or "")
            sources_by_kind_icp[key] = src
    except Exception as e:
        log.warning("signal miner sources query failed: %s", e)

    # Per (source_name) bucket: approvals vs rejects. ``edits`` are tracked
    # separately as engagement (the founder kept the draft, just reworked it)
    # and deliberately excluded from the precision denominator — precision is
    # approves / (approves + rejects).
    by_source: dict[str, dict] = defaultdict(
        lambda: {"approves": 0, "rejects": 0, "edits": 0, "total": 0,
                 "doc": None, "evidence_excerpts": []})

    for s in signals:
        key = (s.get("source") or "", s.get("icp_segment") or "")
        src_doc = sources_by_kind_icp.get(key)
        if not src_doc:
            continue
        name = src_doc.get("name")
        if not name:
            continue
        bucket = by_source[name]
        bucket["doc"] = src_doc
        tid = s.get("triggered_telemetry_id")
        decision = approvals.get(tid)
        if decision == "approve":
            bucket["approves"] += 1
        elif decision == "reject":
            bucket["rejects"] += 1
        elif decision == "edit":
            bucket["edits"] += 1
        bucket["total"] += 1

    proposals: list[dict] = []
    for name, b in by_source.items():
        decided = b["approves"] + b["rejects"]
        if decided < MIN_SAMPLE:
            continue
        precision = b["approves"] / decided if decided else 0.0
        src_doc = b["doc"]
        current_floor = float(src_doc.get("score_floor") or 0.5)

        if precision >= PRECISION_LOWER:
            new_floor = max(0.0, round(current_floor - SCORE_FLOOR_DELTA, 2))
            if abs(new_floor - current_floor) < 1e-9:
                continue
            proposals.append({
                "kind": "signal",
                "target_kind": "signal_source",
                "target_id": name,
                "issue": (
                    f"Source {name!r} has approve-rate "
                    f"{precision:.0%} over {decided} decided signals "
                    f"in the last {lookback_days}d. Lower the floor to "
                    f"catch more high-precision matches."
                ),
                "proposed_change": (
                    f"Set signal_sources.{name}.score_floor: "
                    f"{current_floor:.2f} → {new_floor:.2f}"
                ),
                "confidence": "high" if decided >= 20 else "medium",
                "evidence_count": decided,
                "evidence": {
                    "source_name": name,
                    "approves": b["approves"],
                    "rejects": b["rejects"],
                    "approve_rate": round(precision, 3),
                    "current_score_floor": current_floor,
                    "proposed_score_floor": new_floor,
                },
            })

        elif precision <= PRECISION_KILL:
            # Disable the source — almost nothing matched.
            proposals.append({
                "kind": "signal",
                "target_kind": "signal_source",
                "target_id": name,
                "issue": (
                    f"Source {name!r} has approve-rate "
                    f"{precision:.0%} over {decided} decided signals "
                    f"in the last {lookback_days}d. Approves vs rejects "
                    f"{b['approves']}/{b['rejects']}. Almost nothing "
                    f"lands; disable to stop polluting the queue."
                ),
                "proposed_change": (
                    f"Set signal_sources.{name}.enabled: true → false.\n"
                    f"The watcher will skip this source on the next tick. "
                    f"Re-enable later if the ICP keywords change."
                ),
                "confidence": "high",
                "evidence_count": decided,
                "evidence": {
                    "source_name": name,
                    "approves": b["approves"],
                    "rejects": b["rejects"],
                    "approve_rate": round(precision, 3),
                    "proposed_enabled": False,
                },
            })

        if len(proposals) >= max_proposals:
            break

    return proposals[:max_proposals]
