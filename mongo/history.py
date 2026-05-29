"""Write-through-history helper.

Every mutation of a canonical document copies the pre-image to
``history.<collection>`` first. This is application-level discipline: if you
skip these helpers, you skip the history capture.

Canonical collection names are flat (e.g., ``experiments``, ``skills``,
``approvals``); the matching history collection is ``history.<name>``. The
``history.`` prefix already exists in mongo/schema.py's HISTORY_COLLECTIONS.

Usage:
    from mongo.history import update_with_history

    update_with_history("skills", {"_id": "linkedin_post"},
                        {"$set": {"current_version": "v4.txt"}},
                        actor_id="founder",
                        change_kind="promotion")
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from shared import mongo_tools


def _history_collection(collection: str) -> str:
    """Map a canonical collection name to its history shadow.

    Accepts both flat names (``experiments``) and the legacy ``state.<name>``
    prefix — the latter is normalized away so old call-sites keep working
    while we migrate.
    """
    if collection.startswith("history."):
        return collection
    if collection.startswith("state."):
        return collection.replace("state.", "history.", 1)
    return f"history.{collection}"


def _capture(state_collection: str, filt: dict, actor_id: str,
              change_kind: str,
              secret_name: str | None = None) -> dict | None:
    """Read the current state doc and append it to history.* with a
    _superseded_at marker. Returns the captured doc (or None if not found).
    """
    db = mongo_tools.db(secret_name)
    current = db[state_collection].find_one(filt)
    if not current:
        return None
    history_doc = {
        **current,
        "_original_id": current.get("_id"),
        "_superseded_at": datetime.now(UTC),
        "_superseded_by": actor_id,
        "_change_kind": change_kind,
    }
    # Don't reuse the _id — history rows are independent
    history_doc.pop("_id", None)
    db[_history_collection(state_collection)].insert_one(history_doc)
    return current


def _stamp_provenance_on_update(update: dict, *, now: datetime,
                                  actor_id: str, existing: dict | None = None) -> None:
    """Inject ``_provenance.updated_at`` + ``_provenance.actor_id`` into a $set
    payload, AND extend the ``supersedes`` lineage chain when the pre-image is
    available. Mutates ``update`` in place.

    The supersedes chain records each prior version this write supersedes
    (``{ref_id}@{prev_updated_at}``) — previously computed only by the unused
    ``shared.provenance.bump_provenance`` and never maintained on real writes,
    so lineage silently stopped at the seed. We now feed the captured
    pre-image through ``bump_provenance`` to keep it growing.

    MongoDB rejects mixing dotted (``_provenance.updated_at``) and whole-path
    (``_provenance``) writes in the same update, so we pick the shape based on
    what the caller already supplied.
    """
    set_part = update.setdefault("$set", {})

    # Lineage: derive the new supersedes list from the pre-image. Skipped for
    # legacy/un-provenanced docs (existing has no _provenance) → behavior
    # there is unchanged (updated_at/actor_id only).
    supersedes = None
    if existing and isinstance(existing.get("_provenance"), dict):
        from shared.provenance import bump_provenance
        src_kind = (existing["_provenance"].get("source") or {}).get("kind") or "update"
        supersedes = bump_provenance(
            existing, actor_id=actor_id, source_kind=src_kind,
        ).get("_provenance.supersedes")

    if isinstance(set_part.get("_provenance"), dict):
        set_part["_provenance"]["updated_at"] = now
        set_part["_provenance"]["actor_id"] = actor_id
        if supersedes is not None:
            set_part["_provenance"]["supersedes"] = supersedes
    else:
        set_part["_provenance.updated_at"] = now
        set_part["_provenance.actor_id"] = actor_id
        if supersedes is not None:
            set_part["_provenance.supersedes"] = supersedes


def update_with_history(state_collection: str, filt: dict, update: dict,
                         *, actor_id: str, change_kind: str = "update",
                         secret_name: str | None = None) -> dict:
    """Capture pre-image, then apply update. Bumps _provenance.updated_at.

    Raises ``DocumentNotFound`` if the filter matches nothing — without this
    guard, ``update_one`` silently no-ops on a typo'd id (e.g., a bad
    experiment_id passed to ``transition_experiment_state``).

    Returns the new doc (post-update).
    """
    pre = _capture(state_collection, filt, actor_id, change_kind, secret_name)
    _stamp_provenance_on_update(update,
                                 now=datetime.now(UTC),
                                 actor_id=actor_id,
                                 existing=pre)
    db = mongo_tools.db(secret_name)
    result = db[state_collection].update_one(filt, update)
    if result.matched_count == 0:
        raise DocumentNotFound(
            f"update_with_history matched zero docs in {state_collection} "
            f"for filter {filt} (change_kind={change_kind})"
        )
    return db[state_collection].find_one(filt) or {}


class DocumentNotFound(LookupError):
    """update_with_history / replace_with_history matched zero docs."""


def replace_with_history(state_collection: str, filt: dict, replacement: dict,
                          *, actor_id: str, change_kind: str = "replace",
                          secret_name: str | None = None) -> dict:
    """Full document replace with history capture. Use sparingly — prefer
    update_with_history for partial mutations.

    Raises ``DocumentNotFound`` if the filter matches nothing (mirrors
    update_with_history's behavior — silent no-ops are worse than loud
    failures here).
    """
    _capture(state_collection, filt, actor_id, change_kind, secret_name)
    now = datetime.now(UTC)
    if "_provenance" in replacement:
        replacement["_provenance"]["updated_at"] = now
        replacement["_provenance"]["actor_id"] = actor_id
    db = mongo_tools.db(secret_name)
    result = db[state_collection].replace_one(filt, replacement, upsert=False)
    if result.matched_count == 0:
        raise DocumentNotFound(
            f"replace_with_history matched zero docs in {state_collection} "
            f"for filter {filt} (change_kind={change_kind})"
        )
    return db[state_collection].find_one(filt) or {}


def default_provenance_block(
    *,
    actor_id: str,
    kind: str = "system",
    trust_tier: str = "verified",
    confidence: float = 1.0,
    source_kind: str = "seed",
    workspace: str = "default",
) -> dict:
    """Return a fresh ``{_provenance, _workspace, _owner}`` dict block.

    Intended for seed scripts and one-shot writers that don't carry their
    own provenance — merge the result into your doc with ``{**block, **doc}``
    (caller wins) so explicit values override defaults.

    Args:
        actor_id: who is doing the write (e.g., ``"seed"``, ``"founder"``).
        kind: one of ``"human" | "agent" | "system" | "ingestion"``.
        trust_tier: ``"verified" | "inferred" | "hypothesis" | "stale"``.
            Defaults to ``"verified"`` — seed data is intentionally chosen,
            so it qualifies. Agent-emitted docs should override to
            ``"inferred"``.
        confidence: 0.0–1.0. ``1.0`` for seed/founder data.
        source_kind: short label for the source — ``"seed"``,
            ``"founder_decision"``, ``"experiment_outcome"``, etc.
        workspace: multi-tenant scope label. Defaults to ``"default"``.
    """
    now = datetime.now(UTC)
    return {
        "_workspace": workspace,
        "_owner": actor_id,
        "_provenance": {
            "kind": kind,
            "actor_id": actor_id,
            "source": {"kind": source_kind, "ref_id": None,
                       "ref_collection": None},
            "created_at": now,
            "updated_at": now,
            "confidence": confidence,
            "evidence_count": 0,
            "trust_tier": trust_tier,
            "supersedes": [],
            "ttl": None,
        },
    }


def seed_canonical(state_collection: str, docs: list[dict],
                   *, actor_id: str,
                   kind: str = "ingestion",
                   trust_tier: str = "verified",
                   change_kind: str = "seed",
                   secret_name: str | None = None) -> int:
    """Bulk-seed a canonical collection with full architectural compliance.

    For each doc:
      1. Merges in ``default_provenance_block(...)`` if the doc didn't
         supply its own ``_provenance`` / ``_workspace`` / ``_owner``.
      2. Calls ``insert_with_provenance``, which appends a ``create`` row
         to ``history.<collection>`` for the audit trail.

    Returns the count of docs inserted.

    Note: iterates one insert per doc (two round-trips: canonical + history).
    For seed sizes (~50-500 docs) the latency is negligible; for bulk
    ingest of 10K+ rows, write a bulk variant.
    """
    inserted = 0
    block = default_provenance_block(
        actor_id=actor_id, kind=kind, trust_tier=trust_tier,
        source_kind=change_kind,
    )
    for d in docs:
        merged = {**block, **d}  # caller-supplied fields win
        insert_with_provenance(
            state_collection, merged,
            actor_id=actor_id,
            change_kind=change_kind,
            secret_name=secret_name,
        )
        inserted += 1
    return inserted


def insert_with_provenance(state_collection: str, doc: dict,
                            *, actor_id: str, change_kind: str = "create",
                            secret_name: str | None = None) -> dict:
    """Insert a brand-new doc. No history capture needed (nothing to supersede),
    but we DO write a 'create' record to history so the audit trail is complete.
    """
    db = mongo_tools.db(secret_name)
    result = db[state_collection].insert_one(doc)
    history_doc = {
        **doc,
        "_original_id": result.inserted_id,
        "_superseded_at": None,
        "_superseded_by": None,
        "_change_kind": "create",
        "_created_by": actor_id,
        "_created_at": datetime.now(UTC),
    }
    history_doc.pop("_id", None)
    db[_history_collection(state_collection)].insert_one(history_doc)
    return db[state_collection].find_one({"_id": result.inserted_id}) or {}


def get_at(state_collection: str, doc_id: Any, at: datetime,
           secret_name: str | None = None) -> dict | None:
    """Reconstruct the state of a document at a given point in time.

    A history row's ``_superseded_at`` is the moment its payload STOPPED
    being canonical. So the row canonical at time ``at`` is the one whose
    ``_superseded_at`` is the *smallest value strictly greater than* ``at``
    — i.e. the next supersession after ``at``. If no such row exists, the
    doc hasn't been superseded since ``at`` and the current state IS the
    state at ``at``.

    The create-event row (which carries ``_superseded_at = None``) is
    excluded by ``{$gt: at}`` because BSON null is not greater than a date.
    """
    db = mongo_tools.db(secret_name)
    history = list(db[_history_collection(state_collection)].find(
        {"_original_id": doc_id, "_superseded_at": {"$gt": at}}
    ).sort([("_superseded_at", 1)]).limit(1))
    if history:
        return history[0]
    # Doc has not been superseded since `at` → current state is the answer.
    return db[state_collection].find_one({"_id": doc_id})
