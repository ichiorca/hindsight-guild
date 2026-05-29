"""PRD-03 self-critique — unified list/approve/dismiss + miner runs.

Existing endpoints (kept for backward compatibility with the existing
LlmAgent's per-skill flow):
  - ``GET  /api/self-critique``                — list skills with proposals
  - ``POST /api/self-critique/{skill_id}``     — accept/reject (skill-only)

PRD-03 unified endpoints (handle miner proposals across skill /
paid_actions_proposed / signal_sources):
  - ``GET  /api/self-critique/proposals``      — all pending across destinations
  - ``GET  /api/self-critique/runs``           — last N nightly miner runs
  - ``POST /api/self-critique/proposals/{id}/approve``
  - ``POST /api/self-critique/proposals/{id}/dismiss``
  - ``POST /api/self-critique/run-now``        — manual tick (ops escape hatch)

Proposal ID format on the unified endpoints::

    skill:{skill_id}            # skills.<id>.self_critique_proposal
    paid:{mongo_oid}            # paid_actions_proposed._id
    signal_source:{source_name} # signal_sources.<name>.self_critique_proposal
"""
import hashlib
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from agents._schema_constants import Status
from agents.self_critique_runner import load_proposals, primary_pending
from mongo import queries
from shared import mongo_tools

router = APIRouter()
log = logging.getLogger(__name__)


def _subid(signature: str) -> str:
    """Stable, URL-safe per-proposal sub-id derived from its signature. Used
    to address an individual entry in a doc's self_critique_proposals array
    via the unified id ``skill:{id}::{subid}`` / ``signal_source:{name}::{subid}``."""
    return hashlib.blake2b((signature or "").encode(), digest_size=8).hexdigest()


class CritiqueDecision(BaseModel):
    decision: str  # "accept" | "reject"


# ---------------------------------------------------------------------------
# Legacy GET — preserved so the existing weekly LlmAgent flow keeps
# working unchanged. (The legacy POST /{skill_id} is registered LAST so
# more-specific PRD-03 endpoints don't get swallowed by the catch-all.)
# ---------------------------------------------------------------------------

@router.get("/api/self-critique")
def list_self_critique():
    return queries.skills_with_self_critique_proposal()


def decide_critique(skill_id: str, body: CritiqueDecision):
    """Legacy by-skill_id decision (used by the legacy POST endpoint and any
    UI path without a sub-id). Operates on the PRIMARY pending proposal."""
    if body.decision == "accept":
        return _decide_skill(skill_id, None, accept=True)
    if body.decision == "reject":
        return _decide_skill(skill_id, None, accept=False)
    raise HTTPException(400, "decision must be accept or reject")


_VERSION_RE = re.compile(r"v(\d+)")


def _next_version_label(doc: dict) -> str:
    """Next ``vN`` label for a Mongo-versioned (agent_skill) doc — one past
    the highest numeric version across versions/history/candidates."""
    seen = set(doc.get("versions") or {})
    seen.update(doc.get("history") or [])
    seen.update(doc.get("candidates") or [])
    highest = 0
    for label in seen:
        m = _VERSION_RE.fullmatch(str(label))
        if m:
            highest = max(highest, int(m.group(1)))
    return f"v{highest + 1}"


def _select_entry(proposals: list[dict], subid: str | None) -> dict | None:
    """Pick the array entry a decision targets: by sub-id when given,
    otherwise the primary pending (the singleton-mirror equivalent)."""
    if subid:
        return next((p for p in proposals
                     if _subid(p.get("signature", "")) == subid), None)
    return primary_pending(proposals) or (proposals[0] if proposals else None)


def _mint_candidate(doc: dict, entry: dict):
    """Author a candidate version body from an accepted miner proposal.

    Returns (label, body, applied, reason). applied is False when the skill
    body isn't Mongo-editable (file-based playbook / missing body)."""
    versions = doc.get("versions") or {}
    current = doc.get("current_version")
    current_body = (versions.get(current) or {}).get("body_md") if current else None
    if doc.get("skill_kind") != "agent_skill" \
            or not isinstance(current_body, str) or not current_body:
        return None, None, False, (
            "skill body is not Mongo-editable (file-based playbook or missing "
            "version body); candidate authoring not automated for this kind")
    new_label = _next_version_label(doc)
    rule_block = (entry.get("proposed_change") or entry.get("issue") or "").strip()
    miner = entry.get("miner") or "self-critique"
    marker = "## Learned rules (self-critique)"
    addition = f"- ({miner}) {rule_block}"
    if marker in current_body:
        new_body = current_body.rstrip() + "\n" + addition + "\n"
    else:
        new_body = current_body.rstrip() + f"\n\n{marker}\n\n" + addition + "\n"
    return new_label, new_body, True, None


