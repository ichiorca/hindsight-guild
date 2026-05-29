"""Local-dev Mongo seed.

Two modes:

    python -m scripts.local_seed              # CLEAN: just reference data
    python -m scripts.local_seed --with-demo  # DEMO: + mock voice/negatives/
                                              #        experiments + a staged
                                              #        agent_skill promotion
                                              #        request on house-style

CLEAN mode is the default. It writes only the Skill reference docs the
system NEEDS to function:

  - 4 playbook skills (linkedin_post, nurture_email, blog_outline,
    substack_post) so the Content agent can resolve current_version
  - 5 agent_skill docs (house-style, copywriting, customer-research,
    copy-editing, cro) with versions["v1"].body_md loaded from disk so
    the Self-Critique + promotion loop has something to operate on

Everything else (customer_voice quotes, negative_examples, messaging_library
claims, experiments, the simulated promotion_request that pre-stages the
WeeklyReview card) is mock data behind --with-demo.

CLEAN is the right starting state for running the real pipeline + watching
the self-evolution loop produce genuine proposals over time. DEMO is the
right state for clicking around the UI to see what the surfaces look like
once the system has been running for a while.

Always writes ONLY to MongoDB (no BigQuery, no GCS). Reads MONGO_URI_DIRECT
from .env or env.
"""
from __future__ import annotations

import difflib
import os
from datetime import UTC, datetime, timedelta

from mongo.data.agent_skills import build_agent_skill_docs  # noqa: E402
from mongo.data.customer_voice import build_voice_docs  # noqa: E402
from mongo.data.experiments import EXPERIMENTS  # noqa: E402
from mongo.data.messaging_library import MESSAGING_CLAIMS  # noqa: E402
from mongo.data.negative_examples import NEGATIVE_EXAMPLES  # noqa: E402
from mongo.data.skills import SKILLS  # noqa: E402

# Env bootstrap — loads .env, sets MONGO_URI_DIRECT default
# (mongodb://localhost:27017), sets PROJECT_ID=local-dev so mongo_tools
# follows the LOCAL_DEV path. Side effect of import: bootstrap_env() runs
# BEFORE the shared.mongo_tools import below.
from scripts._test_bootstrap import REPO_ROOT  # noqa: E402, F401
from shared import mongo_tools  # noqa: E402

NOW = datetime.now(UTC)


# ---------------------------------------------------------------------------
# Sample agent_skill promotion_request so the UI has something to render
# without running Self-Critique + promotion_gate. Mirrors the shape that
# services/promotion_gate/main.py:_raise_agent_skill_promotion_request
# would produce after a real evaluation pass.
# ---------------------------------------------------------------------------

_HOUSE_STYLE_V2_BODY = """---
name: house-style
description: Brand voice rules — load this before drafting or reviewing any customer-facing content. Use when the user mentions 'tone', 'voice', 'how we sound', 'rewrite this on-brand', or asks for an edit on something we shipped.
metadata:
  version: 2.1.0
---

<!--
Imported from https://github.com/iannuttall/marketingskills (MIT License).
v2.1.0 — Self-Critique pass added the absolute-language watchlist after
edit_capture flagged 14 founder edits softening "guaranteed" / "eliminates"
across linkedin + email drafts over a 14-day window.
-->

# House Style — voice rules of record

## Voice in one sentence
Crisp, direct, evidence-led. Confident without being absolute. Specific
beats clever.

## Never write
- "guaranteed", "eliminates", "100%", "completely", "always", "never"
  — these are the absolute-language patterns the founder edits out most
  consistently. Use directional language ("typically", "reliably",
  "in our last 8 campaigns") with a concrete anchor.
- "synergy", "leverage", "unlock", "10x", "game-changer",
  "transformative" — corporate stock phrases that read as filler.
- Adverb stacks ("really very effectively") — pick one verb.

## Always
- Lead with the customer's problem in their words. If you don't have a
  verbatim quote handy, call out the gap and ask Research to fill it.
- Anchor every claim to a source — an approved_claim from the messaging
  library, a customer-voice quote, or a number from a decided experiment.
  Anything without an anchor gets flagged `needs_evidence`.
- One specific number beats three rough adjectives.

## Tone calibration
| Channel    | Tone reference                                              |
|------------|-------------------------------------------------------------|
| linkedin   | A senior IC talking to a peer. Slight wry. No hedging.      |
| email      | The founder writing one human. Conversational, short paras. |
| blog       | Editorial. Argued, with examples. No corporate hedging.     |
| substack   | Reflective. First-person. Conviction with humility.         |
"""


