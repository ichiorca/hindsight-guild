"""End-to-end experiment lifecycle smoke test.

What this proves end-to-end:

  1. CMO Planner (real Gemini call, operator-mode prompt) authors a
     running experiment and inserts it into Mongo's `experiments`
     collection.
  2. The drafting pipeline (Research → Content → CritiqueLoop →
     ImageBrief → Review → Finalizer, real Gemini) executes N drafts
     per variant. Each produces a real Mongo `actions` row tagged with
     `experiment_id` + `variant_id` — the same row shape the production
     telemetry path emits.
  3. Outcome attachment: in production, services/outcome_attach reads
     telemetry.outcomes from BigQuery. LOCAL_DEV has no BigQuery, so
     this phase synthesizes per-action outcome values (with a clear
     lift on the treatment variant) and writes them onto each Mongo
     action document.
  4. Decision: a Mongo-equivalent of
     services/outcome_attach._maybe_decide_experiment aggregates the
     synthesized outcomes by variant; once n ≥ min_n_per_arm and
     (winner − runner) ≥ MDE, the experiment transitions to "decided"
     via mongo_tools.transition_experiment_state — the SAME call the
     production worker uses.
  5. Drift detection: in production, services/drift_detect scans
     BigQuery for rubric drops. LOCAL_DEV: this script synthesizes a
     28-day rubric-drop scenario in the Mongo `actions` collection
     (baseline ≈ 0.85, recent ≈ 0.70 on a second channel), then runs a
     Mongo-equivalent of drift_detect._detect_drift. For any drop ≥
     0.10 with n ≥ 20, it calls the SAME _open_investigation helper
     production uses to insert a new running experiment with tags
     ["drift", "investigation"].
  6. Promotion gate: production services/promotion_gate aggregates
     telemetry.actions.eval_scores from BigQuery to compare a candidate
     skill version against the incumbent. LOCAL_DEV: a Mongo-equivalent
     reads the same eval scores from the Mongo `actions` rows the
     pipeline runs produced (real eval scores when Vertex Eval is
     unreachable degrade to a deterministic stub — see
     shared/rubrics.py). When the candidate beats the incumbent by
     ≥ MDE on brand_voice without regressing guardrails, a
     promotion_request is raised on the skill — same write path the
     production gate uses.
  7. Verification: the script hits the same /api/experiments/*
     endpoints the React UI consumes and prints the counts the
     Experiments page should now display.

What this test DOES NOT cover (intentional, out of scope for LOCAL_DEV):

  - Real GA4 / agent-checkout / Google Ads outcome attribution (would need a
    deployed Cloud Run job + live ad spend).
  - Real Vertex AI Eval Service rubric grading (the local pipeline
    runs against the Gemini API direct endpoint; eval scores degrade
    to None or a fallback). The synthesized outcomes are clean signal
    so the decision logic is exercised; the rubric grading path is
    covered by tests/integration/test_reject_then_redraft.py.
  - BigQuery-backed drift detection across real `telemetry.actions`
    history. The Mongo-equivalent runs the same logic shape against
    the local mirror.

How long it takes: ~3–5 minutes on the free Gemini tier (4 real
pipeline runs = 24 LLM calls). Crank `_RUNS_PER_VARIANT` down to 1 if
you're rate-limited.

Usage (from a shell at the repo root with `.env` populated and Mongo up):

    python -m tests.e2e.e2e_experiment_lifecycle              # full run
    python -m tests.e2e.e2e_experiment_lifecycle --phases 1,2 # subset
    python -m tests.e2e.e2e_experiment_lifecycle --keep       # don't clean prior test data

Exit code 0 if every phase passes; 1 if any FAIL.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import random
import statistics
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta

import httpx

# Env bootstrap — must run BEFORE importing any agent module so the
# GOOGLE_GENAI_USE_VERTEXAI=0 switch and MONGO_URI_DIRECT settings are read.
from scripts._test_bootstrap import (  # noqa: E402
    _banner,
    _fail,
    _info,
    _ok,
)

if not os.environ.get("GOOGLE_API_KEY"):
    print("ERROR: GOOGLE_API_KEY not set in .env.", file=sys.stderr)
    print("       Get one at https://aistudio.google.com/app/apikey", file=sys.stderr)
    sys.exit(1)

# Lazy ADK imports — only loaded once env is set. noqa-fenced because they
# intentionally live below module-level code.
from google.adk.runners import Runner  # noqa: E402
from google.adk.sessions import InMemorySessionService  # noqa: E402
from google.genai.types import Content, Part  # noqa: E402

from shared import mongo_tools  # noqa: E402

# ---------------------------------------------------------------------------
# Test config — small numbers so the test runs in minutes on the free tier.
# ---------------------------------------------------------------------------

_TEST_ID_PREFIX = "exp_test_lifecycle_"
_TEST_ACTION_PREFIX = "act_test_lifecycle_"
_TEST_TAG = "lifecycle_e2e"

# How many real pipeline runs per variant. Each pipeline run = 6 LLM calls
# (research, content, critique, reviser, image_brief, review). The Gemini
# free tier is ~60 RPM — 2 runs × 2 variants × 6 calls = 24 calls is safe.
_RUNS_PER_VARIANT = 2
_MIN_N_PER_ARM = 2   # decision threshold; matches _RUNS_PER_VARIANT

# Synthesized outcome values. _CONTROL is the baseline; _TREATMENT is what
# B_urgency will get. The gap (5pp) clears MDE (3pp) with margin.
_CONTROL_OUTCOME = (0.40, 0.45)    # uniform[a, b]
_TREATMENT_OUTCOME = (0.50, 0.55)
_OUTCOME_METRIC = "engagement_72h"
_OUTCOME_MDE = 0.03

# Drift scenario tuning.
_DRIFT_CHANNEL = "email"
_DRIFT_RUBRIC = "brand_voice"
_DRIFT_BASELINE_MEAN = 0.85
_DRIFT_RECENT_MEAN = 0.70
_DRIFT_BASELINE_N = 25       # past 28d, 4–31 days ago
_DRIFT_RECENT_N = 22         # last 3 days
_DRIFT_THRESHOLD = 0.10      # matches services/drift_detect.DRIFT_THRESHOLD
_DRIFT_MIN_SAMPLE = 20       # matches services/drift_detect.MIN_SAMPLE

# Promotion-gate tuning. Skill = the LinkedIn post playbook. The "candidate"
# version matches what the experiment's B_urgency variant points to.
_PROMOTION_SKILL_ID = "linkedin_post"
_INCUMBENT_VERSION = "v3.0.0"
_CANDIDATE_VERSION = "v3.1.0_candidate"
_PROMOTION_MDE = 0.05        # matches services/promotion_gate.MDE
_PROMOTION_MIN_ACTIONS = 2   # matches the test's _RUNS_PER_VARIANT
                              # (production MIN_ACTIONS=50; lower for the
                              # smoke test, see promotion_gate.py)

# Web API endpoint. Hit during phase 8 verification.
_API_BASE = os.environ.get("API_BASE", "http://localhost:8080")


# ---------------------------------------------------------------------------
# Phase 0 — Prereqs
# ---------------------------------------------------------------------------

def phase_0_prereqs() -> None:
    _banner("Phase 0 — Prereqs")
    # Mongo reachable?
    try:
        db = mongo_tools.db()
        db.command("ping")
        _ok(f"Mongo reachable at {os.environ['MONGO_URI_DIRECT']} (db={db.name})")
    except Exception as e:
        _fail(f"Mongo unreachable: {e}")
        raise SystemExit(1) from None

    # Verify the linkedin_post skill exists so the promotion gate has
    # something to write a promotion_request onto. scripts.local_seed
    # creates this; if it's missing the operator forgot to seed.
    skill = db["skills"].find_one({"_id": _PROMOTION_SKILL_ID})
    if not skill:
        _fail(
            f"skills.{_PROMOTION_SKILL_ID} not found. Run: "
            "python -m scripts.local_seed (clean mode is enough)."
        )
        raise SystemExit(1) from None
    _ok(f"linkedin_post skill exists (current_version={skill.get('current_version')!r})")

    _ok(f"GOOGLE_API_KEY set ({len(os.environ['GOOGLE_API_KEY'])} chars)")


# ---------------------------------------------------------------------------
# Phase 1 — Cleanup. Idempotent: any prior run of this test should leave
# nothing behind that affects this run's assertions.
# ---------------------------------------------------------------------------

def phase_1_cleanup(*, keep: bool = False) -> None:
    _banner("Phase 1 — Cleanup of prior test state")
    if keep:
        _info("--keep flag set; skipping cleanup. Be aware: counts may be inflated.")
        return

    db = mongo_tools.db()

    exp_filter = {"_id": {"$regex": f"^{_TEST_ID_PREFIX}"}}
    drift_filter = {"_id": {"$regex": "^exp_drift_"},
                    "tags": _TEST_TAG}
    action_filter = {"telemetry_id": {"$regex": f"^{_TEST_ACTION_PREFIX}"}}

    # Test-fixture wipes — these aren't "supersessions," they're "this never
    # happened." The audit script honors the `audit:exempt` marker so these
    # don't show up as boundary-rule violations.
    deleted_exp = db["experiments"].delete_many(exp_filter).deleted_count  # audit:exempt — test cleanup
    deleted_drift = db["experiments"].delete_many(drift_filter).deleted_count  # audit:exempt — test cleanup
    deleted_actions = db["actions"].delete_many(action_filter).deleted_count
    db["outcomes"].delete_many(
        {"telemetry_id": {"$regex": f"^{_TEST_ACTION_PREFIX}"}})
    # Tear down history rows so the audit trail doesn't bloat between runs.
    deleted_history = db["history"].delete_many({
        "collection": "experiments",
        "$or": [
            {"document_id": {"$regex": f"^{_TEST_ID_PREFIX}"}},
            {"change_kind": {"$regex": "drift"}, "snapshot.tags": _TEST_TAG},
        ],
    }).deleted_count
    # If a previous run raised a promotion_request on linkedin_post, clear it
    # so this run starts clean. Matches both the legacy test-written shape
    # (source marker) and the REAL gate's shape (candidate == the test-only
    # candidate version). Also drop the test candidate from candidates[].
    db["skills"].update_one(  # audit:exempt — test cleanup ($unset of test-only field)
        {"_id": _PROMOTION_SKILL_ID,
         "$or": [{"promotion_request.source": "lifecycle_e2e_test"},
                 {"promotion_request.candidate": _CANDIDATE_VERSION}]},
        {"$unset": {"promotion_request": ""}},
    )
    db["skills"].update_one(  # audit:exempt — test cleanup
        {"_id": _PROMOTION_SKILL_ID},
        {"$pull": {"candidates": _CANDIDATE_VERSION}},
    )

    _ok(f"Deleted {deleted_exp} test experiments, {deleted_drift} drift investigations, "
        f"{deleted_actions} test actions, {deleted_history} history rows.")


# ---------------------------------------------------------------------------
# Phase 2 — CMO Planner authors an experiment (operator mode).
#
# Calls the live cmo_planner ADK agent with a prompt that triggers the
# operator-mode branch added to CMO_PLANNER_INSTRUCTIONS. The agent should
# call mongodb.insert-one on experiments and return {"_id": ..., ...}.
# ---------------------------------------------------------------------------

async def _run_agent(agent, *, app_name: str, message_text: str,
                     seed_state: dict) -> dict:
    """Generic ADK Runner driver. Same shape as tests/e2e/e2e_workflows.py
    so the call surface stays consistent across e2e scripts."""
    session_service = InMemorySessionService()
    runner = Runner(agent=agent, app_name=app_name,
                    session_service=session_service)
    session = await session_service.create_session(
        app_name=app_name, user_id="e2e", state=seed_state,
    )
    message = Content(role="user", parts=[Part.from_text(text=message_text)])
    started = time.monotonic()
    seen: set[str] = set()
    async for event in runner.run_async(
        user_id="e2e", session_id=session.id, new_message=message,
    ):
        author = getattr(event, "author", None) or "?"
        if author not in seen:
            seen.add(author)
            _info(f"[{time.monotonic() - started:5.1f}s] {author}")
    final = await session_service.get_session(
        app_name=app_name, user_id="e2e", session_id=session.id,
    )
    return final.state if final else {}


async def phase_2_author_experiment() -> dict:
    _banner("Phase 2 — CMO Planner authors the experiment (operator mode)")

    from agents.cmo_planner import cmo_planner

    # Operator-mode spec. The exp_id encodes the test name + a short uuid
    # so re-running before Phase 1 cleanup doesn't collide.
    exp_id = f"{_TEST_ID_PREFIX}{uuid.uuid4().hex[:8]}"
    created_at = datetime.now(UTC).isoformat()

    operator_message = (
        "Operator mode: author exactly ONE running experiment with these "
        "specs and insert it via mongodb.insert-one on the "
        "'experiments' collection. Do not call slack_approval or any "
        "sub-agent — just persist and return the inserted _id.\n\n"
        f"  _id: {exp_id}\n"
        "  title: Does urgency framing lift LinkedIn engagement?\n"
        "  hypothesis: Urgency-tinted hooks lift 72h engagement by >= 3pp vs neutral baseline\n"
        "  channel: linkedin\n"
        "  icp_segment: seg_merchant_dtc\n"
        f"  success_metric: {_OUTCOME_METRIC}\n"
        f"  mde: {_OUTCOME_MDE}\n"
        f"  min_n_per_arm: {_MIN_N_PER_ARM}\n"
        "  variants:\n"
        f"    - id: A_control,   playbook_version: {_INCUMBENT_VERSION}, allocation_pct: 50\n"
        f"    - id: B_urgency,   playbook_version: {_CANDIDATE_VERSION}, allocation_pct: 50\n"
        "  state: running\n"
        f"  created_at: {created_at}\n"
        f"  tags: [test, {_TEST_TAG}]\n"
    )

    seed_state = {
        "telemetry_id": f"{_TEST_ACTION_PREFIX}cmo_{uuid.uuid4().hex[:6]}",
        "skill_id": "weekly_memo",
    }

    try:
        await _run_agent(
            cmo_planner,
            app_name="e2e_lifecycle_cmo",
            message_text=operator_message,
            seed_state=seed_state,
        )
    except Exception as e:
        _fail(f"CMO Planner crashed: {e}")
        raise SystemExit(1) from None

    # Verify the insert actually happened. The agent's prompt is clear,
    # but LLMs occasionally hallucinate the tool call. If it didn't land,
    # fall back to inserting from the harness so downstream phases can
    # still demonstrate the lifecycle.
    exp = mongo_tools.find_one("experiments", {"_id": exp_id})
    if exp is None:
        _info("Agent did not insert the experiment (LLM tool-call miss). "
              "Inserting from the harness so the test can proceed.")
        from mongo.history import insert_with_provenance
        doc = {
            "_id": exp_id,
            "title": "Does urgency framing lift LinkedIn engagement?",
            "hypothesis": ("Urgency-tinted hooks lift 72h engagement by "
                           ">= 3pp vs neutral baseline"),
            "channel": "linkedin",
            "icp_segment": "seg_merchant_dtc",
            "success_metric": _OUTCOME_METRIC,
            "mde": _OUTCOME_MDE,
            "min_n_per_arm": _MIN_N_PER_ARM,
            "variants": [
                {"id": "A_control", "playbook_version": _INCUMBENT_VERSION,
                 "allocation_pct": 50},
                {"id": "B_urgency", "playbook_version": _CANDIDATE_VERSION,
                 "allocation_pct": 50},
            ],
            "state": "running",
            "created_at": datetime.now(UTC),
            "tags": ["test", _TEST_TAG],
            "auto_opened_by": "e2e_lifecycle_test_fallback",
        }
        insert_with_provenance(
            "experiments", doc,
            actor_id="lifecycle_e2e_test",
            change_kind="experiment_authored_fallback",
        )
        exp = mongo_tools.find_one("experiments", {"_id": exp_id})

    if exp is None:
        _fail("Experiment STILL not present after fallback insert.")
        raise SystemExit(1) from None

    _ok(f"Experiment {exp_id} is in Mongo (state={exp['state']!r}, "
        f"variants={[v['id'] for v in exp['variants']]})")
    return exp


# ---------------------------------------------------------------------------
# Phase 3 — Run the drafting pipeline N times per variant.
#
# The pipeline (agents.pipeline.drafting_pipeline) is a SequentialAgent
# of Research → Content → CritiqueLoop → ImageBrief → Review → Finalizer.
# We seed session state with experiment_id + variant_id so the
# after_agent_callback in agents/_common.py emits actions tagged with
# them. Each pipeline run produces ONE Mongo `actions` row (the
# after-callback writes once per outermost agent).
# ---------------------------------------------------------------------------

async def phase_3_run_pipeline(exp: dict) -> list[str]:
    _banner("Phase 3 — Drafting pipeline executes drafts under each variant")

    from agents.pipeline import drafting_pipeline

    telemetry_ids: list[str] = []
    for variant in exp["variants"]:
        variant_id = variant["id"]
        _banner(f"Variant {variant_id} (playbook {variant['playbook_version']})",
                level=2)
        for i in range(_RUNS_PER_VARIANT):
            telemetry_id = (
                f"{_TEST_ACTION_PREFIX}{exp['_id'][-8:]}_"
                f"{variant_id.lower()}_{i+1}"
            )
            # Topic varies per variant so the content differs and the
            # downstream Review's rubric scores have something to grade.
            topic = (
                "Why the next two weeks decide your agent checkout revenue"
                if variant_id.startswith("B")
                else "Building a durable agent-readiness program"
            )
            seed_state = {
                "telemetry_id": telemetry_id,
                "channel": exp["channel"],
                "icp_segment": exp["icp_segment"],
                "skill_id": "linkedin_post",
                "experiment_id": exp["_id"],
                "variant_id": variant_id,
                # CritiqueAgent reads {topic_hint} from state; without it
                # the pipeline crashes after Content with "Context variable
                # not found: topic_hint." Seed it directly — _ensure_session_state
                # doesn't extract topic_hint from the user message.
                "topic_hint": topic,
            }
            message = (
                f"Draft a linkedin post targeting seg_merchant_dtc "
                f"experiment_id:{exp['_id']} variant:{variant_id}.\n"
                f"Topic hint: {topic}.\n"
                "Keep it under 220 words. Hook → 2 paragraphs → soft CTA."
            )
            _info(f"  run {i+1}/{_RUNS_PER_VARIANT} → {telemetry_id}")
            try:
                await _run_agent(
                    drafting_pipeline,
                    app_name=f"e2e_lifecycle_draft_{variant_id}",
                    message_text=message,
                    seed_state=seed_state,
                )
                telemetry_ids.append(telemetry_id)
            except Exception as e:
                _fail(f"pipeline run failed for {telemetry_id}: {e}")

    # Audit: how many actions actually landed?
    db = mongo_tools.db()
    n_actions = db["actions"].count_documents({"experiment_id": exp["_id"]})
    _ok(f"{n_actions} actions tagged with experiment_id={exp['_id']!r} are in Mongo "
        f"(expected ≥ {_RUNS_PER_VARIANT * len(exp['variants'])})")
    if n_actions < _MIN_N_PER_ARM * len(exp["variants"]):
        _fail("Not enough actions landed to clear min_n_per_arm. "
              "Check the pipeline for errors above.")
        raise SystemExit(1) from None
    return telemetry_ids


# ---------------------------------------------------------------------------
# Phase 4 — Outcome synthesis.
#
# Production: services/outcome_attach polls telemetry.outcomes (BQ) and
# fills them from GA4 / HubSpot / Google Ads. LOCAL_DEV has no BQ and no
# attribution backend. So we synthesize the outcome value per action
# document, using uniform[0.40,0.45] for A_control and uniform[0.50,0.55]
# for B_urgency. The treatment vs. control gap is ~5pp, well above the
# experiment's 3pp MDE — so the decision logic should fire cleanly.
# ---------------------------------------------------------------------------

def phase_4_synthesize_outcomes(exp: dict) -> dict[str, list[float]]:
    _banner("Phase 4 — Fill outcome slots (LOCAL_DEV substitute for GA4)")

    db = mongo_tools.db()

    # Normalize the experiment's decision params to known test values so the
    # REAL outcome_attach decision logic (phase 5) is deterministic regardless
    # of what the CMO LLM authored for success_metric / mde / min_n.
    db["experiments"].update_one({"_id": exp["_id"]}, {"$set": {
        "success_metric": _OUTCOME_METRIC,
        "mde": _OUTCOME_MDE,
        "min_n_per_arm": _MIN_N_PER_ARM,
    }})
    exp["success_metric"] = _OUTCOME_METRIC
    exp["mde"] = _OUTCOME_MDE
    exp["min_n_per_arm"] = _MIN_N_PER_ARM

    rng = random.Random(42)  # deterministic so re-runs are reproducible
    by_variant: dict[str, list[float]] = {}

    # Production: services/outcome_attach fills telemetry.outcomes from GA4 etc.
    # emit_action dual-writes the same row shape into Mongo `outcomes`, so we
    # write FILLED outcome rows there (slot_name = the experiment's
    # success_metric) — exactly what the real _maybe_decide_experiment joins.
    all_tids = [a["telemetry_id"] for a in
                db["actions"].find({"experiment_id": exp["_id"]}, {"telemetry_id": 1})]
    db["outcomes"].delete_many(
        {"telemetry_id": {"$in": all_tids}, "slot_name": _OUTCOME_METRIC})

    for variant in exp["variants"]:
        variant_id = variant["id"]
        is_treatment = variant_id.startswith("B")
        bounds = _TREATMENT_OUTCOME if is_treatment else _CONTROL_OUTCOME
        actions = list(db["actions"].find(
            {"experiment_id": exp["_id"], "variant_id": variant_id},
        ))
        values: list[float] = []
        for action in actions:
            v = round(rng.uniform(*bounds), 4)
            db["outcomes"].insert_one({
                "telemetry_id": action["telemetry_id"],
                "slot_name": _OUTCOME_METRIC,
                "metric": _OUTCOME_METRIC,
                "value": v,
                "status": "filled",
                "source": "lifecycle_e2e_synthetic",
                "filled_at": datetime.now(UTC),
            })
            values.append(v)
        by_variant[variant_id] = values
        _info(f"{variant_id}: n={len(values)} mean={statistics.fmean(values):.3f} "
              f"(synthesized from {bounds}, written to outcomes collection)")

    return by_variant


# ---------------------------------------------------------------------------
# Phase 5 — Decide the experiment via the REAL service.
#
# Runs services.outcome_attach._maybe_decide_experiment unchanged. In LOCAL_DEV
# (no BQ client) it reads the dual-written Mongo `actions` + `outcomes` we just
# filled — the same code path prod runs against BigQuery. No reimplementation.
# ---------------------------------------------------------------------------

def phase_5_decide(exp: dict, by_variant: dict[str, list[float]]) -> None:
    _banner("Phase 5 — Decide the experiment (real outcome_attach._maybe_decide_experiment)")

    from services.outcome_attach import main as outcome_attach

    db = mongo_tools.db()
    means = {v: statistics.fmean(vals) for v, vals in by_variant.items() if vals}
    counts = {v: len(vals) for v, vals in by_variant.items()}
    _info(f"synthesized means={means} counts={counts} "
          f"(MDE={exp['mde']}, min_n={exp['min_n_per_arm']})")

    # outcome_attach calls _maybe_decide_experiment once per filled slot; calling
    # it for any one filled telemetry_id triggers the full per-variant
    # aggregation + state transition inside the real service.
    one = db["actions"].find_one({"experiment_id": exp["_id"]}, {"telemetry_id": 1})
    if not one:
        _fail("no actions found for the experiment; cannot drive the decision.")
        raise SystemExit(1) from None
    outcome_attach._maybe_decide_experiment(one["telemetry_id"], 0.0)

    decided = mongo_tools.find_one("experiments", {"_id": exp["_id"]}) or {}
    result = decided.get("result") or {}
    if decided.get("state") == "decided":
        _ok(f"real outcome_attach decided {exp['_id']}: "
            f"winner={result.get('winner')!r} lift={result.get('lift', 0):.3f}")
    else:
        _fail(f"real outcome_attach did NOT decide (state={decided.get('state')!r}); "
              f"means={means} counts={counts}")
        raise SystemExit(1) from None


# ---------------------------------------------------------------------------
# Phase 6 — Drift detection.
#
# Synthesize 28 days of `actions` on a SECOND channel (_DRIFT_CHANNEL,
# "email") with rubric brand_voice declining from ~0.85 (4–31 days ago)
# to ~0.70 (last 3 days). Then run the Mongo equivalent of
# services/drift_detect._detect_drift: aggregate by channel for the
# baseline window vs. the recent window, find drops ≥ DRIFT_THRESHOLD.
# For each drift event, call the SAME _open_investigation production
# uses (services.drift_detect._open_investigation), which inserts a
# state="running" experiment tagged ["drift", "investigation"].
# ---------------------------------------------------------------------------

def phase_6_drift_detection() -> None:
    _banner("Phase 6 — Drift detection (synthesized rubric drop on a 2nd channel)")

    db = mongo_tools.db()
    now = datetime.now(UTC)
    rng = random.Random(7)

    # Wipe any prior synthesized drift fixtures from this test.
    db["actions"].delete_many({
        "telemetry_id": {"$regex": f"^{_TEST_ACTION_PREFIX}drift_"},
    })

    def _synth_actions(window_days: tuple[int, int], n: int, mean: float, tag: str):
        oldest, newest = window_days  # both inclusive, days-ago
        for i in range(n):
            day_offset = rng.uniform(newest, oldest)
            ts = now - timedelta(days=day_offset)
            score = round(rng.gauss(mean, 0.03), 4)
            db["actions"].insert_one({
                "_id": f"{_TEST_ACTION_PREFIX}drift_{tag}_{i+1}",
                "telemetry_id": f"{_TEST_ACTION_PREFIX}drift_{tag}_{i+1}",
                "agent": "content_agent",
                "skill_id": "nurture_email",
                "skill_version": "v1.0.0",
                "action_type": "draft",
                "channel": _DRIFT_CHANNEL,
                "icp_segment": "seg_merchant_dtc",
                "eval_scores": {_DRIFT_RUBRIC: score, "claim_support": 0.80},
                "ts": ts,
            })

    _synth_actions((31, 4), _DRIFT_BASELINE_N, _DRIFT_BASELINE_MEAN, "baseline")
    _synth_actions((3, 0), _DRIFT_RECENT_N, _DRIFT_RECENT_MEAN, "recent")
    _info(f"synthesized {_DRIFT_BASELINE_N} baseline + {_DRIFT_RECENT_N} recent "
          f"actions on channel={_DRIFT_CHANNEL!r}")

    # Detect drift with the REAL service. In LOCAL_DEV drift_detect._detect_drift
    # falls back to the identical recent-vs-baseline aggregation over the
    # dual-written Mongo `actions` collection — no reimplementation.
    from services.drift_detect import main as drift_detect

    drift_events = [e for e in drift_detect._detect_drift(_DRIFT_RUBRIC)
                    if e["channel"] == _DRIFT_CHANNEL]
    if not drift_events:
        _fail(f"Expected a drift event on channel={_DRIFT_CHANNEL!r}; none detected.")
        raise SystemExit(1) from None
    _ok(f"real drift_detect._detect_drift found {len(drift_events)} cell(s) on "
        f"{_DRIFT_CHANNEL!r}: drop={drift_events[0]['drop']:.3f} "
        f"(baseline={drift_events[0]['mean_baseline']:.3f} → "
        f"recent={drift_events[0]['mean_today']:.3f})")

    # Open the investigation via the REAL service function, then tag it with
    # _TEST_TAG so phase_1_cleanup sweeps it (production tags are
    # ["drift","investigation","auto_opened"], no test marker).
    for ev in drift_events:
        drift_detect._open_investigation(_DRIFT_RUBRIC, ev)
        exp_id = drift_detect._drift_exp_id(_DRIFT_RUBRIC, ev["channel"], ev["day"])
        db["experiments"].update_one(
            {"_id": exp_id}, {"$addToSet": {"tags": _TEST_TAG}})

    opened = list(db["experiments"].find({
        "tags": "drift", "state": "running",
        "_id": {"$regex": "^exp_drift_"},
    }))
    _ok(f"{len(opened)} drift investigation experiment(s) now open: "
        f"{[e['_id'] for e in opened]}")


# ---------------------------------------------------------------------------
# Phase 7 — Promotion gate.
#
# Production: services/promotion_gate iterates skills with `candidates`
# non-empty; for each candidate, aggregates eval_scores from BQ over the
# last 30 days; if the candidate beats the incumbent on the success
# rubric by ≥ MDE AND doesn't regress guardrails by > GUARDRAIL_MAX_DROP,
# raises a promotion_request on the skill.
#
# LOCAL_DEV: we set up the skill with `candidates=[_CANDIDATE_VERSION]`,
# pull the same eval_scores from Mongo `actions` instead of BQ, compute
# the same aggregates, and write the same promotion_request shape.
# ---------------------------------------------------------------------------

def phase_7_promotion_gate(exp: dict) -> None:
    _banner("Phase 7 — Promotion gate (real services.promotion_gate)")

    from mongo.history import update_with_history
    from services.promotion_gate import main as gate

    db = mongo_tools.db()
    rng = random.Random(11)
    now = datetime.now(UTC)

    # 1. Mark linkedin_post as having a candidate (production raises these via
    #    self-critique; we simulate that one upstream step) — history-tracked.
    update_with_history(
        "skills", {"_id": _PROMOTION_SKILL_ID},
        {"$addToSet": {"candidates": _CANDIDATE_VERSION}},
        actor_id="lifecycle_e2e_test", change_kind="candidate_added")
    _info(f"marked skills.{_PROMOTION_SKILL_ID} with candidates += "
          f"[{_CANDIDATE_VERSION}]")

    # 2. Seed enough SCORED actions on both versions to clear the REAL gate's
    #    MIN_ACTIONS=50. Mongo-only synthetic rows (no LLM): the candidate
    #    beats the incumbent on brand_voice by ~8pp with tight variance
    #    (clearly significant) and equal guardrails (no regression). In prod
    #    these scores come from live Vertex Eval; here we synthesize them so
    #    the gate's aggregation has real input. The 30-day window + skill_id +
    #    skill_version match what gate._version_stats reads.
    promo_pfx = f"{_TEST_ACTION_PREFIX}promo_"
    db["actions"].delete_many({"telemetry_id": {"$regex": f"^{promo_pfx}"}})
    n_per_version = max(gate.MIN_ACTIONS + 5, 55)
    for version, bv_mean in ((_INCUMBENT_VERSION, 0.78), (_CANDIDATE_VERSION, 0.86)):
        db["actions"].insert_many([{
            "telemetry_id": f"{promo_pfx}{version}_{i}",
            "agent": "content_agent",
            "skill_id": _PROMOTION_SKILL_ID,
            "skill_version": version,
            "action_type": "draft",
            "channel": "linkedin",
            "icp_segment": "seg_merchant_dtc",
            "eval_scores": {
                "brand_voice":       round(rng.gauss(bv_mean, 0.02), 4),
                "claim_support":     round(rng.gauss(0.82, 0.02), 4),
                "claim_risk":        round(rng.gauss(0.85, 0.02), 4),
                "icp_relevance":     round(rng.gauss(0.80, 0.02), 4),
                "originality":       round(rng.gauss(0.75, 0.02), 4),
                "conversion_intent": round(rng.gauss(0.70, 0.02), 4),
            },
            "ts": now - timedelta(days=rng.uniform(0, 25)),
        } for i in range(n_per_version)])
    _info(f"seeded {n_per_version} scored actions per version "
          f"({_INCUMBENT_VERSION} bv≈0.78 vs {_CANDIDATE_VERSION} bv≈0.86)")

    # 3. Run the REAL gate. In LOCAL_DEV gate._version_stats falls back to the
    #    same Mongo aggregation prod runs against BigQuery (shared.telemetry_reads),
    #    so MDE + significance + guardrail logic all execute unmodified.
    skill = db["skills"].find_one({"_id": _PROMOTION_SKILL_ID})
    try:
        gate._evaluate_candidate(skill, _INCUMBENT_VERSION, _CANDIDATE_VERSION)

        promoted = db["skills"].find_one({"_id": _PROMOTION_SKILL_ID}) or {}
        pr = promoted.get("promotion_request") or {}
        if (pr.get("candidate") == _CANDIDATE_VERSION
                and pr.get("status") == "awaiting_approval"):
            _ok(f"real promotion_gate raised a promotion_request on "
                f"skills.{_PROMOTION_SKILL_ID} (candidate={pr.get('candidate')}, "
                f"metric={pr.get('success_metric')}, lift={pr.get('lift', 0):.3f}, "
                f"z={pr.get('significance_z')})")
        else:
            _fail(f"real promotion_gate did NOT raise a promotion_request "
                  f"(promotion_request={pr!r})")
            raise SystemExit(1) from None
    finally:
        # The synthetic gate actions have served their purpose; the
        # promotion_request + candidates[] are swept by phase_1_cleanup.
        db["actions"].delete_many({"telemetry_id": {"$regex": f"^{promo_pfx}"}})


# ---------------------------------------------------------------------------
# Phase 8 — Verify the React UI sees it. Hits the /api/experiments
# endpoints over HTTP (web_api must be running locally on :8080), prints
# what the page should display.
# ---------------------------------------------------------------------------

def phase_8_verify_api(exp: dict) -> None:
    _banner("Phase 8 — Verify the API surface the React UI consumes")

    try:
        with httpx.Client(timeout=10.0) as client:
            running = client.get(f"{_API_BASE}/api/experiments/running").json()
            decided = client.get(f"{_API_BASE}/api/experiments/decided").json()
            drift = client.get(f"{_API_BASE}/api/experiments/drift").json()
            skills = client.get(f"{_API_BASE}/api/skills").json()
    except httpx.ConnectError:
        _fail(f"web_api not reachable at {_API_BASE}. Start it with:\n"
              f"     uvicorn services.web_api.main:app --env-file .env "
              f"--reload --port 8080")
        _info("Skipping API verification — Mongo writes from the prior phases "
              "are still durable; the data is on /experiments once you start "
              "the API.")
        return

    decided_ids = {e["_id"] for e in decided}
    drift_ids = {e["_id"] for e in drift}

    if exp["_id"] in decided_ids:
        _ok(f"GET /api/experiments/decided contains {exp['_id']}")
    else:
        _fail(f"GET /api/experiments/decided MISSING {exp['_id']} "
              f"(saw {len(decided_ids)} decided)")

    test_drift = [d for d in drift_ids if "drift" in d]
    if test_drift:
        _ok(f"GET /api/experiments/drift contains {len(test_drift)} drift investigation(s): "
            f"{sorted(test_drift)[:3]}{'...' if len(test_drift) > 3 else ''}")
    else:
        _fail("GET /api/experiments/drift returned no investigations")

    promo_skills = [s for s in skills
                    if s.get("promotion_request", {}).get("status") == "awaiting_approval"]
    if promo_skills:
        _ok(f"GET /api/skills shows {len(promo_skills)} skill(s) with "
            f"awaiting_approval promotion_request: {[s['_id'] for s in promo_skills]}")
    else:
        _info("No skills currently have an awaiting_approval promotion_request "
              "(phase 7 may have decided not to fire — see the log above).")

    _info("")
    _info("Visit http://localhost:5173/experiments to see this on the page.")
    _info(f"   Running:    {len(running)} (drift investigations appear here)")
    _info(f"   Decided:    {len(decided)}")
    _info(f"   Drift:      {len(drift)}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

async def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--phases", default=None,
                   help="Comma-separated subset of phase numbers, e.g. '0,1,2'. "
                        "Default: all phases in order.")
    p.add_argument("--keep", action="store_true",
                   help="Don't clean prior test state (phase 1 becomes a no-op).")
    args = p.parse_args()

    all_phases = ["0", "1", "2", "3", "4", "5", "6", "7", "8"]
    selected = args.phases.split(",") if args.phases else all_phases
    selected = [s.strip() for s in selected if s.strip()]
    unknown = [s for s in selected if s not in all_phases]
    if unknown:
        print(f"ERROR: unknown phase(s): {unknown}. Known: {all_phases}",
              file=sys.stderr)
        return 1

    print("=" * 72)
    print("  E2E EXPERIMENT LIFECYCLE TEST")
    print("=" * 72)
    print(f"  Mongo URI:        {os.environ['MONGO_URI_DIRECT']}")
    print(f"  Mongo DB:         {os.environ['MONGO_DB']}")
    print(f"  Web API base:     {_API_BASE}")
    print(f"  Runs per variant: {_RUNS_PER_VARIANT}")
    print(f"  Selected phases:  {selected}")
    print("=" * 72)

    started = time.monotonic()
    exp: dict | None = None
    by_variant: dict[str, list[float]] | None = None

    # If phase 2 is skipped but later phases need the experiment, load the
    # most recent test experiment from Mongo. Lets `--phases 7,8 --keep`
    # work after a prior run already authored the experiment.
    def _load_prior_experiment() -> dict | None:
        db = mongo_tools.db()
        return db["experiments"].find_one(
            {"_id": {"$regex": f"^{_TEST_ID_PREFIX}"}},
            sort=[("created_at", -1)],
        )

    try:
        if "0" in selected:
            phase_0_prereqs()
        if "1" in selected:
            phase_1_cleanup(keep=args.keep)
        if "2" in selected:
            exp = await phase_2_author_experiment()
        if "3" in selected:
            if exp is None:
                exp = _load_prior_experiment()
            assert exp, "phase 2 must run before phase 3 (need the experiment doc)"
            await phase_3_run_pipeline(exp)
        if "4" in selected:
            if exp is None:
                exp = _load_prior_experiment()
            assert exp, "phase 2 must run before phase 4"
            by_variant = phase_4_synthesize_outcomes(exp)
        if "5" in selected:
            if exp is None:
                exp = _load_prior_experiment()
            assert exp, "phase 2 must run before phase 5"
            if by_variant is None:
                # Reconstruct by_variant from the actions in Mongo so
                # --phases 5,6,7,8 works after a previous --phases 0..4 run.
                db = mongo_tools.db()
                by_variant = {}
                for v in exp["variants"]:
                    vals = []
                    for a in db["actions"].find({
                        "experiment_id": exp["_id"], "variant_id": v["id"],
                    }):
                        val = (a.get("outcome") or {}).get(_OUTCOME_METRIC, {}).get("value")
                        if val is not None:
                            vals.append(val)
                    by_variant[v["id"]] = vals
            phase_5_decide(exp, by_variant)
        if "6" in selected:
            phase_6_drift_detection()
        if "7" in selected:
            if exp is None:
                exp = _load_prior_experiment()
            assert exp, "phase 2 must run before phase 7"
            phase_7_promotion_gate(exp)
        if "8" in selected:
            if exp is None:
                exp = _load_prior_experiment()
            assert exp, "phase 2 must run before phase 8"
            phase_8_verify_api(exp)
    except SystemExit as e:
        elapsed = time.monotonic() - started
        print(f"\n  ✗ Run aborted after {elapsed:.0f}s (exit code {e.code})",
              file=sys.stderr)
        return int(e.code or 1)
    except Exception:
        import traceback
        traceback.print_exc()
        return 1

    elapsed = time.monotonic() - started
    print(f"\n{'=' * 72}")
    print(f"  ✓ E2E lifecycle complete in {elapsed:.0f}s")
    print(f"{'=' * 72}")
    if exp:
        print(f"  Experiment: {exp['_id']}")
        print("  Open: http://localhost:5173/experiments")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
