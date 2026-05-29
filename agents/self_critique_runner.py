"""PRD-03 self-critique runner.

Pure-Python orchestrator that runs all configured miners on a nightly
tick. Distinct from ``agents/self_critique.py`` (the existing weekly
LlmAgent) — the runner is deterministic + mechanical; the LlmAgent is
LLM-reasoning over the same telemetry.

Pipeline:
  1. ``self_critique_runs`` row inserted with ``started_at``.
  2. For each miner: ``mine(db)`` -> list of proposals.
  3. For each proposal: persist to the right destination (see
     ``_persist_proposal``).
  4. ``self_critique_runs`` row updated with per-miner counters +
     ``completed_at``.

Persistence destinations (per proposal['target_kind']):
  - ``"skill"`` (voice / negative-claim_risk / aeo) -> upsert into
    ``skills.{id}.self_critique_proposal``. Same slot the existing
    /api/self-critique router reads.
  - ``"negative_example"`` (negative miner default) -> insert into the
    ``negative_examples`` collection so Review Agent's grounding pulls
    it on the next draft. ALSO write a skill-level proposal onto the
    review-rule skill so the founder sees it in Weekly Review.
  - ``"paid_action"`` (paid miner) -> insert into
    ``paid_actions_proposed`` collection.
  - ``"signal_source"`` (signal miner) -> proposal lands as a skill-style
    row inside ``signal_sources.{name}.self_critique_proposal`` (a new
    slot on signal_sources docs — same shape as skills).

Kill switches:
  - ``SELF_CRITIQUE_DISABLED=1`` skips the whole tick.
  - ``SELF_CRITIQUE_DISABLED_MINERS=paid,signal`` skips named miners.

Returns:
    Same shape as ``self_critique_runs._id`` row content — used by the
    `/api/self-critique/runs` endpoint and the live ticker.
"""
from __future__ import annotations

import importlib
import logging
import os
from datetime import UTC, datetime, timedelta

from agents._schema_constants import Status
from shared import mongo_tools

log = logging.getLogger(__name__)

# How long a founder's accept/dismiss on a given pattern suppresses the
# miner from re-surfacing the same proposal. Without this, a dismissed
# proposal recurs on the very next tick (the dismissal never sticks).
DECISION_COOLDOWN_DAYS = 14
_DECIDED_STATUSES = ("dismissed", Status.ACCEPTED, Status.REJECTED_BY_GATE)

# Miners registered for v1. The runner imports them lazily so a broken
# miner module (e.g. PRD-01's aeo_audits collection missing) doesn't
# block the whole tick.
DEFAULT_MINERS = ("voice", "negative", "paid", "aeo", "signal")


def _kill_switch() -> bool:
    return os.environ.get("SELF_CRITIQUE_DISABLED", "").lower() in ("1", "true", "yes")


def _disabled_miners() -> set[str]:
    raw = os.environ.get("SELF_CRITIQUE_DISABLED_MINERS", "")
    return {m.strip() for m in raw.split(",") if m.strip()}


def run_once(db=None, *, miners: tuple[str, ...] = DEFAULT_MINERS,
             lookback_days: int = 14) -> dict:
    """Single nightly tick. Returns the self_critique_runs row content."""
    if _kill_switch():
        return {"status": "disabled", "miners": {}, "total_proposals": 0}

    db = db if db is not None else mongo_tools.db()
    disabled = _disabled_miners()
    started_at = datetime.now(UTC)
    run_doc = {
        "started_at": started_at,
        "completed_at": None,
        "miners": {},
        "lookback_days": lookback_days,
    }
    try:
        run_id = db["self_critique_runs"].insert_one(run_doc).inserted_id
    except Exception as e:
        log.warning("self_critique_runs initial insert failed: %s", e)
        run_id = None

    total = 0
    for name in miners:
        # ``proposals`` counts SURVIVING proposals (newly persisted or an
        # existing pending entry refreshed) — not raw persist attempts.
        # ``skipped`` covers proposals suppressed by a recent founder
        # decision or dropped for a missing target.
        miner_result = {"proposals": 0, "skipped": 0,
                         "evidence_scanned": 0, "errors": [], "disabled": False}
        if name in disabled:
            miner_result["disabled"] = True
            run_doc["miners"][name] = miner_result
            continue
        try:
            mod = importlib.import_module(f"agents._miners.{name}")
        except ImportError as e:
            # Miner module not present (e.g., aeo/signal in M5 might not
            # exist yet during partial rollout). Record + continue.
            miner_result["errors"].append(f"import: {e}")
            run_doc["miners"][name] = miner_result
            continue

        try:
            proposals = mod.mine(db, lookback_days=lookback_days) or []
        except Exception as e:
            log.warning("miner %s failed: %s", name, e)
            miner_result["errors"].append(f"mine: {e!s}"[:200])
            run_doc["miners"][name] = miner_result
            continue

        for p in proposals:
            try:
                outcome = _persist_proposal(db, p, run_id=run_id, miner=name)
            except Exception as e:
                miner_result["errors"].append(f"persist: {e!s}"[:200])
                continue
            if outcome in ("persisted", "updated"):
                miner_result["proposals"] += 1
                total += 1
            else:  # skipped_recent_decision / skipped_no_target
                miner_result["skipped"] += 1

        run_doc["miners"][name] = miner_result

    run_doc["completed_at"] = datetime.now(UTC)
    run_doc["total_proposals"] = total
    run_doc["status"] = "ok"

    if run_id is not None:
        try:
            db["self_critique_runs"].update_one(
                {"_id": run_id},
                {"$set": {k: v for k, v in run_doc.items()
                          if k != "started_at"}},
            )
        except Exception as e:
            log.warning("self_critique_runs update failed: %s", e)

    # pymongo's ``insert_one`` mutates run_doc with an ObjectId ``_id``.
    # Stringify so FastAPI's Pydantic serializer doesn't trip on it.
    out = {**run_doc}
    out["_id"] = str(run_id) if run_id else None
    return out


