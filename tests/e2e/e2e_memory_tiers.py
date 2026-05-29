"""End-to-end memory-tier demonstration + gap audit.

The codebase documents a 4-layer memory architecture in
``mongo/MEMORY_ARCHITECTURE.md``:

    1. Event log (BigQuery telemetry.* + Mongo mirror) — append-only
    2. Canonical state (Mongo flat collections + history.<coll> shadows) —
       mutations must go through mongo/history.py so every change is
       captured in history.<coll>; every doc carries a _provenance block.
    3. Derived state (Mongo derived.* + BigQuery views) — recomputed on
       a schedule, has freshness_sla, readers MUST check _derived.stale.
    4. Episodic memory (Vertex AI Memory Bank, scoped per agent/ICP/
       channel/campaign/skill) — async extraction from sessions.

This test:

    - Exercises each layer end-to-end so an operator can see how the
      memory architecture actually behaves.
    - Probes for known and unknown gaps: missing _provenance blocks,
      direct mutations that bypass mongo/history.py, derived-state
      consumers that don't honor freshness, agent code that assumes
      Memory Bank is always reachable.
    - Audit phases (5, 6) are WARN-ONLY by design — they print gaps
      with file:line refs and counts, but the test only exit-fails on
      a functional-phase failure. Lets you ship this as a regression
      baseline that improves over time.

Usage (from agentic-marketing/, with Docker Mongo up + scripts.local_seed run):

    python -m tests.e2e.e2e_memory_tiers              # run all 7 phases
    python -m tests.e2e.e2e_memory_tiers --phases 1,2 # subset
    python -m tests.e2e.e2e_memory_tiers --strict     # audit findings cause exit 1

Exit 0 if every functional phase passes. Audit findings are tabulated
to stderr in the summary regardless.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

# Env bootstrap — must run BEFORE the shared.mongo_tools import below so
# MONGO_URI_DIRECT / MONGO_DB / PROJECT_ID are populated at first read.
from scripts._test_bootstrap import (  # noqa: E402
    REPO_ROOT,
    _banner,
    _fail,
    _info,
    _ok,
    _sub,
    _warn,
)

# Lazy imports below module-level env bootstrap.
from shared import mongo_tools  # noqa: E402

_TEST_PREFIX = "_mem_audit_"
_TEST_WORKSPACE = "mem_audit_test"


# ---------------------------------------------------------------------------
# Phase 1 — Layer 1: Event log (BigQuery telemetry.* + Mongo mirror).
#
# What we prove: shared.telemetry.emit_action writes to the Mongo `actions`
# collection (the LOCAL_DEV mirror of telemetry.actions). The mirror has the
# same (telemetry_id, agent, action_type) dedup key as production.
#
# Probe: the architecture doc says Layer 1 is "append-only, immutable." The
# Mongo mirror has no application-level enforcement — anyone with the writer
# secret can update_one. We demonstrate that and recommend an audit.
# ---------------------------------------------------------------------------

def phase_1_event_log() -> dict:
    _banner("Phase 1 — Layer 1: Event log (Mongo mirror of telemetry.actions)")

    from shared.telemetry import OutcomeSlot, TelemetryRecord, emit_action

    db = mongo_tools.db()
    # Clean prior test events. Append-only in principle, but we own this prefix.
    db["actions"].delete_many({"telemetry_id": {"$regex": f"^{_TEST_PREFIX}"}})
    db["outcomes"].delete_many({"telemetry_id": {"$regex": f"^{_TEST_PREFIX}"}})

    telemetry_id = f"{_TEST_PREFIX}{uuid.uuid4().hex[:10]}"
    record = TelemetryRecord(
        telemetry_id=telemetry_id,
        agent="mem_audit_probe",
        skill_id="mem_audit",
        skill_version="v0",
        action_type="audit_probe",
        channel="linkedin",
        raw={"phase": "1_event_log"},
    )
    outcomes = [OutcomeSlot(
        slot_name="engagement_72h",
        metric="engagement_72h",
        source="local_dev",
        expected_by=datetime.now(UTC) + timedelta(days=3),
    )]
    returned_id = emit_action(record, outcomes=outcomes)
    if returned_id != telemetry_id:
        _fail(f"emit_action returned {returned_id!r}, expected {telemetry_id!r}")
        return {"ok": False}
    _ok(f"emit_action({telemetry_id}) returned the same id")

    stored = db["actions"].find_one({"telemetry_id": telemetry_id})
    if stored is None:
        _fail("Mongo mirror missing the action — emit_action did not dual-write")
        return {"ok": False}
    _ok(f"Mongo `actions` mirror contains the row "
        f"(agent={stored['agent']!r}, action_type={stored['action_type']!r})")

    outcome_count = db["outcomes"].count_documents({"telemetry_id": telemetry_id})
    if outcome_count != 1:
        _fail(f"Expected 1 outcome slot, got {outcome_count}")
        return {"ok": False}
    _ok(f"Mongo `outcomes` mirror contains {outcome_count} pending slot")

    # Probe: try to mutate the "immutable" mirror. Document the gap.
    _sub("PROBE — immutability of the Mongo event mirror")
    mutate_result = db["actions"].update_one(
        {"telemetry_id": telemetry_id},
        {"$set": {"agent": "MUTATED_BY_PROBE"}},
    )
    if mutate_result.modified_count == 1:
        _warn(
            "The Mongo `actions` mirror accepted an arbitrary update. "
            "Architecture says Layer 1 is append-only/immutable; the mirror "
            "has no application-level enforcement, only the Atlas user role. "
            "Production BQ ingestion is genuinely append-only, but the "
            "LOCAL_DEV mirror diverges — keep the writer secret tightly scoped."
        )
    # Restore the row so re-runs see consistent state.
    db["actions"].update_one(
        {"telemetry_id": telemetry_id}, {"$set": {"agent": "mem_audit_probe"}},
    )

    return {
        "ok": True,
        "telemetry_id": telemetry_id,
        "mirror_mutation_unrestricted": mutate_result.modified_count == 1,
    }


# ---------------------------------------------------------------------------
# Phase 2 — Layer 2: Canonical state + history capture.
#
# Uses `experiments` as a guinea-pig collection (with _mem_audit_ prefixed
# _ids so we don't collide with anything real). Walks through the
# documented happy path, then the BAD-PATH variants to surface what they
# silently miss.
# ---------------------------------------------------------------------------

def phase_2_canonical_state() -> dict:
    _banner("Phase 2 — Layer 2: Canonical state + history capture")

    from mongo.history import (
        get_at,
        insert_with_provenance,
        update_with_history,
    )

    db = mongo_tools.db()
    collection = "experiments"
    history_coll = f"history.{collection}"

    # Clean prior test docs across canonical + history.
    db[collection].delete_many({"_id": {"$regex": f"^{_TEST_PREFIX}"}})
    db[history_coll].delete_many(
        {"_original_id": {"$regex": f"^{_TEST_PREFIX}"}}
    )

    findings: dict[str, Any] = {"ok": True, "violations": []}

    # ----- 2.1 insert_with_provenance — happy path -----
    _sub("2.1 — insert_with_provenance writes a canonical doc + history row")
    good_id = f"{_TEST_PREFIX}good_{uuid.uuid4().hex[:8]}"
    now = datetime.now(UTC)
    good_doc = {
        "_id": good_id,
        "title": "Memory audit probe — good insert",
        "hypothesis": "Demonstrate the happy-path write",
        "state": "running",
        "channel": "linkedin",
        "created_at": now,
        # Architecture says every canonical doc carries _provenance + _workspace
        # + _owner. insert_with_provenance doesn't auto-fill these for you —
        # the caller is expected to supply them.
        "_provenance": {
            "kind": "system",
            "actor_id": "e2e_memory_tiers_test",
            "source": {"kind": "audit_probe", "ref_id": None,
                       "ref_collection": None},
            "created_at": now,
            "updated_at": now,
            "confidence": 1.0,
            "evidence_count": 1,
            "trust_tier": "verified",
            "supersedes": [],
            "ttl": None,
        },
        "_workspace": _TEST_WORKSPACE,
        "_owner": "system",
    }
    insert_with_provenance(
        collection, good_doc,
        actor_id="e2e_memory_tiers_test", change_kind="audit_probe_create",
    )
    canonical = db[collection].find_one({"_id": good_id})
    if not canonical or not canonical.get("_provenance"):
        _fail("Canonical doc missing _provenance after insert_with_provenance")
        findings["ok"] = False
    else:
        _ok(f"Canonical doc has _provenance.trust_tier={canonical['_provenance']['trust_tier']!r} "
            f"_owner={canonical['_owner']!r} _workspace={canonical['_workspace']!r}")

    create_history = list(db[history_coll].find({"_original_id": good_id}))
    if len(create_history) == 1 and create_history[0].get("_change_kind") == "create":
        _ok(f"history.{collection} captured the create event "
            f"(_change_kind={create_history[0]['_change_kind']!r})")
    else:
        _fail(f"history.{collection} did NOT capture the create event "
              f"(found {len(create_history)} rows)")
        findings["ok"] = False

    # ----- 2.2 update_with_history — happy path -----
    _sub("2.2 — update_with_history captures pre-image before mutating")
    update_with_history(
        collection, {"_id": good_id},
        {"$set": {"state": "decided",
                  "decided_at": datetime.now(UTC)}},
        actor_id="e2e_memory_tiers_test",
        change_kind="audit_probe_decided",
    )
    after_update = db[collection].find_one({"_id": good_id})
    if after_update["state"] != "decided":
        _fail("Canonical doc didn't transition to state=decided")
        findings["ok"] = False
    else:
        _ok(f"Canonical doc transitioned to state={after_update['state']!r}, "
            f"_provenance.updated_at advanced to "
            f"{after_update['_provenance']['updated_at']}")

    history_after_update = list(
        db[history_coll].find({"_original_id": good_id}).sort("_superseded_at", 1)
    )
    if len(history_after_update) != 2:
        _fail(f"Expected 2 history rows (create + pre-update), got "
              f"{len(history_after_update)}")
        findings["ok"] = False
    else:
        kinds = [h.get("_change_kind") for h in history_after_update]
        _ok(f"history.{collection} now has {len(history_after_update)} rows: kinds={kinds}")

    # ----- 2.3 get_at — time travel reconstruction -----
    _sub("2.3 — get_at() reconstructs state at a point in time")
    # The pre-update history row's _superseded_at ≈ the moment of the update.
    # That row's payload is the state value that was canonical AT or
    # BEFORE its _superseded_at — i.e. the correct answer for any `at`
    # earlier than _superseded_at.
    superseded_at = history_after_update[-1]["_superseded_at"]
    # Mongo round-trips datetimes as naive (BSON drops tzinfo). Coerce to
    # UTC so the comparison with our aware `earlier` succeeds.
    if superseded_at.tzinfo is None:
        superseded_at = superseded_at.replace(tzinfo=UTC)
    earlier = superseded_at - timedelta(seconds=1)
    rebuilt = get_at(collection, good_id, earlier)
    rebuilt_state = (rebuilt or {}).get("state")
    if rebuilt_state == "running":
        _ok(f"get_at({earlier.isoformat()[:19]}) returned state="
            f"{rebuilt_state!r} (pre-update value — CORRECT)")
    else:
        _warn(
            f"LATENT BUG in mongo/history.py:get_at() — "
            f"asked for state at {earlier.isoformat()[:19]} (1s before the "
            f"update at {superseded_at.isoformat()[:19]}), expected "
            f"state='running' (pre-update value), got state={rebuilt_state!r}."
        )
        _info(
            "Root cause: the query is `{_superseded_at: {$lte: at}}` and "
            "sorts DESCENDING, returning the history row most recently "
            "SUPERSEDED before `at`. But a history row IS the pre-image of "
            "the doc that got superseded — its payload was canonical UNTIL "
            "_superseded_at. The correct query is `{$gt: at}` sorted "
            "ASCENDING — i.e. the row with the smallest _superseded_at "
            "greater than `at` holds the value that was canonical at `at`."
        )
        _info(
            "Suggested fix in mongo/history.py:get_at():\n"
            "      history = list(db[_history_collection(state_collection)]\n"
            "          .find({\"_original_id\": doc_id,\n"
            "                  \"_superseded_at\": {\"$gt\": at}})\n"
            "          .sort([(\"_superseded_at\", 1)]).limit(1))"
        )
        findings.setdefault("latent_bugs", []).append({
            "where": "mongo/history.py:get_at",
            "symptom": (
                f"asked for state 1s before an update; got the post-update "
                f"value ({rebuilt_state!r}) instead of the pre-update value "
                f"('running')."
            ),
            "fix": "invert $lte→$gt and sort ascending",
        })

    # ----- 2.4 BAD PATH: raw insert_one -----
    _sub("2.4 — PROBE: raw db.insert_one (the architecture-violating path)")
    bad_id = f"{_TEST_PREFIX}bad_{uuid.uuid4().hex[:8]}"
    db[collection].insert_one({
        "_id": bad_id,
        "title": "Memory audit probe — direct insert (architecturally wrong)",
        "state": "running",
        "channel": "email",
        "created_at": datetime.now(UTC),
        # Intentionally NO _provenance, _workspace, _owner. This is what
        # raw `mongodb.insert-one` MCP calls and `scripts/local_seed.py`
        # produce today.
    })
    bad_doc = db[collection].find_one({"_id": bad_id})
    history_after_bad = db[history_coll].count_documents({"_original_id": bad_id})
    if bad_doc.get("_provenance") is None:
        _warn(
            "Raw db.insert_one inserted a canonical doc with NO _provenance / "
            "_workspace / _owner. The architecture says every canonical doc "
            "MUST carry these. Real call sites doing the same thing: see "
            "Phase 5 grep output."
        )
        findings["violations"].append("raw_insert_one_skips_provenance")
    if history_after_bad == 0:
        _warn(
            f"Raw db.insert_one wrote zero rows to history.{collection}. "
            f"insert_with_provenance writes a 'create' history record for the "
            f"audit trail; the raw path skips it entirely."
        )
        findings["violations"].append("raw_insert_one_skips_history_create_event")

    # ----- 2.5 BAD PATH: raw update_one -----
    _sub("2.5 — PROBE: raw db.update_one (silent history miss)")
    history_pre_bad_update = db[history_coll].count_documents(
        {"_original_id": good_id}
    )
    db[collection].update_one(
        {"_id": good_id},
        {"$set": {"hypothesis": "raw-update bypassed history (probe)"}},
    )
    history_post_bad_update = db[history_coll].count_documents(
        {"_original_id": good_id}
    )
    if history_post_bad_update == history_pre_bad_update:
        _warn(
            f"Raw db.update_one mutated the canonical doc but history."
            f"{collection} got NO new row. Reconstructing past state via "
            f"get_at() will return an incorrect value after this point. "
            f"Real call sites with the same pattern: see Phase 5."
        )
        findings["violations"].append("raw_update_one_silent_history_miss")

    # Cleanup so re-runs are deterministic.
    db[collection].delete_many({"_id": {"$regex": f"^{_TEST_PREFIX}"}})
    db[history_coll].delete_many(
        {"_original_id": {"$regex": f"^{_TEST_PREFIX}"}}
    )

    return findings


# ---------------------------------------------------------------------------
# Phase 3 — Layer 3: Derived state freshness.
#
# Write a fake derived doc whose _derived.derived_at is older than its
# freshness_sla. Check whether the codebase has any readers that honor
# `_derived.stale`. As of this commit: the schema defines the contract but
# no reader actually computes / honors it.
# ---------------------------------------------------------------------------

def phase_3_derived_state() -> dict:
    _banner("Phase 3 — Layer 3: Derived state freshness contract")

    db = mongo_tools.db()
    coll = "derived.skill_track_records"
    test_id = f"{_TEST_PREFIX}skill_track_record"

    # Clean prior test docs
    db[coll].delete_one({"_id": test_id})

    now = datetime.now(UTC)
    # Simulate a derived doc that's 25h old with a 24h SLA — should be stale.
    db[coll].insert_one({
        "_id": test_id,
        "skill_id": "linkedin_post",
        "metric_aggregates": {"brand_voice_mean": 0.78, "n": 47},
        "_workspace": _TEST_WORKSPACE,
        "_derived": {
            "derived_at": now - timedelta(hours=25),
            "derived_by": "service:e2e_memory_tiers",
            "derived_from": [
                {"kind": "bigquery_view", "id": "analytics.skill_track_record"},
                {"kind": "mongo_collection", "id": "skills"},
            ],
            "freshness_sla": "PT24H",
            "stale": False,   # The derive job left this False; readers must
                              # check derived_at + freshness_sla themselves.
        },
    })

    stored = db[coll].find_one({"_id": test_id})
    derived_at = stored["_derived"]["derived_at"]
    # Mongo strips tzinfo on round-trip; re-attach UTC.
    if derived_at.tzinfo is None:
        derived_at = derived_at.replace(tzinfo=UTC)
    sla_hours = 24  # parse PT24H — simple here
    age_hours = (now - derived_at).total_seconds() / 3600
    is_actually_stale = age_hours > sla_hours
    _ok(f"Test derived doc age={age_hours:.1f}h, sla={sla_hours}h, "
        f"actually stale={is_actually_stale}, _derived.stale field={stored['_derived']['stale']}")
    if is_actually_stale and stored["_derived"]["stale"] is False:
        _warn(
            "The derive job left _derived.stale=False. The architecture says "
            "readers must check `derived_at + freshness_sla` themselves. "
            "If readers naively trust `stale`, they consume stale data without "
            "knowing it. The derive cron should set `stale=True` AT WRITE TIME "
            "if it knows the source is degraded — or readers should ALWAYS "
            "compute freshness from derived_at + freshness_sla."
        )

    # Audit: who reads derived.* and do they check freshness?
    _sub("AUDIT — derived.* readers in the codebase")
    readers = _grep(
        ["derived\\.[a-z_]+|derived\\.skill_track_records|derived\\.icp_profiles"],
        roots=["services", "agents", "shared"],
    )
    freshness_aware = _grep(
        ["_derived.stale|freshness_sla|derived_at"],
        roots=["services", "agents", "shared"],
    )
    # Strip false positives (this script itself, history.py, schema.py docs).
    readers = [r for r in readers
               if "e2e_memory_tiers" not in r and "MEMORY_ARCHITECTURE" not in r]
    if not readers:
        _warn("No code currently reads from derived.* collections. The contract "
              "exists but is unconsumed — confirm whether the derive cron jobs "
              "are even running (services/derive_track_records is wired in "
              "deploy/04-schedulers.sh).")
    else:
        _info(f"Found {len(readers)} reader site(s) for derived.* collections:")
        for line in readers[:8]:
            _info(f"  {line}")
        if not freshness_aware:
            _warn("None of the reader sites check freshness (`_derived.stale`, "
                  "`freshness_sla`, or `derived_at`). Readers may silently "
                  "consume stale data.")

    db[coll].delete_one({"_id": test_id})

    return {
        "ok": True,
        "stale_field_inconsistent": is_actually_stale and stored["_derived"]["stale"] is False,
        "reader_sites": len(readers),
        "freshness_aware_sites": len(freshness_aware),
    }


# ---------------------------------------------------------------------------
# Phase 4 — Layer 4: Episodic memory (Vertex AI Memory Bank).
#
# memory_service() raises RuntimeError when AGENT_ENGINE_ID is unset. Confirm
# the failure mode is loud-at-call-site, not silent-corruption. Spot-check
# whether agent callers (research_agent's search_past_lessons, etc.) catch
# and log gracefully so a missing env var doesn't kill agent runs.
# ---------------------------------------------------------------------------

def phase_4_episodic_memory() -> dict:
    _banner("Phase 4 — Layer 4: Episodic memory (Vertex AI Memory Bank)")

    saved = os.environ.pop("AGENT_ENGINE_ID", None)
    try:
        from shared import memory as memory_mod
        # Clear the lru_cache so the next call re-evaluates AGENT_ENGINE_ID.
        memory_mod.memory_service.cache_clear()
        memory_mod.AGENT_ENGINE_ID = ""  # reflect env state inside module
        try:
            memory_mod.memory_service()
        except RuntimeError as e:
            _ok(f"memory_service() raised RuntimeError (expected): {str(e)[:80]}…")
        else:
            _fail("memory_service() did NOT raise without AGENT_ENGINE_ID. "
                  "This silently allows a misconfigured agent to no-op on "
                  "memory writes/reads — a real bug.")
            return {"ok": False}
    finally:
        if saved:
            os.environ["AGENT_ENGINE_ID"] = saved

    # Audit: count agent code sites that call into shared.memory. Each one
    # must either tolerate the RuntimeError or short-circuit on missing env.
    _sub("AUDIT — call sites of shared.memory (must handle RuntimeError)")
    memory_callers = _grep(
        ["from shared.memory|shared\\.memory\\.|search_past_lessons|remember_lesson|recall\\("],
        roots=["agents", "services"],
    )
    # The actual shared/memory.py module itself shouldn't count.
    memory_callers = [c for c in memory_callers if "shared/memory.py" not in c
                       and "shared\\memory.py" not in c]
    if not memory_callers:
        _warn("No agent or service references shared.memory. Memory Bank is "
              "defined but not wired up yet — Layer 4 is currently aspirational.")
    else:
        _info(f"Found {len(memory_callers)} site(s) referencing shared.memory:")
        for line in memory_callers[:8]:
            _info(f"  {line}")
    # Of those callers, who guards with a try/except or env-presence check?
    guarded = _grep(
        ["AGENT_ENGINE_ID|try:.*memory|except.*Memory|except RuntimeError"],
        roots=["agents", "services"],
    )
    if memory_callers and not guarded:
        _warn("Memory callers exist but no caller appears to guard the "
              "RuntimeError path. A missing AGENT_ENGINE_ID could crash agent "
              "runs in production. Verify each caller wraps its calls.")

    return {
        "ok": True,
        "caller_sites": len(memory_callers),
        "guarded_sites": len(guarded),
    }


# ---------------------------------------------------------------------------
# Phase 5 — Boundary-rule audit.
#
# Grep the codebase for direct `db["coll"].insert_one(` / `db["coll"].
# update_one(` calls on canonical state collections. The rule: those go
# through mongo/history.py. Append-only collections (actions, outcomes,
# skill_usage, ops_targets, paid_thresholds) are exempt.
# ---------------------------------------------------------------------------

_APPEND_ONLY_OR_EXEMPT = {
    "actions", "outcomes", "skill_usage", "paid_thresholds",
    "history",  # writes to history.* are always direct (that's the helper itself)
}


def phase_5_boundary_audit() -> dict:
    _banner("Phase 5 — Boundary-rule audit (raw mutations on canonical state)")

    # Find every direct mutation site.
    raw_writes = _grep(
        [r'db\[".*?"\]\.(insert_one|update_one|update_many|replace_one|delete_one|delete_many)\b'],
        roots=["services", "agents", "scripts", "shared"],
    )
    suspect: list[tuple[str, str]] = []
    for hit in raw_writes:
        # hit format: path:line:content
        path, _, rest = hit.partition(":")
        # Extract the collection inside db["..."]
        m = re.search(r'db\["([^"]+)"\]', rest)
        if not m:
            continue
        coll = m.group(1)
        if coll in _APPEND_ONLY_OR_EXEMPT or coll.startswith("history.") \
                or coll.startswith("derived."):
            continue
        # Filter out this audit script + the history.py helper + telemetry.py
        # which legitimately writes to actions/outcomes via append-only path.
        if any(x in path for x in [
            "e2e_memory_tiers", "mongo/history.py", "mongo\\history.py",
            "shared/telemetry.py", "shared\\telemetry.py",
        ]):
            continue
        # Lines explicitly tagged `# audit:exempt` are intentional bypasses
        # (e.g. test cleanup that wipes fixtures rather than supersedes
        # state). Honor the marker so the audit stays focused on real gaps.
        if "audit:exempt" in rest:
            continue
        suspect.append((hit, coll))

    if not suspect:
        _ok("No raw mutations on canonical state outside the history-aware path.")
    else:
        _warn(f"Found {len(suspect)} raw-mutation site(s) on canonical state. "
              f"These bypass mongo/history.py — no pre-image is captured.")
        by_coll: dict[str, list[str]] = defaultdict(list)
        for hit, coll in suspect:
            by_coll[coll].append(hit)
        for coll, hits in sorted(by_coll.items()):
            _info("")
            _info(f"  Collection: {coll!r}  ({len(hits)} site(s))")
            for h in hits:
                _info(f"    {h}")
    return {"ok": True, "violations": len(suspect)}


# ---------------------------------------------------------------------------
# Phase 6 — Provenance audit.
#
# For every canonical collection in Mongo, count how many docs carry a
# _provenance block. Report per-collection.
# ---------------------------------------------------------------------------

def phase_6_provenance_audit() -> dict:
    _banner("Phase 6 — Provenance audit (per-collection coverage)")

    from mongo.schema import COLLECTIONS

    # Append-only / pseudo-canonical collections that legitimately don't have
    # the full provenance block (they ARE the event log; their provenance is
    # their telemetry_id + ts).
    no_provenance_required = {"actions", "outcomes", "skill_usage",
                               "attribution_map", "paid_thresholds"}

    db = mongo_tools.db()
    coverage: dict[str, dict[str, int]] = {}
    for coll in COLLECTIONS:
        if coll in no_provenance_required:
            continue
        total = db[coll].count_documents({})
        with_prov = db[coll].count_documents({"_provenance": {"$exists": True}})
        coverage[coll] = {"total": total, "with_prov": with_prov,
                          "missing": total - with_prov}

    print(f"\n  {'collection':<28}{'total':>8}{'with _prov':>14}{'missing':>10}")
    print(f"  {'-' * 28}{'-' * 8}{'-' * 14}{'-' * 10}")
    total_missing = 0
    total_docs = 0
    for coll, c in sorted(coverage.items()):
        total_missing += c["missing"]
        total_docs += c["total"]
        flag = "" if c["missing"] == 0 else "  ⚠"
        print(f"  {coll:<28}{c['total']:>8}{c['with_prov']:>14}{c['missing']:>10}{flag}")
    print(f"  {'-' * 28}{'-' * 8}{'-' * 14}{'-' * 10}")
    print(f"  {'TOTAL':<28}{total_docs:>8}{total_docs - total_missing:>14}{total_missing:>10}")

    if total_missing > 0:
        _warn(
            f"{total_missing}/{total_docs} canonical docs are missing _provenance. "
            f"Architecture says every canonical doc MUST carry the block. "
            f"Likely causes: scripts/local_seed.py + mongo/cli.py use raw "
            f"insert_many; agent MCP calls insert without _provenance; the "
            f"web_api inserts negative_examples directly."
        )

    return {"ok": True, "coverage": coverage, "total_missing": total_missing}


# ---------------------------------------------------------------------------
# Phase 7 — Summary
# ---------------------------------------------------------------------------

def phase_7_summary(results: dict[str, dict]) -> int:
    _banner("Phase 7 — Summary")

    functional_phases = ["phase_1", "phase_2", "phase_3", "phase_4"]
    audit_phases = ["phase_5", "phase_6"]

    print()
    print(f"  Functional phases ({len(functional_phases)})")
    fn_ok = True
    for p in functional_phases:
        r = results.get(p, {})
        status = "OK " if r.get("ok") else "FAIL"
        if not r.get("ok"):
            fn_ok = False
        print(f"    {p:<10} {status}")

    print()
    print(f"  Audit phases ({len(audit_phases)}) — warn-only")
    audit_warnings = 0
    for p in audit_phases:
        r = results.get(p, {})
        n = r.get("violations", r.get("total_missing", 0)) or 0
        audit_warnings += n
        print(f"    {p:<10} {n} finding(s)")

    # Pull together all collected violation tags + latent bug reports.
    print()
    print("  Architectural violations surfaced this run")
    violations = results.get("phase_2", {}).get("violations", [])
    if not violations:
        print("    (none in phase 2)")
    for v in violations:
        print(f"    - {v}")

    print()
    print("  Latent bugs surfaced this run")
    latent = results.get("phase_2", {}).get("latent_bugs", [])
    if not latent:
        print("    (none)")
    for bug in latent:
        print(f"    ⚠ {bug['where']}")
        print(f"       symptom: {bug['symptom']}")
        print(f"       fix:     {bug['fix']}")

    print()
    if fn_ok:
        _ok("All functional phases passed.")
        return 0
    else:
        _fail("One or more functional phases failed.")
        return 1


# ---------------------------------------------------------------------------
# Helpers — minimal grep wrapper. We use python rather than shelling out so
# the script is portable to Windows-only operators without ripgrep.
# ---------------------------------------------------------------------------

def _grep(patterns: list[str], roots: list[str]) -> list[str]:
    """Return list of 'path:line:content' for every line matching any pattern
    under any root. Restricted to .py files."""
    compiled = [re.compile(p) for p in patterns]
    out: list[str] = []
    for root in roots:
        root_path = REPO_ROOT / root
        if not root_path.is_dir():
            continue
        for py in root_path.rglob("*.py"):
            try:
                text = py.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for i, line in enumerate(text.splitlines(), start=1):
                if any(rx.search(line) for rx in compiled):
                    rel = py.relative_to(REPO_ROOT).as_posix()
                    out.append(f"{rel}:{i}: {line.strip()[:120]}")
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--phases", default=None,
                   help="Comma-separated subset of phase numbers (1-6). "
                        "Phase 7 (summary) always runs.")
    p.add_argument("--strict", action="store_true",
                   help="Treat audit findings as failures (exit 1 on any).")
    args = p.parse_args()

    all_phases = ["1", "2", "3", "4", "5", "6"]
    selected = args.phases.split(",") if args.phases else all_phases
    selected = [s.strip() for s in selected if s.strip()]
    unknown = [s for s in selected if s not in all_phases]
    if unknown:
        print(f"ERROR: unknown phase(s): {unknown}. Known: {all_phases}",
              file=sys.stderr)
        return 1

    print("=" * 72)
    print("  E2E MEMORY TIERS TEST")
    print("=" * 72)
    print(f"  Mongo URI:        {os.environ['MONGO_URI_DIRECT']}")
    print(f"  Mongo DB:         {os.environ['MONGO_DB']}")
    print(f"  Selected phases:  {selected}")
    print(f"  Strict mode:      {args.strict}")
    print("=" * 72)

    results: dict[str, dict] = {}
    started = time.monotonic()
    try:
        if "1" in selected:
            results["phase_1"] = phase_1_event_log()
        if "2" in selected:
            results["phase_2"] = phase_2_canonical_state()
        if "3" in selected:
            results["phase_3"] = phase_3_derived_state()
        if "4" in selected:
            results["phase_4"] = phase_4_episodic_memory()
        if "5" in selected:
            results["phase_5"] = phase_5_boundary_audit()
        if "6" in selected:
            results["phase_6"] = phase_6_provenance_audit()
    except Exception:
        import traceback
        traceback.print_exc()
        return 1

    rc = phase_7_summary(results)

    elapsed = time.monotonic() - started
    print(f"\n  Elapsed: {elapsed:.1f}s")

    if args.strict:
        audit_findings = (
            results.get("phase_5", {}).get("violations", 0)
            + results.get("phase_6", {}).get("total_missing", 0)
        )
        if audit_findings > 0:
            print(f"  --strict: {audit_findings} audit finding(s) → exit 1",
                  file=sys.stderr)
            return 1
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
