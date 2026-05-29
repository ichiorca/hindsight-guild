"""End-to-end coverage for the three feature PRDs (01-AEO, 02-Signals,
03-Self-Critique).

This is the Python complement to ``web/tests/12-aeo.spec.ts``,
``13-signals.spec.ts``, and ``14-self-critique.spec.ts``. Where the
Playwright specs exercise the UI surface, this script exercises the
**data plane** — direct Mongo writes/reads + HTTP round-trips — so a
PRD regression that doesn't visibly break a page is still caught.

Usage::

    python -m tests.e2e.e2e_prd_features                  # uses :8080
    python -m tests.e2e.e2e_prd_features --base http://localhost:8080
    python -m tests.e2e.e2e_prd_features --only aeo       # one section
    python -m tests.e2e.e2e_prd_features --only signals
    python -m tests.e2e.e2e_prd_features --only self_critique

Exit code 0 = all sections passed; 1 = at least one failure.

The script is idempotent. It tags every row it inserts with a unique
``E2E_RUN_TAG`` and cleans up at the end (atexit). Re-runs leave Mongo
in the same state they found it.
"""
from __future__ import annotations

import argparse
import atexit
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

import requests

from agents._schema_constants import Coll, Status  # noqa: E402

# Bootstrap LOCAL_DEV defaults + REPO_ROOT.
from scripts._test_bootstrap import REPO_ROOT  # noqa: E402,F401
from shared import mongo_tools  # noqa: E402

# A short unique tag so every row we touch is recognizable + cleanable.
E2E_RUN_TAG = f"e2e_prd_{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# Result tracking
# ---------------------------------------------------------------------------

@dataclass
class Section:
    name: str
    checks: list[tuple[str, str, str]] = field(default_factory=list)
    # tuples: (check_id, "PASS"|"FAIL"|"SKIP", message)

    def pass_(self, check_id: str, msg: str = "") -> None:
        self.checks.append((check_id, "PASS", msg))

    def fail(self, check_id: str, msg: str) -> None:
        self.checks.append((check_id, "FAIL", msg))

    def skip(self, check_id: str, msg: str) -> None:
        self.checks.append((check_id, "SKIP", msg))

    @property
    def failed_count(self) -> int:
        return sum(1 for _, s, _ in self.checks if s == "FAIL")


# ---------------------------------------------------------------------------
# Cleanup registry — atexit hooks so we don't leave debris in Mongo.
# ---------------------------------------------------------------------------

_CLEANUPS: list[Callable[[], None]] = []


def _register_cleanup(fn: Callable[[], None]) -> None:
    _CLEANUPS.append(fn)


@atexit.register
def _run_cleanups() -> None:
    for fn in _CLEANUPS:
        try:
            fn()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# PRD-01 — AEO
# ---------------------------------------------------------------------------

