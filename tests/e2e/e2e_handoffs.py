"""End-to-end handoff test — drives each agent through ``POST /api/draft``
with ``agent_id`` set, polls until the job completes, and asserts the
response shape matches what the Drafting page's per-shape renderer expects.

This is the third e2e layer. The other two cover:
  - ``e2e_workflows.py`` runs every agent IN-PROCESS via real LLM, populating
    Mongo with authentic data. Validates the agent code path.
  - ``e2e_smoke.py`` hits every GET endpoint, validates page-render shape.
  - ``e2e_handoffs.py`` (this file) tests the ``/api/draft`` routing layer:
    web_api receives the request, routes to the right A2A URL or
    synthetic fallback, unwraps, returns the agent-specific shape.

Validates BOTH paths:
  - Synthetic fallback (no A2A servers running): asserts each
    ``_synthetic_draft`` branch returns the right shape with the right keys
  - Real A2A (when ``A2A_URL_BASE`` resolves): asserts live agent responses
    parse the same way through ``_unwrap_a2a_pipeline_response``

The shape contract lives in ``web/src/routes/Drafting.tsx``'s
``AgentResult`` component — if these checks pass, the UI's per-shape
renderer will resolve correctly. If they fail, the user sees a blank
result panel or a JS crash.

Usage:
  python -m tests.e2e.e2e_handoffs                       # all 9 agents
  python -m tests.e2e.e2e_handoffs --base http://localhost:8081
  python -m tests.e2e.e2e_handoffs --agents lifecycle_email_agent,positioning_agent
  python -m tests.e2e.e2e_handoffs --boot                # spawn API + tear down
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass

import requests

# Env bootstrap — loads .env, sets LOCAL_DEV/Mongo defaults, forces UTF-8
# stdio. Side effect of import: bootstrap_env() runs.
from scripts._test_bootstrap import REPO_ROOT  # noqa: E402

# ---------------------------------------------------------------------------
# Per-agent expected shape. Each tuple is:
#   (agent_id, channel, expected_shape, validator, default_topic)
#
# The validator takes the parsed result dict + agent_id and returns
# ("PASS"|"FAIL", reason). PASS means the UI will render correctly.
# ---------------------------------------------------------------------------


def _require_keys(d: dict, *keys: str) -> str | None:
    missing = [k for k in keys if k not in d]
    return f"missing keys: {missing}" if missing else None


def validate_pipeline(r: dict, agent_id: str) -> tuple[str, str]:
    """Full drafting pipeline OR Content-only — must have a ``draft`` text."""
    err = _require_keys(r, "draft")
    if err:
        return ("FAIL", err)
    if not isinstance(r["draft"], str) or len(r["draft"]) < 20:
        return ("FAIL", f"draft text too short / wrong type "
                        f"({type(r['draft']).__name__}, "
                        f"len={len(r.get('draft', ''))})")
    return ("PASS", f"draft={len(r['draft'])} chars")


def validate_lifecycle(r: dict, agent_id: str) -> tuple[str, str]:
    """Lifecycle Email — must have email_sequence with steps."""
    err = _require_keys(r, "email_sequence")
    if err:
        return ("FAIL", err)
    seq = r["email_sequence"]
    if not isinstance(seq, dict):
        return ("FAIL", f"email_sequence is {type(seq).__name__}, expected dict")
    err = _require_keys(seq, "sequence_name", "icp_segment", "steps")
    if err:
        return ("FAIL", f"email_sequence {err}")
    steps = seq["steps"]
    if not isinstance(steps, list) or len(steps) == 0:
        return ("FAIL", f"steps is {type(steps).__name__}/{len(steps) if hasattr(steps, '__len__') else '?'}")
    # Each step needs the keys the UI renders.
    for i, step in enumerate(steps):
        err = _require_keys(step, "step_num", "subject", "body", "cta", "delay_days")
        if err:
            return ("FAIL", f"step[{i}] {err}")
    return ("PASS", f"{len(steps)} steps, name={seq['sequence_name']!r}")


def validate_paid_media(r: dict, agent_id: str) -> tuple[str, str]:
    """Paid Media — must have paid_media_action with variants_proposed."""
    err = _require_keys(r, "paid_media_action")
    if err:
        return ("FAIL", err)
    action = r["paid_media_action"]
    err = _require_keys(action, "variants_proposed", "stop_loss_incidents",
                        "campaigns_reviewed")
    if err:
        return ("FAIL", f"paid_media_action {err}")
    variants = action["variants_proposed"]
    if not isinstance(variants, list):
        return ("FAIL", f"variants_proposed is {type(variants).__name__}")
    # Validate first variant's shape (UI renders headline + body + cta).
    if variants:
        v = variants[0]
        for k in ("platform", "headline", "body", "cta"):
            if k not in v and not (k == "headline" and "headlines" in v):
                return ("FAIL", f"variants_proposed[0] missing {k!r}")
    return ("PASS",
            f"{len(variants)} variants, "
            f"{len(action.get('stop_loss_incidents', []))} incidents")


def validate_positioning(r: dict, agent_id: str) -> tuple[str, str]:
    """Positioning — must have positioning_proposals list."""
    err = _require_keys(r, "positioning_proposals")
    if err:
        return ("FAIL", err)
    props = r["positioning_proposals"]
    if not isinstance(props, list) or len(props) == 0:
        return ("FAIL", f"positioning_proposals is {type(props).__name__}, "
                        f"len={len(props) if hasattr(props, '__len__') else '?'}")
    p = props[0]
    err = _require_keys(p, "_id", "kind", "claim_text", "applies_to_icp", "rationale")
    if err:
        return ("FAIL", f"positioning_proposals[0] {err}")
    return ("PASS", f"{len(props)} proposals")


def validate_customer_voice(r: dict, agent_id: str) -> tuple[str, str]:
    """Customer Voice — must have inserted + skipped_low_value + summary."""
    err = _require_keys(r, "inserted", "skipped_low_value", "summary")
    if err:
        return ("FAIL", err)
    if not isinstance(r["inserted"], int):
        return ("FAIL", f"inserted is {type(r['inserted']).__name__}, expected int")
    return ("PASS", f"inserted={r['inserted']}, skipped={r['skipped_low_value']}")


def validate_ops_qa(r: dict, agent_id: str) -> tuple[str, str]:
    """Ops/QA — must have checked + incidents_opened + summary."""
    err = _require_keys(r, "checked", "incidents_opened")
    if err:
        return ("FAIL", err)
    return ("PASS",
            f"checked={r['checked']}, incidents_opened={r['incidents_opened']}")


def validate_cmo_memo(r: dict, agent_id: str) -> tuple[str, str]:
    """CMO weekly memo — must have memo_markdown."""
    err = _require_keys(r, "memo_markdown")
    if err:
        return ("FAIL", err)
    if not isinstance(r["memo_markdown"], str) or len(r["memo_markdown"]) < 50:
        return ("FAIL", f"memo_markdown too short or wrong type "
                        f"(len={len(r.get('memo_markdown', ''))})")
    return ("PASS", f"memo_markdown={len(r['memo_markdown'])} chars")


def validate_research(r: dict, agent_id: str) -> tuple[str, str]:
    """Research — must have research_findings."""
    err = _require_keys(r, "research_findings")
    if err:
        return ("FAIL", err)
    rf = r["research_findings"]
    err = _require_keys(rf, "icp_segment", "customer_voice", "approved_claims")
    if err:
        return ("FAIL", f"research_findings {err}")
    return ("PASS",
            f"{len(rf.get('customer_voice', []))} voice quotes, "
            f"{len(rf.get('approved_claims', []))} approved claims")


def validate_image_brief(r: dict, agent_id: str) -> tuple[str, str]:
    """ImageBrief — must have image with mode and alt_text."""
    # The synthetic fallback for image_brief returns generic; the live
    # agent writes ``image`` with full fields. Accept either gracefully.
    if "image" in r:
        img = r["image"]
        err = _require_keys(img, "mode", "alt_text")
        if err:
            return ("FAIL", f"image {err}")
        return ("PASS",
                f"image mode={img['mode']}, url={(img.get('url') or 'null')[:40]}")
    if r.get("shape") == "generic":
        return ("PASS",
                "generic synthetic preview (image_brief synthetic-only "
                "fallback documented in main.py)")
    return ("FAIL", "no image field and no generic note")


def validate_analytics(r: dict, agent_id: str) -> tuple[str, str]:
    """Analytics — mini-dashboard snapshot (Drafting.tsx renders shape
    'analytics'). The synthetic path returns real Mongo counts."""
    if r.get("shape") != "analytics":
        return ("FAIL", f"expected shape=analytics, got {r.get('shape')!r}")
    snap = r.get("analytics_snapshot")
    if not isinstance(snap, dict):
        return ("FAIL", "missing analytics_snapshot")
    err = _require_keys(snap, "window", "total_actions", "drafts",
                        "channels_touched", "by_agent")
    if err:
        return ("FAIL", f"analytics_snapshot {err}")
    return ("PASS", f"window={snap['window']} actions={snap['total_actions']} "
                    f"drafts={snap['drafts']}")


def validate_self_critique(r: dict, agent_id: str) -> tuple[str, str]:
    """Self-critique — flagged skills + sweep counts (Drafting.tsx renders
    shape 'self_critique')."""
    if r.get("shape") != "self_critique":
        return ("FAIL", f"expected shape=self_critique, got {r.get('shape')!r}")
    sc = r.get("self_critique")
    if not isinstance(sc, dict):
        return ("FAIL", "missing self_critique block")
    if not isinstance(sc.get("awaiting_review"), list):
        return ("FAIL", "self_critique.awaiting_review must be a list")
    if "sweeps_7d" not in sc:
        return ("FAIL", "self_critique missing sweeps_7d")
    return ("PASS", f"awaiting_review={len(sc['awaiting_review'])} "
                    f"sweeps_7d={sc['sweeps_7d']}")


# ---------------------------------------------------------------------------
# Handoff catalog. Each entry tests one agent_id through /api/draft.
#
# (agent_id, channel, topic_hint, validator)
#
# - The "topic_hint" picks a realistic prompt for that agent.
# - The channel matters for pipeline + paid; ignored by others.
# ---------------------------------------------------------------------------

HANDOFFS: list[tuple[str, str, str, Callable]] = [
    # Full pipeline (no agent_id) — the default route.
    ("pipeline",              "linkedin",      "protocol conformance gaps in agent checkout",
     validate_pipeline),

    # Drafting domain — Content only.
    ("content_agent",         "linkedin",      "the agent checkout failures we keep hearing about",
     validate_pipeline),

    # Lifecycle email — produces a sequence.
    ("lifecycle_email_agent", "lifecycle_email", "agent-readiness nurture for DTC merchants",
     validate_lifecycle),

    # Paid media — variants + stop-loss.
    ("paid_media_agent",      "google_ads",    "protocol conformance gaps for e-commerce leaders",
     validate_paid_media),

    # Positioning — proposes claims.
    ("positioning_agent",     "linkedin",      "auto-generated agent-readiness diagnostic brief",
     validate_positioning),

    # Customer voice — ingests raw text.
    ("customer_voice_agent",  "linkedin",
     "Sales call: customer said their store fails silently in ChatGPT checkout…",
     validate_customer_voice),

    # Ops/QA sweep.
    ("ops_qa_agent",          "linkedin",      "",
     validate_ops_qa),

    # CMO weekly memo.
    ("cmo_planner",           "linkedin",      "lost AI-shopper revenue",
     validate_cmo_memo),

    # Research only.
    ("research_agent",        "substack",      "agentic commerce advancements",
     validate_research),

    # ImageBrief — falls back to ``shape=generic`` in synthetic mode
    # because no draft state is available; live path writes ``image``.
    ("image_brief_agent",     "substack",      "agentic commerce illustration",
     validate_image_brief),

    # Analytics + Self-critique → now fully implemented synthetic shapes
    # (Drafting.tsx renders 'analytics' + 'self_critique'). Previously these
    # were unimplemented (validate_generic); the table was outdated.
    ("analytics_agent",       "linkedin",      "",
     validate_analytics),
    ("self_critique_agent",   "linkedin",      "",
     validate_self_critique),
]


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

@dataclass
class HandoffResult:
    agent_id: str
    status: str             # PASS | FAIL | TIMEOUT | HTTP_ERR
    reason: str
    elapsed_s: float
    shape: str | None = None
    synthetic: bool | None = None
    job_id: str | None = None


def fire_handoff(base: str, agent_id: str, channel: str, topic_hint: str,
                  validator: Callable, poll_interval_s: float = 1.0,
                  max_wait_s: float = 180.0) -> HandoffResult:
    """Submit one handoff job, poll until done, validate the result shape."""
    started = time.monotonic()
    payload = {
        "icp_segment": "seg_merchant_dtc",
        "channel": channel,
        "topic_hint": topic_hint,
    }
    # Only set agent_id if not pipeline — pipeline is the default.
    if agent_id != "pipeline":
        payload["agent_id"] = agent_id

    try:
        r = requests.post(f"{base.rstrip('/')}/api/draft", json=payload, timeout=10)
    except requests.RequestException as e:
        return HandoffResult(agent_id=agent_id, status="HTTP_ERR",
                              reason=f"POST failed: {e}",
                              elapsed_s=time.monotonic() - started)
    if not r.ok:
        return HandoffResult(agent_id=agent_id, status="HTTP_ERR",
                              reason=f"POST HTTP {r.status_code}: {r.text[:120]}",
                              elapsed_s=time.monotonic() - started)
    job_id = r.json().get("job_id")
    if not job_id:
        return HandoffResult(agent_id=agent_id, status="FAIL",
                              reason="no job_id in POST response",
                              elapsed_s=time.monotonic() - started)

    # Poll until status ∈ {done, failed} or timeout.
    deadline = time.monotonic() + max_wait_s
    while time.monotonic() < deadline:
        time.sleep(poll_interval_s)
        try:
            j = requests.get(f"{base.rstrip('/')}/api/draft/{job_id}",
                              timeout=5).json()
        except requests.RequestException as e:
            return HandoffResult(agent_id=agent_id, status="HTTP_ERR",
                                  reason=f"GET {job_id} failed: {e}",
                                  elapsed_s=time.monotonic() - started,
                                  job_id=job_id)
        st = j.get("status")
        if st == "done":
            result = j.get("result") or {}
            shape = result.get("shape")
            synthetic = bool(result.get("synthetic"))
            verdict, reason = validator(result, agent_id)
            return HandoffResult(
                agent_id=agent_id, status=verdict, reason=reason,
                elapsed_s=time.monotonic() - started,
                shape=shape, synthetic=synthetic, job_id=job_id,
            )
        if st == "failed":
            return HandoffResult(
                agent_id=agent_id, status="FAIL",
                reason=f"job failed: {(j.get('error') or '')[:140]}",
                elapsed_s=time.monotonic() - started, job_id=job_id,
            )

    return HandoffResult(agent_id=agent_id, status="TIMEOUT",
                          reason=f"no terminal status after {max_wait_s}s",
                          elapsed_s=time.monotonic() - started, job_id=job_id)


def print_results(results: list[HandoffResult]) -> int:
    by_status = {"PASS": 0, "FAIL": 0, "TIMEOUT": 0, "HTTP_ERR": 0}
    for r in results:
        by_status[r.status] = by_status.get(r.status, 0) + 1

    print()
    print("=" * 84)
    print(f"  E2E HANDOFF RESULTS — {len(results)} agents tested via "
          f"/api/draft routing")
    print("=" * 84)

    for r in results:
        symbol = {
            "PASS":     "[OK]",
            "FAIL":     "[XX]",
            "TIMEOUT":  "[TO]",
            "HTTP_ERR": "[!!]",
        }.get(r.status, "[??]")
        mode = ("synth" if r.synthetic else "live ") if r.shape else "-----"
        shape = r.shape or "-"
        print(f"  {symbol}  {r.agent_id:24}  shape={shape:18}  "
              f"{mode}  {r.elapsed_s:5.1f}s")
        if r.status != "PASS":
            print(f"          -> {r.reason}")
        else:
            print(f"          -> {r.reason}")

    print()
    print("=" * 84)
    print(f"  [OK] PASS:     {by_status['PASS']}")
    print(f"  [XX] FAIL:     {by_status['FAIL']}  (shape mismatch - UI will not render)")
    print(f"  [TO] TIMEOUT:  {by_status['TIMEOUT']}  (job never completed)")
    print(f"  [!!] HTTP_ERR: {by_status['HTTP_ERR']}  (API down / endpoint broken)")
    print("=" * 84)

    return 0 if (by_status["FAIL"] + by_status["TIMEOUT"] + by_status["HTTP_ERR"]) == 0 else 1


# Boot helpers reused from e2e_smoke.

def _boot_api(port: int) -> subprocess.Popen:
    env = os.environ.copy()
    env.setdefault("LOCAL_DEV", "1")
    env.setdefault("MONGO_URI_DIRECT", "mongodb://localhost:27017")
    env.setdefault("MONGO_DB", "hindsight_guild")
    env.setdefault("PROJECT_ID", "local-dev")
    env.setdefault("DRAFTING_FALLBACK", "synthetic")
    cmd = [
        sys.executable, "-m", "uvicorn",
        "services.web_api.main:app",
        "--port", str(port),
        "--log-level", "warning",
    ]
    return subprocess.Popen(cmd, env=env, cwd=str(REPO_ROOT))


def _wait_for_api(base: str, timeout_s: float = 20.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            if requests.get(base + "/api/health", timeout=2).ok:
                return True
        except requests.RequestException:
            pass
        time.sleep(0.5)
    return False


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--base", default="http://localhost:8080",
                   help="Web API base URL (default: localhost:8080)")
    p.add_argument("--agents", default=None,
                   help="Comma-separated subset of agent_ids to test.")
    p.add_argument("--boot", action="store_true",
                   help="Spawn API locally + tear it down on exit.")
    p.add_argument("--max-wait", type=float, default=180.0,
                   help="Max seconds to wait per job (default 180).")
    args = p.parse_args()

    api_proc: subprocess.Popen | None = None
    if args.boot:
        port = int(args.base.rsplit(":", 1)[-1])
        print(f"  Spawning API on :{port}…")
        api_proc = _boot_api(port)
        if not _wait_for_api(args.base, timeout_s=25):
            print("  API failed to start within 25s.", file=sys.stderr)
            if api_proc:
                api_proc.terminate()
            sys.exit(2)
        print("  API up; running handoffs…\n")

    try:
        # Filter the handoff list if --agents passed.
        if args.agents:
            wanted = {a.strip() for a in args.agents.split(",") if a.strip()}
            selected = [h for h in HANDOFFS if h[0] in wanted]
            unknown = wanted - {h[0] for h in HANDOFFS}
            if unknown:
                print(f"ERROR: unknown agent(s): {unknown}", file=sys.stderr)
                sys.exit(1)
        else:
            selected = HANDOFFS

        results: list[HandoffResult] = []
        for agent_id, channel, topic, validator in selected:
            print(f"  >> {agent_id:24} on channel={channel}")
            r = fire_handoff(args.base, agent_id, channel, topic, validator,
                              max_wait_s=args.max_wait)
            print(f"     {r.status} ({r.elapsed_s:.1f}s) - {r.reason}")
            results.append(r)

        exit_code = print_results(results)
    finally:
        if api_proc:
            api_proc.terminate()
            try:
                api_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                api_proc.kill()

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