# ---------------------------------------------------------------------------
# Proposal persistence — one path per target_kind.
# ---------------------------------------------------------------------------

def _proposal_signature(p: dict, *, miner: str) -> str:
    """Stable identity for a proposal so a re-run of the same finding maps
    to the same slot/row — which is what lets a dismissal suppress the
    recurrence and lets us tell "same proposal again" from "a different
    proposal collided on this skill's slot"."""
    ev = p.get("evidence") or {}
    disc = (ev.get("ngram") or ev.get("phrase") or ev.get("category")
            or ev.get("rewrite_kind") or ev.get("source_name")
            or ev.get("action_kind") or p.get("kind") or "")
    return f"{miner}:{p.get('target_kind')}:{p.get('target_id')}:{disc}"


def _proposal_envelope(p: dict, *, miner: str, run_id) -> dict:
    """Common envelope for self_critique_proposal slots — matches the
    shape already read by ``mongo/queries.skills_with_self_critique_proposal``
    and the /api/self-critique router."""
    now = datetime.now(UTC)
    return {
        "miner": miner,
        "run_id": str(run_id) if run_id else None,
        "kind": p["kind"],
        "issue": p["issue"],
        "proposed_change": p["proposed_change"],
        "confidence": p.get("confidence", "medium"),
        "evidence_count": p.get("evidence_count", 0),
        "evidence": p.get("evidence", {}),
        "signature": _proposal_signature(p, miner=miner),
        "proposed_at": now,
        "status": Status.AWAITING_HUMAN_REVIEW,
    }


_CONFIDENCE_RANK = {"high": 3, "medium": 2, "low": 1}


def _recently_decided(slot: dict | None, signature: str) -> bool:
    """True when ``slot`` holds the SAME proposal the founder already
    accepted/dismissed within the cooldown — i.e. we must not re-surface it."""
    if not slot or slot.get("signature") != signature:
        return False
    if slot.get("status") not in _DECIDED_STATUSES:
        return False
    decided = slot.get("decided_at") or slot.get("gated_at")
    if not isinstance(decided, datetime):
        # Decided but undated — treat as still in cooldown (conservative:
        # honor the decision rather than re-nag).
        return True
    cutoff = datetime.now(UTC) - timedelta(days=DECISION_COOLDOWN_DAYS)
    decided_cmp = decided if decided.tzinfo else decided.replace(tzinfo=UTC)
    return decided_cmp >= cutoff


def load_proposals(doc: dict) -> list[dict]:
    """Canonical proposal list for a skills/signal_sources doc.

    ``self_critique_proposals`` (array) is the source of truth. For docs that
    predate the array we fall back to wrapping the legacy singleton
    ``self_critique_proposal`` so old data + every legacy reader keep working
    during/after migration."""
    arr = doc.get("self_critique_proposals")
    if isinstance(arr, list):
        return list(arr)
    single = doc.get("self_critique_proposal")
    return [single] if single else []


def primary_pending(proposals: list[dict]) -> dict | None:
    """The proposal that should occupy the legacy singleton MIRROR — the
    highest-confidence pending one, newest as the tiebreak. ``None`` when
    nothing is pending."""
    pending = [p for p in proposals
               if p and p.get("status") == Status.AWAITING_HUMAN_REVIEW]
    if not pending:
        return None
    return max(pending, key=lambda p: (
        _CONFIDENCE_RANK.get(p.get("confidence"), 2),
        str(p.get("proposed_at") or ""),
    ))


def _mirror_update(proposals: list[dict]) -> dict:
    """Build the $set/$unset that writes the array AND refreshes the singleton
    mirror so legacy readers (weekly_review, promotion_gate, badge, agents
    inbox) keep seeing the primary pending proposal with zero changes."""
    mirror = primary_pending(proposals)
    update: dict = {"$set": {"self_critique_proposals": proposals}}
    if mirror is not None:
        update["$set"]["self_critique_proposal"] = mirror
    else:
        update["$unset"] = {"self_critique_proposal": ""}
    return update


