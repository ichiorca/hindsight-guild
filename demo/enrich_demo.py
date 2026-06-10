"""ADDITIVE demo enrichment — fill the learning-history gaps WITHOUT wiping.

``seed_demo.py`` is destructive (wipe + reseed) and its synthetic window ends
at its frozen ``NOW`` — which leaves the dashboard pages thin on exactly the
state the demo's story is about: skill version evolution, decided experiments,
a recent rubric arc, and approvals with edit categories. This script ADDS that
state on top of whatever exists (seeded or real); it never deletes anything,
so it is safe to run against a deployment that already has live loop data
(real signals, real drafts, real miner proposals).

What it adds (all timestamps relative to the actual current date):
  1. BQ ``telemetry.actions`` — ~260 draft actions over the last 14 days with
     all-rubric eval_scores INCLUDING ``answer_extractability`` (AEO). The
     linkedin channel tells the promotion arc: stable on v0 → dip → recovery
     ABOVE baseline once ``linkedin_post@v1`` takes over (last 5 days).
     BQ-only on purpose: the Mongo ``actions`` mirror feeds the live approval
     queue, which must stay clean.
  2. BQ ``telemetry.outcomes`` + ``training.edits`` — outcome slots for
     experiment-tagged rows; edit rows with before/after + categories.
  3. Mongo ``approvals`` — ~30 founder decisions over the window with
     ``edit_categories`` (Weekly Review's edit-category breakdown).
  4. Mongo ``skills`` — version evolution that matches the telemetry:
       - ``linkedin_post``: history [v0, v1], current_version v1, plus the
         ACCEPTED self-critique proposal that produced v1 (the closed loop).
         Safe: a playbook's current_version only stamps telemetry; the
         Content agent still loads its on-disk prompt (see
         agents/pipeline.py::_resolve_skill_version_and_body).
       - ``copywriting`` (agent skill): a v1 body authored from v0 + the
         learned house-style additions, current_version v1. Safe: agent
         skill bodies are served from Mongo versions[current].body_md.
       - ``house-style`` is NOT touched — it carries the live pending
         proposal for the on-camera approval moment.
  5. Mongo ``experiments`` — one recently DECIDED experiment (the v0-vs-v1
     rollout that justified the promotion) + one running drift investigation.

Idempotent-ish: enriched telemetry uses an ``enr_`` id prefix; if BQ already
has enr_ rows the telemetry step is skipped (BQ streaming buffer makes
delete-and-rewrite unreliable). Mongo writes are upserts.

Run (against the demo Atlas + BQ project):
    PROJECT_ID=<id> ENRICH_CONFIRM=<id> MONGO_URI_DIRECT='mongodb+srv://...' \
        python -m demo.enrich_demo

Afterwards trigger ``derive-track-records`` (admin cron panel) so the derived
rollups pick up linkedin_post@v1.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import sys
from datetime import UTC, datetime, timedelta

from google.auth.exceptions import DefaultCredentialsError
from google.cloud import bigquery

# Reuse the canonical synthesizers so enriched rows are shaped exactly like
# seeded ones (queue-card raw blocks, per-agent skills_loaded patterns).
from demo.seed_demo import (  # noqa: E402  (import has module-level use_secret)
    SEED_ICPS,
    _seeded_skills_loaded,
    _synthesize_draft_raw,
)
from shared import mongo_tools

RNG = random.Random(4242)
NOW = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
WINDOW_DAYS = 14

PROJECT_ID = os.environ["PROJECT_ID"]

_SKILL_BY_CHANNEL = {"linkedin": "linkedin_post", "email": "nurture_email",
                     "blog": "blog_outline", "substack": "substack_post"}

# linkedin arc: v1 took over PROMOTED_DAYS_AGO days ago, after a dip.
PROMOTED_DAYS_AGO = 5
DIP_RANGE = (6, 10)  # days-ago window of the brand_voice dip on linkedin


def _guard() -> None:
    """Refuse unless the operator names the target project (non-destructive,
    but it mutates live skill docs + inserts telemetry)."""
    if os.environ.get("ENRICH_CONFIRM", "") == PROJECT_ID:
        return
    sys.exit(
        "REFUSING demo/enrich_demo.py — set ENRICH_CONFIRM="
        f"{PROJECT_ID} to confirm the target project. (Additive: nothing is "
        "deleted, but skills/experiments/approvals are upserted and telemetry "
        "rows are inserted.)"
    )


def _bq() -> bigquery.Client:
    """BigQuery client via ADC, falling back to the gcloud CLI's token.

    Dev laptops are often `gcloud auth login`-ed without Application Default
    Credentials; minting a short-lived token from the CLI keeps this script
    runnable there without an extra interactive auth step."""
    try:
        return bigquery.Client(project=PROJECT_ID)
    except DefaultCredentialsError:
        import subprocess

        from google.oauth2.credentials import Credentials
        token = subprocess.run(
            ["gcloud", "auth", "print-access-token"], shell=(os.name == "nt"),
            capture_output=True, text=True, check=True).stdout.strip()
        return bigquery.Client(project=PROJECT_ID,
                               credentials=Credentials(token=token))


def _already_enriched(bq: bigquery.Client) -> bool:
    sql = (f"SELECT COUNT(*) AS n FROM `{PROJECT_ID}.telemetry.actions` "
           f"WHERE telemetry_id LIKE 'enr_%'")
    row = next(iter(bq.query(sql).result()), None)
    return bool(row and row.n)


# ---------------------------------------------------------------------------
# 1. telemetry actions — the 14-day rubric arc, AEO scores, v0→v1 story
# ---------------------------------------------------------------------------

def _linkedin_scores(days_ago: int) -> tuple[str, float]:
    """(skill_version, brand_voice mean) telling the promotion arc."""
    if days_ago < PROMOTED_DAYS_AGO:
        return "v1", 0.87          # post-promotion: above old baseline
    if DIP_RANGE[0] <= days_ago <= DIP_RANGE[1]:
        return "v0", 0.70          # the dip that triggered the loop
    return "v0", 0.80              # old baseline


def enrich_actions() -> list[dict]:
    actions: list[dict] = []
    volume = 260
    for i in range(volume):
        days_ago = RNG.randint(0, WINDOW_DAYS)
        ts = NOW - timedelta(days=days_ago, hours=RNG.randint(0, 23))
        agent_type = RNG.choices(
            ["content", "research", "review", "cmo_planner"],
            weights=[70, 15, 10, 5])[0]
        channel = RNG.choices(
            ["linkedin", "email", "blog", "substack"],
            weights=[45, 25, 15, 15])[0]

        skill_version = "v0"
        if channel == "linkedin":
            skill_version, bv_mean = _linkedin_scores(days_ago)
        else:
            bv_mean = 0.82
        brand_voice = max(0.0, min(1.0, RNG.gauss(bv_mean, 0.05)))
        claim_support = max(0.0, min(1.0, RNG.gauss(0.86, 0.05)))
        # AEO rubric ramps up over the window (the AEO loop maturing).
        aeo_mean = 0.80 if days_ago < PROMOTED_DAYS_AGO else 0.68
        answer_extractability = max(0.0, min(1.0, RNG.gauss(aeo_mean, 0.06)))

        is_draft = agent_type == "content"
        icp = RNG.choice(SEED_ICPS) if is_draft else None
        raw: dict = {"seeded": True, "enriched": True}
        if is_draft:
            raw.update(_synthesize_draft_raw(
                channel=channel, icp=icp,
                brand_voice=brand_voice, claim_support=claim_support))

        tid = (f"enr_{ts.strftime('%Y%m%d')}_"
               f"{hashlib.sha256(str(i).encode()).hexdigest()[:6]}")
        actions.append({
            "telemetry_id": tid,
            "ts": ts.isoformat(),
            "agent": f"{agent_type}_agent",
            "skill_id": _SKILL_BY_CHANNEL[channel],
            "skills_loaded": _seeded_skills_loaded(
                agent_type, in_drift=(channel == "linkedin"
                                       and skill_version == "v0"
                                       and bv_mean < 0.75)),
            "skill_version": skill_version,
            "action_type": (f"draft_{channel}" if is_draft
                            else f"{agent_type}_op"),
            "channel": channel,
            "experiment_id": ("exp_linkedin_v1_rollout"
                              if channel == "linkedin" and days_ago <= 8
                              and RNG.random() < 0.5 else None),
            "eval_scores": ({
                "brand_voice": brand_voice,
                "claim_support": claim_support,
                "answer_extractability": answer_extractability,
            } if is_draft else None),
            "model_armor": {"decision": "allow", "categories": []},
            "raw": raw,
        })
    # JSON-typed columns must be JSON-encoded STRINGS for the streaming
    # insert API (same rule as shared/telemetry.py) — dicts are rejected
    # row-by-row with "field is not a record".
    bq_rows = []
    for a in actions:
        row = dict(a)
        for json_col in ("eval_scores", "model_armor", "raw"):
            if row.get(json_col) is not None:
                row[json_col] = json.dumps(row[json_col])
        bq_rows.append(row)
    errors = _bq().insert_rows_json(f"{PROJECT_ID}.telemetry.actions", bq_rows)
    if errors:
        sys.exit(f"BQ actions insert errors: {errors[:3]}")
    return actions


def enrich_outcomes(actions: list[dict]) -> int:
    rows = []
    for a in actions:
        if not a.get("experiment_id"):
            continue
        ts = datetime.fromisoformat(a["ts"])
        filled = RNG.random() < 0.8
        # v1 arm outcomes run visibly hotter — the experiment's whole point.
        mean = 0.052 if a["skill_version"] == "v1" else 0.038
        rows.append({
            "telemetry_id": a["telemetry_id"],
            "slot_name": "engagement_72h", "metric": "engagement",
            "source": "linkedin_ads",
            "expected_by": (ts + timedelta(hours=72)).isoformat(),
            "filled_at": (ts + timedelta(hours=80)).isoformat() if filled else None,
            "value": max(0.0, RNG.gauss(mean, 0.008)) if filled else None,
            "status": "filled" if filled else "pending",
        })
    if rows:
        errors = _bq().insert_rows_json(f"{PROJECT_ID}.telemetry.outcomes", rows)
        if errors:
            sys.exit(f"BQ outcomes insert errors: {errors[:3]}")
    return len(rows)


# ---------------------------------------------------------------------------
# 2. approvals (Mongo) + edits (BQ) — founder decisions with edit categories
# ---------------------------------------------------------------------------

_EDIT_CATEGORY_POOL = [
    ["softened_tone"],
    ["trimmed_length"],
    ["added_evidence"],
    ["sharpened_hook"],
    ["softened_tone", "trimmed_length"],
    ["added_evidence", "sharpened_hook"],
]

_REJECTION_REASONS = [
    "too vague — no concrete outcome for the reader",
    "claim not in the approved messaging library",
    "reads generic; missing our customer voice",
    "hook buried below the fold",
]


def enrich_approvals_and_edits(actions: list[dict]) -> tuple[int, int]:
    drafts = [a for a in actions if a.get("eval_scores")]
    sample = RNG.sample(drafts, min(30, len(drafts)))
    approvals, edits = [], []
    for a in sample:
        decided_at = datetime.fromisoformat(a["ts"]) + timedelta(
            hours=RNG.randint(2, 8))
        decision = RNG.choices(["approve", "edit", "reject"],
                               weights=[55, 30, 15])[0]
        doc: dict = {
            "telemetry_id": a["telemetry_id"],
            "decision": decision,
            "decided_by": "rohit",
            "decided_at": decided_at,
        }
        if decision == "edit":
            doc["edit_categories"] = RNG.choice(_EDIT_CATEGORY_POOL)
            edits.append({
                "edit_id": f"edit_{a['telemetry_id']}",
                "telemetry_id": a["telemetry_id"],
                "ts": decided_at.isoformat(),
                "before_text": (a["raw"].get("draft")
                                if isinstance(a["raw"].get("draft"), str)
                                else "Original draft content."),
                "after_text": "Founder-edited draft content.",
                "edit_categories": doc["edit_categories"],
            })
        elif decision == "reject":
            doc["rejection_reason"] = RNG.choice(_REJECTION_REASONS)
        approvals.append(doc)
    db = mongo_tools.db()
    for doc in approvals:
        db["approvals"].update_one(
            {"telemetry_id": doc["telemetry_id"]}, {"$set": doc}, upsert=True)
    if edits:
        errors = _bq().insert_rows_json(f"{PROJECT_ID}.training.edits", edits)
        if errors:
            sys.exit(f"BQ edits insert errors: {errors[:3]}")
    return len(approvals), len(edits)


# ---------------------------------------------------------------------------
# 3. skill evolution — the closed loop's visible artifact
# ---------------------------------------------------------------------------

_COPYWRITING_V1_ADDENDUM = """

