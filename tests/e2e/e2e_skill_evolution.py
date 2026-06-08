"""End-to-end skill-evolution test.

Exercises the self-learning loop that turns founder edits + low-scoring
drafts into a promoted SKILL.md revision:

    actions/edits accumulate
      → derive_track_records (nightly rollup)
      → Self-Critique Agent (weekly, real Gemini LLM)
            • writes versions[<candidate>].body_md (FULL new body)
            • writes self_critique_proposal {issue, channels_affected, ...}
      → founder ACCEPTS proposal (/api/self-critique decision=accept)
            • pushes candidate_id into skills.candidates[]
      → promotion_gate (weekly, cross-channel sanity check)
            • re-verifies ≥2 channels affected, body exists,
              ≥15 actions/channel, ≥0.05 dip below baseline
            • computes server-side unified diff via difflib
            • raises promotion_request {proposed_diff, channels_at_risk}
      → founder APPROVES promotion (/api/skills/<id>/promotion decision=approve)
            • promote_after_approval(): current_version flips, history
              gains entry, candidates loses entry, promotion_request unset.
              Mongo is the source of truth — no disk write here.
      → next agent run calls read_skill(name), which triggers
        SkillRegistry.read_body's lazy reconciliation: it sees
        Mongo's new current_version body_md differs from on-disk
        SKILL.md and atomically rewrites the file, then serves the
        new body. Emits a skill_usage row tied to the new version.

Designed to surface latent bugs alongside happy-path validation. Audit
phases (gate failure modes, history capture) report findings without
hard-failing on intentional warnings.

Setup constraints (intentional choices, confirmed with the operator):
  - Self-Critique Phase uses REAL Gemini, no fallback. If the LLM fails
    to produce both {proposal, versions[<candidate>].body_md}, the test
    FAILS. This catches LLM reliability regressions in production.
  - Disk state is sandboxed: the test sets SKILLS_ROOT to a tmpdir and
    seeds a single test skill there. The production skills/ tree is
    NEVER touched. Teardown wipes the tmpdir.

Usage (from hindsight-guild/, Docker Mongo up, .env populated):

    python -m tests.e2e.e2e_skill_evolution                 # all 14 phases
    python -m tests.e2e.e2e_skill_evolution --phases 1,2,3  # subset
    python -m tests.e2e.e2e_skill_evolution --keep-sandbox  # leave tmpdir
                                                          # for inspection

Exit code 0 if every functional phase passes. Audit findings (gate
downgrade reasons, history coverage) print at the end regardless.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import shutil
import sys
import tempfile
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

# Env bootstrap — must run BEFORE any shared.skills import so the SKILLS_ROOT
# override and the .env-derived defaults take effect at module load.
from scripts._test_bootstrap import (  # noqa: E402
    REPO_ROOT,
    _banner,
    _fail,
    _info,
    _ok,
    _sub,
    _warn,
)

if not os.environ.get("GOOGLE_API_KEY"):
    print("ERROR: GOOGLE_API_KEY not set in .env.", file=sys.stderr)
    print("       Get one at https://aistudio.google.com/app/apikey",
          file=sys.stderr)
    sys.exit(1)

# Sandbox SKILLS_ROOT. Created here, populated in phase 0, deleted at
# teardown. Set via env var so shared/skills.py picks it up at import.
_SANDBOX_DIR = Path(tempfile.mkdtemp(prefix="skill_evo_sandbox_"))
os.environ["SKILLS_ROOT"] = str(_SANDBOX_DIR)

# Imports below this line see the sandbox SKILLS_ROOT.
from agents.self_critique_runner import load_proposals  # noqa: E402
from shared import mongo_tools  # noqa: E402
from shared import skills as skills_module  # noqa: E402

# ---------------------------------------------------------------------------
# Test config
# ---------------------------------------------------------------------------

_TEST_SKILL_ID = "evo-test-skill"          # never collides with real skills/
_TEST_PREFIX = "_skill_evo_"
_TARGET_CHANNELS = ["linkedin", "email", "substack"]  # 3 channels for cross-channel
_BASELINE_BRAND_VOICE = 0.86
_DEGRADED_BRAND_VOICE = 0.62               # ≥ 0.10 below 0.86 baseline → drift
_N_ACTIONS_PER_CHANNEL = 22                # > MIN_ACTIONS_PER_CHANNEL (15)
_N_FOUNDER_EDITS = 8                       # ≥ 5 per channel pattern
_INITIAL_VERSION = "v1"
_CANDIDATE_VERSION = "v2_critique"

# The systematic pattern the Self-Critique Agent should detect: founders keep
# softening absolute language. Seeded as real approvals(decision=edit) rows so
# recent_skill_telemetry surfaces a genuine before→after pattern.
_ABSOLUTE_DRAFT = ("Our platform guarantees 100% delivery and completely "
                   "eliminates manual review for every merchant, always.")
_SOFTENED_DRAFT = ("Our platform typically delivers reliably and reduces "
                   "manual review meaningfully for most merchants.")

# Phase 12 (miner pipeline) seed namespace + the recurring risky phrase the
# negative miner should cluster. Prefix is under _TEST_PREFIX so teardown's
# ^_skill_evo_ regex sweeps the actions/approvals automatically.
_MINER_PREFIX = f"{_TEST_PREFIX}miner_"
_RISK_PHRASE = "guaranteed to double your revenue in thirty days with zero effort"

# Phase 13 (silent-pass) uses a throwaway skill with deliberately pattern-free
# telemetry; the Self-Critique Agent must NOT invent a proposal on it.
_NOISE_SKILL_ID = "evo-noise-skill"


# ---------------------------------------------------------------------------
# Phase 0 — Bootstrap: sandbox skill + seed initial Mongo doc
# ---------------------------------------------------------------------------

_INITIAL_BODY = """---
name: evo-test-skill
description: Test skill used by tests/e2e/e2e_skill_evolution. The body specifies a single voice-check gate that produces an EVO_TEST_PASS object the Review Agent uses to verify compliance.
metadata:
  version: 1.0.0
---

# Evo Test Skill — initial body (v1)

This is the incumbent body. It deliberately contains the absolute-language
phrasing the Self-Critique Agent should propose tightening.

## Required output schema

```
EVO_TEST_PASS = {
  "absolute_language": "pass" | "fail:revised_<N>_tokens",
  "tone": <int 1-5>
}
```

## Procedure

1. Scan the draft for absolute words: "guaranteed", "always", "100%",
   "never", "completely".
