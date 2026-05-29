"""End-to-end smoke test for every page's API surface.

Hits each endpoint the React UI consumes, asserts:
  - HTTP 200 (not 404 / 500 / timeout)
  - Response parses as JSON
  - Response shape matches the TypeScript interface the page expects
  - For pages backed by transactional data, response is non-empty

Designed to run AFTER ``tests/e2e/e2e_workflows.py`` has driven real
agent runs that populated Mongo. Pages that legitimately stay empty in
LOCAL_DEV (rubric trend without Vertex Eval auth) are flagged as
``EMPTY OK`` rather than failed.

Usage (from a fresh terminal, with the API already running on :8080):
  python -m tests.e2e.e2e_smoke
  python -m tests.e2e.e2e_smoke --base http://localhost:8080
  python -m tests.e2e.e2e_smoke --boot   # spawn the API itself + tear it down

Exit code 0 if every check passes; 1 if any FAIL surfaced.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import requests

# Env bootstrap — loads .env, sets LOCAL_DEV/Mongo defaults, forces UTF-8
# stdio. Side effect of import: bootstrap_env() runs.
from scripts._test_bootstrap import REPO_ROOT  # noqa: E402

# ---------------------------------------------------------------------------
# Check definitions. A Check is one (endpoint, asserter) pair. The
# asserter takes the parsed JSON body + the page name; returns
# ("PASS"|"EMPTY_OK"|"FAIL", reason).
# ---------------------------------------------------------------------------

@dataclass
class CheckResult:
    page: str
    endpoint: str
    status: str             # PASS | EMPTY_OK | FAIL | HTTP_ERR
    reason: str
    http_status: int | None = None
    elapsed_ms: int | None = None


def expect_list_nonempty(min_rows: int = 1) -> Callable:
    def _check(body: Any, page: str) -> tuple[str, str]:
        if not isinstance(body, list):
            return ("FAIL", f"expected list, got {type(body).__name__}")
        if len(body) < min_rows:
            return ("EMPTY_OK",
                    f"got {len(body)} rows (expected >={min_rows}) — "
                    f"either run e2e_workflows first or the agent isn't "
                    f"producing rows for this page yet")
        return ("PASS", f"{len(body)} rows")
    return _check


def expect_dict_with_keys(*required_keys: str) -> Callable:
    def _check(body: Any, page: str) -> tuple[str, str]:
        if not isinstance(body, dict):
            return ("FAIL", f"expected dict, got {type(body).__name__}")
        missing = [k for k in required_keys if k not in body]
        if missing:
            return ("FAIL", f"missing keys: {missing}")
        return ("PASS", f"keys present: {list(required_keys)}")
    return _check


def expect_agents_roster_well_formed() -> Callable:
    def _check(body: Any, page: str) -> tuple[str, str]:
        if not isinstance(body, dict) or "agents" not in body:
            return ("FAIL", "missing 'agents' key")
        agents = body["agents"]
        if len(agents) < 10:
            return ("FAIL", f"expected >=10 agents, got {len(agents)}")
        # Spot-check the first agent has all required fields the UI reads.
        required = {"agent_id", "skills_allowed", "tools", "a2a_port",
                    "inbox", "recent_actions"}
        a = agents[0]
        missing = required - a.keys()
        if missing:
            return ("FAIL", f"first agent missing fields: {missing}")
        inbox_keys = {"count", "label", "sample"}
        if (set(a["inbox"].keys()) & inbox_keys) != inbox_keys:
            return ("FAIL", f"agent.inbox missing required keys: "
                            f"{inbox_keys - set(a['inbox'].keys())}")
        # Confirm at least one agent has either a non-empty inbox OR
        # recent_actions — otherwise the page would render as all-empty.
        any_data = any(
            (a["inbox"]["count"] > 0 or len(a.get("recent_actions") or []) > 0)
            for a in agents
        )
        if not any_data:
            return ("EMPTY_OK",
                    "all 12 agents have empty inbox + zero recent_actions "
                    "— run e2e_workflows.py first to populate")
        # Make sure inbox sample items include _summary (the human-readable
        # description we added — the user explicitly called out the IDs
        # showing up before this fix).
        for a in agents:
            for it in a["inbox"]["sample"]:
                if "_summary" not in it:
                    return ("FAIL",
                            f"{a['agent_id']} inbox item missing _summary — "
                            f"UI will fall back to opaque IDs")
        return ("PASS", f"{len(agents)} agents, "
                        f"{sum(a['inbox']['count'] for a in agents)} total inbox items")
    return _check


def expect_weekly_review_assembled() -> Callable:
    def _check(body: Any, page: str) -> tuple[str, str]:
        required = {"summary", "decided_experiments", "running_experiments",
                    "drift_investigations", "self_critique_proposals"}
        if not isinstance(body, dict):
            return ("FAIL", f"expected dict, got {type(body).__name__}")
        missing = required - body.keys()
        if missing:
            return ("FAIL", f"missing keys: {missing}")
        return ("PASS",
                f"summary has {body['summary'].get('total_actions', 0)} actions, "
                f"{len(body['decided_experiments'])} decided, "
                f"{len(body['running_experiments'])} running")
    return _check


def expect_capabilities_well_formed() -> Callable:
    def _check(body: Any, page: str) -> tuple[str, str]:
        for k in ("catalog", "by_agent", "by_skill", "summary",
                  "timeseries", "recent"):
            if k not in body:
                return ("FAIL", f"missing key {k!r}")
        cat = body["catalog"]
        if len(cat) < 5:
            return ("FAIL", f"catalog has {len(cat)} skills (expected >=5)")
        total_loads = body["summary"].get("loads_period", 0)
        if total_loads == 0:
            return ("EMPTY_OK",
                    f"{len(cat)} skills in catalog but 0 loads recorded — "
                    f"run drafting workflow to populate skill_usage")
        return ("PASS",
                f"{len(cat)} skills, {total_loads} loads in window, "
                f"{len(body['by_agent'])} agents with loads")
    return _check


def expect_skill_body() -> Callable:
    def _check(body: Any, page: str) -> tuple[str, str]:
        for k in ("body", "format", "version_label", "source_path"):
            if k not in body:
                return ("FAIL", f"missing key {k!r}")
        if not body["body"] or len(body["body"]) < 50:
            return ("FAIL", f"body too short ({len(body.get('body', ''))} chars)")
        return ("PASS",
                f"{len(body['body'])} chars, format={body['format']}, "
                f"version={body['version_label']}")
    return _check


def expect_live_now_shape() -> Callable:
    def _check(body: Any, page: str) -> tuple[str, str]:
        for k in ("server_time", "active_jobs", "recent_agents"):
            if k not in body:
                return ("FAIL", f"missing key {k!r}")
        return ("PASS",
                f"active_jobs={len(body['active_jobs'])}, "
                f"recent_agents={len(body['recent_agents'])}")
    return _check


def expect_live_full_shape() -> Callable:
    def _check(body: Any, page: str) -> tuple[str, str]:
        for k in ("recent_actions", "heartbeats", "scheduled_jobs", "server_time"):
            if k not in body:
                return ("FAIL", f"missing key {k!r}")
        return ("PASS",
                f"recent_actions={len(body['recent_actions'])}, "
                f"heartbeats={len(body['heartbeats'])}, "
                f"scheduled_jobs={len(body['scheduled_jobs'])}")
    return _check


# ---------------------------------------------------------------------------
# The catalog of checks. Each tuple = (page, endpoint, asserter, ttl_seconds)
# ---------------------------------------------------------------------------

def expect_signal_sources() -> Callable:
    """Signals page — source-health table. Must surface the 3 seeded sources,
    each with the SignalSource shape the table reads."""
    def _check(body: Any, page: str) -> tuple[str, str]:
        if not isinstance(body, list):
            return ("FAIL", f"expected list, got {type(body).__name__}")
        if len(body) < 3:
            return ("FAIL", f"expected >=3 seeded sources, got {len(body)}")
        required = {"name", "source", "enabled", "icp_segment",
                    "poll_interval_sec", "score_floor", "default_channel"}
        missing = required - set(body[0].keys())
        if missing:
            return ("FAIL", f"source row missing keys: {missing}")
        return ("PASS", f"{len(body)} sources, shape OK")
    return _check


def expect_learning_summary() -> Callable:
    """Self-Learning page — the closed-loop KPI band. Validates the summary
    contract the /learning page renders."""
    def _check(body: Any, page: str) -> tuple[str, str]:
        if not isinstance(body, dict):
            return ("FAIL", f"expected dict, got {type(body).__name__}")
        missing = {"days", "this_window", "pending_proposals",
                   "per_miner_28d", "events"} - body.keys()
        if missing:
            return ("FAIL", f"missing keys: {missing}")
        tw = body["this_window"]
        tw_missing = {"proposals_emitted", "proposals_accepted",
                      "proposals_dismissed", "promotions", "miner_runs"} - tw.keys()
        if tw_missing:
            return ("FAIL", f"this_window missing: {tw_missing}")
        if not isinstance(body["per_miner_28d"], list):
            return ("FAIL", "per_miner_28d must be a list")
        return ("PASS",
                f"pending={body['pending_proposals']} "
                f"runs={tw['miner_runs']} miners={len(body['per_miner_28d'])}")
    return _check


def expect_unified_proposals() -> Callable:
    """Weekly Review / Learning — unified proposals list. Each row (if any)
    must carry the dispatch envelope the UI + approve/dismiss rely on."""
    def _check(body: Any, page: str) -> tuple[str, str]:
        if not isinstance(body, list):
            return ("FAIL", f"expected list, got {type(body).__name__}")
        for p in body:
            missing = {"id", "target_kind", "target_id", "issue",
                       "status"} - p.keys()
            if missing:
                return ("FAIL", f"proposal missing keys: {missing}")
            if p["target_kind"] not in ("skill", "paid_action", "signal_source"):
                return ("FAIL", f"bad target_kind {p['target_kind']!r}")
        return ("PASS" if body else "EMPTY_OK", f"{len(body)} pending proposals")
    return _check


def expect_integrations_status() -> Callable:
    def _check(body: Any, page: str) -> tuple[str, str]:
        # Shape varies (dict keyed by adapter, or {adapters:[...]}); accept a
        # non-empty dict/list so a broken endpoint (500/empty) still fails.
        if isinstance(body, dict) and body:
            return ("PASS", f"{len(body)} keys")
        if isinstance(body, list) and body:
            return ("PASS", f"{len(body)} adapters")
        return ("FAIL", "empty / unexpected integrations status shape")
    return _check


CHECKS: list[tuple[str, str, Callable, int]] = [
    # /queue
    ("/queue",         "/api/queue",                  expect_list_nonempty(),                10),

    # /weekly-review
    ("/weekly-review", "/api/weekly-review",          expect_weekly_review_assembled(),      15),
    ("/weekly-review", "/api/this-week-summary",      expect_dict_with_keys("drafts", "approvals", "armor_blocks", "total_actions", "edits"), 10),

    # /agents — the new page
    ("/agents",        "/api/agents",                 expect_agents_roster_well_formed(),    10),

    # /experiments
    ("/experiments",   "/api/experiments/running",    expect_list_nonempty(min_rows=0),       5),
    ("/experiments",   "/api/experiments/decided",    expect_list_nonempty(min_rows=0),       5),
    ("/experiments",   "/api/experiments/drift",      expect_list_nonempty(min_rows=0),       5),

    # /skills
    ("/skills",        "/api/skills",                 expect_list_nonempty(),                 5),
    ("/skills",        "/api/skills/linkedin_post/body", expect_skill_body(),                  5),
    ("/skills",        "/api/skills/house-style/body",  expect_skill_body(),                   5),

    # /capabilities
    ("/capabilities",  "/api/capabilities?days=7",    expect_capabilities_well_formed(),     10),

    # /voice
    ("/voice",         "/api/voice",                  expect_list_nonempty(),                 5),

    # /telemetry
    ("/telemetry",     "/api/rubric-trend?days=28",   expect_list_nonempty(min_rows=0),       5),
    ("/telemetry",     "/api/negatives",              expect_list_nonempty(min_rows=0),       5),
    # this-week reused for armor block count

    # /live
    ("/live",          "/api/live",                   expect_live_full_shape(),              10),
    ("/live",          "/api/live/now",               expect_live_now_shape(),                5),

    # /signals (PRD-02) — previously uncovered by the smoke.
    ("/signals",       "/api/signals",                expect_list_nonempty(min_rows=0),       5),
    ("/signals",       "/api/signals/sources",        expect_signal_sources(),               10),

    # /learning + Weekly Review proposals (PRD-03) — previously uncovered.
    ("/learning",      "/api/self-critique/summary?days=7", expect_learning_summary(),        10),
    ("/learning",      "/api/self-critique/runs",     expect_list_nonempty(min_rows=0),       5),
    ("/weekly-review", "/api/self-critique/proposals", expect_unified_proposals(),            10),
    ("/weekly-review", "/api/self-critique",          expect_list_nonempty(min_rows=0),       5),

    # /aeo (PRD-01) + integrations — previously uncovered.
    ("/aeo",           "/api/aeo/cited-by",           expect_list_nonempty(min_rows=0),       5),
    ("/aeo",           "/api/integrations/status",    expect_integrations_status(),          10),
]


# ---------------------------------------------------------------------------

def run_checks(base: str) -> list[CheckResult]:
    results: list[CheckResult] = []
    for page, endpoint, asserter, ttl in CHECKS:
        url = base.rstrip("/") + endpoint
        t0 = time.monotonic()
        try:
            r = requests.get(url, timeout=ttl)
            elapsed_ms = int((time.monotonic() - t0) * 1000)
        except requests.RequestException as e:
            results.append(CheckResult(
                page=page, endpoint=endpoint, status="HTTP_ERR",
                reason=str(e), elapsed_ms=int((time.monotonic() - t0) * 1000),
            ))
            continue

        if not r.ok:
            results.append(CheckResult(
                page=page, endpoint=endpoint, status="HTTP_ERR",
                reason=f"HTTP {r.status_code}: {r.text[:120]}",
                http_status=r.status_code, elapsed_ms=elapsed_ms,
            ))
            continue

        try:
            body = r.json()
        except ValueError as e:
            results.append(CheckResult(
                page=page, endpoint=endpoint, status="FAIL",
                reason=f"non-JSON response: {e}",
                http_status=r.status_code, elapsed_ms=elapsed_ms,
            ))
            continue

        status, reason = asserter(body, page)
        results.append(CheckResult(
            page=page, endpoint=endpoint, status=status, reason=reason,
            http_status=r.status_code, elapsed_ms=elapsed_ms,
        ))

    return results


def print_results(results: list[CheckResult]) -> int:
    by_status = {"PASS": [], "EMPTY_OK": [], "FAIL": [], "HTTP_ERR": []}
    for r in results:
        by_status[r.status].append(r)

    print()
    print("=" * 78)
    print(f"  E2E SMOKE TEST RESULTS — {len(results)} checks across "
          f"{len({r.page for r in results})} pages")
    print("=" * 78)

    for r in results:
        symbol = {
            "PASS": "[OK]",
            "EMPTY_OK": "[--]",
            "FAIL": "[XX]",
            "HTTP_ERR": "[!!]",
        }.get(r.status, "[??]")
        elapsed = f"{r.elapsed_ms:>4}ms" if r.elapsed_ms is not None else "    -"
        print(f"  {symbol} [{r.status:8}] {elapsed}  {r.page:18} "
              f"{r.endpoint}")
        if r.status != "PASS":
            print(f"        -> {r.reason}")

    print()
    print("=" * 78)
    print(f"  [OK]  PASS:     {len(by_status['PASS'])}")
    print(f"  [--]  EMPTY_OK: {len(by_status['EMPTY_OK'])}  (page renders, no data yet)")
    print(f"  [XX]  FAIL:     {len(by_status['FAIL'])}     (shape mismatch - UI will break)")
    print(f"  [!!]  HTTP_ERR: {len(by_status['HTTP_ERR'])} (endpoint broken / API down)")
    print("=" * 78)

    return 0 if (len(by_status["FAIL"]) + len(by_status["HTTP_ERR"])) == 0 else 1


def _boot_api(port: int) -> subprocess.Popen:
    """Spawn uvicorn in a child process so the smoke test is self-contained.
    Caller is responsible for terminating via the returned Popen."""
    env = os.environ.copy()
    env.setdefault("LOCAL_DEV", "1")
    env.setdefault("MONGO_URI_DIRECT", "mongodb://localhost:27017")
    env.setdefault("MONGO_DB", "agentic_marketing")
    env.setdefault("PROJECT_ID", "local-dev")
    env.setdefault("DRAFTING_FALLBACK", "synthetic")
    cmd = [
        sys.executable, "-m", "uvicorn",
        "services.web_api.main:app",
        "--port", str(port),
        "--log-level", "warning",
    ]
    return subprocess.Popen(cmd, env=env, cwd=str(REPO_ROOT))


def _wait_for_api(base: str, timeout_s: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            r = requests.get(base + "/api/health", timeout=2)
            if r.ok:
                return True
        except requests.RequestException:
            pass
        time.sleep(0.5)
    return False


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--base", default="http://localhost:8080",
                   help="Base URL of the running web_api (default: localhost:8080)")
    p.add_argument("--boot", action="store_true",
                   help="Spawn uvicorn locally + tear it down on exit "
                        "(use when no API is already running).")
    args = p.parse_args()

    api_proc: subprocess.Popen | None = None
    if args.boot:
        port = int(args.base.rsplit(":", 1)[-1])
        print(f"  Spawning API on :{port}…")
        api_proc = _boot_api(port)
        if not _wait_for_api(args.base, timeout_s=20):
            print("  API failed to start within 20s.", file=sys.stderr)
            if api_proc:
                api_proc.terminate()
            sys.exit(2)
        print("  API is up. Running checks…\n")

    try:
        results = run_checks(args.base)
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