def _decide_skill(skill_id: str, subid: str | None, *, accept: bool) -> dict:
    """Accept/dismiss a single skill proposal (array entry), keeping the
    mirror in sync. Accept mints a candidate version so the artifact actually
    changes (A2); dismiss preserves the entry as an audit row."""
    db = mongo_tools.db()
    doc = mongo_tools.find_one("skills", {"_id": skill_id})
    if not doc:
        raise HTTPException(404, f"no skill {skill_id}")
    proposals = load_proposals(doc)
    entry = _select_entry(proposals, subid)
    if entry is None:
        raise HTTPException(404, f"no self_critique_proposal on {skill_id}")

    now = datetime.now(UTC)
    result: dict = {"ok": True}
    extra_set: dict = {}
    add_to_set: dict = {}

    if accept:
        candidate = entry.get("candidate_id")
        if candidate and not entry.get("applied"):
            # Legacy LlmAgent envelope already carrying a candidate id.
            entry["status"] = Status.ACCEPTED
            entry["decided_at"] = now
            add_to_set["candidates"] = candidate
            result["candidate_added"] = candidate
        else:
            label, new_body, applied, reason = _mint_candidate(doc, entry)
            entry["status"] = Status.ACCEPTED
            entry["decided_at"] = now
            entry["applied"] = applied
            if applied:
                entry["candidate_id"] = label
                extra_set[f"versions.{label}"] = {
                    "body_md": new_body,
                    "source": f"self_critique:{entry.get('miner') or 'self-critique'}",
                    "proposed_at": now,
                }
                add_to_set["candidates"] = label
                result["candidate_added"] = label
                result["applied"] = True
            else:
                entry["apply_reason"] = reason
                result["applied"] = False
                result["reason"] = "body_not_mongo_editable"
                result["miner_proposal_accepted"] = True
    else:
        entry["status"] = "dismissed"
        entry["decided_at"] = now
        result["rejected"] = True

    # Single write: array + mirror + (optional) minted version + candidate push.
    mirror = primary_pending(proposals)
    update: dict = {"$set": {"self_critique_proposals": proposals, **extra_set}}
    if mirror is not None:
        update["$set"]["self_critique_proposal"] = mirror
    else:
        update["$unset"] = {"self_critique_proposal": ""}
    if add_to_set:
        update["$addToSet"] = add_to_set
    db["skills"].update_one({"_id": skill_id}, update)
    return result


def _decide_signal_source(name: str, subid: str | None, *, accept: bool) -> dict:
    """Accept/dismiss a single signal_source proposal (array entry). Accept
    applies the proposed enable/disable change."""
    db = mongo_tools.db()
    doc = mongo_tools.find_one("signal_sources", {"name": name})
    if not doc:
        raise HTTPException(404, "signal source proposal not found")
    proposals = load_proposals(doc)
    entry = _select_entry(proposals, subid)
    if entry is None:
        raise HTTPException(404, "signal source proposal not found")

    now = datetime.now(UTC)
    new_enabled = None
    if accept:
        entry["status"] = Status.ACCEPTED
        entry["decided_at"] = now
        new_enabled = (entry.get("evidence") or {}).get("proposed_enabled")
    else:
        entry["status"] = "dismissed"
        entry["decided_at"] = now

    mirror = primary_pending(proposals)
    update: dict = {"$set": {"self_critique_proposals": proposals}}
    if mirror is not None:
        update["$set"]["self_critique_proposal"] = mirror
    else:
        update["$unset"] = {"self_critique_proposal": ""}
    if accept and new_enabled is not None:
        update["$set"]["enabled"] = bool(new_enabled)
    db["signal_sources"].update_one({"name": name}, update)
    return {"ok": True, "target_kind": "signal_source",
            **({"enabled": new_enabled} if accept else {})}