def run_aeo_section(base: str) -> Section:
    s = Section(name="PRD-01 AEO")
    db = mongo_tools.db()

    # 1. The aeo skill is registered in the skill library.
    try:
        from shared.skills import registry
        aeo_skill = registry().skills.get("aeo")
        if aeo_skill is None:
            s.fail("skill_registered", "shared.skills.registry has no 'aeo' entry")
        else:
            ref_count = len(list(aeo_skill.skill_dir.glob("references/*.md")))
            s.pass_("skill_registered",
                     f"aeo skill v{aeo_skill.frontmatter.version} with {ref_count} references")
    except Exception as e:
        s.fail("skill_registered", f"registry import failed: {e}")

    # 2. AEO scripts import without errors and produce sensible output
    # on a fixture draft.
    try:
        from scripts.aeo import content_quality, parse_draft, passage_blocks, schema_generate
        sample = ("# Stop renewal slip at the CSM handoff\n\n"
                  "## What changes\n\n"
                  "Per Ahrefs December 2025, brand mentions correlate 3x more "
                  "strongly with AI citation than backlinks. The CSM handoff "
                  "is where most renewal data drops. " * 6)
        cq = content_quality.analyse(sample)
        pd = parse_draft.extract_structure(sample)
        pb = passage_blocks.detect_blocks(sample)
        sg = schema_generate.emit_article_jsonld({
            "headline": "Stop renewal slip at the CSM handoff",
            "author": {"name": "Test Author"},
            "publisher": {"name": "Test Co"},
        })
        if not isinstance(cq, dict) or "overall_quality" not in cq:
            s.fail("scripts_smoke", f"content_quality shape: {cq}")
        elif pd["h1"] != "Stop renewal slip at the CSM handoff":
            s.fail("scripts_smoke", f"parse_draft H1: {pd['h1']!r}")
        elif "self_contained_blocks_signal" not in pb:
            s.fail("scripts_smoke", f"passage_blocks shape: {pb}")
        elif sg.get("@type") != "Article":
            s.fail("scripts_smoke", f"schema_generate shape: {sg}")
        else:
            s.pass_("scripts_smoke",
                     f"content_quality={cq['overall_quality']}/100, "
                     f"passage_signal={pb['self_contained_blocks_signal']}")
    except Exception as e:
        s.fail("scripts_smoke", f"AEO scripts import or call failed: {e}")

    # 3. GET /api/aeo/cited-by returns an array.
    try:
        r = requests.get(f"{base}/api/aeo/cited-by?days=28", timeout=10)
        if not r.ok:
            s.fail("cited_by_endpoint", f"HTTP {r.status_code}: {r.text[:120]}")
        elif not isinstance(r.json(), list):
            s.fail("cited_by_endpoint", "response not a list")
        else:
            s.pass_("cited_by_endpoint", f"{len(r.json())} citations in window")
    except Exception as e:
        s.fail("cited_by_endpoint", f"request failed: {e}")

    # 4. log_citation round-trip — insert via direct Mongo (same path
    # the CLI uses), verify the endpoint returns it, then clean up.
    try:
        ts_now = datetime.now(UTC)
        row = {
            "ts": ts_now,
            "platform": "perplexity",
            "query": f"{E2E_RUN_TAG} test query",
            "cited_url": f"https://example.com/{E2E_RUN_TAG}/blog-post",
            "evidence_url": f"https://perplexity.ai/search/{E2E_RUN_TAG}",
            "telemetry_id": None,
            "added_by": "founder",
        }
        result = db[Coll.AEO_CITATIONS].insert_one(row)
        _register_cleanup(
            lambda oid=result.inserted_id:
                db[Coll.AEO_CITATIONS].delete_one({"_id": oid})
        )
        r = requests.get(f"{base}/api/aeo/cited-by?days=28", timeout=10)
        rows = r.json()
        matched = [x for x in rows
                    if E2E_RUN_TAG in (x.get("query") or "")]
        if not matched:
            s.fail("cited_by_round_trip",
                    "inserted citation not visible on /api/aeo/cited-by")
        else:
            s.pass_("cited_by_round_trip",
                     f"inserted citation visible: id={matched[0]['id']}")
    except Exception as e:
        s.fail("cited_by_round_trip", f"round-trip failed: {e}")

    # 5. /api/rubric-trend includes the mean_answer_extractability key.
    try:
        r = requests.get(f"{base}/api/rubric-trend?days=28", timeout=10)
        rows = r.json()
        if not isinstance(rows, list):
            s.fail("rubric_trend_key", f"non-list response: {rows}")
        elif rows and "mean_answer_extractability" not in rows[0]:
            s.fail("rubric_trend_key",
                    f"first row missing mean_answer_extractability: {list(rows[0].keys())}")
        elif not rows:
            s.skip("rubric_trend_key",
                    "no rubric-trend rows yet (run a blog draft first)")
        else:
            s.pass_("rubric_trend_key",
                     f"mean_answer_extractability present in {len(rows)} rows")
    except Exception as e:
        s.fail("rubric_trend_key", f"request failed: {e}")

    # 6. integrations status reports all four adapters.
    try:
        r = requests.get(f"{base}/api/integrations/status", timeout=10)
        body = r.json()
        missing = [k for k in ("devto", "linkedin", "google_ads", "meta_ads")
                    if k not in body]
        if missing:
            s.fail("integrations_status_shape",
                    f"missing integration slugs: {missing}")
        else:
            s.pass_("integrations_status_shape",
                     "all 4 adapters present in /api/integrations/status")
    except Exception as e:
        s.fail("integrations_status_shape", f"request failed: {e}")

    return s