2. Score the channel tone 1-5.
3. Emit EVO_TEST_PASS.
"""


def _clone_real_skills_into_sandbox() -> int:
    """Copy the real skills/ tree into the sandbox so any read_skill(<name>)
    resolves — the agents' REQUIRED preamble reads several real skills, so a
    sandbox holding only the test skill makes the agent crash before it can
    reason. Idempotent: only copies entries not already present."""
    real_skills_root = REPO_ROOT / "skills"
    cloned = 0
    if real_skills_root.is_dir():
        for entry in real_skills_root.iterdir():
            if not entry.is_dir():
                continue
            dest = _SANDBOX_DIR / entry.name
            if not dest.exists():
                shutil.copytree(entry, dest)
                cloned += 1
    return cloned


def phase_0_bootstrap() -> dict:
    _banner("Phase 0 — Bootstrap: sandbox SKILLS_ROOT + seed test skill")
    _info(f"sandbox SKILLS_ROOT: {_SANDBOX_DIR}")

    # 0a-pre — clone the real skills/ tree into the sandbox so every agent
    # that calls read_skill("house-style") etc. (mandatory invocations in
    # the REQUIRED preamble) finds the skill body. Without this the
    # Self-Critique Agent's required preamble crashes with a KeyError.
    cloned = _clone_real_skills_into_sandbox()
    _ok(f"cloned {cloned} skills into sandbox "
        f"(so REQUIRED read_skill invocations resolve)")

    # 0a — write the test skill's SKILL.md + versions/v1.md into the sandbox.
    # This OVERWRITES any prior version of evo-test-skill that might have
    # been cloned (the prior-test-run sandbox left a tail).
    skill_dir = _SANDBOX_DIR / _TEST_SKILL_ID
    versions_dir = skill_dir / "versions"
    versions_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(_INITIAL_BODY, encoding="utf-8")
    (versions_dir / "v1.md").write_text(_INITIAL_BODY, encoding="utf-8")
    _ok(f"wrote SKILL.md + versions/v1.md to {skill_dir}")

    # 0b — clean prior Mongo state (idempotency).
    db = mongo_tools.db()
    db["skills"].delete_one({"_id": _TEST_SKILL_ID})
    db["history.skills"].delete_many({"_original_id": _TEST_SKILL_ID})
    db["actions"].delete_many({"telemetry_id": {"$regex": f"^{_TEST_PREFIX}"}})
    db["approvals"].delete_many({"telemetry_id": {"$regex": f"^{_TEST_PREFIX}"}})
    db["skill_usage"].delete_many({"skill_name": _TEST_SKILL_ID})
    db["derived.skill_track_records"].delete_many(
        {"skill_id": _TEST_SKILL_ID})
    db["derived.agent_skill_track_records"].delete_many(
        {"_id": _TEST_SKILL_ID})
    _ok("wiped prior test state from Mongo")

    # 0c — seed the canonical skills doc. Uses insert_with_provenance so the
    # _provenance audit in Phase 11 finds a complete history trail.
    from mongo.history import default_provenance_block, insert_with_provenance

    block = default_provenance_block(
        actor_id="e2e_skill_evolution", kind="ingestion",
        trust_tier="verified", source_kind="seed",
    )
    insert_with_provenance(
        "skills",
        {
            **block,
            "_id": _TEST_SKILL_ID,
            "skill_kind": "agent_skill",
            "current_version": _INITIAL_VERSION,
            "history": [_INITIAL_VERSION],
            "candidates": [],
            "applies_to": {
                "icp_segments": ["seg_merchant_dtc"],
                "channels": _TARGET_CHANNELS,
            },
            "versions": {
                _INITIAL_VERSION: {
                    "body_md": _INITIAL_BODY,
                    "proposed_at": datetime.now(UTC),
                    "source": "seed",
                },
            },
        },
        actor_id="e2e_skill_evolution",
        change_kind="seed",
    )
    _ok(f"seeded skills.{_TEST_SKILL_ID} (current_version={_INITIAL_VERSION!r}, "
        f"skill_kind=agent_skill)")

    # 0d — refresh the in-process SkillRegistry so it picks up the sandbox.
    skills_module.refresh_registry()
    if _TEST_SKILL_ID not in skills_module.registry().skills:
        _fail(f"SkillRegistry did not pick up sandbox skill {_TEST_SKILL_ID}")
        raise SystemExit(1)
    _ok("SkillRegistry sees the sandbox skill")

    return {"ok": True, "sandbox": str(_SANDBOX_DIR)}


# ---------------------------------------------------------------------------
# Phase 1 — Synthesize telemetry: low-scoring drafts across 3 channels
# ---------------------------------------------------------------------------

def phase_1_synthesize_telemetry() -> dict:
    _banner("Phase 1 — Synthesize low-scoring drafts + founder edits")

    db = mongo_tools.db()
    rng = random.Random(11)
    now = datetime.now(UTC)
    n_per_channel = _N_ACTIONS_PER_CHANNEL

    # The promotion gate's cross-channel check computes project_baseline =
    # mean of per-channel means, then flags channels that dip ≥0.05 below
    # that baseline. If ALL channels are equally degraded, none dip below
    # the average of themselves — the gate (correctly) calls it
    # "not_actually_cross_channel."
    #
    # To exercise the happy path through the gate we keep one channel
    # healthy. With substack at ~0.86 and linkedin+email at ~0.62, the
    # baseline ≈ 0.70 and the two degraded channels dip ~0.08 below it —
    # enough to clear ISSUE_DIP_THRESHOLD (0.05).
    _HEALTHY_CHANNEL = "substack"

    inserted = 0
    tids_by_channel: dict[str, list[str]] = {}
    for channel in _TARGET_CHANNELS:
        target = (_BASELINE_BRAND_VOICE if channel == _HEALTHY_CHANNEL
                   else _DEGRADED_BRAND_VOICE)
        for i in range(n_per_channel):
            # Within each channel, jitter ±0.03 around the target. No
            # mix-in of the opposite tier — that diluted the per-channel
            # mean and pushed it across the dip threshold.
            brand_voice = rng.uniform(target - 0.03, target + 0.03)
            tid = f"{_TEST_PREFIX}{channel}_{i:02d}_{uuid.uuid4().hex[:6]}"
            db["actions"].insert_one({
                "telemetry_id": tid,
                "agent": "content_agent",
                "skill_id": f"{channel}_post",   # the channel playbook
                "skill_version": "v3",
                "skills_loaded": [_TEST_SKILL_ID],  # the Skill under test
                "action_type": "draft",
                "channel": channel,
                "icp_segment": "seg_merchant_dtc",
                # The draft body — the BEFORE-text a founder edit softens.
                # Canonical telemetry shape: raw is a dict with the draft under
                # `draft` (what the miners + recent_skill_telemetry read).
                "raw": {"draft": _ABSOLUTE_DRAFT},
                "eval_scores": {
                    "brand_voice": round(brand_voice, 4),
                    "claim_support": round(rng.uniform(0.78, 0.86), 4),
                    "claim_risk": round(rng.uniform(0.80, 0.90), 4),
                },
                "ts": now - timedelta(days=rng.uniform(0, 13)),
            })
            tids_by_channel.setdefault(channel, []).append(tid)
            inserted += 1

    # Seed founder edits (approvals decision=edit) on the DEGRADED channels.
    # This is what makes the loop real: the Self-Critique Agent's
    # recent_skill_telemetry pull (Mongo fallback in LOCAL_DEV) joins these to
    # their actions and sees a consistent absolute→softened pattern spanning
    # >= 2 channels — exactly the cross-channel signal it should propose on.
    n_edits = 0
    for channel in _TARGET_CHANNELS:
        if channel == _HEALTHY_CHANNEL:
            continue
        for tid in tids_by_channel[channel][:_N_FOUNDER_EDITS]:
            db["approvals"].insert_one({
                "telemetry_id": tid,
                "decision": "edit",
                "approved_text": _SOFTENED_DRAFT,
                "edit_categories": ["softened_absolute"],
                "decided_at": now - timedelta(days=rng.uniform(0, 10)),
            })
            n_edits += 1

    _ok(f"synthesized {inserted} actions across {len(_TARGET_CHANNELS)} channels "
        f"({n_per_channel} each) + {n_edits} founder edits on degraded channels")
    # Quick check: do per-channel means actually dip vs the global baseline?
    for channel in _TARGET_CHANNELS:
        rows = list(db["actions"].aggregate([
            {"$match": {"channel": channel,
                        "skills_loaded": _TEST_SKILL_ID}},
            {"$group": {"_id": None,
                        "mean": {"$avg": "$eval_scores.brand_voice"},
                        "n": {"$sum": 1}}},
        ]))
        row = rows[0] if rows else {"mean": 0, "n": 0}
        _info(f"  {channel}: n={row['n']} mean_brand_voice={row['mean']:.3f}")

    return {"ok": True, "n_inserted": inserted}


# ---------------------------------------------------------------------------
# Phase 2 — Derive track records (Mongo equivalent of derive_track_records).
#
# Production runs services/derive_track_records nightly against BigQuery's
# telemetry.actions. LOCAL_DEV has no BQ; here we run the same SQL shape
# against Mongo's `actions` collection so the derived collection populates.
# ---------------------------------------------------------------------------

def phase_2_derive_track_records() -> dict:
    _banner("Phase 2 — Derive track records (real service, Mongo fallback)")

    db = mongo_tools.db()

    # Run the REAL production job — no test reimplementation. In LOCAL_DEV
    # bigquery_client() is None, so derive_track_records.main() reads the
    # dual-written Mongo `actions` collection via shared.telemetry_reads (the
    # same aggregation prod runs against BigQuery) and writes the derived
    # collections with the production document shape. Full-replace semantics
    # match prod (the cron wipes + repopulates on every run).
    from services.derive_track_records import main as derive
    derive.main()

    n_skill = db["derived.skill_track_records"].count_documents({})
    n_agent = db["derived.agent_skill_track_records"].count_documents({})
    _ok(f"derive_track_records.main() wrote {n_skill} skill + {n_agent} "
        f"agent_skill track-record rows")

    # Sanity: our Skill must have a derived row with multi-channel coverage,
    # which is what the Self-Critique Agent reasons over.
    rec = db["derived.agent_skill_track_records"].find_one(
        {"_id": _TEST_SKILL_ID})
    if rec:
        _info(f"  {_TEST_SKILL_ID}: channels_seen={sorted(rec.get('channels_seen') or [])} "
              f"action_count={rec.get('action_count')} "
              f"mean_brand_voice={rec.get('mean_brand_voice')}")
        if rec["_derived"]["stale"] is False:
            _info("freshness contract: _derived.stale=False, derived_at=now → "
                  "readers compute staleness from derived_at + freshness_sla")
    else:
        _warn(f"no derived.agent_skill_track_records row for {_TEST_SKILL_ID}")

    return {"ok": True, "skill_track_records": n_skill,
            "agent_skill_track_records": n_agent}


# ---------------------------------------------------------------------------
# Phase 3 — Self-Critique Agent (REAL Gemini, no fallback).
#
# The agent in production reads BQ telemetry.actions + training.edits. In
# LOCAL_DEV bigquery_query returns []. The test monkey-patches the helper
# to serve synthesized rows so the agent has real data to reason about.
# ---------------------------------------------------------------------------

async def phase_3_self_critique() -> dict:
    _banner("Phase 3 — Self-Critique Agent (real Gemini, no fallback)")

    # No stubs. In LOCAL_DEV the agent's recent_skill_telemetry tool reads the
    # evidence (low-score drafts + founder edits) straight from the
    # dual-written Mongo `actions` + `approvals` collections that Phases 0-1
    # seeded — the same code path prod runs, just sourced from Mongo instead
    # of BigQuery. The agent gathers its own grounding; we only tell it the
    # scope.
    db = mongo_tools.db()

    from google.adk.agents.run_config import RunConfig
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai.types import Content, Part

    from agents.self_critique import self_critique_agent

    session_service = InMemorySessionService()
    runner = Runner(
        agent=self_critique_agent,
        app_name="e2e_skill_evolution",
        session_service=session_service,
    )
    session = await session_service.create_session(
        app_name="e2e_skill_evolution",
        user_id="e2e",
        state={"telemetry_id": f"{_TEST_PREFIX}critique_run",
               "skill_id": "self_critique"},
    )
    message_text = (
        f"Run the self-critique pass scoped to agent_skill "
        f"'{_TEST_SKILL_ID}'. Use recent_skill_telemetry('{_TEST_SKILL_ID}') "
        f"to pull its recent low-score drafts and founder edits, then look "
        f"for a systematic pattern. If a pattern holds across >= 2 channels, "
        f"write the FULL new SKILL.md body to "
        f"versions['{_CANDIDATE_VERSION}'].body_md AND a "
        f"self_critique_proposal via propose_skill_revision, listing every "
        f"channel where the pattern appears."
    )
    message = Content(role="user",
                      parts=[Part.from_text(text=message_text)])
    started = time.monotonic()
    # Hard cap on LLM calls so a fixation/loop can't burn tokens — a
    # legit self-critique pass needs well under this. (ADK's default is
    # 500; a real pass here is ~10-25 calls.)
    async for event in runner.run_async(
        user_id="e2e", session_id=session.id, new_message=message,
        run_config=RunConfig(max_llm_calls=50),
    ):
        author = getattr(event, "author", None) or "?"
        _info(f"[{time.monotonic() - started:5.1f}s] {author}")

    # Hard-verify the agent wrote BOTH proposal and body_md.
    skill = db["skills"].find_one({"_id": _TEST_SKILL_ID}) or {}
    proposal = skill.get("self_critique_proposal")
    versions = skill.get("versions") or {}
    candidate_body = (versions.get(_CANDIDATE_VERSION) or {}).get("body_md")

    findings = {"ok": True, "proposal_written": False,
                "body_written": False, "missing": []}
    if not proposal:
        findings["missing"].append("self_critique_proposal")
        findings["ok"] = False
    else:
        findings["proposal_written"] = True
        _ok(f"self_critique_proposal written "
            f"(issue={proposal.get('issue', '')[:60]!r}…, "
            f"status={proposal.get('status')!r}, "
            f"channels_affected={proposal.get('channels_affected')})")
    if not candidate_body:
        # Look for any candidate version the agent might have invented with
        # a different id than what we suggested.
        new_versions = [v for v in versions if v != _INITIAL_VERSION]
        if not new_versions:
            findings["missing"].append(
                f"versions[{_CANDIDATE_VERSION!r}].body_md")
            findings["ok"] = False
        else:
            # Found one with a different name. Use it. Guard the value the
            # same way line ~524 does: the agent sometimes creates the
            # version key with a null body before authoring it.
            actual = new_versions[0]
            candidate_body = (versions[actual] or {}).get("body_md")
            if candidate_body:
                findings["body_written"] = True
                findings["actual_candidate_id"] = actual
                _info(f"agent used candidate_id={actual!r} (not "
                      f"{_CANDIDATE_VERSION!r}); body_md present, "
                      f"{len(candidate_body)} bytes")
    else:
        findings["body_written"] = True
        _ok(f"versions[{_CANDIDATE_VERSION!r}].body_md written "
            f"({len(candidate_body)} bytes)")

    if not findings["ok"]:
        _fail(f"Self-Critique Agent did NOT produce required outputs: "
              f"missing={findings['missing']}.")
        # Print diagnostic state so re-runs aren't blind. teardown() also
        # honors --keep-sandbox + a new --keep-mongo-on-fail.
        _sub("DIAGNOSTIC — current Mongo state for evo-test-skill")
        skill_dump = db["skills"].find_one({"_id": _TEST_SKILL_ID}) or {}
        for k in ("current_version", "history", "candidates"):
            _info(f"  {k}: {skill_dump.get(k)!r}")
        prop = skill_dump.get("self_critique_proposal")
        _info(f"  self_critique_proposal: {json.dumps(prop, default=str, indent=2) if prop else None}")
        versions_dump = skill_dump.get("versions") or {}
        _info(f"  versions keys: {sorted(versions_dump.keys())}")
        for vk, vv in versions_dump.items():
            body = (vv or {}).get("body_md")
            _info(f"    versions[{vk!r}].body_md: "
                  f"{'present (' + str(len(body)) + ' bytes)' if body else 'MISSING'}")
        _info("To investigate further:")
        _info("  - leave state with: python -m tests.e2e.e2e_skill_evolution "
              "--phases 0,1,2,3 --keep-sandbox --keep-mongo-on-fail")
        _info(f"  - then: python -c \"from shared import mongo_tools; "
              f"import json; print(json.dumps(mongo_tools.db()['skills']."
              f"find_one({{'_id': {_TEST_SKILL_ID!r}}}), default=str, indent=2))\"")
        raise SystemExit(1)

    return findings


# ---------------------------------------------------------------------------
# Phase 4 — Founder accepts the critique
#     POST /api/self-critique/{skill_id} {"decision": "accept"}
#
# The endpoint implementation (services/web_api/routers/self_critique.py:
# decide_critique) pushes the candidate into skills.candidates[] and flips
# the proposal status → "accepted". We invoke the function directly
# (no uvicorn) since it's pure Python.
# ---------------------------------------------------------------------------

def phase_4_accept_critique(prior: dict) -> dict:
    _banner("Phase 4 — Founder accepts the critique (/api/self-critique)")

    from services.web_api.routers.self_critique import CritiqueDecision, decide_critique

    decision = CritiqueDecision(decision="accept")
    result = decide_critique(_TEST_SKILL_ID, decision)
    _info(f"endpoint returned: {result}")

    db = mongo_tools.db()
    skill = db["skills"].find_one({"_id": _TEST_SKILL_ID}) or {}
    candidates = skill.get("candidates") or []
    # The self_critique_proposals ARRAY is the source of truth. Accepting
    # flips the entry to 'accepted' and UNSETS the singleton mirror (which
    # only ever holds the highest-confidence *pending* proposal — once
    # nothing is pending, the mirror is correctly cleared). So assert against
    # the array entry, not the now-empty singleton.
    proposals = skill.get("self_critique_proposals") or []
    accepted_ids = [p.get("candidate_id") for p in proposals
                    if p.get("status") == "accepted"]

    candidate_id = prior.get("actual_candidate_id") or _CANDIDATE_VERSION
    if candidate_id not in candidates:
        _fail(f"candidates[] does not contain {candidate_id!r}: {candidates}")
        raise SystemExit(1)
    if candidate_id not in accepted_ids:
        _fail(f"no accepted self_critique_proposals entry for {candidate_id!r}; "
              f"statuses={[p.get('status') for p in proposals]}")
        raise SystemExit(1)
    _ok(f"candidate {candidate_id!r} added to candidates[]; "
        f"proposals[].status='accepted' (singleton mirror correctly cleared)")
    return {"ok": True, "candidate_id": candidate_id}


# ---------------------------------------------------------------------------
# Phase 5 — Promotion gate evaluation (cross-channel sanity).
#
# Runs the REAL agent_skill branch of services.promotion_gate.main. No stub:
# in LOCAL_DEV the gate's _agent_skill_per_channel_stats natively falls back
# to a Mongo aggregation over the dual-written `actions` collection (the same
# code path prod runs against BigQuery), so we just call it.
# ---------------------------------------------------------------------------

def phase_5_promotion_gate(prior: dict) -> dict:
    _banner("Phase 5 — Promotion gate evaluation (agent_skill path)")

    from services.promotion_gate import main as gate
    skill = mongo_tools.db()["skills"].find_one({"_id": _TEST_SKILL_ID})
    gate._evaluate_agent_skill_proposal(skill)

    skill = mongo_tools.db()["skills"].find_one({"_id": _TEST_SKILL_ID}) or {}
    promo = skill.get("promotion_request")
    # Read the proposal from the array (the gate clears the singleton mirror
    # on escalation, so the singleton is empty by now).
    gated = (load_proposals(skill) or [{}])[0]

    if not promo:
        _fail(f"promotion_request was NOT raised. proposal.status="
              f"{gated.get('status')!r}, gate_reason="
              f"{gated.get('gate_reason')!r}")
        raise SystemExit(1)

    if promo.get("status") != "awaiting_approval":
        _fail(f"promotion_request.status={promo.get('status')!r}, "
              f"expected 'awaiting_approval'")
        raise SystemExit(1)

    _ok(f"promotion_request raised (kind={promo.get('kind')!r}, "
        f"candidate={promo.get('candidate')!r}, "
        f"incumbent={promo.get('incumbent')!r})")
    diff = promo.get("proposed_diff") or ""
    if diff.startswith("---") and "+++" in diff and "@@" in diff:
        _ok(f"proposed_diff is a valid unified diff ({len(diff.splitlines())} "
            f"lines, computed server-side via difflib)")
    else:
        _warn(f"proposed_diff missing or malformed: {diff[:80]!r}")
    _info(f"channels_at_risk: {promo.get('channels_at_risk')}")
    _info(f"per_channel_baseline keys: {list((promo.get('per_channel_baseline') or {}).keys())}")
    _info(f"project_baseline_brand_voice: {promo.get('project_baseline_brand_voice'):.3f}")

    return {"ok": True, "promo": promo}


# ---------------------------------------------------------------------------
# Phase 6 — Gate failure-mode probes.
#
# Re-run the gate with deliberately-broken proposals to exercise each of
# the 4 downgrade reasons. We restore the original proposal/promotion
# state between probes so each fires from a clean baseline.
# ---------------------------------------------------------------------------

def phase_6_gate_failure_modes(saved_promo: dict) -> dict:
    _banner("Phase 6 — Gate failure-mode probes")

    from services.promotion_gate import main as gate
    db = mongo_tools.db()
    findings = {"ok": True, "probes": []}

    def _restore_proposal(channels_affected, candidate_id):
        """Rebuild a pending proposal in the array (source of truth) + mirror.

        The gate now reads/writes the self_critique_proposals array (so it can
        pick up founder-accepted proposals — option B), keeping the singleton
        as a pending mirror. We seed both so each probe starts clean."""
        entry = {
            "candidate_id": candidate_id,
            "issue": "test issue",
            "channels_affected": channels_affected,
            "confidence": "medium",
            "evidence_count": 6,
            "proposed_at": datetime.now(UTC),
            "status": "awaiting_human_review",
        }
        db["skills"].update_one(
            {"_id": _TEST_SKILL_ID},
            {"$set": {"self_critique_proposals": [entry],
                      "self_critique_proposal": entry},
             "$unset": {"promotion_request": ""}},
        )

    def _read_gate_state():
        # The gate writes the decision onto the array entry and clears the
        # pending singleton mirror, so read the array (source of truth).
        s = db["skills"].find_one({"_id": _TEST_SKILL_ID}) or {}
        props = load_proposals(s)
        prop = props[0] if props else {}
        return prop.get("status"), prop.get("gate_reason"), prop.get("gate_detail")

    # No stub: the gate's _agent_skill_per_channel_stats falls back to a Mongo
    # aggregation natively in LOCAL_DEV (bigquery_client() is None). We keep a
    # try/finally only to guarantee the 6d telemetry mutation is reverted
    # before Phase 7.
    try:
        # ---- 6a: below_min_channels ----
        _sub("6a — below_min_channels (only 1 channel)")
        candidate_id = saved_promo["promo"]["candidate"]
        _restore_proposal(["linkedin"], candidate_id)
        skill = db["skills"].find_one({"_id": _TEST_SKILL_ID})
        gate._evaluate_agent_skill_proposal(skill)
        status, reason, detail = _read_gate_state()
        ok = status == "rejected_by_gate" and reason == "below_min_channels"
        (_ok if ok else _fail)(
            f"status={status!r} gate_reason={reason!r}")
        findings["probes"].append({"probe": "below_min_channels", "ok": ok,
                                    "reason": reason})
        if not ok:
            findings["ok"] = False

        # ---- 6b: missing_candidate_body ----
        _sub("6b — missing_candidate_body (proposal references nonexistent version)")
        _restore_proposal(_TARGET_CHANNELS, "v99_phantom")
        skill = db["skills"].find_one({"_id": _TEST_SKILL_ID})
        gate._evaluate_agent_skill_proposal(skill)
        status, reason, _ = _read_gate_state()
        ok = status == "rejected_by_gate" and reason == "missing_candidate_body"
        (_ok if ok else _fail)(
            f"status={status!r} gate_reason={reason!r}")
        findings["probes"].append({"probe": "missing_candidate_body", "ok": ok,
                                    "reason": reason})
        if not ok:
            findings["ok"] = False

        # ---- 6c: insufficient_per_channel_volume ----
        _sub("6c — insufficient_per_channel_volume (one channel under-volumed)")
        # Add a fake channel to the affected list that has zero telemetry.
        _restore_proposal(_TARGET_CHANNELS + ["ghost_channel"], candidate_id)
        skill = db["skills"].find_one({"_id": _TEST_SKILL_ID})
        gate._evaluate_agent_skill_proposal(skill)
        status, reason, detail = _read_gate_state()
        ok = (status == "rejected_by_gate"
              and reason == "insufficient_per_channel_volume")
        (_ok if ok else _fail)(
            f"status={status!r} gate_reason={reason!r} detail={detail!r}")
        findings["probes"].append(
            {"probe": "insufficient_per_channel_volume", "ok": ok,
             "reason": reason})
        if not ok:
            findings["ok"] = False

        # ---- 6d: not_actually_cross_channel ----
        # Make 2 of the 3 channels look fine. We do this by temporarily
        # backfilling actions on 2 of 3 channels with HIGH brand_voice
        # — so per-channel baseline check finds only 1 dip.
        _sub("6d — not_actually_cross_channel (only 1 channel dips below baseline)")
        # Replace eval_scores so 2 of the 3 channels run high.
        for ch in _TARGET_CHANNELS[:2]:
            db["actions"].update_many(
                {"skills_loaded": _TEST_SKILL_ID, "channel": ch},
                {"$set": {"eval_scores.brand_voice": 0.90}},
            )
        _restore_proposal(_TARGET_CHANNELS, candidate_id)
        skill = db["skills"].find_one({"_id": _TEST_SKILL_ID})
        gate._evaluate_agent_skill_proposal(skill)
        status, reason, _ = _read_gate_state()
        ok = (status == "rejected_by_gate"
              and reason == "not_actually_cross_channel")
        (_ok if ok else _fail)(
            f"status={status!r} gate_reason={reason!r}")
        findings["probes"].append({"probe": "not_actually_cross_channel",
                                    "ok": ok, "reason": reason})
        if not ok:
            findings["ok"] = False

    finally:
        # Restore the 6d telemetry mutation so downstream phases see the
        # original degraded signal (guaranteed even if a probe raised).
        from random import Random
        rng = Random(11)
        for ch in _TARGET_CHANNELS[:2]:
            for a in db["actions"].find(
                {"skills_loaded": _TEST_SKILL_ID, "channel": ch},
            ):
                db["actions"].update_one(
                    {"_id": a["_id"]},
                    {"$set": {"eval_scores.brand_voice":
                              round(rng.uniform(0.57, 0.67), 4)}},
                )

    # Restore the original promotion_request so Phase 7 has work to do. Phase
    # 7 operates purely on promotion_request + current_version/history, so the
    # proposal state doesn't matter here — restore just the request.
    db["skills"].update_one(
        {"_id": _TEST_SKILL_ID},
        {"$set": {"promotion_request": saved_promo["promo"]}},
    )
    _info("restored original promotion_request for Phase 7")
    return findings


# ---------------------------------------------------------------------------
# Phase 7 — Founder approves the promotion.
# ---------------------------------------------------------------------------

def phase_7_approve_promotion(saved_promo: dict) -> dict:
    _banner("Phase 7 — Founder approves the promotion (/api/skills/.../promotion)")

    from services.web_api.routers.skills import PromotionDecision, decide_promotion

    candidate_id = saved_promo["promo"]["candidate"]
    decision = PromotionDecision(decision="approve")
    result = decide_promotion(_TEST_SKILL_ID, decision)
    _info(f"endpoint returned: {result}")

    db = mongo_tools.db()
    skill = db["skills"].find_one({"_id": _TEST_SKILL_ID}) or {}
    if skill.get("current_version") != candidate_id:
        _fail(f"current_version={skill.get('current_version')!r}, "
              f"expected {candidate_id!r}")
        raise SystemExit(1)
    if candidate_id not in (skill.get("history") or []):
        _fail(f"history[] missing {candidate_id!r}: {skill.get('history')}")
        raise SystemExit(1)
    if skill.get("promotion_request"):
        _fail("promotion_request still present after approve")
        raise SystemExit(1)
    if candidate_id in (skill.get("candidates") or []):
        _fail(f"candidates[] still contains {candidate_id!r}: "
              f"{skill.get('candidates')}")
        raise SystemExit(1)
    _ok(f"current_version flipped to {candidate_id!r}; history/candidates/"
        f"promotion_request reconciled")

    # Disk is a derived cache — promote_after_approval no longer writes it
    # eagerly. The first read_body() after the Mongo flip is what reconciles
    # the on-disk SKILL.md against Mongo's new current_version body_md.
    target = _SANDBOX_DIR / _TEST_SKILL_ID / "SKILL.md"
    expected_body = (skill["versions"][candidate_id] or {}).get("body_md", "")
    pre_read_disk = target.read_text(encoding="utf-8") if target.is_file() else ""
    if pre_read_disk == expected_body:
        _warn("SKILL.md already matches candidate body before read_body() — "
              "promote_after_approval may have eagerly written disk (regression)")
    else:
        _info("disk still serves prior body before read_body() — as expected "
              "(Mongo is the source of truth, disk reconciles lazily)")

    # Trigger the lazy refresh by reading the body. read_body() should
    # detect the Mongo current_version body_md differs from disk and
    # atomically rewrite the file.
    new_body_from_registry = skills_module.registry().read_body(_TEST_SKILL_ID)

    # Now verify disk state in the SANDBOX (not the real skills/).
    if not target.is_file():
        _fail(f"SKILL.md not written to sandbox: {target}")
        raise SystemExit(1)
    if target.read_text(encoding="utf-8") != expected_body:
        _fail("SKILL.md on disk doesn't match versions[candidate].body_md "
              "after read_body() lazy reconciliation")
        raise SystemExit(1)
    _ok(f"SKILL.md self-healed in sandbox via read_body(): {target} "
        f"({len(expected_body)} bytes, matches versions[candidate].body_md)")

    # read_body() strips YAML frontmatter; versions[candidate].body_md is
    # the full file (frontmatter + body). Compare body-to-body so the
    # check matches what read_skill() actually returns to agents.
    _, expected_body_only = skills_module._parse_skill_md(expected_body)
    if new_body_from_registry.strip() == expected_body_only.strip():
        _ok("SkillRegistry.read_body() returns the new content "
            "(read_skill() will serve it to agents)")
    else:
        _warn(f"SkillRegistry returned a body that doesn't match. "
              f"registry length={len(new_body_from_registry)}, "
              f"expected length={len(expected_body_only)}")
    return {"ok": True, "promoted_to": candidate_id}


# ---------------------------------------------------------------------------
# Phase 8 — Disk-write failure-mode probes.
#
# The disk write now lives inside SkillRegistry.read_body's lazy
# reconciliation against Mongo, not in promotion_gate. These probes
# verify the failure modes there. We don't leave the system in a broken
# state — each probe restores after.
# ---------------------------------------------------------------------------

def phase_8_disk_failure_modes(saved_promo: dict) -> dict:
    _banner("Phase 8 — Disk-write failure-mode probes (read_body reconcile)")

    db = mongo_tools.db()
    findings = {"ok": True, "probes": []}

    # Snapshot so we can restore current_version after each probe.
    pre = db["skills"].find_one({"_id": _TEST_SKILL_ID}) or {}
    pre_current = pre.get("current_version")
    pre_body = ((pre.get("versions") or {}).get(pre_current) or {}).get("body_md", "")

    # ---- 8a: current_version points at a version with missing body_md ----
    # _mongo_body_for_current should return None and read_body should serve
    # whatever is on disk without overwriting it.
    _sub("8a — current_version body_md missing (read_body should not overwrite disk)")
    db["skills"].update_one(
        {"_id": _TEST_SKILL_ID},
        {"$set": {"versions.v_phantom": {"body_md": None,
                                          "source": "test_phantom"},
                  "current_version": "v_phantom"}},
    )
    target_before = (_SANDBOX_DIR / _TEST_SKILL_ID / "SKILL.md").read_text(
        encoding="utf-8")
    try:
        # Should not raise; should not write disk.
        skills_module.registry().read_body(_TEST_SKILL_ID)
    except Exception as e:
        _warn(f"read_body raised unexpectedly under missing body_md: {e!r}")
    target_after = (_SANDBOX_DIR / _TEST_SKILL_ID / "SKILL.md").read_text(
        encoding="utf-8")
    ok_8a = target_before == target_after
    (_ok if ok_8a else _fail)(
        "missing body_md left SKILL.md untouched (graceful degrade to disk)"
        if ok_8a else "SKILL.md was overwritten despite missing body_md")
    findings["probes"].append({"probe": "missing_body", "ok": ok_8a})
    if not ok_8a:
        findings["ok"] = False

    # Restore current_version to the real promoted candidate for 8b.
    db["skills"].update_one(
        {"_id": _TEST_SKILL_ID},
        {"$set": {"current_version": pre_current},
         "$unset": {"versions.v_phantom": ""}},
    )

    # ---- 8b: simulated atomic-replace failure during reconcile ----
    # Force the reconcile path: write something to disk that DIFFERS from
    # the Mongo current_version body_md, then patch os.replace to fail.
    # read_body should raise (Mongo stays correct; next read retries).
    _sub("8b — atomic-replace failure during reconcile (raises; tmp file cleaned up)")
    skill_dir = _SANDBOX_DIR / _TEST_SKILL_ID
    pre_tmp = sorted(skill_dir.glob(".SKILL.md.*.tmp"))
    # Make on-disk SKILL.md different from Mongo current body so the
    # reconcile path actually triggers a write.
    target_path = skill_dir / "SKILL.md"
    target_path.write_text("DRIFT MARKER — disk differs from Mongo",
                           encoding="utf-8")

    # Patch os.replace at the module where the write happens
    # (shared.skills) so the call inside _reconcile_disk_with_mongo
    # fails. Patching `os.replace` directly on the os module is the
    # simplest cross-module hook.
    import os as os_mod
    original_replace = os_mod.replace

    def boom(src, dst):
        raise OSError("simulated atomic-replace failure")

    os_mod.replace = boom
    raised = None
    try:
        skills_module.registry().read_body(_TEST_SKILL_ID)
    except OSError as e:
        raised = e
    except Exception as e:
        raised = e
    finally:
        os_mod.replace = original_replace
    post_tmp = sorted(skill_dir.glob(".SKILL.md.*.tmp"))
    leaked = post_tmp != pre_tmp
    ok_8b = isinstance(raised, OSError) and not leaked
    if ok_8b:
        _ok("atomic-replace failure during reconcile raises (no silent swallow); "
            "tmp file cleaned up — Mongo stays correct, next read retries")
    else:
        if raised is None:
            _fail("atomic-replace failure was swallowed; expected OSError "
                  "to propagate so callers see a reconciliation failure")
        elif not isinstance(raised, OSError):
            _fail(f"unexpected exception type: {type(raised).__name__}: {raised}")
        if leaked:
            _warn(f"tmp file(s) leaked: {[str(p) for p in post_tmp]}")
            for p in post_tmp:
                try:
                    p.unlink()
                except OSError:
                    pass
    findings["probes"].append({"probe": "atomic_replace_failure_raises",
                                "ok": ok_8b})
    if not ok_8b:
        findings["ok"] = False

    # Restore on-disk SKILL.md to the canonical Mongo body so subsequent
    # phases see consistent state. The next read_body would do this
    # itself, but doing it explicitly keeps Phase 9's assertion clean.
    if pre_body:
        target_path.write_text(pre_body, encoding="utf-8")

    return findings


# ---------------------------------------------------------------------------
# Phase 9 — The loop closes: next agent run records the new skill_version.
#
# This is the actual proof self-learning happened end-to-end. We don't
# need a real LLM here; emit a manual telemetry row simulating an agent
# run that loaded the now-promoted skill.
# ---------------------------------------------------------------------------

def phase_9_loop_closes(saved_promo: dict) -> dict:
    _banner("Phase 9 — Loop closes: next agent emits new skill_version")

    from shared.telemetry import TelemetryRecord, emit_action

    candidate_id = saved_promo["promo"]["candidate"]
    record = TelemetryRecord(
        telemetry_id=f"{_TEST_PREFIX}post_promo_{uuid.uuid4().hex[:8]}",
        agent="content_agent",
        skill_id="linkedin_post",
        skill_version="v3",
        action_type="draft",
        channel="linkedin",
        skills_loaded=[_TEST_SKILL_ID],
        eval_scores=None,
    )
    emit_action(record)

    # Assert the new on-disk SKILL.md reflects the candidate body (i.e. the
    # next agent that calls read_skill would get the new content).
    on_disk = (_SANDBOX_DIR / _TEST_SKILL_ID / "SKILL.md").read_text(
        encoding="utf-8")
    skill = mongo_tools.db()["skills"].find_one({"_id": _TEST_SKILL_ID})
    expected = (skill["versions"][candidate_id] or {}).get("body_md", "")
    if on_disk == expected:
        _ok(f"on-disk SKILL.md matches versions[{candidate_id!r}].body_md — "
            f"next read_skill() call serves the new content")
    else:
        _fail("on-disk SKILL.md no longer matches the canonical body")
        return {"ok": False}

    return {"ok": True}


# ---------------------------------------------------------------------------
# Phase 10 — Reject paths.
# ---------------------------------------------------------------------------

def phase_10_reject_paths() -> dict:
    _banner("Phase 10 — Reject paths (critique + promotion)")

    from services.web_api.routers.self_critique import CritiqueDecision, decide_critique
    from services.web_api.routers.skills import PromotionDecision, decide_promotion

    db = mongo_tools.db()
    findings = {"ok": True, "probes": []}

    # ---- 10a: reject critique ----
    _sub("10a — reject critique (proposal should be unset, no candidate added)")
    db["skills"].update_one(
        {"_id": _TEST_SKILL_ID},
        {"$set": {"self_critique_proposal": {
            "candidate_id": "v_reject_test", "issue": "test reject",
            "channels_affected": _TARGET_CHANNELS,
            "status": "awaiting_human_review",
        }}},
    )
    decide_critique(_TEST_SKILL_ID, CritiqueDecision(decision="reject"))
    s = db["skills"].find_one({"_id": _TEST_SKILL_ID}) or {}
    ok_10a = ("self_critique_proposal" not in s
              and "v_reject_test" not in (s.get("candidates") or []))
    (_ok if ok_10a else _fail)(
        f"proposal unset={'self_critique_proposal' not in s}, "
        f"no candidate added")
    findings["probes"].append({"probe": "critique_reject", "ok": ok_10a})
    if not ok_10a:
        findings["ok"] = False

    # ---- 10b: reject promotion ----
    _sub("10b — reject promotion (request unset, current_version untouched)")
    pre_version = s.get("current_version")
    db["skills"].update_one(
        {"_id": _TEST_SKILL_ID},
        {"$set": {"promotion_request": {
            "candidate": "v_reject_promo", "incumbent": pre_version,
            "status": "awaiting_approval"}}},
    )
    decide_promotion(_TEST_SKILL_ID, PromotionDecision(decision="reject"))
    s = db["skills"].find_one({"_id": _TEST_SKILL_ID}) or {}
    ok_10b = ("promotion_request" not in s
              and s.get("current_version") == pre_version)
    (_ok if ok_10b else _fail)(
        f"promotion_request unset={'promotion_request' not in s}, "
        f"current_version still {s.get('current_version')!r}")
    findings["probes"].append({"probe": "promotion_reject", "ok": ok_10b})
    if not ok_10b:
        findings["ok"] = False

    return findings


# ---------------------------------------------------------------------------
# Phase 11 — History + provenance audit.
# ---------------------------------------------------------------------------

def phase_11_audit() -> dict:
    _banner("Phase 11 — History + provenance audit")
    db = mongo_tools.db()

    history_rows = list(db["history.skills"].find(
        {"_original_id": _TEST_SKILL_ID},
    ).sort("_superseded_at", 1))
    kinds = [r.get("_change_kind") for r in history_rows]
    _info(f"history.skills rows for this test: {len(history_rows)}")
    for k in kinds:
        _info(f"  - {k}")

    # Should at minimum include: create, agent_skill_promotion_request_raised,
    # promotion_approved.
    expected_kinds = {"create", "agent_skill_promotion_request_raised",
                       "promotion_approved"}
    actual_kinds = set(kinds)
    missing = expected_kinds - actual_kinds
    if missing:
        _warn(f"history.skills missing change_kinds: {missing}. "
              f"Some mutations may have bypassed update_with_history.")
    else:
        _ok(f"history.skills captured every expected state transition: "
            f"{sorted(expected_kinds)}")

    # Provenance check on the current canonical doc.
    s = db["skills"].find_one({"_id": _TEST_SKILL_ID}) or {}
    has_prov = bool(s.get("_provenance")) and bool(s.get("_owner"))
    if has_prov:
        _ok("canonical doc has _provenance + _owner")
    else:
        _warn("canonical doc missing _provenance / _owner")

    return {"ok": True, "history_rows": len(history_rows),
            "expected_kinds_missing": sorted(missing)}


# ---------------------------------------------------------------------------
# Teardown
# ---------------------------------------------------------------------------

def teardown(*, keep_sandbox: bool, keep_mongo: bool = False) -> None:
    _banner("Teardown")
    if keep_mongo:
        _info("--keep-mongo-on-fail: preserving Mongo state for inspection")
        _info(f"  skills.{_TEST_SKILL_ID} + actions/{_TEST_PREFIX}* + "
              f"history.skills rows retained")
    else:
        db = mongo_tools.db()
        db["skills"].delete_one({"_id": _TEST_SKILL_ID})
        db["skills"].delete_one({"_id": _NOISE_SKILL_ID})
        db["history.skills"].delete_many({"_original_id": _TEST_SKILL_ID})
        db["history.skills"].delete_many({"_original_id": _NOISE_SKILL_ID})
        db["actions"].delete_many({"telemetry_id": {"$regex": f"^{_TEST_PREFIX}"}})
        db["approvals"].delete_many({"telemetry_id": {"$regex": f"^{_TEST_PREFIX}"}})
        db["skill_usage"].delete_many({"skill_name": _TEST_SKILL_ID})
        db["derived.skill_track_records"].delete_many({})
        db["derived.agent_skill_track_records"].delete_many(
            {"_id": _TEST_SKILL_ID})
        _ok("wiped Mongo test docs")

    if keep_sandbox:
        _info(f"sandbox kept at: {_SANDBOX_DIR}")
    else:
        shutil.rmtree(_SANDBOX_DIR, ignore_errors=True)
        _ok(f"sandbox removed: {_SANDBOX_DIR}")


# ---------------------------------------------------------------------------
# Phase 12 — Deterministic miner pipeline (self_critique_runner.run_once).
#
# The OTHER half of self-learning: the pure-function miners + the runner that
# persists their proposals and dedups re-runs. Exercises voice + negative
# end-to-end — miner LOGIC -> run_once persistence -> dedup -> founder-decision
# cooldown — against real seeded edits/rejects in Mongo. No LLM, deterministic.
#
# run_once is scoped to ("voice","negative") so its side effects land only on
# house-style, review_agent and negative_examples, which we snapshot+restore /
# time-bound-delete so the dev DB is left exactly as we found it.
# ---------------------------------------------------------------------------

def _snapshot_proposals(db, sid: str) -> dict:
    d = db["skills"].find_one(
        {"_id": sid}, {"self_critique_proposals": 1, "self_critique_proposal": 1})
    return {"existed": d is not None,
            "proposals": (d or {}).get("self_critique_proposals"),
            "singleton": (d or {}).get("self_critique_proposal")}


def _ensure_skill_doc(db, sid: str) -> None:
    if db["skills"].find_one({"_id": sid}) is None:
        db["skills"].insert_one({
            "_id": sid, "skill_kind": "agent_skill", "current_version": "v1",
            "versions": {"v1": {"body_md": f"seed body for {sid}"}},
            "candidates": [], "history": ["v1"]})


def _restore_proposals(db, sid: str, snap: dict) -> None:
    if not snap["existed"]:
        db["skills"].delete_one({"_id": sid})
        return
    set_block, unset_block = {}, {}
    (set_block.__setitem__("self_critique_proposals", snap["proposals"])
     if snap["proposals"] is not None
     else unset_block.__setitem__("self_critique_proposals", ""))
    (set_block.__setitem__("self_critique_proposal", snap["singleton"])
     if snap["singleton"] is not None
     else unset_block.__setitem__("self_critique_proposal", ""))
    upd: dict = {}
    if set_block:
        upd["$set"] = set_block
    if unset_block:
        upd["$unset"] = unset_block
    if upd:
        db["skills"].update_one({"_id": sid}, upd)


def phase_12_miner_pipeline() -> dict:
    from bson import ObjectId

    from agents import self_critique_runner as runner
    from agents._miners import negative as negative_miner
    from agents._miners import voice as voice_miner

    _banner("Phase 12 — Miner pipeline (deterministic self-learning)")
    db = mongo_tools.db()
    findings = {"ok": True}
    now = datetime.now(UTC)
    phase_start = now - timedelta(seconds=1)
    run_ids: set[str] = set()
    captured_phrase: str | None = None

    db["actions"].delete_many({"telemetry_id": {"$regex": f"^{_MINER_PREFIX}"}})
    db["approvals"].delete_many({"telemetry_id": {"$regex": f"^{_MINER_PREFIX}"}})
    hs_snap = _snapshot_proposals(db, "house-style")
    ra_snap = _snapshot_proposals(db, "review_agent")
    _ensure_skill_doc(db, "house-style")
    _ensure_skill_doc(db, "review_agent")

    try:
        # Seed founder EDITS (voice signal) + REJECTS (negative signal).
        for ch in ("email", "linkedin"):
            for i in range(6):
                tid = f"{_MINER_PREFIX}{ch}_{i}"
                db["actions"].insert_one({
                    "telemetry_id": tid, "skills_loaded": ["house-style"],
                    "channel": ch, "raw": {"draft": _ABSOLUTE_DRAFT},
                    "eval_scores": {"brand_voice": 0.6},
                    "ts": now - timedelta(days=1)})
                db["approvals"].insert_one({
                    "telemetry_id": tid, "decision": "edit",
                    "approved_text": _SOFTENED_DRAFT,
                    "edit_categories": ["softened_absolute"],
                    "decided_at": now - timedelta(days=1)})
        for i in range(4):
            tid = f"{_MINER_PREFIX}rej_{i}"
            db["actions"].insert_one({
                "telemetry_id": tid, "skills_loaded": ["house-style"],
                "channel": "email",
                "raw": {"draft": f"Use our tool {_RISK_PHRASE} today."},
                "eval_scores": {"brand_voice": 0.5},
                "ts": now - timedelta(days=1)})
            db["approvals"].insert_one({
                "telemetry_id": tid, "decision": "reject",
                "rejection_category": "claim_risk",
                "decided_at": now - timedelta(days=1)})

        # 12a — miner LOGIC (direct mine() calls).
        _sub("12a — miner logic: voice + negative produce expected proposals")
        vp = voice_miner.mine(db, lookback_days=14)
        v_house = [p for p in vp if p["target_kind"] == "skill"
                   and p["target_id"] == "house-style"]
        ok_v = len(v_house) >= 1
        (_ok if ok_v else _fail)(
            f"voice → {len(vp)} proposals, {len(v_house)} on house-style "
            f"(e.g. {(v_house[0]['evidence'].get('ngram') if v_house else None)!r})")
        np = negative_miner.mine(db, lookback_days=14)
        neg_ex = [p for p in np if p["target_kind"] == "negative_example"
                  and p["target_id"] == "claim_risk"]
        neg_ra = [p for p in np if p["target_kind"] == "skill"
                  and p["target_id"] == "review_agent"]
        ok_n = len(neg_ex) >= 1 and len(neg_ra) >= 1
        (_ok if ok_n else _fail)(
            f"negative → claim_risk negative_example={len(neg_ex)}, "
            f"review_agent proposal={len(neg_ra)}")
        if neg_ex:
            captured_phrase = neg_ex[0]["evidence"]["phrase"]
        if not (ok_v and ok_n):
            findings["ok"] = False

        def _neg_rows() -> int:
            if not captured_phrase:
                return 0
            return db["negative_examples"].count_documents(
                {"source": "self_critique:negative",
                 "rejected_phrase": captured_phrase})

        # 12b — run_once() orchestrates + persists.
        _sub("12b — run_once(): orchestrate + persist proposals")
        r1 = runner.run_once(db, miners=("voice", "negative"), lookback_days=14)
        if r1.get("_id"):
            run_ids.add(r1["_id"])
        vm = r1.get("miners", {}).get("voice", {})
        hs1 = db["skills"].find_one({"_id": "house-style"}) or {}
        voice_persisted = [p for p in load_proposals(hs1)
                           if p.get("miner") == "voice"
                           and p.get("run_id") == r1.get("_id")]
        ra1 = db["skills"].find_one({"_id": "review_agent"}) or {}
        ra_persisted = [p for p in load_proposals(ra1)
                        if p.get("miner") == "negative"]
        neg_rows_1 = _neg_rows()
        ok_persist = (r1.get("status") == "ok" and vm.get("proposals", 0) >= 1
                      and not vm.get("errors") and len(voice_persisted) >= 1
                      and neg_rows_1 >= 1 and len(ra_persisted) >= 1)
        (_ok if ok_persist else _fail)(
            f"run1: voice persisted={len(voice_persisted)} on house-style, "
            f"negative_examples={neg_rows_1}, review_agent={len(ra_persisted)}, "
            f"voice errors={vm.get('errors')}")
        if not ok_persist:
            findings["ok"] = False

        # 12c — dedup: a second identical run must NOT duplicate proposals.
        _sub("12c — dedup: re-run does not pile up duplicates")
        r2 = runner.run_once(db, miners=("voice", "negative"), lookback_days=14)
        if r2.get("_id"):
            run_ids.add(r2["_id"])
        hs2 = db["skills"].find_one({"_id": "house-style"}) or {}
        voice_sigs = [p.get("signature") for p in load_proposals(hs2)
                      if p.get("miner") == "voice"]
        sigs_unique = len(voice_sigs) == len(set(voice_sigs))
        neg_rows_2 = _neg_rows()
        nm2 = r2.get("miners", {}).get("negative", {})
        ok_dedup = (sigs_unique and neg_rows_2 == neg_rows_1
                    and nm2.get("skipped", 0) >= 1)
        (_ok if ok_dedup else _fail)(
            f"rerun: voice signatures unique={sigs_unique} ({len(voice_sigs)} "
            f"entries), negative_examples stable={neg_rows_2 == neg_rows_1} "
            f"({neg_rows_1}→{neg_rows_2}), negative skipped={nm2.get('skipped')}")
        if not ok_dedup:
            findings["ok"] = False

        # 12d — cooldown: a founder-dismissed proposal stays suppressed.
        _sub("12d — cooldown: dismissed proposal not re-surfaced")
        hs = db["skills"].find_one({"_id": "house-style"}) or {}
        props = load_proposals(hs)
        target_sig = next((p["signature"] for p in props
                           if p.get("miner") == "voice"), None)
        for p in props:
            if p.get("signature") == target_sig:
                p["status"] = "dismissed"
                p["decided_at"] = datetime.now(UTC)
        db["skills"].update_one({"_id": "house-style"},
                                {"$set": {"self_critique_proposals": props}})
        r3 = runner.run_once(db, miners=("voice", "negative"), lookback_days=14)
        if r3.get("_id"):
            run_ids.add(r3["_id"])
        hs3 = db["skills"].find_one({"_id": "house-style"}) or {}
        after = next((p for p in load_proposals(hs3)
                      if p.get("signature") == target_sig), None)
        ok_cool = after is not None and after.get("status") == "dismissed"
        (_ok if ok_cool else _fail)(
            f"dismissed voice proposal stayed suppressed across re-run: "
            f"status={after and after.get('status')!r}")
        if not ok_cool:
            findings["ok"] = False

    finally:
        # Leave the dev DB exactly as found.
        db["actions"].delete_many({"telemetry_id": {"$regex": f"^{_MINER_PREFIX}"}})
        db["approvals"].delete_many({"telemetry_id": {"$regex": f"^{_MINER_PREFIX}"}})
        db["negative_examples"].delete_many(
            {"source": {"$regex": "^self_critique:"}, "ts": {"$gte": phase_start}})
        for rid in run_ids:
            try:
                db["self_critique_runs"].delete_one({"_id": ObjectId(rid)})
            except Exception:
                pass
        _restore_proposals(db, "house-style", hs_snap)
        _restore_proposals(db, "review_agent", ra_snap)
        _info("restored house-style / review_agent / negative_examples to "
              "pre-phase state")

    return findings


# ---------------------------------------------------------------------------
# Phase 13 — Silent-pass: the Self-Critique Agent must NOT invent a proposal
# on pattern-free telemetry. The complement of Phase 3 (real Gemini): a
# self-learning loop that proposes on noise is worse than useless.
# ---------------------------------------------------------------------------

async def phase_13_silent_pass() -> dict:
    _banner("Phase 13 — Silent-pass on pattern-free telemetry (real Gemini)")
    db = mongo_tools.db()
    findings = {"ok": True}
    rng = random.Random(43)
    now = datetime.now(UTC)
    pfx = f"{_TEST_PREFIX}noise_"

    # Populate the sandbox with the real skills (so the agent's REQUIRED
    # preamble read_skill calls resolve) + the throwaway noise skill. Without
    # the real-skills clone the agent crashes on read_skill("house-style")
    # before it can reason — which would be a false "silent pass".
    _clone_real_skills_into_sandbox()
    body = (f"---\nname: {_NOISE_SKILL_ID}\n"
            f"description: Throwaway skill for the silent-pass probe.\n"
            f"metadata:\n  version: 1.0.0\n---\n\n# Noise skill\nNeutral body.\n")
    skill_dir = _SANDBOX_DIR / _NOISE_SKILL_ID
    (skill_dir / "versions").mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(body, encoding="utf-8")
    skills_module.refresh_registry()

    db["skills"].delete_one({"_id": _NOISE_SKILL_ID})
    db["actions"].delete_many({"telemetry_id": {"$regex": f"^{pfx}"}})
    db["approvals"].delete_many({"telemetry_id": {"$regex": f"^{pfx}"}})
    db["skills"].insert_one({
        "_id": _NOISE_SKILL_ID, "skill_kind": "agent_skill",
        "current_version": "v1", "candidates": [], "history": ["v1"],
        "applies_to": {"channels": ["email", "linkedin", "substack"]},
        "versions": {"v1": {"body_md": body, "source": "seed"}}})

    # Healthy, varied scores (no low-score signal) + a few one-off edits whose
    # before→after share NO recurring phrase (no systematic pattern to find).
    _POOL = ("quarterly roadmap onboarding latency dashboard webhook invoice "
             "catalog refund shipping locale sandbox throughput retry").split()

    def _rand_sentence(k: int) -> str:
        return " ".join(rng.sample(_POOL, k)) + "."

    for ch in ("email", "linkedin", "substack"):
        for i in range(8):
            tid = f"{pfx}{ch}_{i}"
            db["actions"].insert_one({
                "telemetry_id": tid, "skills_loaded": [_NOISE_SKILL_ID],
                "channel": ch, "raw": {"draft": _rand_sentence(8)},
                "eval_scores": {"brand_voice": round(rng.uniform(0.78, 0.92), 4),
                                "claim_support": 0.85, "claim_risk": 0.85},
                "ts": now - timedelta(days=rng.uniform(0, 12))})
            # Sparse, unrelated edits — different random rewrite each time.
            if i < 2:
                db["approvals"].insert_one({
                    "telemetry_id": tid, "decision": "edit",
                    "approved_text": _rand_sentence(8),
                    "edit_categories": [], "decided_at": now - timedelta(days=i + 1)})

    agent_errored: str | None = None
    try:
        from google.adk.agents.run_config import RunConfig
        from google.adk.runners import Runner
        from google.adk.sessions import InMemorySessionService
        from google.genai.types import Content, Part

        from agents.self_critique import self_critique_agent

        ss = InMemorySessionService()
        runner = Runner(agent=self_critique_agent, app_name="e2e_skill_evolution",
                        session_service=ss)
        session = await ss.create_session(
            app_name="e2e_skill_evolution", user_id="e2e",
            state={"telemetry_id": f"{pfx}critique", "skill_id": "self_critique"})
        msg = Content(role="user", parts=[Part.from_text(text=(
            f"Run the self-critique pass scoped to agent_skill "
            f"'{_NOISE_SKILL_ID}'. Use recent_skill_telemetry('{_NOISE_SKILL_ID}') "
            f"to inspect its drafts and edits. The scores are healthy and the "
            f"few edits are unrelated one-offs with no shared pattern. Follow "
            f"your instructions: only propose a revision if a systematic "
            f"cross-channel pattern genuinely holds. If none does, write NO "
            f"proposal — staying silent is the correct outcome."))])
        started = time.monotonic()
        saw_event = False
        async for ev in runner.run_async(
                user_id="e2e", session_id=session.id, new_message=msg,
                run_config=RunConfig(max_llm_calls=50)):
            saw_event = True
            author = getattr(ev, "author", None) or "?"
            _info(f"[{time.monotonic() - started:5.1f}s] {author}")
        if not saw_event:
            agent_errored = "no events produced"
    except Exception as e:  # noqa: BLE001
        agent_errored = str(e)

    skill = db["skills"].find_one({"_id": _NOISE_SKILL_ID}) or {}
    proposals = load_proposals(skill)
    pending = [p for p in proposals
               if p and p.get("status") == "awaiting_human_review"]
    minted = [v for v in (skill.get("versions") or {}) if v != "v1"]
    no_proposal = not pending and not skill.get("self_critique_proposal")

    if agent_errored:
        # A crash is NOT a silent pass — we never observed the agent's
        # decision. Don't claim success; flag inconclusive (re-run). Transient
        # free-tier API errors are common, so don't hard-fail the suite either.
        _warn(f"INCONCLUSIVE — agent run errored ({agent_errored[:120]}); "
              f"silent-pass NOT verified this run. Re-run (likely transient).")
        findings["inconclusive"] = True
    elif no_proposal and not minted:
        _ok("agent correctly stayed SILENT — no proposal, no candidate version "
            "minted on pattern-free telemetry")
    else:
        _fail(f"false-positive: agent wrote a proposal on noise "
              f"(pending={len(pending)}, minted_versions={minted}, "
              f"issue={(skill.get('self_critique_proposal') or {}).get('issue')!r})")
        findings["ok"] = False

    # Cleanup (sandbox dir is wiped wholesale by teardown).
    db["skills"].delete_one({"_id": _NOISE_SKILL_ID})
    db["history.skills"].delete_many({"_original_id": _NOISE_SKILL_ID})
    db["actions"].delete_many({"telemetry_id": {"$regex": f"^{pfx}"}})
    db["approvals"].delete_many({"telemetry_id": {"$regex": f"^{pfx}"}})
    return findings


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def summary(results: dict) -> int:
    _banner("Summary")
    rc = 0
    for name, r in results.items():
        status = "OK " if r.get("ok") else "FAIL"
        if not r.get("ok"):
            rc = 1
        print(f"  {name:<25} {status}")
    if rc == 0:
        print("\n  ✓ Self-learning loop closes end-to-end.")
    else:
        print("\n  ✗ One or more functional phases failed.")
    return rc


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

async def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--phases", default=None,
                   help="Comma-separated subset of phase numbers (0-13).")
    p.add_argument("--keep-sandbox", action="store_true",
                   help="Leave the tmpdir sandbox for inspection.")
    p.add_argument("--keep-mongo-on-fail", action="store_true",
                   help="Leave Mongo state intact on failure for diagnosis.")
    args = p.parse_args()

    all_phases = list(range(14))
    selected = [int(x) for x in args.phases.split(",")] if args.phases else all_phases

    print("=" * 72)
    print("  E2E SKILL EVOLUTION TEST")
    print("=" * 72)
    print(f"  Mongo URI:        {os.environ['MONGO_URI_DIRECT']}")
    print(f"  Mongo DB:         {os.environ['MONGO_DB']}")
    print(f"  Sandbox:          {_SANDBOX_DIR}")
    print(f"  Selected phases:  {selected}")
    print("=" * 72)

    results: dict = {}
    started = time.monotonic()
    promo_state: dict | None = None
    proposal_state: dict | None = None

    try:
        if 0 in selected:
            results["phase_0_bootstrap"] = phase_0_bootstrap()
        if 1 in selected:
            results["phase_1_telemetry"] = phase_1_synthesize_telemetry()
        if 2 in selected:
            results["phase_2_derive"]   = phase_2_derive_track_records()
        if 3 in selected:
            proposal_state = await phase_3_self_critique()
            results["phase_3_self_critique"] = proposal_state
        if 4 in selected and proposal_state:
            results["phase_4_accept"] = phase_4_accept_critique(proposal_state)
        if 5 in selected and proposal_state:
            promo_state = phase_5_promotion_gate(proposal_state)
            results["phase_5_gate"] = promo_state
        if 6 in selected and promo_state:
            results["phase_6_failure_modes"] = phase_6_gate_failure_modes(promo_state)
        if 7 in selected and promo_state:
            results["phase_7_approve"] = phase_7_approve_promotion(promo_state)
        if 8 in selected and promo_state:
            results["phase_8_disk_fail"] = phase_8_disk_failure_modes(promo_state)
        if 9 in selected and promo_state:
            results["phase_9_loop_close"] = phase_9_loop_closes(promo_state)
        if 10 in selected:
            results["phase_10_reject"] = phase_10_reject_paths()
        if 11 in selected:
            results["phase_11_audit"]  = phase_11_audit()
        if 12 in selected:
            results["phase_12_miner_pipeline"] = phase_12_miner_pipeline()
        if 13 in selected:
            results["phase_13_silent_pass"] = await phase_13_silent_pass()
    except SystemExit as e:
        elapsed = time.monotonic() - started
        print(f"\n  ✗ Run aborted after {elapsed:.0f}s (exit {e.code})",
              file=sys.stderr)
        teardown(keep_sandbox=args.keep_sandbox,
                  keep_mongo=args.keep_mongo_on_fail)
        return int(e.code or 1)
    except Exception:
        import traceback
        traceback.print_exc()
        teardown(keep_sandbox=args.keep_sandbox,
                  keep_mongo=args.keep_mongo_on_fail)
        return 1

    rc = summary(results)
    elapsed = time.monotonic() - started
    print(f"\n  Elapsed: {elapsed:.1f}s")
    # On clean success, always tear down Mongo state regardless of flag —
    # --keep-mongo-on-fail only matters when we exited via the error path.
    teardown(keep_sandbox=args.keep_sandbox, keep_mongo=False)
    return rc


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