# ---------------------------------------------------------------------------
# PRD-03 unified endpoints
# ---------------------------------------------------------------------------

class UnifiedProposal(BaseModel):
    id: str                               # see module docstring for format
    target_kind: Literal["skill", "paid_action", "signal_source"]
    target_id: str
    miner: str | None                  # voice|negative|paid|aeo|signal
    kind: str | None
    issue: str
    proposed_change: str | None = ""
    confidence: str | None = None
    evidence_count: int | None = 0
    evidence: dict = {}
    proposed_at: str | None = None
    status: str | None = None


@router.get("/api/self-critique/proposals", response_model=list[UnifiedProposal])
def list_unified_proposals(
    status: str | None = Query(None,
        description="Filter — default ``awaiting_human_review``"),
    limit: int = Query(50, le=200),
) -> list[dict]:
    """Pending proposals across skills, paid_actions_proposed, and
    signal_sources. The Weekly Review UI calls this for the
    "Proposed updates" section."""
    db = mongo_tools.db()
    target_status = status or Status.AWAITING_HUMAN_REVIEW
    rows: list[dict] = []

    # 1. Skill-targeted proposals. Each doc can now hold MULTIPLE proposals
    #    (self_critique_proposals array); we emit one row per matching entry,
    #    addressable via id ``skill:{id}::{subid}``. The $or also matches
    #    legacy docs that only have the singleton field.
    try:
        for s in db["skills"].find(
            {"$or": [
                {"self_critique_proposals.status": target_status},
                {"self_critique_proposal.status": target_status},
            ]},
            {"_id": 1, "self_critique_proposals": 1, "self_critique_proposal": 1},
        ).limit(limit):
            for p in load_proposals(s):
                if (p or {}).get("status") != target_status:
                    continue
                rows.append({
                    "id":               f"skill:{s['_id']}::{_subid(p.get('signature', ''))}",
                    "target_kind":      "skill",
                    "target_id":        s["_id"],
                    "miner":            p.get("miner"),
                    "kind":             p.get("kind"),
                    "issue":            p.get("issue", ""),
                    "proposed_change":  p.get("proposed_change") or p.get("proposed_change_text", ""),
                    "confidence":       p.get("confidence"),
                    "evidence_count":   p.get("evidence_count", 0),
                    "evidence":         p.get("evidence") or {},
                    "proposed_at":      _iso(p.get("proposed_at")),
                    "status":           p.get("status"),
                })
    except Exception as e:
        log.warning("list_unified_proposals (skills) failed: %s", e)

    # 2. Paid action proposals.
    try:
        for r in db["paid_actions_proposed"].find(
            {"status": target_status},
        ).sort("proposed_at", -1).limit(limit):
            rows.append({
                "id":              f"paid:{r['_id']}",
                "target_kind":     "paid_action",
                "target_id":       r.get("variant_id") or "",
                "miner":           r.get("miner") or "paid",
                "kind":            r.get("kind") or "pause",
                "issue":           r.get("issue") or r.get("rationale", ""),
                "proposed_change": r.get("proposed_change", ""),
                "confidence":      r.get("confidence"),
                "evidence_count":  r.get("evidence_count", 0),
                "evidence":        r.get("evidence") or {},
                "proposed_at":     _iso(r.get("proposed_at")),
                "status":          r.get("status"),
            })
    except Exception as e:
        log.warning("list_unified_proposals (paid) failed: %s", e)

    # 3. Signal-source proposals (e.g., signal_miner disabling sources).
    try:
        for s in db["signal_sources"].find(
            {"$or": [
                {"self_critique_proposals.status": target_status},
                {"self_critique_proposal.status": target_status},
            ]},
            {"name": 1, "self_critique_proposals": 1, "self_critique_proposal": 1},
        ).limit(limit):
            for p in load_proposals(s):
                if (p or {}).get("status") != target_status:
                    continue
                rows.append({
                    "id":               f"signal_source:{s['name']}::{_subid(p.get('signature', ''))}",
                    "target_kind":      "signal_source",
                    "target_id":        s["name"],
                    "miner":            p.get("miner"),
                    "kind":             p.get("kind"),
                    "issue":            p.get("issue", ""),
                    "proposed_change":  p.get("proposed_change", ""),
                    "confidence":       p.get("confidence"),
                    "evidence_count":   p.get("evidence_count", 0),
                    "evidence":         p.get("evidence") or {},
                    "proposed_at":      _iso(p.get("proposed_at")),
                    "status":           p.get("status"),
                })
    except Exception as e:
        log.warning("list_unified_proposals (signal_sources) failed: %s", e)

    # Sort most-recent-first across all sources.
    rows.sort(key=lambda r: r.get("proposed_at") or "", reverse=True)
    return rows[:limit]