# ---------------------------------------------------------------------------
# PRD-02 — Signals
# ---------------------------------------------------------------------------

def run_signals_section(base: str) -> Section:
    s = Section(name="PRD-02 Signals")
    db = mongo_tools.db()

    # 1. The 3 seeded sources exist (M1).
    try:
        r = requests.get(f"{base}/api/signals/sources", timeout=10)
        sources = r.json()
        names = {src["name"] for src in sources}
        for seed in ("hn-revops-handoff", "reddit-saas-marketing",
                     "rss-google-ai-blog"):
            if seed not in names:
                s.fail("seeded_sources", f"seed {seed!r} missing")
                break
        else:
            s.pass_("seeded_sources",
                     f"all 3 seeded sources present ({len(sources)} total)")
    except Exception as e:
        s.fail("seeded_sources", f"request failed: {e}")

    # 2. Adapter modules import + have the (poll, base_score) contract.
    try:
        for kind in ("hn", "reddit", "rss"):
            mod = __import__(f"scripts.signals.{kind}_adapter",
                              fromlist=[kind])
            if not callable(getattr(mod, "poll", None)):
                s.fail("adapter_contract", f"{kind}: missing poll()")
                break
            if not callable(getattr(mod, "base_score", None)):
                s.fail("adapter_contract", f"{kind}: missing base_score()")
                break
        else:
            s.pass_("adapter_contract", "hn/reddit/rss adapters expose poll + base_score")
    except Exception as e:
        s.fail("adapter_contract", f"adapter import failed: {e}")

    # 3. Manual signal insert end-to-end via the POST endpoint.
    try:
        url = f"https://example.com/{E2E_RUN_TAG}/manual-signal"
        r = requests.post(
            f"{base}/api/signals/manual",
            json={"url": url, "icp_segment": "seg_revops_director"},
            timeout=10,
        )
        if not r.ok:
            s.fail("manual_insert", f"HTTP {r.status_code}: {r.text[:120]}")
        else:
            body = r.json()
            if "signal_id" not in body:
                s.fail("manual_insert", f"missing signal_id: {body}")
            else:
                sid = body["signal_id"]
                _register_cleanup(
                    lambda u=url:
                        db[Coll.SIGNALS].delete_many({"evidence_url": u})
                )
                # Verify it surfaces in the list endpoint.
                r2 = requests.get(f"{base}/api/signals?limit=200", timeout=10)
                rows = r2.json()
                found = next((x for x in rows if x.get("id") == sid), None)
                if not found:
                    s.fail("manual_insert", "manual signal not in /api/signals")
                else:
                    s.pass_("manual_insert",
                             f"signal_id={sid[:8]}… source={found.get('source')}")
    except Exception as e:
        s.fail("manual_insert", f"manual insert flow failed: {e}")

    # 4. Suppress flow.
    try:
        url = f"https://example.com/{E2E_RUN_TAG}/to-suppress"
        ins = requests.post(
            f"{base}/api/signals/manual",
            json={"url": url, "icp_segment": "seg_founder_b2b"},
            timeout=10,
        ).json()
        sid = ins.get("signal_id")
        if not sid:
            s.fail("suppress_flow", f"setup insert failed: {ins}")
        else:
            _register_cleanup(
                lambda u=url: db[Coll.SIGNALS].delete_many({"evidence_url": u})
            )
            sup = requests.post(f"{base}/api/signals/{sid}/suppress", timeout=10)
            if not sup.ok:
                s.fail("suppress_flow", f"HTTP {sup.status_code}: {sup.text[:120]}")
            else:
                # Verify the suppressed_reason landed.
                listing = requests.get(
                    f"{base}/api/signals?status=suppressed&limit=200", timeout=10,
                ).json()
                if not any(x.get("id") == sid for x in listing):
                    s.fail("suppress_flow", "suppressed signal not in suppressed list")
                else:
                    s.pass_("suppress_flow",
                             f"signal {sid[:8]}… marked suppressed via API")
    except Exception as e:
        s.fail("suppress_flow", f"suppress flow failed: {e}")

    # 5. poll-now and route-now do not 500 even when nothing is enabled.
    try:
        r = requests.post(f"{base}/api/signals/poll-now", timeout=20)
        if not r.ok:
            s.fail("poll_now_safe", f"HTTP {r.status_code}: {r.text[:120]}")
        else:
            body = r.json()
            if body.get("status") not in ("ok", "no_sources", "disabled"):
                s.fail("poll_now_safe", f"unexpected status: {body}")
            else:
                s.pass_("poll_now_safe", f"status={body['status']}")
    except Exception as e:
        s.fail("poll_now_safe", f"request failed: {e}")

    try:
        r = requests.post(f"{base}/api/signals/route-now", timeout=20)
        if not r.ok:
            s.fail("route_now_safe", f"HTTP {r.status_code}: {r.text[:120]}")
        else:
            body = r.json()
            if body.get("status") not in ("ok", "no_pending", "disabled"):
                s.fail("route_now_safe", f"unexpected status: {body}")
            else:
                s.pass_("route_now_safe",
                         f"status={body['status']} enqueued={body.get('total_enqueued',0)}")
    except Exception as e:
        s.fail("route_now_safe", f"request failed: {e}")

    # 6. Watcher dedupe: re-inserting the same URL via the manual
    # endpoint returns {status: "exists"} OR yields no duplicate row.
    try:
        url = f"https://example.com/{E2E_RUN_TAG}/dedupe-test"
        r1 = requests.post(f"{base}/api/signals/manual",
                            json={"url": url}, timeout=10)
        r2 = requests.post(f"{base}/api/signals/manual",
                            json={"url": url}, timeout=10)
        _register_cleanup(
            lambda u=url: db[Coll.SIGNALS].delete_many({"evidence_url": u})
        )
        if not (r1.ok and r2.ok):
            s.fail("dedupe", f"second insert failed: {r2.status_code}")
        elif r2.json().get("status") != "exists":
            # If not exists, count must be exactly 1 (unique sparse index).
            n = db[Coll.SIGNALS].count_documents({"evidence_url": url})
            if n != 1:
                s.fail("dedupe", f"duplicate URLs in signals: {n} rows")
            else:
                s.pass_("dedupe", "unique index enforces single row per URL")
        else:
            s.pass_("dedupe", 'second insert returned status="exists"')
    except Exception as e:
        s.fail("dedupe", f"dedupe flow failed: {e}")

    return s