def _persist_slot_proposal(db, collection: str, key: dict, envelope: dict) -> str:
    """Persist a proposal into the ``self_critique_proposals`` ARRAY on a doc
    (skills / signal_sources), keeping the singleton mirror in sync. Multiple
    proposals (different signatures) now COEXIST instead of clobbering. Returns:

      - ``"skipped_no_target"``       doc doesn't exist (no upsert).
      - ``"skipped_recent_decision"`` same proposal was recently decided
                                       (within cooldown) — don't re-surface.
      - ``"updated"``                 same-signature pending entry refreshed.
      - ``"persisted"``               new entry appended (or a cooled-down
                                       decided entry re-opened).
    """
    doc = db[collection].find_one(
        key, {"self_critique_proposals": 1, "self_critique_proposal": 1})
    if doc is None:
        log.warning("self_critique_runner: %s %s has no doc; proposal dropped",
                    collection, key)
        return "skipped_no_target"

    proposals = load_proposals(doc)
    sig = envelope["signature"]
    existing = next((p for p in proposals if p.get("signature") == sig), None)

    if existing is not None:
        if _recently_decided(existing, sig):
            return "skipped_recent_decision"
        idx = proposals.index(existing)
        # Pending → refresh; decided-but-cooled-down → re-open as pending.
        outcome = ("updated"
                   if existing.get("status") == Status.AWAITING_HUMAN_REVIEW
                   else "persisted")
        proposals[idx] = envelope
    else:
        proposals.append(envelope)
        outcome = "persisted"

    db[collection].update_one(key, _mirror_update(proposals))
    return outcome


def _persist_proposal(db, p: dict, *, run_id, miner: str) -> str:
    """Route a proposal to its destination collection / field. Returns an
    outcome string the runner uses to count survivors vs skips/deferrals."""
    target_kind = p.get("target_kind")
    target_id = p.get("target_id")
    envelope = _proposal_envelope(p, miner=miner, run_id=run_id)

    if target_kind == "skill":
        if not target_id:
            return "skipped_no_target"
        return _persist_slot_proposal(db, "skills", {"_id": target_id}, envelope)

    if target_kind == "negative_example":
        # Insert into the negative_examples collection directly — Review
        # Agent's grounding picks it up on the next draft. Dedupe on
        # (category, phrase, source) so nightly re-runs don't pile up
        # duplicate rows for the same recurring pattern.
        evidence = p.get("evidence", {})
        phrase = evidence.get("phrase") or ""
        category = evidence.get("category") or target_id or "other"
        source = f"self_critique:{miner}"
        existing = db["negative_examples"].find_one({
            "rejection_category": category,
            "rejected_phrase": phrase,
            "source": source,
        })
        if existing is not None:
            return "skipped_recent_decision"
        db["negative_examples"].insert_one({
            "channel":            (evidence.get("channels") or ["unknown"])[0],
            "rejection_category": category,
            "rejected_phrase":    phrase,
            "reason":             p.get("issue", ""),
            "ts":                 datetime.now(UTC),
            "tags":               [],
            "source":             source,
            "run_id":             str(run_id) if run_id else None,
        })
        return "persisted"

    if target_kind == "paid_action":
        evidence = p.get("evidence", {})
        kind = evidence.get("action_kind") or "pause"
        coll = db["paid_actions_proposed"]

        # 1. Existing PENDING proposal for this (variant, kind) → refresh it
        #    in place (dedupe: don't pile up duplicate pending rows).
        pending = coll.find_one({
            "variant_id": target_id, "kind": kind,
            "status": Status.AWAITING_HUMAN_REVIEW,
        })
        if pending is not None:
            coll.update_one({"_id": pending["_id"]}, {"$set": {
                **envelope,
                "variant_id": target_id,
                "platform":   evidence.get("platform"),
                "external_id": evidence.get("external_id"),
                "rationale":  p.get("issue", ""),
                "kind":       kind,
            }})
            return "updated"

        # 2. Founder recently decided this exact (variant, kind) → honor it.
        #    Without this a dismissed pause re-appears every tick the variant
        #    still breaches stop-loss.
        cutoff = datetime.now(UTC) - timedelta(days=DECISION_COOLDOWN_DAYS)
        recent_decided = coll.find_one({
            "variant_id": target_id, "kind": kind,
            "status": {"$in": list(_DECIDED_STATUSES)},
            "decided_at": {"$gte": cutoff},
        })
        if recent_decided is not None:
            return "skipped_recent_decision"

        # 3. New proposal.
        coll.insert_one({
            **envelope,
            "variant_id": target_id,
            "platform":   evidence.get("platform"),
            "external_id": evidence.get("external_id"),
            "rationale":  p.get("issue", ""),
            "kind":       kind,
        })
        return "persisted"

    if target_kind == "signal_source":
        if not target_id:
            return "skipped_no_target"
        return _persist_slot_proposal(
            db, "signal_sources", {"name": target_id}, envelope)

    # Unknown target_kind — record but don't crash.
    log.warning("self_critique_runner: unknown target_kind %r on proposal %r",
                target_kind, p.get("issue", "")[:80])
    return "skipped_no_target"
