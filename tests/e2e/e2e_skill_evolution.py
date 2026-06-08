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

    python -m tests.e2e.e2e_skill_evolution                 # all 11 phases
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


def phase_0_bootstrap() -> dict:
    _banner("Phase 0 — Bootstrap: sandbox SKILLS_ROOT + seed test skill")
    _info(f"sandbox SKILLS_ROOT: {_SANDBOX_DIR}")

    # 0a-pre — clone the real skills/ tree into the sandbox so every agent
    # that calls read_skill("house-style") etc. (mandatory invocations in
    # the REQUIRED preamble) finds the skill body. Without this the
    # Self-Critique Agent's required preamble crashes with a KeyError.
    real_skills_root = REPO_ROOT / "skills"
    if real_skills_root.is_dir():
        cloned = 0
        for entry in real_skills_root.iterdir():
            if not entry.is_dir():
                continue
            dest = _SANDBOX_DIR / entry.name
            if not dest.exists():
                shutil.copytree(entry, dest)
                cloned += 1
        _ok(f"cloned {cloned} skills from {real_skills_root} into sandbox "
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
                "eval_scores": {
                    "brand_voice": round(brand_voice, 4),
                    "claim_support": round(rng.uniform(0.78, 0.86), 4),
                    "claim_risk": round(rng.uniform(0.80, 0.90), 4),
                },
                "ts": now - timedelta(days=rng.uniform(0, 13)),
            })
            inserted += 1

    _ok(f"synthesized {inserted} actions across {len(_TARGET_CHANNELS)} channels "
        f"({n_per_channel} each)")
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
    _banner("Phase 2 — Derive track records (Mongo equivalent)")

    db = mongo_tools.db()
    now = datetime.now(UTC)

    # Per-(skill_id, skill_version) rollup.
    rollup = list(db["actions"].aggregate([
        {"$match": {"eval_scores": {"$ne": None}}},
        {"$group": {
            "_id": {"skill_id": "$skill_id", "skill_version": "$skill_version"},
            "n": {"$sum": 1},
            "mean_brand_voice": {"$avg": "$eval_scores.brand_voice"},
            "mean_claim_support": {"$avg": "$eval_scores.claim_support"},
            "mean_claim_risk": {"$avg": "$eval_scores.claim_risk"},
            "first_seen": {"$min": "$ts"},
            "last_seen": {"$max": "$ts"},
        }},
    ]))
    # Full replace, matching production's derive_track_records semantics —
    # the derive cron wipes and re-populates this collection on every run.
    db["derived.skill_track_records"].delete_many({})
    docs = [{
        "_id": f"{r['_id']['skill_id']}@{r['_id']['skill_version']}",
        "skill_id": r["_id"]["skill_id"],
        "skill_version": r["_id"]["skill_version"],
        "action_count": r["n"],
        "mean_brand_voice": r["mean_brand_voice"],
        "mean_claim_support": r["mean_claim_support"],
        "mean_claim_risk": r["mean_claim_risk"],
        "first_seen": r["first_seen"],
        "last_seen": r["last_seen"],
        "_derived": {
            "derived_at": now,
            "derived_by": "service:derive_track_records",
            "derived_from": [
                {"kind": "mongo_collection", "id": "actions"},
            ],
            "freshness_sla": "PT24H",
            "stale": False,
        },
    } for r in rollup]
    if docs:
        db["derived.skill_track_records"].insert_many(docs)
    _ok(f"wrote {len(docs)} derived.skill_track_records rows")

    # Per-Agent-Skill cross-channel rollup (UNNEST(skills_loaded) equivalent).
    agent_skill_rollup = list(db["actions"].aggregate([
        {"$match": {"skills_loaded": {"$ne": []},
                    "eval_scores": {"$ne": None}}},
        {"$unwind": "$skills_loaded"},
        {"$match": {"skills_loaded": _TEST_SKILL_ID}},
        {"$group": {
            "_id": {"skill": "$skills_loaded", "channel": "$channel"},
            "n": {"$sum": 1},
            "mean_brand_voice": {"$avg": "$eval_scores.brand_voice"},
            "mean_claim_support": {"$avg": "$eval_scores.claim_support"},
        }},
    ]))
    # Reshape into one doc per agent_skill with per-channel breakdown.
    by_skill: dict[str, dict] = {}
    for r in agent_skill_rollup:
        sk = r["_id"]["skill"]
        ch = r["_id"]["channel"]
        d = by_skill.setdefault(sk, {"per_channel": {}, "channels_seen": []})
        d["per_channel"][ch] = {
            "n": r["n"],
            "mean_brand_voice": r["mean_brand_voice"],
            "mean_claim_support": r["mean_claim_support"],
        }
        d["channels_seen"].append(ch)
    db["derived.agent_skill_track_records"].delete_many(
        {"_id": _TEST_SKILL_ID})
    for sk, d in by_skill.items():
        means = [c["mean_brand_voice"] for c in d["per_channel"].values()
                 if c["mean_brand_voice"] is not None]
        db["derived.agent_skill_track_records"].insert_one({
            "_id": sk,
            "skill": sk,
            "channels": d["channels_seen"],
            "per_channel": d["per_channel"],
            "project_baseline_brand_voice": (sum(means) / len(means)
                                              if means else None),
            "_derived": {
                "derived_at": now,
                "derived_by": "service:derive_track_records",
                "derived_from": [{"kind": "mongo_collection", "id": "actions"}],
                "freshness_sla": "PT24H",
                "stale": False,
            },
        })
    _ok(f"wrote {len(by_skill)} derived.agent_skill_track_records rows "
        f"(channels seen: {sorted({c for d in by_skill.values() for c in d['channels_seen']})})")

    # Smoke check on the freshness contract — was Phase 3 of the memory-tier
    # test's discovery (readers should honor stale=True).
    for d in db["derived.agent_skill_track_records"].find(
        {"_id": _TEST_SKILL_ID},
    ):
        if d["_derived"]["stale"] is False:
            _info("freshness contract: _derived.stale=False, derived_at=now → "
                  "readers must compute staleness from derived_at + freshness_sla")
    return {"ok": True, "skill_track_records": len(docs),
            "agent_skill_track_records": len(by_skill)}


