"""Skill (playbook) definitions.

Each skill has:
  - _id: stable identifier (linkedin_post, nurture_email, blog_outline)
  - current_version: pointer to the prompt filename in prompts/<area>/
  - candidates: alternate versions running alongside the incumbent
  - history: every version ever shipped
  - applies_to: ICP segments + channels this playbook serves
  - promoted_at / promoted_from_experiment: provenance of the current version

track_record is recomputed by demo/seed_demo._aggregate_track_records()
after the synthetic actions are generated, so it reconciles with telemetry.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

NOW = datetime(2026, 5, 26, 9, 0, tzinfo=UTC)


SKILLS: list[dict] = [
    {
        "_id": "linkedin_post",
        "current_version": "linkedin_post_v3.txt",
        "candidates": ["linkedin_post_v4_candidate.txt"],
        "history": ["linkedin_post_v1.txt", "linkedin_post_v2.txt",
                    "linkedin_post_v3.txt"],
        "applies_to": {
            "icp_segments": ["seg_revops_director", "seg_ae_growth"],
            "channels": ["linkedin"],
        },
        "promoted_at": NOW - timedelta(days=12),
        "promoted_from_experiment": "exp_linkedin_hooks_q1",
    },
    {
        "_id": "nurture_email",
        "current_version": "nurture_email_v2.txt",
        "candidates": [],
        "history": ["nurture_email_v1.txt", "nurture_email_v2.txt"],
        "applies_to": {
            "icp_segments": ["seg_revops_director"],
            "channels": ["email"],
        },
        "promoted_at": NOW - timedelta(days=20),
        "promoted_from_experiment": "exp_subject_line_personalization",
    },
    {
        "_id": "blog_outline",
        "current_version": "blog_outline_v1.txt",
        "candidates": [],
        "history": ["blog_outline_v1.txt"],
        "applies_to": {
            "icp_segments": ["seg_saas_founder"],
            "channels": ["blog"],
        },
    },
    {
        "_id": "substack_post",
        "current_version": "substack_post_v1.txt",
        "candidates": [],
        "history": ["substack_post_v1.txt"],
        "applies_to": {
            "icp_segments": ["seg_saas_founder", "seg_revops_director"],
            "channels": ["substack"],
        },
    },
]