## Learned from founder edits (v1 — promoted {date})

These rules were mined from 14 days of founder edit patterns by the voice
miner and verified by the promotion gate before this version went live:

- **Front-load the outcome.** The founder moved the concrete result into the
  first sentence in 9 of 12 edited drafts. Open with what changed, then why.
- **One claim per draft, sourced.** Edits consistently cut secondary claims;
  keep the single strongest one and name its source inline.
- **Cut hedge words.** "can help", "may improve", "potentially" were removed
  in every edit that touched tone — state what the work does.
"""


def _evolve_linkedin_post(db) -> None:
    promoted_at = NOW - timedelta(days=PROMOTED_DAYS_AGO)
    db["skills"].update_one({"_id": "linkedin_post"}, {"$set": {
        "current_version": "v1",
        "history": ["v0", "v1"],
        "candidates": [],
        "self_critique_proposal": {
            "miner": "voice",
            "issue": ("Hooks routinely buried mid-draft; founder edits moved "
                      "the concrete outcome into line 1 in 9/12 edited posts "
                      "during the brand-voice dip."),
            "proposed_change": ("Playbook v1: open with the measurable "
                                "outcome, one sourced claim max, no hedge "
                                "words. Candidate A/B'd as "
                                "exp_linkedin_v1_rollout before promotion."),
            "confidence": "high",
            "evidence_count": 12,
            "proposed_at": promoted_at - timedelta(days=4),
            "status": "accepted",
            "candidate_id": "v1",
            "decided_at": promoted_at,
        },
    }})


def _evolve_copywriting(db) -> None:
    doc = db["skills"].find_one({"_id": "copywriting"}) or {}
    versions = doc.get("versions") or {}
    v0_body = (versions.get("v0") or {}).get("body_md") or ""
    if not v0_body:
        print("  copywriting: no v0 body in Mongo — skipping agent-skill evolution")
        return
    promoted_at = NOW - timedelta(days=PROMOTED_DAYS_AGO)
    v1_body = v0_body.rstrip() + _COPYWRITING_V1_ADDENDUM.format(
        date=promoted_at.date().isoformat())
    history = doc.get("history") or ["v0"]
    if "v1" not in history:
        history = [*history, "v1"]
    db["skills"].update_one({"_id": "copywriting"}, {"$set": {
        "versions.v1": {"body_md": v1_body, "source": "self_critique:voice"},
        "current_version": "v1",
        "history": history,
        "candidates": [],
    }})


def evolve_skills() -> None:
    db = mongo_tools.db()
    _evolve_linkedin_post(db)
    _evolve_copywriting(db)


# ---------------------------------------------------------------------------
# 4. experiments — one decided (the promotion's evidence), one drift probe
# ---------------------------------------------------------------------------

def enrich_experiments() -> int:
    db = mongo_tools.db()
    decided_at = NOW - timedelta(days=PROMOTED_DAYS_AGO)
    docs = [
        {
            "_id": "exp_linkedin_v1_rollout",
            "title": "LinkedIn playbook v1 (front-loaded outcome) vs v0",
            "hypothesis": ("Drafts that open with the measurable outcome and "
                           "carry exactly one sourced claim lift engagement "
                           "and brand_voice vs the v0 playbook."),
            "icp_segment": "seg_merchant_dtc",
            "channel": "linkedin",
            "variants": [
                {"id": "A_incumbent_v0", "playbook_version": "v0",
                 "allocation_pct": 50},
                {"id": "B_candidate_v1", "playbook_version": "v1",
                 "allocation_pct": 50},
            ],
            "success_metric": "engagement_72h",
            "mde": 0.05,
            "state": "decided",
            "created_at": decided_at - timedelta(days=6),
            "decided_at": decided_at,
            "result": {"winner": "B_candidate_v1", "lift": 0.37,
                       "p_value": 0.03, "root_cause_confirmed": True},
            "lesson": ("Front-loading the outcome beat the incumbent on "
                       "engagement_72h (+37% rel.) and recovered brand_voice "
                       "above its pre-dip baseline. Promoted to "
                       "linkedin_post@v1."),
            "tags": ["promotion", "playbook"],
        },
        {
            "_id": "exp_drift_substack_aeo",
            "title": "Drift probe: substack answer_extractability plateau",
            "hypothesis": ("Substack drafts plateaued on the AEO rubric while "
                           "other channels improved — testing whether the "
                           "section-heading rewrite pattern transfers."),
            "icp_segment": "seg_merchant_dtc",
            "channel": "substack",
            "variants": [
                {"id": "A_current", "playbook_version": "v0",
                 "allocation_pct": 70},
                {"id": "B_aeo_headings", "playbook_version": "v0_aeo_candidate",
                 "allocation_pct": 30},
            ],
            "success_metric": "answer_extractability",
            "mde": 0.05,
            "state": "running",
            "created_at": NOW - timedelta(days=2),
            "decided_at": None,
            "tags": ["drift", "investigation"],
        },
    ]
    for d in docs:
        db["experiments"].update_one({"_id": d["_id"]}, {"$set": d}, upsert=True)
    return len(docs)


# ---------------------------------------------------------------------------
# Candidate A/B staging — give the promotion gate something REAL to decide.
#
# The gate's playbook path (services/promotion_gate/main.py::_evaluate_candidate)
# compares candidate-vs-incumbent telemetry: >=50 scored actions per arm in 30d,
# success-metric lift >= MDE (0.05) at z >= 1.96, guardrails within 3pp. This
# stages exactly that evidence for nurture_email (success metric:
# conversion_intent) — openly seeded arms, but the GATE'S DECISION is computed
# for real by the deployed job, and the founder's approval flips
# current_version through the real promote_after_approval path.
# ---------------------------------------------------------------------------

_AB_PREFIX = "enr_ab_"


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def _ab_arm_rows(version: str, n: int, ci_mean: float,
                 days_span: float) -> list[dict]:
    rows = []
    for i in range(n):
        ts = NOW - timedelta(days=RNG.uniform(0, days_span),
                             hours=RNG.randint(0, 23))
        brand_voice = _clamp(RNG.gauss(0.82, 0.05))
        claim_support = _clamp(RNG.gauss(0.86, 0.04))
        scores = {
            "brand_voice": brand_voice,
            "claim_support": claim_support,
            "claim_risk": _clamp(RNG.gauss(0.84, 0.04)),
            "icp_relevance": _clamp(RNG.gauss(0.80, 0.05)),
            "originality": _clamp(RNG.gauss(0.74, 0.06)),
            "conversion_intent": _clamp(RNG.gauss(ci_mean, 0.07)),
            "answer_extractability": _clamp(RNG.gauss(0.72, 0.06)),
        }
        raw = {"seeded": True, "enriched": True}
        raw.update(_synthesize_draft_raw(
            channel="email", icp=RNG.choice(SEED_ICPS),
            brand_voice=brand_voice, claim_support=claim_support))
        tid = (f"{_AB_PREFIX}{version}_"
               f"{hashlib.sha256(f'{version}:{i}'.encode()).hexdigest()[:8]}")
        rows.append({
            "telemetry_id": tid,
            "ts": ts.isoformat(),
            "agent": "content_agent",
            "skill_id": "nurture_email",
            "skills_loaded": ["house-style", "copywriting", "emails"],
            "skill_version": version,
            "action_type": "draft_email",
            "channel": "email",
            "experiment_id": "exp_nurture_email_v1_rollout",
            "eval_scores": scores,
            "model_armor": {"decision": "allow", "categories": []},
            "raw": raw,
        })
    return rows


def stage_nurture_email_candidate() -> None:
    """Stage the v0-vs-v1 A/B for nurture_email so the gate can decide."""
    db = mongo_tools.db()
    skill = db["skills"].find_one({"_id": "nurture_email"}) or {}
    if skill.get("promotion_request"):
        print("nurture_email already has a promotion_request — nothing to stage")
        return

    bq = _bq()
    sql = (f"SELECT COUNT(*) AS n FROM `{PROJECT_ID}.telemetry.actions` "
           f"WHERE telemetry_id LIKE '{_AB_PREFIX}%'")
    have = next(iter(bq.query(sql).result())).n
    if have:
        print(f"A/B telemetry already present ({have} rows) — skipping insert")
    else:
        # v1 lift on conversion_intent: 0.74 vs 0.62 = +0.12 (MDE is 0.05);
        # sd 0.07 @ n=60/arm gives z ~ 9 — clearly significant. Guardrails
        # are drawn from the same distributions on both arms.
        rows = _ab_arm_rows("v0", 60, 0.62, 12) + _ab_arm_rows("v1", 60, 0.74, 8)
        bq_rows = []
        for a in rows:
            row = dict(a)
            for json_col in ("eval_scores", "model_armor", "raw"):
                row[json_col] = json.dumps(row[json_col])
            bq_rows.append(row)
        errors = bq.insert_rows_json(f"{PROJECT_ID}.telemetry.actions", bq_rows)
        if errors:
            sys.exit(f"BQ A/B insert errors: {errors[:3]}")
        print(f"inserted {len(rows)} A/B telemetry rows (60 per arm)")

    # Candidate registration — what the gate's playbook query keys on.
    history = skill.get("history") or ["v0"]
    db["skills"].update_one({"_id": "nurture_email"}, {"$set": {
        "candidates": ["v1"],
    }})
    db["experiments"].update_one({"_id": "exp_nurture_email_v1_rollout"}, {"$set": {
        "_id": "exp_nurture_email_v1_rollout",
        "title": "Nurture email v1 (sub-40-char subjects) vs v0",
        "hypothesis": ("Mobile-first subject lines (<40 chars, hook in the "
                       "first 5 words) lift conversion_intent vs the v0 "
                       "playbook."),
        "icp_segment": "seg_merchant_dtc",
        "channel": "email",
        "variants": [
            {"id": "A_incumbent_v0", "playbook_version": "v0", "allocation_pct": 50},
            {"id": "B_candidate_v1", "playbook_version": "v1", "allocation_pct": 50},
        ],
        "success_metric": "conversion_intent",
        "mde": 0.05,
        "state": "running",
        "created_at": NOW - timedelta(days=12),
        "decided_at": None,
        "tags": ["playbook", "rollout"],
    }}, upsert=True)
    print("staged: nurture_email.candidates=['v1'] + experiment doc "
          f"(history={history}).\n"
          "NEXT: run the promotion-gate job — it will evaluate the arms and, "
          "if the lift holds, raise a promotion_request for founder approval.")


# ---------------------------------------------------------------------------

def main() -> None:
    _guard()
    bq = _bq()
    print(f"Enriching {PROJECT_ID} (window: last {WINDOW_DAYS}d, NOW={NOW})")

    if _already_enriched(bq):
        print("telemetry: enr_ rows already present in BQ — skipping "
              "actions/outcomes/edits (Mongo upserts still applied)")
        actions: list[dict] = []
        n_out = 0
        n_appr = n_edits = 0
    else:
        actions = enrich_actions()
        n_out = enrich_outcomes(actions)
        n_appr, n_edits = enrich_approvals_and_edits(actions)

    evolve_skills()
    n_exp = enrich_experiments()

    print(
        f"Enriched:\n"
        f"  BQ    -> {len(actions)} actions, {n_out} outcome slots, "
        f"{n_edits} edits\n"
        f"  Mongo -> {n_appr} approvals (+edit_categories), "
        f"{n_exp} experiments upserted,\n"
        f"          linkedin_post v0->v1 (promoted "
        f"{PROMOTED_DAYS_AGO}d ago, accepted voice proposal),\n"
        f"          copywriting v0->v1 (body authored from founder-edit "
        f"learnings)\n"
        f"NEXT: trigger 'derive-track-records' from the admin cron panel so\n"
        f"      derived rollups pick up linkedin_post@v1."
    )


if __name__ == "__main__":
    if "--stage-candidate" in sys.argv:
        _guard()
        stage_nurture_email_candidate()
    else:
        main()
