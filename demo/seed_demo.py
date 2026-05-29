"""Seed the system with 30 days of synthetic-but-consistent operation.

Thin orchestrator. The actual MongoDB data definitions live in mongo/data/.
This module:
  1. wipes BigQuery + Mongo
  2. delegates to mongo.cli's seed loader for Mongo content
  3. writes calibration JSONLs to GCS
  4. generates synthetic telemetry actions / outcomes / approvals / edits
     in BigQuery (with the brand_voice drift injection on days 13-17)
  5. recomputes skill track_records to reconcile with the synthetic actions
  6. pre-seeds one self-critique proposal so the demo always has something
     to show in Moment 4

Idempotent, deterministic (RNG seed=42). Re-running produces identical state.
Run 60+ minutes before any live demo so Voyage AI embeddings have populated.

TRUNCATE TABLE bypasses BigQuery's 90-min streaming buffer DML lockout.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import random
from datetime import UTC, datetime, timedelta

from google.cloud import bigquery, storage

from mongo.data.customer_voice import build_voice_docs
from mongo.data.experiments import EXPERIMENTS
from mongo.data.messaging_library import MESSAGING_CLAIMS
from mongo.data.negative_examples import NEGATIVE_EXAMPLES
from mongo.data.skills import SKILLS
from shared import mongo_tools

log = logging.getLogger(__name__)

mongo_tools.use_secret("mongo_uri_writer")

RNG = random.Random(42)
NOW = datetime(2026, 5, 26, 9, 0, tzinfo=UTC)

PROJECT_ID = os.environ["PROJECT_ID"]
BQ = bigquery.Client(project=PROJECT_ID)

# ICP / voice / claim corpus for synthesizing demo draft bodies. Seeded into
# every Content action's `raw` block so the Approval Queue card has real text
# to render — without this, the UI shows empty channel-native previews.
SEED_ICPS = [
    "seg_revops_director",
    "seg_founder_b2b",
    "seg_pmm_growth",
]

SEED_VOICE_BY_ICP = {
    "seg_revops_director": [
        "Our pipeline visibility was a black box until forecasts started missing by 30%.",
        "Half our reps spend Friday afternoons cleaning Salesforce instead of closing.",
    ],
    "seg_founder_b2b": [
        "We were shipping fast but the GTM motion never caught up.",
        "Every demo felt like a custom build — nothing repeatable.",
    ],
    "seg_pmm_growth": [
        "Launches kept slipping because messaging review was the bottleneck.",
        "I had no honest way to tell which positioning lift was real vs noise.",
    ],
}

SEED_CLAIMS_BY_CHANNEL = {
    "linkedin": [
        "Teams that automate the messaging review loop ship 2.3× more campaigns per quarter.",
        "Drift on brand voice usually shows up 14 days before the metric does.",
    ],
    "email": [
        "Our nurture sequence converted 18% better after we cut three setup steps.",
        "First-touch personalization beats generic intent data 4 weeks in a row.",
    ],
    "blog": [
        "The fastest GTM teams I've talked to share one habit: they kill bad positioning early.",
        "Most go-to-market debt isn't in the tools — it's in the handoffs between them.",
    ],
    "substack": [
        "The post-LLM GTM motion isn't about more content — it's about closing the feedback loop faster.",
        "Most marketing teams I talk to have 80% of the data they need; they just can't act on it.",
    ],
}

SEED_REVIEW_FLAG_POOL = [
    {"phrase": "guaranteed results", "issue": "absolute claim"},
    {"phrase": "100% adoption", "issue": "unsupported"},
    {"phrase": "the only solution", "issue": "exaggeration"},
    {"phrase": "instant ROI", "issue": "claim_risk"},
]


def _seeded_skills_loaded(agent_type: str, in_drift: bool) -> list[str]:
    """Synthesize a realistic skills_loaded list per agent type.

    Real runs populate this from state._skills_loaded inside the
    after-callback; the seeder mimics typical load patterns so the
    derived.agent_skill_track_records rollup + Self-Critique loop have
    data on a fresh demo, not just on live runs.

    Drift days bias toward copywriting/copy-editing loads so the
    cross-channel signal traceable to those Skills lines up with the
    drift-period brand_voice dip — gives Self-Critique a clean pattern
    to find in the demo.
    """
    # house-style is loaded on virtually every drafting/review run.
    loaded = {"house-style"}
    if agent_type == "content":
        # Pick 1–2 content-shaping Skills. Bias toward copywriting/
        # copy-editing during drift so the cross-channel rollup correlates
        # those Skills with the dip.
        pool = ["copywriting", "copy-editing", "cro"]
        n_extra = 2 if in_drift else RNG.choice([1, 1, 2])
        loaded.update(RNG.sample(pool, n_extra))
        # SequentialAgent state propagation: Research ran upstream and
        # almost always loaded customer-research, so Content's after-
        # callback inherits that load. Reflect this in the seed so
        # customer-research accumulates eval-scored attribution; without
        # it the rollup only sees the (eval-less) research_op rows.
        if RNG.random() < 0.85:
            loaded.add("customer-research")
    elif agent_type == "research":
        loaded.add("customer-research")
        if RNG.random() < 0.35:
            loaded.add("competitor-profiling")
    elif agent_type == "review":
        # Review loads house-style + copy-editing (the rules ARE its rubric)
        # plus often copywriting / cro for substantive critique.
        loaded.add("copy-editing")
        if RNG.random() < 0.5:
            loaded.add("copywriting")
        if RNG.random() < 0.25:
            loaded.add("cro")
    elif agent_type == "cmo_planner":
        loaded.add("customer-research")
        if RNG.random() < 0.3:
            loaded.add("cro")
    return sorted(loaded)


def _synthesize_draft_raw(channel: str, icp: str, brand_voice: float,
                           claim_support: float) -> dict:
    """Build a believable raw block for a seeded Content action.

    Keeps the same shape Content's after_callback would emit: `draft` text,
    `customer_voice_used` (verbatim snippets), `icp_segment`, optional
    `review_flags` when the rubric scores dip.
    """
    voice = RNG.choice(SEED_VOICE_BY_ICP[icp])
    claim = RNG.choice(SEED_CLAIMS_BY_CHANNEL[channel])
    icp_words = icp.replace('seg_', '').replace('_', ' ')
    if channel == "linkedin":
        body = (
            f"{voice}\n\n"
            f"That's what we kept hearing from {icp_words} teams. "
            f"And it's the reason this changed for us: {claim}\n\n"
            f"What's the one bottleneck you've been meaning to fix for a quarter?"
        )
    elif channel == "email":
        body = (
            f"Hi {{first_name}},\n\n"
            f"{voice} I see this constantly with {icp_words} teams.\n\n"
            f"Here's what shifted for the teams that fixed it: {claim}\n\n"
            f"Worth a 15-minute look at how it'd map to your stack?\n\n— The team"
        )
    elif channel == "blog":
        body = (
            f"## A pattern across the teams I work with\n\n"
            f"{voice}\n\n"
            f"It's tempting to chase the next tool, but the lever is usually upstream. "
            f"{claim}\n\n"
            f"In the rest of this post, I'll walk through what that looked like in practice."
        )
    else:  # substack — emit structured shape the publisher consumes
        body = {
            "headline": f"Why {icp_words} teams keep hitting the same wall",
            "subtitle": "A pattern I've seen six times in the last two months.",
            "body_markdown": (
                f"{voice}\n\n"
                f"It comes up almost every conversation with {icp_words} teams. "
                f"And the more I dig in, the more I think the root cause isn't "
                f"the tools — it's the cadence.\n\n"
                f"Here's what changed for the teams that broke the pattern: "
                f"{claim}\n\n"
                f"## What this looks like in practice\n\n"
                f"More on this — and the three steps that mattered — below."
            ),
        }
    flags = []
    if brand_voice < 0.75 or claim_support < 0.80:
        flags = [RNG.choice(SEED_REVIEW_FLAG_POOL)]
    return {
        "draft": body,
        "icp_segment": icp,
        "customer_voice_used": [voice],
        "review_flags": flags,
    }


def main():
    _wipe()
    _seed_calibration_sets_in_gcs()
    voice = _seed_mongo()
    actions = _seed_actions()
    outcomes = _seed_outcomes(actions)
    approvals = _seed_approvals(actions)
    edits = _seed_edits(actions)
    _aggregate_track_records(actions)
    _seed_self_critique_proposal()
    skill_loads = _seed_skill_usage()

    print(
        f"Seeded:\n"
        f"  Mongo  → {len(SKILLS)} skills, {len(voice)} quotes, "
        f"{len(NEGATIVE_EXAMPLES)} negatives, {len(MESSAGING_CLAIMS)} claims, "
        f"{len(EXPERIMENTS)} experiments\n"
        f"  BQ     → {len(actions)} actions, {len(outcomes)} outcomes, "
        f"{len(approvals)} approvals, {len(edits)} edits\n"
        f"  GCS    → calibration JSONLs for brand_voice + claim_support\n"
        f"  Skill usage → {skill_loads} loads across 28 days\n"
        f"  SEED_DATE={NOW.date()}"
    )


# ---------------------------------------------------------------------------
# wipe
# ---------------------------------------------------------------------------

def _wipe():
    """Drop every declared canonical collection + its history + derived shadow.

    Drives off mongo.schema.COLLECTIONS so newly-added collections (e.g.,
    paid_variants, ops_incidents) don't leave stale rows after re-seed.
    """
    from mongo.schema import COLLECTIONS, DERIVED_COLLECTIONS, HISTORY_COLLECTIONS

    db = mongo_tools.db()
    for c in (*COLLECTIONS, *HISTORY_COLLECTIONS, *DERIVED_COLLECTIONS):
        try:
            db[c].delete_many({})
        except Exception as e:
            log.warning("wipe %s failed: %s", c, e)
    for table in ["telemetry.actions", "telemetry.outcomes", "training.edits"]:
        BQ.query(f"TRUNCATE TABLE `{PROJECT_ID}.{table}`").result()


# ---------------------------------------------------------------------------
# mongo content — delegates to the canonical definitions in mongo/data/
# ---------------------------------------------------------------------------

def _seed_mongo():
    from mongo.data.agent_skills import build_agent_skill_docs

    voice = build_voice_docs(RNG)
    # Playbook skills + mirrored Agent Skills land in the same collection,
    # distinguished by skill_kind. The self-learning loop iterates over both
    # without forking.
    mongo_tools.insert_many("skills", SKILLS + build_agent_skill_docs())
    mongo_tools.insert_many("customer_voice", voice)
    mongo_tools.insert_many("negative_examples", NEGATIVE_EXAMPLES)
    mongo_tools.insert_many("messaging_library", MESSAGING_CLAIMS)
    mongo_tools.insert_many("experiments", EXPERIMENTS)
    return voice


# ---------------------------------------------------------------------------
# calibration sets in GCS — used by the rubric harness as positive anchors
# ---------------------------------------------------------------------------

def _seed_calibration_sets_in_gcs():
    gcs = storage.Client()
    bucket = gcs.bucket(f"{PROJECT_ID}-evals")
    calibration = {
        "brand_voice": [
            {"input": {"candidate_text": "Most CS teams measure response time. The best ones measure unstuck time."}, "human_score": 5},
            {"input": {"candidate_text": "Pricing changes don't move the needle if the close rate is broken."}, "human_score": 5},
            {"input": {"candidate_text": "Three weeks of integration, two minutes of handoff. Customers notice the second number."}, "human_score": 5},
            {"input": {"candidate_text": "RevOps teams using our handoff workflow report meaningful reductions in time-to-first-touch."}, "human_score": 4},
            {"input": {"candidate_text": "When integrations take days, your prospects move on. Ours take minutes."}, "human_score": 4},
            {"input": {"candidate_text": "Many companies struggle with sales-to-CS handoffs. We help solve this problem."}, "human_score": 3},
            {"input": {"candidate_text": "Our platform helps RevOps teams improve their workflow efficiency."}, "human_score": 3},
            {"input": {"candidate_text": "Hey RevOps fam! Let's crush those handoff issues together!"}, "human_score": 2},
            {"input": {"candidate_text": "Discover our revolutionary AI-powered synergistic solution today."}, "human_score": 2},
            {"input": {"candidate_text": "Our platform eliminates churn for B2B SaaS companies, guaranteed."}, "human_score": 1},
            {"input": {"candidate_text": "100% of customers see ROI in week one or your money back."}, "human_score": 1},
        ],
        "claim_support": [
            {"input": {"candidate_text": "Integration averages 12 minutes (median across our last 200 setups)."}, "human_score": 5},
            {"input": {"candidate_text": "Customers report a 23% lift in close rate (case study: Acme Corp, Q1 2026)."}, "human_score": 5},
            {"input": {"candidate_text": "Most teams see faster handoffs within the first week."}, "human_score": 3},
            {"input": {"candidate_text": "Our platform eliminates handoff friction entirely."}, "human_score": 1},
        ],
    }
    for rubric, examples in calibration.items():
        blob = bucket.blob(f"calibration/{rubric}.jsonl")
        blob.upload_from_string("\n".join(json.dumps(e) for e in examples))


# ---------------------------------------------------------------------------
# synthetic telemetry — 200 actions, brand_voice drift days 13-17 (linkedin)
# ---------------------------------------------------------------------------

def _seed_actions() -> list[dict]:
    # Seed volume sized so every evolvable Agent Skill clears
    # MIN_ACTIONS_PER_CHANNEL (=15) on ≥ 2 channels — otherwise the
    # cross-channel guardrail in promotion_gate filters them out and the
    # Self-Critique loop can't demo on a fresh seed. 200 actions left
    # copywriting/cro/copy-editing single-channel; 400 brings all five
    # over the threshold across linkedin + email at minimum.
    SEED_VOLUME = 400
    actions = []
    for i in range(SEED_VOLUME):
        days_ago = RNG.randint(0, 30)
        ts = NOW - timedelta(days=days_ago, hours=RNG.randint(0, 23))
        agent_type = RNG.choices(
            ["content", "research", "review", "cmo_planner"],
            weights=[70, 15, 10, 5])[0]
        channel = RNG.choices(
            ["linkedin", "email", "blog", "substack"],
            weights=[45, 25, 15, 15])[0]

        in_drift = (channel == "linkedin" and 13 <= days_ago <= 17)
        brand_voice = RNG.gauss(0.82, 0.06) if not in_drift else RNG.gauss(0.70, 0.05)
        brand_voice = max(0.0, min(1.0, brand_voice))
        claim_support = max(0.0, min(1.0, RNG.gauss(0.86, 0.05)))

        tid = (f"act_{ts.strftime('%Y%m%d')}_"
               f"{hashlib.sha256(str(i).encode()).hexdigest()[:6]}")
        skill_id = {"linkedin": "linkedin_post",
                    "email": "nurture_email",
                    "blog": "blog_outline",
                    "substack": "substack_post"}[channel]
        skill_version = {"linkedin": "linkedin_post_v3.txt",
                         "email": "nurture_email_v2.txt",
                         "blog": "blog_outline_v1.txt",
                         "substack": "substack_post_v1.txt"}[channel]
        # For content actions, synthesize a believable draft body + review
        # flags + voice attribution so the Queue UI renders a non-empty card
        # without needing a real agent run. Non-content actions stay minimal.
        is_draft = agent_type == "content"
        icp = RNG.choice(SEED_ICPS) if is_draft else None
        seed_raw: dict = {"seeded": True}
        if is_draft:
            seed_raw.update(_synthesize_draft_raw(
                channel=channel,
                icp=icp,
                brand_voice=brand_voice,
                claim_support=claim_support,
            ))

        # Attribute Agent Skills to this action so derive_track_records's
        # cross-channel rollup has data to chew on. Real runs populate this
        # from state._skills_loaded inside make_after_callback; here we
        # synthesize a realistic load pattern per agent type.
        skills_loaded = _seeded_skills_loaded(agent_type, in_drift)

        action = {
            "telemetry_id": tid,
            "ts": ts.isoformat(),
            "agent": f"{agent_type}_agent",
            "skill_id": skill_id,
            "skills_loaded": skills_loaded,
            "skill_version": skill_version,
            "action_type": (f"draft_{channel}" if agent_type == "content"
                            else f"{agent_type}_op"),
            "channel": channel,
            "experiment_id": (RNG.choice([None, "exp_linkedin_hooks_q1", None, None])
                              if channel == "linkedin" else None),
            "eval_scores": ({"brand_voice": brand_voice,
                             "claim_support": claim_support}
                            if is_draft else None),
            "model_armor": ({"decision": "block", "categories": ["prompt_injection"]}
                            if RNG.random() < 0.02
                            else {"decision": "allow", "categories": []}),
            "raw": seed_raw,
        }
        actions.append(action)
    BQ.insert_rows_json(f"{PROJECT_ID}.telemetry.actions", actions)
    return actions


def _seed_outcomes(actions) -> list[dict]:
    outcomes = []
    for a in actions:
        if a.get("experiment_id"):
            ts = datetime.fromisoformat(a["ts"])
            filled = RNG.random() < 0.7
            outcomes.append({
                "telemetry_id": a["telemetry_id"],
                "slot_name": "engagement_72h", "metric": "engagement",
                "source": "linkedin_ads",
                "expected_by": (ts + timedelta(hours=72)).isoformat(),
                "filled_at": (ts + timedelta(hours=80)).isoformat() if filled else None,
                "value": RNG.gauss(0.04, 0.01) if filled else None,
                "status": "filled" if filled else "pending",
            })
    if outcomes:
        BQ.insert_rows_json(f"{PROJECT_ID}.telemetry.outcomes", outcomes)
    return outcomes


def _seed_approvals(actions) -> list[dict]:
    approvals = []
    for a in RNG.sample(actions, 10):
        approvals.append({
            "telemetry_id": a["telemetry_id"],
            "decision": RNG.choices(["approve", "edit", "reject"],
                                     weights=[60, 30, 10])[0],
            "decided_by": "rohit",
            "decided_at": datetime.fromisoformat(a["ts"]) + timedelta(hours=4),
        })
    mongo_tools.insert_many("approvals", approvals)
    return approvals


def _seed_edits(actions) -> list[dict]:
    rows = []
    for a in RNG.sample([x for x in actions if x.get("eval_scores")], 50):
        rows.append({
            "edit_id": f"edit_{a['telemetry_id']}",
            "telemetry_id": a["telemetry_id"],
            "ts": (datetime.fromisoformat(a["ts"]) + timedelta(hours=4)).isoformat(),
            "before_text": "Original draft content.",
            "after_text": "Edited draft content.",
            "edit_categories": RNG.choice([
                ["softened_tone"],
                ["trimmed_length"],
                ["added_evidence"],
                ["softened_tone", "trimmed_length"],
            ]),
        })
    if rows:
        BQ.insert_rows_json(f"{PROJECT_ID}.training.edits", rows)
    return rows


def _aggregate_track_records(actions) -> None:
    for s in SKILLS:
        tr: dict = {}
        for v in s["history"]:
            v_actions = [a for a in actions
                         if a["skill_id"] == s["_id"]
                         and a["skill_version"] == v
                         and a.get("eval_scores")]
            if v_actions:
                tr[v] = {
                    "action_count": len(v_actions),
                    "mean_brand_voice": sum(a["eval_scores"]["brand_voice"]
                                              for a in v_actions) / len(v_actions),
                    "mean_claim_support": sum(a["eval_scores"]["claim_support"]
                                                for a in v_actions) / len(v_actions),
                }
        mongo_tools.upsert("skills", {"_id": s["_id"]}, {"track_record": tr})


def _seed_self_critique_proposal() -> None:
    mongo_tools.upsert("skills", {"_id": "nurture_email"}, {
        "self_critique_proposal": {
            "candidate_id": "nurture_email_v2_critique_20260520.txt",
            "issue": ("Subject lines are too long for mobile preview "
                      "(avg 78 chars; mobile clips at ~40)."),
            "proposed_change": (
                "Add to the prompt: 'Subject lines MUST be under 40 characters. "
                "Front-load the hook in the first 5 words. The mobile preview "
                "is what 65% of recipients see first.'"
            ),
            "confidence": "medium",
            "evidence_count": 14,
            "proposed_at": NOW - timedelta(days=2),
            "status": "awaiting_human_review",
        }
    })


def _seed_skill_usage() -> int:
    """Seed ~150 skill_usage rows over the last 28 days so the Capabilities
    page is populated. Honors per-agent allowlists so the heatmap looks
    realistic — an agent can only load skills it's permitted to.

    Distribution heuristic: high-leverage skills (house-style, copywriting,
    ads-meta) get the most loads; a few (e.g. marketing-psychology,
    ads-audit) get fewer to demonstrate the dead-weight signal.
    """
    from agents._skills_config import SKILLS_BY_AGENT

    # Frequency weight per skill — high-leverage gets more, runners-up less
    freq: dict[str, int] = {
        "house-style": 40,
        "copywriting": 20, "copy-editing": 12, "cro": 10, "ab-testing": 8,
        "customer-research": 12, "competitor-profiling": 6,
        "ads-meta": 14, "ads-google": 12, "ads-creative": 10,
        "ads-attribution": 6, "ads-budget": 5, "ads-math": 8,
        "thinking-framework": 4, "pricing": 5, "content-strategy": 5,
        "launch": 3, "product-marketing": 6,
        "emails": 8, "onboarding": 6,
        "marketing-psychology": 2,    # demo runner-up — low usage
        "ads-audit": 1,                # demo runner-up — very low usage
    }

    references_by_skill: dict[str, list[str]] = {
        "copywriting": ["references/copy-frameworks.md"],
        "cro": ["references/experiments.md", "references/form.md"],
        "ab-testing": ["references/sample-size-guide.md",
                        "references/test-templates.md"],
        "customer-research": ["references/source-guides.md"],
        "competitor-profiling": ["references/templates.md",
                                   "references/tool-reference.md"],
        "ads-creative": ["references/platform-specs.md",
                          "references/benchmarks.md"],
        "ads-google": ["references/google-audit.md",
                        "references/google-creative-specs.md"],
        "emails": ["references/copy-guidelines.md",
                    "references/email-types.md",
                    "references/sequence-templates.md"],
        "onboarding": ["references/experiments.md"],
        "pricing": ["references/research-methods.md",
                     "references/tier-structure.md"],
        "content-strategy": ["references/headless-cms.md"],
        "copy-editing": ["references/checklist.md",
                          "references/content-refresh.md",
                          "references/plain-english-alternatives.md"],
        "ads-audit": ["references/scoring-system.md"],
    }

    # Build weighted list of (agent, skill) eligible loads
    eligible: list[tuple[str, str, int]] = []
    for agent, allowed in SKILLS_BY_AGENT.items():
        for skill in allowed:
            w = freq.get(skill, 5)
            eligible.append((agent, skill, w))

    rows: list[dict] = []
    for _ in range(150):
        # Weighted choice of (agent, skill) tuple
        weights = [w for _, _, w in eligible]
        agent, skill, _ = RNG.choices(eligible, weights=weights, k=1)[0]

        # Distribute timestamps across 28 days, weighted toward recent days
        days_ago = int(abs(RNG.gauss(7, 7)))  # mean=7, skewed recent
        days_ago = min(days_ago, 27)
        ts = NOW - timedelta(
            days=days_ago,
            hours=RNG.randint(0, 23),
            minutes=RNG.randint(0, 59),
        )

        # ~25% are Tier 3 (reference loads); only when refs exist for the skill
        refs = references_by_skill.get(skill, [])
        is_tier_3 = bool(refs) and RNG.random() < 0.25
        tier = 3 if is_tier_3 else 2
        ref_path = RNG.choice(refs) if is_tier_3 else None

        tokens = (RNG.randint(300, 1200) if tier == 2
                  else RNG.randint(200, 900))

        rows.append({
            "ts": ts,
            "agent_name": agent,
            "skill_name": skill,
            "tier": tier,
            "reference_path": ref_path,
            "tokens_estimated": tokens,
            "_workspace": "default",
            "_owner": agent,
            "_provenance": {
                "kind": "agent",
                "actor_id": agent,
                "source": {"kind": "skill_load",
                            "ref_id": skill,
                            "ref_collection": "skills/"},
                "created_at": ts,
                "updated_at": ts,
                "confidence": 1.0,
                "evidence_count": 0,
                "trust_tier": "verified",
                "supersedes": [],
                "ttl": None,
            },
        })
    mongo_tools.db()["skill_usage"].insert_many(rows)
    return len(rows)


if __name__ == "__main__":
    main()