# ---------------------------------------------------------------------------
# Phase 3 — Self-Critique Agent (REAL Gemini, no fallback).
#
# The agent in production reads BQ telemetry.actions + training.edits. In
# LOCAL_DEV bigquery_query returns []. The test monkey-patches the helper
# to serve synthesized rows so the agent has real data to reason about.
# ---------------------------------------------------------------------------

async def phase_3_self_critique() -> dict:
    _banner("Phase 3 — Self-Critique Agent (real Gemini, no fallback)")

    # Build the synthesized BQ result rows the agent would have seen.
    db = mongo_tools.db()
    now = datetime.now(UTC)
    low_scoring = list(db["actions"].find(
        {"skills_loaded": _TEST_SKILL_ID,
         "eval_scores.brand_voice": {"$lt": 0.7}},
    ).sort("ts", -1).limit(10))

    fake_edits = [
        {"telemetry_id": a["telemetry_id"],
         "channel": a["channel"],
         "before_text": ("Our platform guarantees 100% delivery and "
                          "completely eliminates manual review."),
         "after_text": ("Our platform typically delivers reliably and "
                         "reduces manual review meaningfully."),
         "edit_categories": ["softened_absolute"],
         "ts": now - timedelta(days=2)}
        for a in low_scoring[:_N_FOUNDER_EDITS]
    ]

    # Monkey-patch bigquery_query to serve our synthesized data. We can't
    # cheaply discriminate which query the agent issues, so we return a
    # union of (low_scoring + fake_edits) and trust the LLM to filter. In
    # practice the agent samples each — a more discriminating stub would
    # need actual SQL parsing.
    from shared import bigquery_helper
    original_bq = bigquery_helper.bigquery_query

    def stub_bq(query: str, max_rows: int = 200):
        q = query.upper()
        if "TRAINING.EDITS" in q or "EDIT" in q:
            return [{k: v for k, v in row.items()
                     if k not in {"_id"}} for row in fake_edits][:max_rows]
        if "TELEMETRY.ACTIONS" in q or "ACTIONS" in q:
            return [{
                "telemetry_id": a["telemetry_id"],
                "channel": a["channel"],
                "skill_version": a.get("skill_version"),
                "brand_voice": a.get("eval_scores", {}).get("brand_voice"),
                "skills_loaded": a.get("skills_loaded"),
                "ts": a["ts"],
            } for a in low_scoring][:max_rows]
        return original_bq(query, max_rows=max_rows)

    bigquery_helper.bigquery_query = stub_bq
    # Patch the imported binding on agents.self_critique too — the agent
    # module captured the function at import time.
    try:
        from agents import self_critique as self_critique_mod
        if hasattr(self_critique_mod, "bigquery_query"):
            self_critique_mod.bigquery_query = stub_bq
    except ImportError:
        pass

    # Now invoke the Self-Critique Agent (real LLM).
    try:
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
            f"'{_TEST_SKILL_ID}'. Inspect the (synthesized) recent low-score "
            f"drafts and founder edits. The pattern is consistent "
            f"absolute-language softening across {len(_TARGET_CHANNELS)} "
            f"channels. If the pattern holds, write the FULL new SKILL.md "
            f"body to versions['{_CANDIDATE_VERSION}'].body_md AND a "
            f"self_critique_proposal that lists "
            f"channels_affected={_TARGET_CHANNELS}."
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
    finally:
        # Restore the helper. Other phases that exercise the real LLM
        # should see honest LOCAL_DEV behavior (i.e. [] from BQ).
        bigquery_helper.bigquery_query = original_bq

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
# We run the agent_skill branch of services.promotion_gate.main._evaluate
# _agent_skill_proposal, but with the BigQuery-backed per-channel stats
# replaced by a Mongo aggregation (the LOCAL_DEV substitute).
# ---------------------------------------------------------------------------

