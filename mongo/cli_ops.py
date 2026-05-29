"""Founder action commands — approve / reject promotion requests.

Mutating ops live here so the read-only and seed surfaces stay free of
write side effects.
"""
from __future__ import annotations

import sys

from shared import mongo_tools


def cmd_approve_promotion(args):
    """Approve a pending promotion request → flips current_version."""
    from services.promotion_gate.main import promote_after_approval

    skill = mongo_tools.find_one("skills", {"_id": args.skill_id})
    if not skill or not skill.get("promotion_request"):
        print(f"No promotion_request on skill {args.skill_id}", file=sys.stderr)
        sys.exit(1)

    candidate = skill["promotion_request"]["candidate"]
    promote_after_approval(args.skill_id, candidate)
    print(f"Promoted {args.skill_id}: current_version = {candidate}")


def cmd_reject_promotion(args):
    """Reject a pending promotion request — clears the field, keeps candidate alive."""
    mongo_tools.db()["skills"].update_one(
        {"_id": args.skill_id},
        {"$unset": {"promotion_request": ""}},
    )
    print(f"Rejected promotion request on {args.skill_id}")
