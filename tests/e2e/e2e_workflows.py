"""End-to-end workflow runner — drives every agent in-process via real
LLM calls, so every page of the product ends up with authentic data.

No mocks, no seeded transactional rows. Each workflow goes through the
same code path the production agent would take (LLM call → tool calls →
Mongo writes), producing real ``actions``, ``customer_voice``,
``positioning_proposals``, ``email_sequences``, ``paid_variants``,
``ops_incidents``, ``self_critique_proposal`` rows.

Order matters — downstream agents read upstream outputs:

  1. customer_voice_agent  → customer_voice
  2. positioning_agent     → positioning_proposals
     (then we auto-promote 1-2 to messaging_library to simulate the
     founder approving a proposal via the UI, so drafting has claims)
  3. drafting_pipeline     → actions, attribution_map, image, review
  4. lifecycle_email_agent → email_sequences
  5. paid_media_agent      → paid_variants
     (skipped gracefully if BigQuery isn't reachable)
  6. self_critique_agent   → self_critique_proposal on skills
  7. cmo_planner           → approvals (weekly memo for slack_approval)
     (skipped if slack_webhook_url isn't configured)

Each workflow runs 2-3 times across different ICPs / topics so the
Capabilities heatmap, Weekly Review, and per-agent inboxes have enough
density to read as a real team's output.

Usage:
  python -m tests.e2e.e2e_workflows                    # full run
  python -m tests.e2e.e2e_workflows --workflows voice  # subset
  python -m tests.e2e.e2e_workflows --dry-run          # show plan only
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

# Env bootstrap — must run BEFORE ADK / shared imports so MONGO_URI_DIRECT
# and the GOOGLE_GENAI_USE_VERTEXAI switch are read at module load. Side
# effect of import: bootstrap_env() runs.
import scripts._test_bootstrap  # noqa: E402, F401

if not os.environ.get("GOOGLE_API_KEY"):
    print("ERROR: GOOGLE_API_KEY not set in .env.", file=sys.stderr)
    sys.exit(1)

# ADK imports must come after env vars so the Vertex/genai switch is read.
from google.adk.runners import Runner  # noqa: E402
from google.adk.sessions import InMemorySessionService  # noqa: E402
from google.genai.types import Content, Part  # noqa: E402

from shared import mongo_tools  # noqa: E402

# Agents — imported lazily inside each workflow function so a single
# agent's import failure (e.g., missing Secret Manager auth) doesn't
# kill the whole run.


# ---------------------------------------------------------------------------
# Workflow harness — each function takes (icp, topic, channel) → dict of
# (ok: bool, telemetry_id, summary, error)
# ---------------------------------------------------------------------------


class WorkflowFailed(Exception):
    """Raised when a workflow's agent run threw. Caught by the dispatcher
    so one agent's failure doesn't take down the whole e2e run."""


async def _run_agent(
    agent,
    *,
    app_name: str,
    message_text: str,
    seed_state: dict,
) -> dict:
    """Generic ADK Runner driver. Spins up an in-memory session, seeds
    state, sends one user message, drains events, returns the final
    session state for the workflow to extract its outputs from."""
    session_service = InMemorySessionService()
    runner = Runner(
        agent=agent,
        app_name=app_name,
        session_service=session_service,
    )

    session = await session_service.create_session(
        app_name=app_name,
        user_id="e2e",
        state=seed_state,
    )

    message = Content(role="user", parts=[Part.from_text(text=message_text)])

    started = time.monotonic()
    seen_agents: set[str] = set()
    async for event in runner.run_async(
        user_id="e2e",
        session_id=session.id,
        new_message=message,
    ):
        author = getattr(event, "author", None) or "?"
        if author not in seen_agents:
            seen_agents.add(author)
            print(f"    [{time.monotonic() - started:5.1f}s] {author}")

    final = await session_service.get_session(
        app_name=app_name,
        user_id="e2e",
        session_id=session.id,
    )
    return final.state if final else {}


# ---------------------------------------------------------------------------
# Workflow 1 — Customer Voice ingest. Real transcripts as raw text;
# the agent extracts quotable customer_voice rows. Multiple runs across
# ICPs produce a varied voice library.
# ---------------------------------------------------------------------------

VOICE_TRANSCRIPTS = [
    {
        "icp": "seg_founder_b2b",
        "source_kind": "sales_call",
        "raw_text": (
            "[Sales call: 2026-04-12 with Maya, founder of a 22-person devtools "
            "startup]\n\n"
            "Maya: We tried Hubspot's sequences last quarter. The handoff from SDR "
            "to AE was the worst part — by the time the AE picked it up, the prospect "
            "had already cooled off. Half the context was missing.\n\n"
            "Sales rep: That's actually a really common pattern. We see it most with "
            "teams that bolted CSM on after sales rather than designing it together.\n\n"
            "Maya: Yeah, our retention is fine but expansion is dead. Every "
            "renewal feels like a cold start. If you could tell me what to actually do "
            "differently in the first 30 days, I'd care about that more than another "
            "dashboard. We don't need more data, we need to know what to do with it.\n\n"
            "Sales rep: Got it — so the gap is between observing churn risk and "
            "knowing the move.\n\n"
            "Maya: Exactly. And honestly, the email automation tools all feel like "
            "they were built for 2019. Nobody's pulling in real product telemetry."
        ),
    },
    {
        "icp": "seg_revops_director",
        "source_kind": "nps",
        "raw_text": (
            "[NPS verbatim responses, March 2026 cohort]\n\n"
            "1. \"The integration with Salesforce takes weeks to get right. I'd give a 9 "
            "if you had a one-click connector for the standard objects.\" — Mid-market "
            "RevOps lead, scored 7\n\n"
            "2. \"Best decision we made this quarter. We replaced three tools with this. "
            "Saves the team probably 6 hours a week on attribution clean-up.\" — VP "
            "RevOps, scored 10\n\n"
            "3. \"Honestly the alerting is too noisy. I muted half the channels and now "
            "I'm worried I'll miss something real.\" — RevOps director, scored 5\n\n"
            "4. \"My CSMs ask me weekly why the renewal forecast doesn't match what they "
            "see in their account view. We're spending more time reconciling than "
            "forecasting.\" — RevOps director, scored 6"
        ),
    },
    {
        "icp": "seg_ae_growth",
        "source_kind": "churn_interview",
        "raw_text": (
            "[Churn interview transcript: 2026-04-30, departing customer Jordan, "
            "AE/Growth at a 50-person SaaS]\n\n"
            "Jordan: Look, the product is fine. The problem is nobody on my team "
            "actually used the AI suggestions. They felt generic — like they were written "
            "for some imaginary BDR, not for what we sell.\n\n"
            "Interviewer: Did the customization options not help?\n\n"
            "Jordan: We tried. But every time we tweaked it, the next batch of "
            "suggestions snapped back to the default voice. It was like fighting the "
            "system to make it sound like us.\n\n"
            "Interviewer: That's really useful. Anything else?\n\n"
            "Jordan: Pricing felt right for what we got, but the per-seat model didn't "
            "fit how we work — our SDRs run sequences for the whole AE team. We were "
            "essentially paying 5x for one workflow."
        ),
    },
]


async def run_customer_voice() -> list[dict]:
    """Run the customer_voice_agent once per transcript. The agent
    extracts quotable customer_voice rows and inserts them via mongodb.
    Returns a list of per-run dicts."""
    from agents.customer_voice import customer_voice_agent

    results = []
    for i, tx in enumerate(VOICE_TRANSCRIPTS):
        print(f"\n  Voice ingest {i+1}/{len(VOICE_TRANSCRIPTS)} "
              f"({tx['source_kind']} for {tx['icp']})")
        telemetry_id = f"act_e2e_voice_{uuid.uuid4().hex[:8]}"
        seed_state = {
            "telemetry_id": telemetry_id,
            "icp_segment": tx["icp"],
            "source_kind": tx["source_kind"],
            "source_id": f"e2e_{i}",
            "icp_segments": ["seg_founder_b2b", "seg_revops_director",
                             "seg_ae_growth", "seg_pmm_growth"],
        }
        message = (
            f"Ingest the following {tx['source_kind']} text into customer_voice. "
            f"Default icp_segment is {tx['icp']}. Extract every quotable "
            f"statement.\n\n=== RAW TEXT ===\n{tx['raw_text']}"
        )
        try:
            await _run_agent(
                customer_voice_agent,
                app_name="e2e_voice",
                message_text=message,
                seed_state=seed_state,
            )
            results.append({"ok": True, "icp": tx["icp"]})
        except Exception as e:
            print(f"    FAILED: {e}")
            results.append({"ok": False, "icp": tx["icp"], "error": str(e)})

    # Audit what landed
    db = mongo_tools.db()
    count = db["customer_voice"].count_documents({})
    print(f"  → customer_voice now has {count} rows")
    return results


# ---------------------------------------------------------------------------
# Workflow 2 — Positioning. Reads the voice library, proposes claims.
# After it runs, we promote 2 claims to messaging_library so the
# drafting pipeline has approved_claims to work with — this simulates
# the founder clicking Approve on positioning_proposals via the UI.
# ---------------------------------------------------------------------------

POSITIONING_BRIEFS = [
    ("seg_founder_b2b",
     "Founders running a 10-50 person B2B SaaS care about expansion revenue, "
     "not vanity metrics. Propose 2-3 claims grounded in our customer_voice "
     "rows for this ICP."),
    ("seg_revops_director",
     "RevOps directors want one source of truth for the renewal forecast. "
     "Propose 2-3 claims targeting that pain — anchor in voice quotes "
     "about attribution reconciliation."),
]


async def run_positioning() -> list[dict]:
    """Run positioning_agent across 2 ICPs, then auto-promote a couple
    of proposed claims to messaging_library."""
    from agents.positioning import positioning_agent

    results = []
    for icp, brief in POSITIONING_BRIEFS:
        print(f"\n  Positioning run for {icp}")
        telemetry_id = f"act_e2e_pos_{uuid.uuid4().hex[:8]}"
        seed_state = {
            "telemetry_id": telemetry_id,
            "icp_segment": icp,
            "channel": "positioning",
            "skill_id": "positioning_maintenance",
        }
        try:
            await _run_agent(
                positioning_agent,
                app_name="e2e_positioning",
                message_text=brief,
                seed_state=seed_state,
            )
            results.append({"ok": True, "icp": icp})
        except Exception as e:
            print(f"    FAILED: {e}")
            results.append({"ok": False, "icp": icp, "error": str(e)})

    # Auto-promote roughly HALF the proposed claims to messaging_library
    # so the drafting pipeline has something to ground in. This simulates
    # the founder approving via the UI — but we deliberately leave the
    # other half as ``status="proposed"`` so the Positioning agent's
    # inbox in the Agents page has something for the founder to action.
    db = mongo_tools.db()
    n_proposed = db["positioning_proposals"].count_documents({"status": "proposed"})
    n_to_promote = max(1, n_proposed // 2)
    promoted = 0
    for prop in db["positioning_proposals"].find(
        {"status": "proposed"}
    ).limit(n_to_promote):
        # Promote the positioning proposal into messaging_library — same
        # write path the founder approval flow takes in production, routed
        # through the history-aware helpers so both writes land with
        # _provenance + a history.<coll> create/update row.
        from mongo.history import (
            default_provenance_block,
            insert_with_provenance,
            update_with_history,
        )
        claim_block = default_provenance_block(
            actor_id="e2e_workflows_simulated_founder",
            kind="human", trust_tier="verified", confidence=1.0,
            source_kind="positioning_proposal_promotion",
        )
        claim = {
            **claim_block,
            "claim_text": prop.get("claim_text", ""),
            "applies_to_icp": prop.get("applies_to_icp", []),
            "status": "approved",
            "approved_at": datetime.now(UTC),
            "evidence_url": "https://internal.example.com/proof",
            "_from_proposal": str(prop["_id"]),
        }
        insert_with_provenance(
            "messaging_library", claim,
            actor_id="e2e_workflows_simulated_founder",
            change_kind="proposal_promoted",
        )
        update_with_history(
            "positioning_proposals", {"_id": prop["_id"]},
            {"$set": {"status": "approved",
                      "approved_at": datetime.now(UTC)}},
            actor_id="e2e_workflows_simulated_founder",
            change_kind="proposal_approved",
        )
        promoted += 1

    print(f"  → positioning_proposals: "
          f"{db['positioning_proposals'].count_documents({})} total, "
          f"{db['positioning_proposals'].count_documents({'status': 'proposed'})} still proposed")
    print(f"  → messaging_library: "
          f"{db['messaging_library'].count_documents({})} approved claims "
          f"(promoted {promoted} in this run)")
    return results


# ---------------------------------------------------------------------------
# Workflow 3 — Drafting pipeline. Full Research → Content → Critique →
# Reviser → ImageBrief → Review → Finalizer chain. Produces actions,
# image, and downstream telemetry. Repeated across 3 (channel, topic)
# combos for density.
# ---------------------------------------------------------------------------

DRAFTING_BRIEFS = [
    ("seg_founder_b2b", "substack", "What B2B founders are getting wrong about agentic GTM"),
    ("seg_revops_director", "linkedin", "Renewal forecast reconciliation in the AI era"),
    ("seg_ae_growth", "blog", "Why personalization-at-scale is finally working"),
]


async def run_drafting() -> list[dict]:
    """Run the full drafting pipeline across several (icp, channel, topic)
    combos. Each run produces 6-7 ``actions`` rows (one per sub-agent),
    plus optional image bytes on disk + a finalizer row."""
    from agents.pipeline import drafting_pipeline

    skill_by_channel = {
        "linkedin": "linkedin_post",
        "email":    "nurture_email",
        "blog":     "blog_outline",
        "substack": "substack_post",
    }
    results = []
    for icp, channel, topic in DRAFTING_BRIEFS:
        print(f"\n  Drafting pipeline: {channel} / {icp} — '{topic[:50]}'")
        telemetry_id = f"act_e2e_draft_{uuid.uuid4().hex[:8]}"
        seed_state = {
            "telemetry_id": telemetry_id,
            "icp_segment": icp,
            "channel": channel,
            "topic_hint": topic,
            "skill_id": skill_by_channel.get(channel, "linkedin_post"),
        }
        message = (
            f"Draft a {channel} post targeting {icp}. Focus on: {topic}"
        )
        try:
            await _run_agent(
                drafting_pipeline,
                app_name="e2e_drafting",
                message_text=message,
                seed_state=seed_state,
            )
            results.append({"ok": True, "channel": channel, "icp": icp,
                            "telemetry_id": telemetry_id})
        except Exception as e:
            print(f"    FAILED: {e}")
            results.append({"ok": False, "channel": channel, "icp": icp,
                            "error": str(e)})

    db = mongo_tools.db()
    print(f"  → actions: {db['actions'].count_documents({})} total")
    return results


# ---------------------------------------------------------------------------
# Workflow 4 — Lifecycle email sequences. The agent inserts to
# email_sequences with status='draft'. Two runs across different ICPs.
# ---------------------------------------------------------------------------

LIFECYCLE_BRIEFS = [
    ("seg_founder_b2b",
     "Draft a 4-step nurture sequence for founders who downloaded the "
     "'expansion revenue' guide but haven't booked a demo. Theme: turning "
     "passive interest into a 15-min conversation."),
    ("seg_revops_director",
     "Draft a 3-step re-engagement sequence for RevOps directors whose "
     "trials expired without converting. Theme: the integration friction "
     "they raised in their churn interviews."),
]


async def run_lifecycle_email() -> list[dict]:
    from agents.lifecycle_email import lifecycle_email_agent

    results = []
    for icp, brief in LIFECYCLE_BRIEFS:
        print(f"\n  Lifecycle Email sequence for {icp}")
        telemetry_id = f"act_e2e_email_{uuid.uuid4().hex[:8]}"
        seed_state = {
            "telemetry_id": telemetry_id,
            "icp_segment": icp,
            "channel": "lifecycle_email",
            "skill_id": "nurture_email_sequence",
        }
        try:
            await _run_agent(
                lifecycle_email_agent,
                app_name="e2e_lifecycle",
                message_text=brief,
                seed_state=seed_state,
            )
            results.append({"ok": True, "icp": icp})
        except Exception as e:
            print(f"    FAILED: {e}")
            results.append({"ok": False, "icp": icp, "error": str(e)})

    db = mongo_tools.db()
    print(f"  → email_sequences: "
          f"{db['email_sequences'].count_documents({})} total, "
          f"{db['email_sequences'].count_documents({'status': 'draft'})} drafts awaiting approval")
    return results


# ---------------------------------------------------------------------------
# Workflow 5 — Paid media. Reads paid_thresholds + voice + claims;
# proposes paused variants + stop-loss incidents. This agent's BQ path
# WILL fail in LOCAL_DEV (no telemetry.outcomes). We handle the failure
# gracefully — the agent should still propose variants from voice +
# claims even if BQ is unreachable.
# ---------------------------------------------------------------------------

PAID_BRIEFS = [
    ("seg_revops_director", "google_ads",
     "Propose 3 paused Google Ads RSA variants targeting RevOps directors. "
     "Test angle: integration friction. No live BQ in this run — focus on "
     "variant proposals."),
    ("seg_founder_b2b", "linkedin_ads",
     "Propose 3 paused LinkedIn Ads variants targeting B2B founders. "
     "Test angle: expansion revenue."),
]


async def run_paid_media() -> list[dict]:
    from agents.paid_media import paid_media_agent

    results = []
    for icp, channel, brief in PAID_BRIEFS:
        print(f"\n  Paid Media: {channel} for {icp}")
        telemetry_id = f"act_e2e_paid_{uuid.uuid4().hex[:8]}"
        platform_skill = {
            "google_ads": "paid_variant_google",
            "meta_ads": "paid_variant_meta",
            "linkedin_ads": "paid_variant_linkedin",
        }.get(channel, "paid_variant_google")
        seed_state = {
            "telemetry_id": telemetry_id,
            "icp_segment": icp,
            "channel": channel,
            "skill_id": platform_skill,
        }
        try:
            await _run_agent(
                paid_media_agent,
                app_name="e2e_paid",
                message_text=brief,
                seed_state=seed_state,
            )
            results.append({"ok": True, "icp": icp, "channel": channel})
        except Exception as e:
            print(f"    FAILED: {e}")
            results.append({"ok": False, "icp": icp, "channel": channel,
                            "error": str(e)})

    db = mongo_tools.db()
    print(f"  → paid_variants: {db['paid_variants'].count_documents({})} total, "
          f"{db['paid_variants'].count_documents({'status': 'paused'})} paused")
    return results


# ---------------------------------------------------------------------------
# Workflow 6 — Self-critique. Reads recent actions + edits, proposes
# revisions to playbooks. Operates on whatever telemetry we just
# produced, so it runs AFTER drafting.
# ---------------------------------------------------------------------------

async def run_self_critique() -> list[dict]:
    from agents.self_critique import self_critique_agent

    print("\n  Self-Critique sweep")
    telemetry_id = f"act_e2e_critique_{uuid.uuid4().hex[:8]}"
    seed_state = {
        "telemetry_id": telemetry_id,
        "skill_id": "self_critique",
    }
    message = (
        "Run the self-critique sweep across all skills with recent activity. "
        "Look at the latest actions and any approval edits. Propose revisions "
        "only where you can see a pattern from at least 2 data points."
    )
    try:
        await _run_agent(
            self_critique_agent,
            app_name="e2e_self_critique",
            message_text=message,
            seed_state=seed_state,
        )
        ok = True
        err = None
    except Exception as e:
        print(f"    FAILED: {e}")
        ok = False
        err = str(e)

    db = mongo_tools.db()
    n_props = db["skills"].count_documents(
        {"self_critique_proposal.status": "awaiting_human_review"}
    )
    print(f"  → skills with self_critique_proposal: {n_props}")
    return [{"ok": ok, "proposals_open": n_props, "error": err}]


# ---------------------------------------------------------------------------
# Workflow 7 — CMO weekly memo. The slack_approval tool gracefully no-ops
# when slack_webhook_url isn't configured, so this run still produces an
# approval row with a memo.
# ---------------------------------------------------------------------------

async def run_ops_qa() -> list[dict]:
    """Run ops_qa_agent against a small set of stable public URLs.

    The agent reads ``ops_targets`` (the founder-curated list of URLs to
    monitor), probes each via ``http_health_check``, and opens
    ``ops_incidents`` for any failures. In the e2e test we seed two
    well-known public URLs (example.com + a likely-broken one to
    trigger an incident). Targets are infrastructure config, not
    transactional data — the agent doesn't produce its OWN target list.
    """
    from agents.ops_qa import ops_qa_agent

    db = mongo_tools.db()
    # Seed 2 ops_targets if the collection is empty. These are stable
    # public URLs — one healthy, one likely-broken — so the agent has
    # something concrete to probe + at least one incident to open.
    if db["ops_targets"].count_documents({}) == 0:
        db["ops_targets"].insert_many([
            {
                "_id": "homepage_example",
                "kind": "landing_page",
                "url": "https://example.com",
                "expected_substring": "Example Domain",
                "description": "Public test target — should always 200",
            },
            {
                "_id": "broken_endpoint",
                "kind": "landing_page",
                "url": "https://example.com/this-endpoint-does-not-exist-xyz",
                "expected_substring": "Order confirmation",
                "description": "Designed to fail — triggers a real ops_incident",
            },
        ])
        print(f"  Seeded {db['ops_targets'].count_documents({})} ops_targets")

    print("\n  Ops/QA sweep")
    telemetry_id = f"act_e2e_ops_{uuid.uuid4().hex[:8]}"
    seed_state = {
        "telemetry_id": telemetry_id,
        "skill_id": "ops_qa_sweep",
    }
    message = (
        "Run the ops/QA sweep. Read ops_targets, probe each URL via "
        "http_health_check, and open an ops_incident for any failure. "
        "Use category='uptime' and severity proportional to impact."
    )
    try:
        await _run_agent(
            ops_qa_agent,
            app_name="e2e_ops_qa",
            message_text=message,
            seed_state=seed_state,
        )
        ok = True
        err = None
    except Exception as e:
        print(f"    FAILED: {e}")
        ok = False
        err = str(e)

    n_open = db["ops_incidents"].count_documents({"status": "open"})
    print(f"  → ops_incidents: {n_open} open")
    return [{"ok": ok, "incidents_open": n_open, "error": err}]


async def run_cmo_planner() -> list[dict]:
    from agents.cmo_planner import cmo_planner

    print("\n  CMO weekly memo")
    telemetry_id = f"act_e2e_cmo_{uuid.uuid4().hex[:8]}"
    seed_state = {
        "telemetry_id": telemetry_id,
        "skill_id": "weekly_memo",
    }
    message = (
        "Compose the weekly CMO memo. Review the last 7 days of telemetry "
        "actions, decided experiments, and any self_critique_proposals. "
        "Self-verify every numeric claim via validate_claim before "
        "calling slack_approval."
    )
    try:
        await _run_agent(
            cmo_planner,
            app_name="e2e_cmo",
            message_text=message,
            seed_state=seed_state,
        )
        ok = True
        err = None
    except Exception as e:
        print(f"    FAILED: {e}")
        ok = False
        err = str(e)

    db = mongo_tools.db()
    n_appr = db["approvals"].count_documents({})
    print(f"  → approvals: {n_appr} total")
    return [{"ok": ok, "approvals": n_appr, "error": err}]


# ---------------------------------------------------------------------------
# Workflow dispatcher.
# ---------------------------------------------------------------------------

WORKFLOWS: dict[str, Callable[[], Any]] = {
    "voice":       run_customer_voice,
    "positioning": run_positioning,
    "drafting":    run_drafting,
    "lifecycle":   run_lifecycle_email,
    "paid":        run_paid_media,
    "ops_qa":      run_ops_qa,
    "critique":    run_self_critique,
    "cmo":         run_cmo_planner,
}

# Default execution order — upstream first so later agents have inputs.
DEFAULT_ORDER = ["voice", "positioning", "drafting", "lifecycle", "paid",
                 "ops_qa", "critique", "cmo"]


async def main():
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--workflows", default=None,
                   help="Comma-separated subset (e.g. 'voice,drafting'). "
                        "Default: all in dependency order.")
    p.add_argument("--dry-run", action="store_true",
                   help="Print the plan without running anything.")
    args = p.parse_args()

    selected = (args.workflows.split(",") if args.workflows
                else DEFAULT_ORDER)
    selected = [w.strip() for w in selected if w.strip()]
    unknown = [w for w in selected if w not in WORKFLOWS]
    if unknown:
        print(f"ERROR: unknown workflow(s): {unknown}", file=sys.stderr)
        print(f"  Known: {sorted(WORKFLOWS)}", file=sys.stderr)
        sys.exit(1)

    print("=" * 72)
    print("  E2E WORKFLOW RUN")
    print("=" * 72)
    print(f"  Workflows:     {selected}")
    print(f"  Mongo URI:     {os.environ['MONGO_URI_DIRECT']}")
    print(f"  Mongo DB:      {os.environ['MONGO_DB']}")
    print(f"  LOCAL_DEV:     {os.environ['LOCAL_DEV']}")
    print(f"  GOOGLE_API_KEY: set ({len(os.environ['GOOGLE_API_KEY'])} chars)")
    print("=" * 72)

    if args.dry_run:
        for w in selected:
            print(f"  would run: {w}")
        return

    overall_started = time.monotonic()
    summary: dict[str, list[dict]] = {}
    for w in selected:
        print(f"\n{'-' * 72}\n>> {w.upper()}\n{'-' * 72}")
        started = time.monotonic()
        try:
            res = await WORKFLOWS[w]()
            summary[w] = res
        except Exception as e:
            print(f"  WORKFLOW {w} CRASHED: {e}")
            summary[w] = [{"ok": False, "error": str(e), "crashed": True}]
        elapsed = time.monotonic() - started
        print(f"  ({elapsed:.1f}s)")

    print(f"\n{'=' * 72}")
    print(f"  COMPLETED in {time.monotonic() - overall_started:.0f}s")
    print(f"{'=' * 72}")
    for w, res in summary.items():
        oks = sum(1 for r in res if r.get("ok"))
        print(f"  {w:14} {oks}/{len(res)} runs ok")

    # Final audit — what landed in every collection
    print("\n  Final collection counts:")
    from pymongo import MongoClient
    db = MongoClient(os.environ["MONGO_URI_DIRECT"])[os.environ["MONGO_DB"]]
    for c in [
        "actions", "outcomes", "approvals", "customer_voice",
        "messaging_library", "negative_examples", "experiments",
        "positioning_proposals", "email_sequences", "paid_variants",
        "ops_incidents", "skills", "skill_usage", "attribution_map",
    ]:
        try:
            n = db[c].count_documents({})
            print(f"    {c:25} {n}")
        except Exception as e:
            print(f"    {c:25} ERR {e}")


if __name__ == "__main__":
    asyncio.run(main())
