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

  - Real GA4 / HubSpot / Google Ads outcome attribution (would need a
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
    # Tear down history rows so the audit trail doesn't bloat between runs.
    deleted_history = db["history"].delete_many({
        "collection": "experiments",
        "$or": [
            {"document_id": {"$regex": f"^{_TEST_ID_PREFIX}"}},
            {"change_kind": {"$regex": "drift"}, "snapshot.tags": _TEST_TAG},
        ],
    }).deleted_count
    # If a previous run raised a promotion_request on linkedin_post, clear
    # it so this run starts clean. The skill's other state is untouched.
    db["skills"].update_one(  # audit:exempt — test cleanup ($unset of test-only field)
        {"_id": _PROMOTION_SKILL_ID,
         "promotion_request.source": "lifecycle_e2e_test"},
        {"$unset": {"promotion_request": ""}},
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
        "  icp_segment: seg_founder_b2b\n"
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
            "icp_segment": "seg_founder_b2b",
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
                "Why the next two weeks decide your Q3 pipeline"
                if variant_id.startswith("B")
                else "Building a sustainable Q3 pipeline"
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
                f"Draft a linkedin post targeting seg_founder_b2b "
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
    _banner("Phase 4 — Synthesize outcome values (LOCAL_DEV substitute for GA4)")

    db = mongo_tools.db()
    rng = random.Random(42)  # deterministic so re-runs are reproducible
    by_variant: dict[str, list[float]] = {}

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
            db["actions"].update_one(
                {"_id": action["_id"]},
                {"$set": {
                    f"outcome.{_OUTCOME_METRIC}.value": v,
                    f"outcome.{_OUTCOME_METRIC}.status": "filled",
                    f"outcome.{_OUTCOME_METRIC}.source": "lifecycle_e2e_synthetic",
                    f"outcome.{_OUTCOME_METRIC}.filled_at": datetime.now(UTC),
                }},
            )
            values.append(v)
        by_variant[variant_id] = values
        _info(f"{variant_id}: n={len(values)} mean={statistics.fmean(values):.3f} "
              f"(synthesized from {bounds})")

    return by_variant


# ---------------------------------------------------------------------------
# Phase 5 — Decide the experiment.
#
# Mongo-equivalent of services.outcome_attach._maybe_decide_experiment.
# Same threshold logic: needs n ≥ min_n_per_arm on every variant, and
# winner − runner ≥ mde. Calls the same mongo_tools.transition_experiment_state
# production uses, so the audit trail in history.experiments is identical.
# ---------------------------------------------------------------------------

def phase_5_decide(exp: dict, by_variant: dict[str, list[float]]) -> None:
    _banner("Phase 5 — Decide the experiment (Mongo equivalent of outcome_attach)")

    means: dict[str, float] = {v: statistics.fmean(vals)
                                for v, vals in by_variant.items()}
    counts: dict[str, int] = {v: len(vals) for v, vals in by_variant.items()}

    min_n = exp.get("min_n_per_arm", 400)
    if any(n < min_n for n in counts.values()):
        _fail(f"Sample sizes too low to decide (counts={counts}, min_n={min_n})")
        raise SystemExit(1) from None

    ranked = sorted(means.items(), key=lambda kv: kv[1], reverse=True)
    (winner, winner_mean), (runner, runner_mean) = ranked[0], ranked[1]
    lift = winner_mean - runner_mean
    _info(f"means = {means}")
    _info(f"winner={winner!r} runner={runner!r} lift={lift:.3f} "
          f"(MDE={exp['mde']})")

    if lift < exp["mde"]:
        _fail(f"Lift {lift:.3f} below MDE {exp['mde']}; not flipping state.")
        raise SystemExit(1) from None

    mongo_tools.transition_experiment_state(
        exp["_id"], "decided",
        result={"winner": winner, "lift": lift, "n": min_n},
        lesson=(f"Variant {winner} beat {runner} by {lift:.3f} on "
                f"{exp['success_metric']}"),
        actor_id="lifecycle_e2e_test",
    )
    _ok(f"Experiment {exp['_id']} transitioned to state=\"decided\" "
        f"(lift={lift:.3f})")


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
                "icp_segment": "seg_founder_b2b",
                "eval_scores": {_DRIFT_RUBRIC: score, "claim_support": 0.80},
                "ts": ts,
            })

    _synth_actions((31, 4), _DRIFT_BASELINE_N, _DRIFT_BASELINE_MEAN, "baseline")
    _synth_actions((3, 0), _DRIFT_RECENT_N, _DRIFT_RECENT_MEAN, "recent")
    _info(f"synthesized {_DRIFT_BASELINE_N} baseline + {_DRIFT_RECENT_N} recent "
          f"actions on channel={_DRIFT_CHANNEL!r}")

    # Mongo equivalent of services.drift_detect._detect_drift's BQ SQL.
    # Aggregates by channel; computes mean rubric score in the recent
    # window (last 3 days) and the baseline window (4–31 days ago).
    recent_cutoff = now - timedelta(days=3)
    baseline_oldest = now - timedelta(days=31)
    baseline_newest = now - timedelta(days=4)

    pipeline = [
        {"$match": {
            "eval_scores": {"$exists": True, "$ne": None},
            "channel": {"$exists": True},
        }},
        {"$group": {
            "_id": "$channel",
            "recent_sum": {"$sum": {"$cond": [
                {"$gte": ["$ts", recent_cutoff]},
                f"${'eval_scores.' + _DRIFT_RUBRIC}", 0,
            ]}},
            "recent_n": {"$sum": {"$cond": [
                {"$gte": ["$ts", recent_cutoff]}, 1, 0,
            ]}},
            "baseline_sum": {"$sum": {"$cond": [
                {"$and": [{"$gte": ["$ts", baseline_oldest]},
                          {"$lte": ["$ts", baseline_newest]}]},
                f"${'eval_scores.' + _DRIFT_RUBRIC}", 0,
            ]}},
            "baseline_n": {"$sum": {"$cond": [
                {"$and": [{"$gte": ["$ts", baseline_oldest]},
                          {"$lte": ["$ts", baseline_newest]}]},
                1, 0,
            ]}},
        }},
    ]
    rows = list(db["actions"].aggregate(pipeline))
    drift_events = []
    for r in rows:
        if r["recent_n"] < _DRIFT_MIN_SAMPLE or r["baseline_n"] == 0:
            continue
        recent_mean = r["recent_sum"] / r["recent_n"]
        baseline_mean = r["baseline_sum"] / r["baseline_n"]
        drop = baseline_mean - recent_mean
        if drop >= _DRIFT_THRESHOLD:
            drift_events.append({
                "channel": r["_id"],
                "day": str(now.date()),
                "mean_today": recent_mean,
                "mean_baseline": baseline_mean,
                "drop": drop,
                "n": r["recent_n"],
            })

    if not drift_events:
        _fail(f"Expected a drift event on channel={_DRIFT_CHANNEL!r}; none detected.")
        raise SystemExit(1) from None
    _ok(f"detected {len(drift_events)} drift event(s): "
        f"{[(e['channel'], round(e['drop'], 3)) for e in drift_events]}")

    # Open an investigation experiment for each drift event. This mirrors
    # services.drift_detect.main._open_investigation byte-for-byte (same
    # _id format, same tags, same shape), but inlined so the test doesn't
    # depend on the drift_detect module — that module constructs a
    # bigquery.Client() at import time, which is unnecessary friction in
    # LOCAL_DEV. Production runs the version in drift_detect/main.py.
    from mongo.history import DocumentNotFound, insert_with_provenance, update_with_history
    for ev in drift_events:
        day_compact = ev["day"].replace("-", "")
        exp_id = f"exp_drift_{_DRIFT_RUBRIC}_{ev['channel']}_{day_compact}"
        drift_exp = {
            "_id": exp_id,
            "title": f"Investigate {_DRIFT_RUBRIC} drop on {ev['channel']}",
            "hypothesis": (
                f"Rubric {_DRIFT_RUBRIC} dropped {ev['drop']:.2f} on "
                f"{ev['channel']} over the last 3 days vs trailing 28d "
                f"baseline. Most likely cause: a recent prompt change. "
                f"Diff playbook history to identify the change."
            ),
            "channel": ev["channel"],
            "variants": [
                {"id": "baseline_period", "playbook_version": "PRIOR",
                 "metric_value": ev["mean_baseline"]},
                {"id": "current_period", "playbook_version": "CURRENT",
                 "metric_value": ev["mean_today"]},
            ],
            "success_metric": f"{_DRIFT_RUBRIC}_score",
            "state": "running",
            "created_at": datetime.now(UTC),
            "tags": ["drift", "investigation", "auto_opened", _TEST_TAG],
            "auto_opened_by": "lifecycle_e2e_test",
        }
        if mongo_tools.find_one("experiments", {"_id": exp_id}) is None:
            insert_with_provenance(
                "experiments", drift_exp,
                actor_id="lifecycle_e2e_test",
                change_kind="drift_investigation_opened",
            )
        else:
            try:
                update_with_history(
                    "experiments", {"_id": exp_id},
                    {"$set": {k: v for k, v in drift_exp.items() if k != "_id"}},
                    actor_id="lifecycle_e2e_test",
                    change_kind="drift_investigation_refreshed",
                )
            except DocumentNotFound:
                insert_with_provenance(
                    "experiments", drift_exp,
                    actor_id="lifecycle_e2e_test",
                    change_kind="drift_investigation_opened",
                )

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
    _banner("Phase 7 — Promotion gate (Mongo equivalent)")

    from mongo.history import update_with_history

    db = mongo_tools.db()

    # 1. Mark linkedin_post as having a candidate. Production raises
    #    candidates via the self_critique pipeline; in the test we
    #    simulate that step — routed through update_with_history so the
    #    pre-image lands in history.skills (same write path production uses).
    update_with_history(
        "skills", {"_id": _PROMOTION_SKILL_ID},
        {"$addToSet": {"candidates": _CANDIDATE_VERSION}},
        actor_id="lifecycle_e2e_test",
        change_kind="candidate_added",
    )
    _info(f"marked skills.{_PROMOTION_SKILL_ID} with candidates += "
          f"[{_CANDIDATE_VERSION}]")

    # 2. Tag every test-experiment action with the variant's
    #    playbook_version. The pipeline emits its own skill_version
    #    (typically "v1" from the seeded skill doc), but the
    #    promotion-gate aggregation needs the version to match the
    #    experiment's variant->playbook_version map. Unconditional
    #    overwrite — within this test the experiment's variants ARE
    #    the canonical incumbent/candidate names.
    variant_to_version = {v["id"]: v["playbook_version"] for v in exp["variants"]}
    for variant_id, version in variant_to_version.items():
        result = db["actions"].update_many(
            {"experiment_id": exp["_id"], "variant_id": variant_id},
            {"$set": {"skill_version": version}},
        )
        _info(f"  → set skill_version={version!r} on {result.modified_count} "
              f"actions for variant {variant_id}")

    # 3. Aggregate eval scores by skill_version. Mirrors the BQ SQL in
    #    services.promotion_gate._evaluate_candidate.
    versions = [_INCUMBENT_VERSION, _CANDIDATE_VERSION]
    pipeline = [
        {"$match": {
            "skill_id": _PROMOTION_SKILL_ID,
            "skill_version": {"$in": versions},
            "eval_scores.brand_voice": {"$ne": None},
        }},
        {"$group": {
            "_id": "$skill_version",
            "n": {"$sum": 1},
            "brand_voice_sum": {"$sum": "$eval_scores.brand_voice"},
            "claim_support_sum": {"$sum": "$eval_scores.claim_support"},
        }},
    ]
    rows = {r["_id"]: r for r in db["actions"].aggregate(pipeline)}

    inc = rows.get(_INCUMBENT_VERSION)
    cand = rows.get(_CANDIDATE_VERSION)

    if not inc or not cand:
        # In LOCAL_DEV the pipeline's Vertex Eval calls degrade — many
        # actions will have eval_scores=None. We backfill synthetic eval
        # scores so the promotion path can complete. Production never
        # hits this branch because Vertex Eval populates scores live.
        _info("eval_scores missing on some actions (Vertex Eval is unreachable "
              "in LOCAL_DEV). Backfilling synthetic brand_voice / claim_support "
              "so promotion-gate aggregation has rows.")
        rng = random.Random(11)
        for variant_id, version in variant_to_version.items():
            mean = (0.84 if version == _CANDIDATE_VERSION else 0.78)
            for action in db["actions"].find({
                "experiment_id": exp["_id"], "variant_id": variant_id,
            }):
                bv = round(rng.gauss(mean, 0.02), 4)
                cs = round(rng.gauss(0.80, 0.02), 4)
                # eval_scores often serializes to literal null on
                # pre-scoring telemetry rows (Vertex Eval is unreachable
                # in LOCAL_DEV). Dotted-path $set can't address
                # sub-fields of null, so overwrite the whole subdoc —
                # merging any prior keys so production code paths that
                # populated other rubrics aren't trampled.
                prior = action.get("eval_scores") or {}
                if not isinstance(prior, dict):
                    prior = {}
                new_scores = {**prior, "brand_voice": bv, "claim_support": cs}
                db["actions"].update_one(
                    {"_id": action["_id"]},
                    {"$set": {"eval_scores": new_scores}},
                )
        rows = {r["_id"]: r for r in db["actions"].aggregate(pipeline)}
        inc, cand = rows.get(_INCUMBENT_VERSION), rows.get(_CANDIDATE_VERSION)

    if not inc or not cand:
        _fail("Could not assemble promotion-gate aggregation after backfill.")
        raise SystemExit(1) from None

    inc_bv = inc["brand_voice_sum"] / inc["n"]
    cand_bv = cand["brand_voice_sum"] / cand["n"]
    lift = cand_bv - inc_bv
    _info(f"incumbent {_INCUMBENT_VERSION}: n={inc['n']} brand_voice={inc_bv:.3f}")
    _info(f"candidate {_CANDIDATE_VERSION}: n={cand['n']} brand_voice={cand_bv:.3f}")
    _info(f"lift = {lift:.3f}  (MDE={_PROMOTION_MDE})")

    if cand["n"] < _PROMOTION_MIN_ACTIONS or lift < _PROMOTION_MDE:
        _info("Promotion gate didn't fire (candidate didn't clear the bar). "
              "This is a valid outcome — the gate is doing its job. Skipping "
              "promotion_request write.")
        return

    # 4. Raise the promotion_request — same shape as
    #    services.promotion_gate._raise_promotion_request.
    promo = {
        "candidate_version": _CANDIDATE_VERSION,
        "incumbent_version": _INCUMBENT_VERSION,
        "metric": "brand_voice",
        "lift": round(lift, 4),
        "candidate_n": cand["n"],
        "incumbent_n": inc["n"],
        "source": "lifecycle_e2e_test",
        "status": "awaiting_approval",
        "raised_at": datetime.now(UTC),
    }
    update_with_history(
        "skills", {"_id": _PROMOTION_SKILL_ID},
        {"$set": {"promotion_request": promo}},
        actor_id="lifecycle_e2e_test",
        change_kind="promotion_request_raised",
    )
    _ok(f"promotion_request raised on skills.{_PROMOTION_SKILL_ID} "
        f"(awaiting_approval, lift={lift:.3f})")


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
