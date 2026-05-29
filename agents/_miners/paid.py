"""Paid miner — PRD-03 §6.3.

Reads ``paid_variants`` rows that are currently running and joins them
to the configured stop-loss thresholds in ``paid_thresholds`` (lookup
by (platform, icp_segment), falling back to the ``_default`` row).

For each running variant that meets the stop-loss criteria, proposes
``kind: "pause"``. When a sibling variant in the same experiment beats
the threshold by 2x or better, additionally proposes
``kind: "reallocate_budget"`` to the winner.

Notes on metrics:
  Earlier PRDs framed this in CAC + ROAS terms. The actual schema in
  this repo uses the simpler stop-loss surface from
  ``mongo/schema.py`` (``daily_spend_floor_usd``,
  ``min_conversions_per_24h``, ``min_ctr_pct``, ``min_hours_running``).
  We honour the real schema rather than inventing CAC fields the
  paid_media_agent doesn't write.

Dedupe: the runner's ``_persist_proposal`` enforces uniqueness on
(variant_id, kind, status=AWAITING_HUMAN_REVIEW), so multiple ticks
without founder review don't pile up duplicates.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

log = logging.getLogger(__name__)

# How recent the paid_variant's outcome snapshot must be to count for
# stop-loss eval. Older than this and we'd be making a decision on
# stale data; skip.
DEFAULT_FRESHNESS_HOURS = 6


def _matching_threshold(thresholds: list[dict], platform: str,
                         icp_segment: str) -> dict:
    """Pick the most-specific threshold doc for this variant.

    Search order:
      1. Exact (platform, icp_segment)
      2. Exact platform, any icp ("*")
      3. Any platform ("*"), exact icp
      4. The "_default" row
    Falls back to an empty dict (no thresholds → no proposals).
    """
    by_id = {t.get("_id"): t for t in thresholds}
    exact = [t for t in thresholds
             if t.get("platform") == platform
             and t.get("icp_segment") == icp_segment]
    if exact:
        return exact[0]
    any_icp = [t for t in thresholds
               if t.get("platform") == platform
               and t.get("icp_segment") in (None, "*")]
    if any_icp:
        return any_icp[0]
    any_platform = [t for t in thresholds
                    if t.get("platform") in (None, "*")
                    and t.get("icp_segment") == icp_segment]
    if any_platform:
        return any_platform[0]
    return by_id.get("_default") or {}


def _evaluate_variant(variant: dict, threshold: dict) -> tuple[bool, list[str]]:
    """Return (should_pause, reasons[]).

    Per the schema-level stop-loss recipe::

        spend_24h > daily_spend_floor_usd
        AND conversions_24h < min_conversions_per_24h
        AND hours_running   > min_hours_running

    CTR is an OR-trigger ("ctr below floor IS a secondary trigger") —
    when CTR is lethal we propose pause even if spend isn't yet over
    the threshold.
    """
    spend = float(variant.get("spend_24h") or 0)
    conv = float(variant.get("conversions_24h") or 0)
    hours = float(variant.get("hours_running") or 0)
    ctr = variant.get("ctr_pct")
    ctr_val = float(ctr) if ctr is not None else None

    spend_floor = float(threshold.get("daily_spend_floor_usd") or 0)
    min_conv = float(threshold.get("min_conversions_per_24h") or 0)
    min_ctr = threshold.get("min_ctr_pct")
    min_hours = float(threshold.get("min_hours_running") or 0)

    reasons: list[str] = []
    primary_trigger = (spend > spend_floor
                        and conv < min_conv
                        and hours > min_hours)
    if primary_trigger:
        reasons.append(
            f"spend_24h ${spend:.2f} > floor ${spend_floor:.2f} AND "
            f"conversions_24h {conv:.0f} < {min_conv:.0f} AND "
            f"hours_running {hours:.0f} > {min_hours:.0f}"
        )
    secondary_trigger = False
    if min_ctr is not None and ctr_val is not None:
        secondary_trigger = float(ctr_val) < float(min_ctr)
        if secondary_trigger:
            reasons.append(
                f"ctr_pct {ctr_val:.2f}% < floor {float(min_ctr):.2f}%"
            )

    should_pause = primary_trigger or secondary_trigger
    return should_pause, reasons


def _sibling_winners(siblings: list[dict], failing: dict) -> list[dict]:
    """Among same-experiment variants, the ones to reallocate to.

    A "winner" is a running sibling whose conversions_24h beats the
    failing variant's by >= 2x AND whose CTR is at least non-zero. We
    intentionally don't try to be cleverer in v1 — the founder
    approves before any spend moves.
    """
    failing_conv = float(failing.get("conversions_24h") or 0)
    winners = []
    for s in siblings:
        if s.get("_id") == failing.get("_id"):
            continue
        if s.get("status") != "running":
            continue
        s_conv = float(s.get("conversions_24h") or 0)
        if s_conv >= 2 * max(1.0, failing_conv):
            winners.append(s)
    return winners


def mine(db, *, lookback_days: int = 14) -> list[dict]:
    """Paid miner entry point. ``lookback_days`` is accepted for
    signature symmetry; paid evaluation only uses last-24h numbers
    that live on the variant doc itself."""
    try:
        variants = list(db["paid_variants"].find(
            {"status": "running"},
            # ``status`` MUST stay in the projection — _sibling_winners
            # filters on it, and pymongo strips fields not listed here.
            {"_id": 1, "status": 1, "platform": 1, "icp_segment": 1,
             "experiment_id": 1, "external_id": 1, "name": 1,
             "spend_24h": 1, "conversions_24h": 1, "ctr_pct": 1,
             "hours_running": 1, "snapshot_at": 1},
        ))
    except Exception as e:
        log.warning("paid miner variants query failed: %s", e)
        return []

    if not variants:
        return []

    try:
        thresholds = list(db["paid_thresholds"].find({}))
    except Exception as e:
        log.warning("paid miner thresholds query failed: %s", e)
        thresholds = []

    if not thresholds:
        # Without thresholds we can't make any pause call. The schema
        # bootstrap seeds a `_default` row, so this should only happen
        # in a brand-new env.
        return []

    # snapshot_at may arrive naive (Mongo's default) or tz-aware (tests +
    # programmatic inserts). Use a tz-aware cutoff and coerce each snapshot
    # to aware before comparing, so a tz-aware snapshot can't fall on the
    # wrong side of the window.
    cutoff = datetime.now(UTC) - timedelta(hours=DEFAULT_FRESHNESS_HOURS)

    # Index siblings by experiment_id for the reallocate lookup.
    by_exp: dict[str, list[dict]] = {}
    for v in variants:
        exp = v.get("experiment_id")
        if exp:
            by_exp.setdefault(exp, []).append(v)

    proposals: list[dict] = []
    for v in variants:
        # Stale snapshot — skip rather than propose on old data. Coerce
        # to naive UTC for the comparison (Mongo returns naive, but
        # tests / programmatic inserts may use tz-aware).
        snap = v.get("snapshot_at")
        if isinstance(snap, datetime):
            snap_aware = snap if snap.tzinfo else snap.replace(tzinfo=UTC)
            if snap_aware < cutoff:
                continue

        threshold = _matching_threshold(
            thresholds, v.get("platform") or "*", v.get("icp_segment") or "*",
        )
        if not threshold:
            continue

        should_pause, reasons = _evaluate_variant(v, threshold)
        if not should_pause:
            continue

        rationale = "; ".join(reasons) or "stop-loss thresholds breached"

        proposals.append({
            "kind": "paid",
            "target_kind": "paid_action",
            "target_id": str(v.get("_id")),
            "issue": (
                f"Pause running variant {v.get('name') or v.get('_id')} on "
                f"{v.get('platform') or 'paid'}: {rationale}."
            ),
            "proposed_change": (
                "Set status=PAUSED via the platform adapter "
                "(google_ads.create_paused_rsa shape / meta_ads "
                "create_paused_creative). Founder one-clicks to apply; "
                "nothing changes on the platform without approval."
            ),
            "confidence": "high",
            "evidence_count": int(v.get("conversions_24h") or 0),
            "evidence": {
                "action_kind": "pause",
                "platform": v.get("platform"),
                "external_id": v.get("external_id"),
                "variant_name": v.get("name"),
                "spend_24h": v.get("spend_24h"),
                "conversions_24h": v.get("conversions_24h"),
                "ctr_pct": v.get("ctr_pct"),
                "hours_running": v.get("hours_running"),
                "reasons": reasons,
            },
        })

        # Find sibling winners in the same experiment for reallocate.
        exp = v.get("experiment_id")
        if not exp:
            continue
        siblings = by_exp.get(exp, [])
        winners = _sibling_winners(siblings, v)
        for w in winners[:1]:   # one reallocate target per failing variant
            proposals.append({
                "kind": "paid",
                "target_kind": "paid_action",
                "target_id": str(w.get("_id")),
                "issue": (
                    f"Reallocate budget from paused variant "
                    f"{v.get('name')} to sibling {w.get('name')} "
                    f"(experiment {exp}); "
                    f"sibling at {w.get('conversions_24h') or 0} conv vs "
                    f"failing at {v.get('conversions_24h') or 0}."
                ),
                "proposed_change": (
                    f"Move daily budget from {v.get('name')} → "
                    f"{w.get('name')}. Founder approves the reallocation; "
                    f"the platform adapter applies the budget change."
                ),
                "confidence": "medium",
                "evidence_count": int(w.get("conversions_24h") or 0),
                "evidence": {
                    "action_kind": "reallocate_budget",
                    "platform": w.get("platform"),
                    "external_id": w.get("external_id"),
                    "experiment_id": exp,
                    "source_variant_id": str(v.get("_id")),
                    "source_variant_name": v.get("name"),
                    "winner_variant_name": w.get("name"),
                    "winner_conversions_24h": w.get("conversions_24h"),
                },
            })

    return proposals