# ---------------------------------------------------------------------------
# PRD-03 — Self-Critique
# ---------------------------------------------------------------------------

def run_self_critique_section(base: str) -> Section:
    s = Section(name="PRD-03 Self-Critique")
    db = mongo_tools.db()

    # Seed paid_thresholds._default if missing (schema bootstrap may
    # have aborted before reaching it).
    if db[Coll.PAID_THRESHOLDS].count_documents({"_id": "_default"}) == 0:
        db[Coll.PAID_THRESHOLDS].insert_one({
            "_id": "_default", "platform": "*", "icp_segment": "*",
            "daily_spend_floor_usd": 100.0, "min_conversions_per_24h": 1,
            "min_ctr_pct": 0.5, "min_hours_running": 12,
        })

    # 1. Seed a failing paid_variant so the paid miner fires.
    variant_id = f"{E2E_RUN_TAG}_failing_variant"
    try:
        db[Coll.PAID_VARIANTS].insert_one({
            "_id": variant_id, "name": variant_id, "status": "running",
            "platform": "google_ads", "icp_segment": "seg_founder_b2b",
            "experiment_id": f"{E2E_RUN_TAG}_exp",
            "external_id": f"ad_{E2E_RUN_TAG}",
            "spend_24h": 250.0, "conversions_24h": 0,
            "ctr_pct": 0.1, "hours_running": 36,
            "snapshot_at": datetime.now(UTC),
        })
        _register_cleanup(
            lambda vid=variant_id:
                db[Coll.PAID_VARIANTS].delete_one({"_id": vid})
        )
        _register_cleanup(
            lambda vid=variant_id:
                db[Coll.PAID_ACTIONS_PROPOSED].delete_many({"variant_id": vid})
        )
        s.pass_("paid_seed", f"variant {variant_id} seeded")
    except Exception as e:
        s.fail("paid_seed", f"seed failed: {e}")
        return s   # no point continuing without seed

    # 2. POST run-now and verify lifecycle.
    try:
        r = requests.post(f"{base}/api/self-critique/run-now", timeout=30)
        if not r.ok:
            s.fail("run_now_lifecycle", f"HTTP {r.status_code}: {r.text[:120]}")
            return s
        run = r.json()
        if run.get("status") not in ("ok", "disabled"):
            s.fail("run_now_lifecycle", f"unexpected status: {run.get('status')}")
        else:
            n_props = run.get("total_proposals", 0)
            s.pass_("run_now_lifecycle",
                     f"status={run['status']} total_proposals={n_props}")
    except Exception as e:
        s.fail("run_now_lifecycle", f"request failed: {e}")

    # 3. Verify the paid miner emitted a proposal for our seeded variant.
    try:
        time.sleep(0.5)  # allow Mongo write to settle
        row = db[Coll.PAID_ACTIONS_PROPOSED].find_one({
            "variant_id": variant_id, "status": Status.AWAITING_HUMAN_REVIEW,
        })
        if not row:
            s.fail("paid_proposal_landed",
                    "no proposal found for seeded variant")
        else:
            s.pass_("paid_proposal_landed",
                     f"proposal id={row['_id']} kind={row.get('kind')}")
    except Exception as e:
        s.fail("paid_proposal_landed", f"Mongo lookup failed: {e}")

    # 4. Unified /api/self-critique/proposals surfaces it.
    try:
        r = requests.get(f"{base}/api/self-critique/proposals", timeout=10)
        props = r.json()
        ours = [p for p in props
                 if p.get("target_kind") == "paid_action"
                 and p.get("target_id") == variant_id]
        if not ours:
            s.fail("unified_endpoint_surfaces",
                    f"variant {variant_id} not in unified proposals "
                    f"(got {len(props)} total)")
        else:
            proposal_id = ours[0]["id"]
            s.pass_("unified_endpoint_surfaces",
                     f"proposal id={proposal_id} miner={ours[0].get('miner')}")

            # 5. Approve it.
            r2 = requests.post(
                f"{base}/api/self-critique/proposals/{proposal_id}/approve",
                timeout=10,
            )
            if not r2.ok:
                s.fail("approve_flow", f"HTTP {r2.status_code}: {r2.text[:120]}")
            else:
                # Verify the Mongo row flipped to "accepted".
                fresh = db[Coll.PAID_ACTIONS_PROPOSED].find_one({
                    "variant_id": variant_id,
                })
                if fresh and fresh.get("status") == Status.ACCEPTED:
                    s.pass_("approve_flow",
                             "paid_actions_proposed.status='accepted' after approve")
                else:
                    s.fail("approve_flow",
                            f"status not flipped: {fresh.get('status') if fresh else None}")
    except Exception as e:
        s.fail("unified_endpoint_surfaces", f"flow failed: {e}")

    # 6. /api/self-critique/runs returns rows.
    try:
        r = requests.get(f"{base}/api/self-critique/runs?limit=5", timeout=10)
        runs = r.json()
        if not isinstance(runs, list) or not runs:
            s.fail("runs_history", "empty or non-list runs response")
        else:
            row = runs[0]
            for k in ("id", "started_at", "completed_at", "status",
                      "total_proposals", "miners"):
                if k not in row:
                    s.fail("runs_history", f"row missing key {k}")
                    break
            else:
                s.pass_("runs_history",
                         f"{len(runs)} recent runs; latest miners="
                         f"{sorted(row['miners'].keys())}")
    except Exception as e:
        s.fail("runs_history", f"request failed: {e}")

    # 7. Scheduled-jobs list now includes self-critique-runner.
    try:
        r = requests.get(f"{base}/api/live", timeout=10)
        live = r.json()
        names = {j["name"] for j in live.get("scheduled_jobs", [])}
        if "self-critique-runner" not in names:
            s.fail("scheduled_job_listed",
                    f"self-critique-runner missing from scheduled_jobs: {sorted(names)}")
        elif "self-critique" not in names:
            s.fail("scheduled_job_listed", "legacy self-critique entry removed")
        else:
            s.pass_("scheduled_job_listed",
                     "both self-critique + self-critique-runner present")
    except Exception as e:
        s.fail("scheduled_job_listed", f"request failed: {e}")

    # 8. Dismiss flow + 404 contract.
    try:
        r = requests.post(
            f"{base}/api/self-critique/proposals/paid:000000000000000000000000/dismiss",
            timeout=10,
        )
        if r.status_code != 404:
            s.fail("dismiss_404", f"expected 404, got {r.status_code}")
        else:
            s.pass_("dismiss_404", "non-existent proposal returns 404")
    except Exception as e:
        s.fail("dismiss_404", f"request failed: {e}")

    return s


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

