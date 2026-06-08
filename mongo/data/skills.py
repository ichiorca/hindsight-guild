"""Skill (playbook) definitions.

Seeded as a CLEAN GENESIS BASELINE: every playbook starts at version ``v0``
with no prior history, no candidates, and no promotion provenance — nothing
has run or been promoted yet. The self-learning loop (self_critique →
promotion_gate → founder approval) is what later adds candidates, advances
``current_version``, and records promotions; the seed must not fabricate that.

Each skill has:
  - _id: stable identifier (linkedin_post, nurture_email, blog_outline)
  - current_version: logical version label (telemetry attribution + the
    candidate-rollout pointer in agents/pipeline.py). The incumbent draft body
    does NOT load from this — it comes from the agent's instruction prompt.
  - candidates: alternate versions running alongside the incumbent (empty at v0)
  - history: every version ever shipped (just ["v0"] at genesis)
  - applies_to: ICP segments + channels this playbook serves

track_record is recomputed by demo/seed_demo._aggregate_track_records()
after the synthetic actions are generated, so it reconciles with telemetry.
"""
from __future__ import annotations

SKILLS: list[dict] = [
    {
        "_id": "linkedin_post",
        "current_version": "v0",
        "candidates": [],
        "history": ["v0"],
        "applies_to": {
            "icp_segments": ["seg_merchant_dtc", "seg_ecom_leader"],
            "channels": ["linkedin"],
        },
    },
    {
        "_id": "nurture_email",
        "current_version": "v0",
        "candidates": [],
        "history": ["v0"],
        "applies_to": {
            "icp_segments": ["seg_ecom_leader"],
            "channels": ["email"],
        },
    },
    {
        "_id": "blog_outline",
        "current_version": "v0",
        "candidates": [],
        "history": ["v0"],
        "applies_to": {
            "icp_segments": ["seg_merchant_dtc"],
            "channels": ["blog"],
        },
    },
    {
        "_id": "substack_post",
        "current_version": "v0",
        "candidates": [],
        "history": ["v0"],
        "applies_to": {
            "icp_segments": ["seg_merchant_dtc", "seg_agent_platform"],
            "channels": ["substack"],
        },
    },
]
