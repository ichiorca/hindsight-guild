"""Experiment registry seed — 3 decided + 2 running (agentic commerce).

Each experiment has:
  - hypothesis (one-sentence falsifiable)
  - icp_segment + channel scope
  - variants with allocation_pct
  - success_metric (slot name OR ends in '_score' for eval-backed)
  - mde and decision_rule
  - state + result + lesson (for decided)

The decided ones carry lessons that the CMO Planner uses for the "what we
learned" section of the weekly memo. The drift investigation
(exp_drift_brand_voice_linkedin_v24) is what Moment 5 of the demo references.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

NOW = datetime(2026, 5, 26, 9, 0, tzinfo=UTC)


EXPERIMENTS: list[dict] = [
    {
        "_id": "exp_linkedin_hooks_q1",
        "title": "Question-format hooks beat statement-format for merchants",
        "hypothesis": "Question-format LinkedIn hooks lift engagement >=20% vs statement.",
        "icp_segment": "seg_merchant_dtc",
        "channel": "linkedin",
        "variants": [
            {"id": "A_statement",
             "playbook_version": "linkedin_post_v2.txt",
             "allocation_pct": 50},
            {"id": "B_question",
             "playbook_version": "linkedin_post_v3.txt",
             "allocation_pct": 50},
        ],
        "success_metric": "engagement_72h",
        "mde": 0.20,
        "decision_rule": "B wins if mean engagement beats A by >= MDE with N>=400 each.",
        "state": "decided",
        "created_at": NOW - timedelta(days=25),
        "decided_at": NOW - timedelta(days=12),
        "result": {"winner": "B_question", "lift": 0.23, "p_value": 0.04},
        "lesson": ("'Is your store agent-ready?'-style question hooks lift "
                   "engagement 23% on merchants. Promote v3."),
        "tags": ["linkedin", "hooks"],
    },
    {
        "_id": "exp_subject_line_personalization",
        "title": "Platform-named subject lines lift open rate on e-comm leaders",
        "hypothesis": "Naming the agent surface (ChatGPT/Gemini) lifts open rate by >=3pp.",
        "icp_segment": "seg_ecom_leader",
        "channel": "email",
        "variants": [
            {"id": "A_generic",
             "playbook_version": "nurture_email_v1.txt",
             "allocation_pct": 50},
            {"id": "B_named_surface",
             "playbook_version": "nurture_email_v2.txt",
             "allocation_pct": 50},
        ],
        "success_metric": "open_rate_24h",
        "mde": 0.03,
        "state": "decided",
        "created_at": NOW - timedelta(days=30),
        "decided_at": NOW - timedelta(days=20),
        "result": {"winner": "B_named_surface", "lift": 0.041, "p_value": 0.02},
        "lesson": "Naming the agent surface lifts opens +4.1pp. Promote v2 default.",
        "tags": ["email", "personalization"],
    },
    {
        "_id": "exp_drift_brand_voice_linkedin_v24",
        "title": "Investigate brand_voice drop on LinkedIn after prompt v2.4",
        "hypothesis": "Drop caused by Content Agent prompt v2.4 (removed examples).",
        "channel": "linkedin",
        "variants": [
            {"id": "v2.3", "playbook_version": "linkedin_post_v2.txt",
             "allocation_pct": 50},
            {"id": "v2.4", "playbook_version": "linkedin_post_v2.txt",
             "allocation_pct": 50},
        ],
        "success_metric": "brand_voice_score",
        "state": "decided",
        "created_at": NOW - timedelta(days=8),
        "decided_at": NOW - timedelta(days=3),
        "result": {"root_cause_confirmed": True},
        "lesson": "Reverting to v2.3 prompt; in-context examples are load-bearing.",
        "tags": ["drift", "investigation"],
    },
    {
        "_id": "exp_hero_copy_readiness",
        "title": "Outcome-led hero ('stop losing AI sales') beats feature-led",
        "hypothesis": "Outcome-led hero copy lifts demo signup by >=15% for merchants.",
        "icp_segment": "seg_merchant_dtc",
        "channel": "landing_page",
        "variants": [
            {"id": "A_feature",
             "playbook_version": "landing_v1.txt",
             "allocation_pct": 50},
            {"id": "B_outcome",
             "playbook_version": "landing_v1_candidate.txt",
             "allocation_pct": 50},
        ],
        "success_metric": "demo_signup_24h",
        "mde": 0.15,
        "state": "running",
        "created_at": NOW - timedelta(days=4),
        "tags": ["landing_page", "hero_copy"],
    },
    {
        "_id": "exp_meta_creative_visual_density",
        "title": "Low-density creative outperforms detailed creative on Meta",
        "hypothesis": "Sparse creative lifts CTR >=20% vs detail-dense.",
        "icp_segment": "seg_ecom_leader",
        "channel": "meta_ads",
        "variants": [
            {"id": "A_dense", "playbook_version": "meta_v1.txt",
             "allocation_pct": 50},
            {"id": "B_sparse", "playbook_version": "meta_v2_candidate.txt",
             "allocation_pct": 50},
        ],
        "success_metric": "ctr_24h",
        "mde": 0.20,
        "state": "running",
        "created_at": NOW - timedelta(days=2),
        "tags": ["paid", "meta"],
    },
]