@router.get("/api/self-critique/runs")
def list_runs(limit: int = Query(14, le=60)) -> list[dict]:
    """Last N nightly miner runs. Powers the Live ticker entry + the
    ops drill-down."""
    db = mongo_tools.db()
    try:
        cursor = db["self_critique_runs"].find({}).sort("started_at", -1).limit(limit)
        return [{
            "id":             str(r.get("_id", "")),
            "started_at":     _iso(r.get("started_at")),
            "completed_at":   _iso(r.get("completed_at")),
            "status":         r.get("status"),
            "lookback_days":  r.get("lookback_days"),
            "total_proposals": r.get("total_proposals", 0),
            "miners":         r.get("miners", {}),
        } for r in cursor]
    except Exception as e:
        log.warning("self_critique_runs query failed: %s", e)
        return []


@router.post("/api/self-critique/proposals/{proposal_id}/approve")
def approve_proposal(proposal_id: str) -> dict:
    """Approve a unified proposal. Dispatches by ID prefix:

    - ``skill:{id}`` -> existing skill-accept path
      (uses legacy ``/api/self-critique/{id}`` logic).
    - ``paid:{oid}`` -> mark paid_actions_proposed.{id}.status=accepted.
      Actual platform-side apply is a separate worker (ops_qa or a
      paid-apply Cloud Run job); we just record approval here so the
      adapter can pick it up out-of-band.
    - ``signal_source:{name}`` -> apply proposed source change
      (e.g., disable) directly to signal_sources row.
    """
    target_kind, target_id, subid = _split_id(proposal_id)
    db = mongo_tools.db()

    if target_kind == "skill":
        return _decide_skill(target_id, subid, accept=True)

    if target_kind == "paid":
        from bson import ObjectId
        try:
            oid = ObjectId(target_id)
        except Exception:
            raise HTTPException(400, "invalid paid proposal id") from None
        proposal = db["paid_actions_proposed"].find_one_and_update(
            {"_id": oid, "status": Status.AWAITING_HUMAN_REVIEW},
            {"$set": {"status": Status.ACCEPTED,
                      "decided_at": datetime.now(UTC)}},
            return_document=True,
        )
        if proposal is None:
            raise HTTPException(404, "proposal not found or already decided")

        # Best-effort platform-side apply. The status is already
        # "accepted" (founder approval); apply_paid_action attempts the
        # actual pause / reallocate against the integration adapter and
        # records the outcome on the same row so the UI can show
        # "applied" vs "skipped (creds missing)" vs "failed".
        from shared.apply_paid_action import apply_paid_action
        outcome = apply_paid_action(proposal)
        db["paid_actions_proposed"].update_one(
            {"_id": oid},
            {"$set": {
                "applied_at":   datetime.now(UTC),
                "apply_result": outcome,
            }},
        )
        return {
            "ok": True,
            "target_kind": "paid_action",
            "apply_status": outcome.get("status"),
            "apply_reason": outcome.get("reason"),
        }

    if target_kind == "signal_source":
        # Approve = apply the proposed change (signal_miner proposes
        # enable/disable). Array-aware + mirror-synced.
        return _decide_signal_source(target_id, subid, accept=True)

    raise HTTPException(400, f"unknown target_kind {target_kind!r}")