def _mongo_agent_skill_per_channel_stats(skill_id: str,
                                          channels: list[str]) -> dict[str, dict]:
    db = mongo_tools.db()
    if not channels:
        return {}
    rows = list(db["actions"].aggregate([
        {"$match": {"skills_loaded": skill_id,
                    "channel": {"$in": channels},
                    "eval_scores": {"$ne": None}}},
        {"$group": {
            "_id": "$channel",
            "n": {"$sum": 1},
            "brand_voice": {"$avg": "$eval_scores.brand_voice"},
            "claim_support": {"$avg": "$eval_scores.claim_support"},
            "claim_risk": {"$avg": "$eval_scores.claim_risk"},
        }},
    ]))
    return {r["_id"]: {k: v for k, v in r.items() if k != "_id"}
            for r in rows}


def phase_5_promotion_gate(prior: dict) -> dict:
    _banner("Phase 5 — Promotion gate evaluation (agent_skill path)")

    # Patch the BQ-backed per-channel stats to read from Mongo.
    from services.promotion_gate import main as gate
    original_stats = gate._agent_skill_per_channel_stats
    gate._agent_skill_per_channel_stats = _mongo_agent_skill_per_channel_stats
    try:
        skill = mongo_tools.db()["skills"].find_one({"_id": _TEST_SKILL_ID})
        gate._evaluate_agent_skill_proposal(skill)
    finally:
        gate._agent_skill_per_channel_stats = original_stats

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

    original_stats = gate._agent_skill_per_channel_stats
    gate._agent_skill_per_channel_stats = _mongo_agent_skill_per_channel_stats

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

        # Restore the original signal for downstream phases.
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
    finally:
        gate._agent_skill_per_channel_stats = original_stats

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
        db["history.skills"].delete_many({"_original_id": _TEST_SKILL_ID})
        db["actions"].delete_many({"telemetry_id": {"$regex": f"^{_TEST_PREFIX}"}})
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
                   help="Comma-separated subset of phase numbers (0-11).")
    p.add_argument("--keep-sandbox", action="store_true",
                   help="Leave the tmpdir sandbox for inspection.")
    p.add_argument("--keep-mongo-on-fail", action="store_true",
                   help="Leave Mongo state intact on failure for diagnosis.")
    args = p.parse_args()

    all_phases = list(range(12))
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