SECTIONS: dict[str, Callable[[str], Section]] = {
    "aeo": run_aeo_section,
    "signals": run_signals_section,
    "self_critique": run_self_critique_section,
}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--base", default="http://localhost:8080",
                   help="web_api base URL (default: http://localhost:8080)")
    p.add_argument("--only", choices=list(SECTIONS.keys()),
                   help="Run only one section (default: all 3)")
    args = p.parse_args()

    sections_to_run = (
        [args.only] if args.only else list(SECTIONS.keys())
    )

    print()
    print("=" * 78)
    print(f"  PRD FEATURE E2E — run tag {E2E_RUN_TAG} — base {args.base}")
    print("=" * 78)

    results: list[Section] = []
    for name in sections_to_run:
        print(f"\n>> {name} ...")
        sec = SECTIONS[name](args.base)
        results.append(sec)
        for cid, status, msg in sec.checks:
            symbol = {"PASS": "[OK]", "FAIL": "[XX]", "SKIP": "[--]"}.get(status, "[??]")
            print(f"  {symbol} {status:4} {cid:32} {msg}")
        print(f"  -- {sec.name}: "
              f"{sum(1 for _, st, _ in sec.checks if st == 'PASS')} pass, "
              f"{sec.failed_count} fail, "
              f"{sum(1 for _, st, _ in sec.checks if st == 'SKIP')} skip")

    total_fail = sum(s.failed_count for s in results)
    total_pass = sum(sum(1 for _, st, _ in s.checks if st == "PASS")
                     for s in results)
    print()
    print("=" * 78)
    print(f"  SUMMARY: {total_pass} pass · {total_fail} fail "
          f"across {len(results)} PRD sections")
    print("=" * 78)

    return 0 if total_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
