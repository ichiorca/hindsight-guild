"""Agent Skill definitions — the Tier-2/3 SKILL.md library, mirrored into
the same ``skills`` Mongo collection that holds playbooks.

Why: the self-learning loop (Self-Critique, promotion_gate, drift_detect)
keys off ``skills`` documents. By giving Agent Skills the same shape with
``skill_kind="agent_skill"``, every loop applies to them too — without any
forking in the core code.

Selection criteria for which Skills are eligible for self-evolution:
  1. **Cross-channel**: loaded for drafts on >= 2 channels, so the cross-
     channel guardrail in promotion_gate can actually fire (single-channel
     issues belong in the channel's playbook, not in a shared Skill).
  2. **Content-shaping**: the Skill's content directly affects the wording
     of drafts the rubrics grade — not measurement / experiment-design
     skills where the feedback loop is too indirect.
  3. **High enough traffic**: cross-channel × frequency score among the
     top ~5 skills (analyzed via SKILLS_BY_AGENT × seed_demo freq table).

The five we migrated, in order of score, are:
  - house-style       (score 160)  voice rules, every draft
  - copywriting       (score 80)   used by content, review, lifecycle_email
  - customer-research (score 48)   shapes Research output → every draft
  - copy-editing      (score 48)   editing rules ARE the review rubric
  - cro               (score 40)   conversion-intent loop

The remaining 17 Skills (ads-*, emails, onboarding, marketing-psychology,
etc.) keep working as static SKILL.md files (Tier-1 metadata + read_skill
returns disk content). They didn't qualify either because they're
single-channel (paid_media ads-*, lifecycle emails), too indirect for
rubric feedback (ads-attribution, ads-math), or low-traffic in practice.

Disk ↔ Mongo contract for migrated Skills:
  - Mongo's ``versions: {v1: <full markdown body>, v2: ...}`` is the
    SOURCE OF TRUTH. promotion_gate.promote_after_approval flips
    ``current_version`` in Mongo only — disk is never written eagerly.
  - ``skills/<name>/SKILL.md`` is a derived cache. ``shared.skills.
    SkillRegistry.read_body`` reconciles it against the Mongo current
    version on every call: if the on-disk content differs (or no file
    exists), it atomically rewrites SKILL.md from Mongo before serving.
    This makes multi-container deployments self-healing.
  - The Mongo ``versions`` map also serves the diff renderer + promotion
    flow so reviewers can compare bodies without round-tripping through
    git.
"""
from __future__ import annotations

from pathlib import Path

# The five Skills under the self-evolution loop. To migrate another Skill,
# (a) confirm it satisfies the selection criteria above, (b) add it here,
# (c) re-seed. The rest of the loop is generic over skill_kind.
EVOLVABLE_SKILL_IDS = (
    "house-style",
    "copywriting",
    "customer-research",
    "copy-editing",
    "cro",
)

# All five apply cross-channel by their nature — verified against the
# cross-channel guardrail criterion. Channel allowlist matches the
# drafting channels supported in DraftRequest.
_CHANNELS_CROSS = ["linkedin", "email", "blog", "substack"]


def _read_skill_md(skill_name: str) -> str:
    """Load the on-disk SKILL.md body so we don't duplicate the markdown
    in this seed file. The disk version IS v1 by definition (the original
    import from marketingskills/claude-ads)."""
    path = Path(__file__).resolve().parents[2] / "skills" / skill_name / "SKILL.md"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def build_agent_skill_docs() -> list[dict]:
    """Return seed docs for every Agent Skill enrolled in the self-evolution
    loop. Called by demo/seed_demo._seed_mongo so the demo Mongo has working
    Agent Skill docs before any cron runs.
    """
    docs = []
    for skill_id in EVOLVABLE_SKILL_IDS:
        body = _read_skill_md(skill_id)
        if not body:
            # Defensive: if a Skill listed here ever loses its SKILL.md
            # on disk, skip rather than seed an empty doc that would
            # later serve "" to read_skill.
            continue
        docs.append({
            "_id": skill_id,
            "skill_kind": "agent_skill",
            # current_version is a logical pointer; the actual markdown body
            # lives under versions[current_version]. v0 = the genesis import,
            # no promotions yet — the self-evolution loop advances this later.
            "current_version": "v0",
            "candidates": [],
            "history": ["v0"],
            "versions": {
                "v0": {
                    "body_md": body,
                    "source": "import:marketingskills@MIT",
                },
            },
            # All five Skills are cross-channel by selection criterion.
            "applies_to": {
                "icp_segments": [],   # empty = applies to all ICPs
                "channels": list(_CHANNELS_CROSS),
            },
        })
    return docs