def _seed_house_style_promotion_request() -> dict:
    """Build a realistic agent_skill promotion_request on house-style.

    Includes the computed unified diff (via difflib, same as the production
    promotion_gate path), per-channel baselines that triggered the gate,
    and a candidate body_md so the approval flow can sync to disk if you
    want to walk through it.
    """
    docs = build_agent_skill_docs()
    hs_doc = next((d for d in docs if d["_id"] == "house-style"), None)
    if not hs_doc:
        raise RuntimeError("house-style not in build_agent_skill_docs()")

    current_body = hs_doc["versions"]["v1"]["body_md"]
    candidate_body = _HOUSE_STYLE_V2_BODY

    diff = "\n".join(difflib.unified_diff(
        current_body.splitlines(),
        candidate_body.splitlines(),
        fromfile="house-style/SKILL.md (v1)",
        tofile="house-style/SKILL.md (v2)",
        n=3, lineterm="",
    ))

    # Add v2 to the versions map so approval has a body to materialize.
    hs_doc["versions"]["v2"] = {
        "body_md": candidate_body,
        "proposed_at": NOW,
        "source": "self_critique",
    }
    # And the promotion_request shape promotion_gate would produce.
    hs_doc["self_critique_proposal"] = {
        "candidate_id": "v2",
        "issue": (
            "Recent drafts loading house-style on linkedin + email over-use "
            "absolute language ('guaranteed', 'eliminates') even though the "
            "current SKILL.md flags absolutes generically. The founder edits "
            "out these specific words ~14 times in 14 days; a more explicit "
            "watchlist would reduce that loop."
        ),
        "confidence": "medium",
        "evidence_count": 14,
        "channels_affected": ["linkedin", "email"],
        "proposed_at": NOW - timedelta(hours=2),
        "status": "gated_through",  # passed the gate, now in promotion
    }
    hs_doc["promotion_request"] = {
        "candidate": "v2",
        "incumbent": "v1",
        "kind": "agent_skill",
        "issue": hs_doc["self_critique_proposal"]["issue"],
        "proposed_diff": diff,
        "channels_at_risk": ["linkedin", "email"],
        "per_channel_baseline": {
            "linkedin": {"brand_voice": 0.741, "claim_support": 0.84,
                          "claim_risk": 0.87, "icp_relevance": 0.82,
                          "originality": 0.73, "conversion_intent": 0.71,
                          "n": 67},
            "email":    {"brand_voice": 0.762, "claim_support": 0.85,
                          "claim_risk": 0.88, "icp_relevance": 0.84,
                          "originality": 0.75, "conversion_intent": 0.74,
                          "n": 34},
            "blog":     {"brand_voice": 0.838, "claim_support": 0.86,
                          "claim_risk": 0.91, "icp_relevance": 0.87,
                          "originality": 0.79, "conversion_intent": 0.72,
                          "n": 17},
            "substack": {"brand_voice": 0.851, "claim_support": 0.87,
                          "claim_risk": 0.92, "icp_relevance": 0.88,
                          "originality": 0.81, "conversion_intent": 0.74,
                          "n": 16},
        },
        "project_baseline_brand_voice": 0.798,
        "evidence_count": 14,
        "confidence": "medium",
        "proposed_at": NOW - timedelta(hours=1),
        "status": "awaiting_approval",
    }
    return hs_doc


# ---------------------------------------------------------------------------
# Main seed
# ---------------------------------------------------------------------------

