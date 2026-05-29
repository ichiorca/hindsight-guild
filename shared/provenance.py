"""Provenance schema — a consistent shape attached to every canonical
state document. Lets queries filter by trust tier, age, source kind, owner.

Use `attach_provenance()` to wrap a payload before insert or update. The
history pattern in `mongo/history.py` reads and increments these fields
automatically on mutation.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

WORKSPACE_DEFAULT = "default"


# Trust tiers — filterable on the wire
TrustTier = Literal["verified", "inferred", "hypothesis", "stale"]
ProvenanceKind = Literal["human", "agent", "system", "ingestion"]


class ProvenanceSource(BaseModel):
    """Where this fact came from. Required."""
    kind: str                       # "experiment_outcome" | "customer_voice_ingest" | ...
    ref_id: str | None = None    # id of the source record
    ref_collection: str | None = None


class Provenance(BaseModel):
    kind: ProvenanceKind
    actor_id: str
    source: ProvenanceSource
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    confidence: float = 1.0
    evidence_count: int = 0
    trust_tier: TrustTier = "inferred"
    supersedes: list[str] = Field(default_factory=list)
    ttl: str | None = None       # ISO-8601 duration like "P30D"


def attach_provenance(
    payload: dict,
    *,
    kind: ProvenanceKind,
    actor_id: str,
    source_kind: str,
    source_ref_id: str | None = None,
    source_ref_collection: str | None = None,
    confidence: float = 1.0,
    evidence_count: int = 0,
    trust_tier: TrustTier = "inferred",
    workspace: str = WORKSPACE_DEFAULT,
    owner: str | None = None,
) -> dict:
    """Wrap a document payload with the provenance + workspace + owner block.

    Use at every insert into state.* collections. Existing code should
    migrate over time; the absence of _provenance on legacy docs is allowed
    but flagged by audit_provenance().
    """
    now = datetime.now(UTC)
    return {
        **payload,
        "_provenance": Provenance(
            kind=kind,
            actor_id=actor_id,
            source=ProvenanceSource(
                kind=source_kind,
                ref_id=source_ref_id,
                ref_collection=source_ref_collection,
            ),
            created_at=now,
            updated_at=now,
            confidence=confidence,
            evidence_count=evidence_count,
            trust_tier=trust_tier,
        ).model_dump(mode="json"),
        "_workspace": workspace,
        "_owner": owner or actor_id,
    }


def bump_provenance(existing: dict, *, actor_id: str,
                     source_kind: str, source_ref_id: str | None = None,
                     confidence: float | None = None,
                     trust_tier: TrustTier | None = None) -> dict:
    """Increment updated_at + append supersedes. Returns the new _provenance
    block to merge into a $set. Existing supersedes are preserved.
    """
    prov = existing.get("_provenance") or {}
    now = datetime.now(UTC)
    supersedes = list(prov.get("supersedes", []))
    if existing.get("_id"):
        # Record that the new version supersedes the previous _id
        prev = f"{prov.get('source', {}).get('ref_id') or existing['_id']}@{prov.get('updated_at') or 'unknown'}"
        if prev not in supersedes:
            supersedes.append(prev)
    return {
        "_provenance.actor_id": actor_id,
        "_provenance.source.kind": source_kind,
        "_provenance.source.ref_id": source_ref_id,
        "_provenance.updated_at": now,
        "_provenance.supersedes": supersedes,
        **({"_provenance.confidence": confidence} if confidence is not None else {}),
        **({"_provenance.trust_tier": trust_tier} if trust_tier else {}),
    }


def audit_provenance(doc: dict) -> dict:
    """Return a summary of what's missing — used by /api/health to surface
    legacy docs that haven't been migrated yet."""
    issues = []
    if "_provenance" not in doc:
        issues.append("no_provenance_block")
    if "_workspace" not in doc:
        issues.append("no_workspace")
    if "_owner" not in doc:
        issues.append("no_owner")
    return {"ok": not issues, "issues": issues}