@router.post("/api/self-critique/proposals/{proposal_id}/dismiss")
def dismiss_proposal(proposal_id: str) -> dict:
    """Dismiss a unified proposal. Logs the dismissal so the miner can
    avoid re-proposing the same pattern (PRD-03 §11)."""
    target_kind, target_id, subid = _split_id(proposal_id)
    db = mongo_tools.db()

    if target_kind == "skill":
        return _decide_skill(target_id, subid, accept=False)

    if target_kind == "paid":
        from bson import ObjectId
        try:
            oid = ObjectId(target_id)
        except Exception:
            raise HTTPException(400, "invalid paid proposal id") from None
        result = db["paid_actions_proposed"].update_one(
            {"_id": oid, "status": Status.AWAITING_HUMAN_REVIEW},
            {"$set": {"status": "dismissed",
                      "decided_at": datetime.now(UTC)}},
        )
        if result.matched_count == 0:
            raise HTTPException(404, "proposal not found or already decided")
        return {"ok": True, "target_kind": "paid_action"}

    if target_kind == "signal_source":
        return _decide_signal_source(target_id, subid, accept=False)

    raise HTTPException(400, f"unknown target_kind {target_kind!r}")


@router.post("/api/self-critique/run-now")
def run_now() -> dict:
    """Manual nightly tick. Returns the self_critique_runs row content."""
    from agents.self_critique_runner import run_once
    try:
        return run_once()
    except Exception as e:
        log.warning("self-critique run-now failed: %s", e)
        raise HTTPException(500, f"run failed: {e}") from e


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _iso(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _split_id(pid: str) -> tuple[str, str, str | None]:
    """Parse a unified proposal id into ``(kind, target, subid)``.

    Examples::
        "skill:house-style::a1b2c3"     -> ("skill", "house-style", "a1b2c3")
        "skill:house-style"             -> ("skill", "house-style", None)
        "paid:64f...oid"                -> ("paid", "64f...oid", None)

    The optional ``::{subid}`` suffix addresses one entry in a doc's
    self_critique_proposals array; absent → operate on the primary pending
    proposal (legacy behavior). Returns ``("unknown", pid, None)`` on
    malformed input."""
    subid: str | None = None
    if "::" in pid:
        pid, subid = pid.split("::", 1)
    parts = pid.split(":", 1)
    if len(parts) != 2:
        return "unknown", pid, subid
    return parts[0], parts[1], subid


# ---------------------------------------------------------------------------
# Learning summary — powers the /learning page.
# ---------------------------------------------------------------------------

MINER_NAMES = ("voice", "negative", "paid", "aeo", "signal")


@router.get("/api/self-critique/summary")
def learning_summary(days: int = Query(7, le=90)) -> dict:
    """Aggregate the closed-loop story over the last N days.

    Returns the KPI band + per-miner activity + recent events feed the
    Self-Learning page renders. Designed to be cheap — single-pass over
    each collection, no joins.
    """
    db = mongo_tools.db()
    now = datetime.now(UTC)
    cutoff = now - timedelta(days=days)
    cutoff_28 = now - timedelta(days=28)

    out: dict = {
        "days": days,
        "this_window": {
            "proposals_emitted":  0,
            "proposals_accepted": 0,
            "proposals_dismissed": 0,
            "promotions":         0,
            "miner_runs":         0,
        },
        "pending_proposals":      0,
        "per_miner_28d":          [],
        "events": [],
        "voice_delta": None,
    }

    # ----- Miner runs (self_critique_runs) -----
    try:
        runs_window = list(db["self_critique_runs"].find(
            {"started_at": {"$gte": cutoff}},
        ).sort("started_at", -1))
        out["this_window"]["miner_runs"] = len(runs_window)
        out["this_window"]["proposals_emitted"] = sum(
            int(r.get("total_proposals", 0) or 0) for r in runs_window
        )
    except Exception as e:
        log.warning("summary runs query failed: %s", e)
        runs_window = []

    # Per-miner 28-day rollup (runs + emitted + errors). Surfacing errors is
    # what lets a chronically-failing miner look DIFFERENT from one that's
    # simply finding nothing — without it, "0 proposals" hides "crashed every
    # night".
    per_miner: dict[str, dict] = {
        m: {"miner": m, "runs": 0, "emitted": 0,
            "accepted": 0, "dismissed": 0, "errors": 0, "last_error": None}
        for m in MINER_NAMES
    }
    miner_error_runs = 0
    try:
        for r in db["self_critique_runs"].find(
            {"started_at": {"$gte": cutoff_28}},
        ):
            run_had_error = False
            for m, info in (r.get("miners") or {}).items():
                if m not in per_miner:
                    continue
                per_miner[m]["runs"] += 1
                per_miner[m]["emitted"] += int(info.get("proposals", 0) or 0)
                errs = info.get("errors") or []
                if errs:
                    per_miner[m]["errors"] += len(errs)
                    per_miner[m]["last_error"] = str(errs[-1])[:200]
                    run_had_error = True
            if run_had_error:
                miner_error_runs += 1
    except Exception as e:
        log.warning("summary per-miner query failed: %s", e)
    # 28-day window (matches per_miner_28d), kept top-level so it isn't
    # confused with the `days`-windowed this_window counters.
    out["miner_error_runs_28d"] = miner_error_runs

    # ----- Pending proposals (across skills + paid_actions + signal_sources) -----
    # Skills/signal_sources can hold MULTIPLE proposals each, so count array
    # entries (not docs). paid_actions_proposed is one-row-per-proposal.
    pending = 0
    try:
        for coll in ("skills", "signal_sources"):
            for d in db[coll].find(
                {"$or": [
                    {"self_critique_proposals.status": Status.AWAITING_HUMAN_REVIEW},
                    {"self_critique_proposal.status": Status.AWAITING_HUMAN_REVIEW},
                ]},
                {"self_critique_proposals": 1, "self_critique_proposal": 1},
            ):
                pending += sum(1 for p in load_proposals(d)
                               if (p or {}).get("status") == Status.AWAITING_HUMAN_REVIEW)
        pending += db["paid_actions_proposed"].count_documents(
            {"status": Status.AWAITING_HUMAN_REVIEW},
        )
    except Exception as e:
        log.warning("summary pending count failed: %s", e)
    out["pending_proposals"] = pending

    # ----- Accepted / dismissed decisions in window (skills + paid + signal_source) -----
    # Skills can hold multiple proposals; iterate every array entry decided in
    # the window (the $or also catches legacy singleton-only docs).
    decisions_events: list[dict] = []
    try:
        cutoff_iso = cutoff
        for s in db["skills"].find({
            "$or": [
                {"self_critique_proposals.decided_at": {"$gte": cutoff}},
                {"self_critique_proposal.decided_at": {"$gte": cutoff}},
            ],
        }):
            for p in load_proposals(s):
                p = p or {}
                decided = p.get("decided_at")
                if not isinstance(decided, datetime):
                    continue
                decided_cmp = decided if decided.tzinfo else decided.replace(tzinfo=UTC)
                if decided_cmp < cutoff_iso:
                    continue
                status = p.get("status")
                miner = p.get("miner")
                if status == Status.ACCEPTED:
                    out["this_window"]["proposals_accepted"] += 1
                    if miner and miner in per_miner:
                        per_miner[miner]["accepted"] += 1
                    decisions_events.append({
                        "ts":      _iso(decided),
                        "kind":    "proposal_accepted",
                        "summary": f"{miner or 'self-critique'} proposal accepted on {s.get('_id')}",
                        "miner":   miner,
                        "target":  f"skill:{s.get('_id')}",
                    })
                elif status == "dismissed":
                    out["this_window"]["proposals_dismissed"] += 1
                    if miner and miner in per_miner:
                        per_miner[miner]["dismissed"] += 1
                    decisions_events.append({
                        "ts":      _iso(decided),
                        "kind":    "proposal_dismissed",
                        "summary": f"{miner or 'self-critique'} proposal dismissed on {s.get('_id')}",
                        "miner":   miner,
                        "target":  f"skill:{s.get('_id')}",
                    })
    except Exception as e:
        log.warning("summary skill decisions failed: %s", e)

    try:
        for r in db["paid_actions_proposed"].find({
            "decided_at": {"$gte": cutoff},
        }):
            status = r.get("status")
            if status == Status.ACCEPTED:
                out["this_window"]["proposals_accepted"] += 1
                per_miner["paid"]["accepted"] += 1
                decisions_events.append({
                    "ts":      _iso(r.get("decided_at")),
                    "kind":    "proposal_accepted",
                    "summary": f"paid {r.get('kind','pause')} on {r.get('variant_id')} accepted",
                    "miner":   "paid",
                    "target":  f"paid:{r.get('_id')}",
                })
            elif status == "dismissed":
                out["this_window"]["proposals_dismissed"] += 1
                per_miner["paid"]["dismissed"] += 1
                decisions_events.append({
                    "ts":      _iso(r.get("decided_at")),
                    "kind":    "proposal_dismissed",
                    "summary": f"paid {r.get('kind','pause')} on {r.get('variant_id')} dismissed",
                    "miner":   "paid",
                    "target":  f"paid:{r.get('_id')}",
                })
    except Exception as e:
        log.warning("summary paid decisions failed: %s", e)

    # ----- Promotions (skills.promoted_at in window) -----
    promotions_events: list[dict] = []
    try:
        for s in db["skills"].find({
            "promoted_at": {"$gte": cutoff},
        }):
            out["this_window"]["promotions"] += 1
            promotions_events.append({
                "ts":      _iso(s.get("promoted_at")),
                "kind":    "promotion",
                "summary": f"{s.get('_id')} promoted to {s.get('current_version')}",
                "target":  f"skill:{s.get('_id')}",
            })
    except Exception as e:
        log.warning("summary promotions query failed: %s", e)

    # ----- Voice rubric delta — this window vs prior window -----
    try:
        from services.web_api.routers.rubric_trends import _rubric_trend_from_mongo
        rows_recent = _rubric_trend_from_mongo(days=days)
        rows_prior  = _rubric_trend_from_mongo(days=days * 2)
        mean_recent = _avg(rows_recent, "mean_brand_voice")
        # prior window means the SECOND half of the larger lookback.
        mean_prior = _avg(
            [r for r in rows_prior if r["day"] < cutoff.date().isoformat()],
            "mean_brand_voice",
        )
        if mean_recent is not None and mean_prior is not None:
            out["voice_delta"] = round(mean_recent - mean_prior, 3)
    except Exception as e:
        log.debug("summary voice_delta failed: %s", e)

    # ----- Recent events feed (runs + decisions + promotions, merged) -----
    run_events = [
        {
            "ts":      _iso(r.get("started_at")),
            "kind":    "miner_run",
            "summary": _summarize_run(r),
            "miner":   None,
            "target":  None,
        }
        for r in runs_window[:20]
    ]
    events = run_events + decisions_events + promotions_events
    events.sort(key=lambda e: e.get("ts") or "", reverse=True)
    out["events"] = events[:20]
    out["per_miner_28d"] = list(per_miner.values())
    return out


def _avg(rows: list[dict], key: str) -> float | None:
    """Mean of ``key`` over rows where it's a positive number; None when
    there's nothing to average."""
    values = [float(r.get(key) or 0) for r in rows
              if isinstance(r.get(key), (int, float)) and float(r[key]) > 0]
    if not values:
        return None
    return sum(values) / len(values)


def _summarize_run(r: dict) -> str:
    """Build a short single-line summary of a self_critique_runs row.

    Errors are surfaced explicitly so a run where miners crashed reads
    differently from a clean run that simply found nothing."""
    miners = r.get("miners") or {}
    parts = []
    errored = []
    for m, info in miners.items():
        n = int(info.get("proposals", 0) or 0)
        if n > 0:
            parts.append(f"{m} {n}")
        if info.get("errors"):
            errored.append(m)
    if parts:
        summary = f"runner · {', '.join(parts)}"
    else:
        summary = f"runner · 0 proposals ({len(miners)} miners scanned)"
    if errored:
        summary += f" · ⚠ errors: {', '.join(sorted(errored))}"
    return summary


# ---------------------------------------------------------------------------
# Legacy POST endpoint registered LAST so more-specific PRD-03 routes
# (``proposals/{id}/...``, ``runs``, ``run-now``) match first. FastAPI
# resolves routes in declaration order — without this ordering, the
# legacy ``{skill_id}`` catch-all would swallow ``run-now`` etc. and
# return 422 because the CritiqueDecision body validation would fail.
# ---------------------------------------------------------------------------

router.post("/api/self-critique/{skill_id}")(decide_critique)