def main():
    import argparse
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--with-demo", action="store_true",
                   help="Also seed mock customer_voice / negative_examples / "
                        "messaging_library / experiments + a pre-staged "
                        "agent_skill promotion_request on house-style. Off "
                        "by default — clean slate is the starting state for "
                        "real pipeline runs.")
    args = p.parse_args()

    db_name = os.environ.get("MONGO_DB", "agentic_marketing")
    mode = "DEMO (mock data)" if args.with_demo else "CLEAN (reference only)"
    print(f"==> Seeding local Mongo at {os.environ['MONGO_URI_DIRECT']} "
          f"db={db_name} [{mode}]")

    db = mongo_tools.db()

    # Wipe everything we're about to seed (idempotent re-runs). This wipes
    # in clean mode too — re-running this script always produces an
    # identical state, never accumulates leftover docs from a prior demo run.
    from mongo.schema import COLLECTIONS, DERIVED_COLLECTIONS, HISTORY_COLLECTIONS
    for c in (*COLLECTIONS, *HISTORY_COLLECTIONS, *DERIVED_COLLECTIONS):
        try:
            db[c].delete_many({})
        except Exception:
            pass

    # ---- Reference data: always seeded ------------------------------------
    # Playbook skills define the channels the pipeline can draft for; the
    # Content agent reads current_version from these docs at run time.
    # Agent Skills carry the SKILL.md content + version metadata that the
    # Self-Critique + promotion_gate loop operates on.
    agent_skill_docs = build_agent_skill_docs()
    if args.with_demo:
        # Splice in the pre-staged promotion_request on house-style so the
        # WeeklyReview UI's AgentSkillPromotionCard has something to render.
        house_style_promoted = _seed_house_style_promotion_request()
        agent_skill_docs = [
            d for d in agent_skill_docs if d["_id"] != "house-style"
        ] + [house_style_promoted]

    # Route every seeded insert through seed_canonical so each doc gets a
    # default _provenance / _workspace / _owner block and a matching
    # history.<coll> create row — keeps Layer-2 canonical state compliant
    # from the first byte. Clean-mode delegates the playbook portion to
    # mongo.cli_seed.seed_playbook_skills() so the two scripts share one
    # source of truth for the SKILLS wipe+seed; the agent_skill docs are
    # spliced in here because they're local_seed-only.
    from mongo.cli_seed import seed_playbook_skills
    from mongo.history import seed_canonical

    if args.with_demo:
        # Demo mode pre-stages a promotion_request on house-style, so we
        # must write the combined skills (playbooks + agent_skills) in
        # one go to keep the wipe+seed transactional from the script's POV.
        seed_canonical("skills", SKILLS + agent_skill_docs,
                        actor_id="seed", kind="ingestion", trust_tier="verified")
    else:
        # Clean mode: delegate playbook seeding to the consolidated helper,
        # then write the agent_skill docs alongside. The helper already
        # wiped skills + history.skills, so seed_canonical here just
        # appends the agent_skill rows.
        seed_playbook_skills(actor_id="seed")
        seed_canonical("skills", agent_skill_docs,
                        actor_id="seed", kind="ingestion", trust_tier="verified")

    # ---- Mock data: only with --with-demo --------------------------------
    voice_count = 0
    if args.with_demo:
        import random
        voice = build_voice_docs(random.Random(42))
        seed_canonical("customer_voice", voice,
                        actor_id="seed", kind="ingestion", trust_tier="verified")
        seed_canonical("negative_examples", NEGATIVE_EXAMPLES,
                        actor_id="seed", kind="ingestion", trust_tier="verified")
        seed_canonical("messaging_library", MESSAGING_CLAIMS,
                        actor_id="seed", kind="ingestion", trust_tier="verified")
        seed_canonical("experiments", EXPERIMENTS,
                        actor_id="seed", kind="ingestion", trust_tier="verified")
        voice_count = len(voice)

    # ASCII-only output — Windows cp1252 stdout can't render arrows/em-dashes.
    print(
        f"  skills:            {len(SKILLS) + len(agent_skill_docs)}  "
        f"({len(SKILLS)} playbooks + {len(agent_skill_docs)} agent_skills)"
    )
    if args.with_demo:
        print(
            f"  customer_voice:    {voice_count}\n"
            f"  negative_examples: {len(NEGATIVE_EXAMPLES)}\n"
            f"  messaging_library: {len(MESSAGING_CLAIMS)}\n"
            f"  experiments:       {len(EXPERIMENTS)}\n"
            f"\n  Pre-staged promotion_request on house-style. Weekly Review "
            f"will render the AgentSkillPromotionCard with the v1->v2 diff."
        )
    else:
        print(
            "\n  Clean slate. customer_voice / negative_examples / "
            "messaging_library / experiments are all empty. The pipeline "
            "will produce drafts purely from the topic_hint + skill bodies; "
            "Research's vector-search returns no quotes until you ingest "
            "real ones. To prefill with mock data for UI demos, re-run "
            "with --with-demo."
        )
    print("\nNext:")
    print("  uvicorn services.web_api.main:app --env-file .env "
          "--reload --reload-exclude .venv --reload-exclude node_modules "
          "--port 8080")
    print("  (in another shell)")
    print("  cd web && npm install && npm run dev")


if __name__ == "__main__":
    main()
