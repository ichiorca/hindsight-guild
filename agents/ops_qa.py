"""Ops/QA Agent — landing pages, UTM hygiene, pixel firing, ad-account health.

Custom http_health_check tool that lets the agent verify URLs respond
with 200 within a timeout. Findings land in ops_incidents.
"""
from __future__ import annotations

import time

import httpx
from google.adk.tools import FunctionTool

from agents._factory import make_llm_agent
from agents._models import LIGHT
from agents._prompts import OPS_QA_INSTRUCTIONS

# mongo_tools defaults to mongo_uri_writer; explicit setter removed (see C5).


def http_health_check(url: str, timeout_s: float = 3.0,
                       expected_substrings: list[str] | None = None) -> dict:
    """Check whether a URL returns 200 within the timeout. Optionally verify
    that the body contains expected substrings (e.g. a form field name to
    confirm the conversion path isn't broken).

    Returns: {ok, status_code, latency_ms, body_contains, error}
    """
    started = time.time()
    try:
        r = httpx.get(url, timeout=timeout_s, follow_redirects=True)
        latency_ms = int((time.time() - started) * 1000)
        body_contains = None
        if expected_substrings:
            body = r.text or ""
            body_contains = {s: (s in body) for s in expected_substrings}
        return {
            "ok": r.status_code == 200,
            "status_code": r.status_code,
            "latency_ms": latency_ms,
            "body_contains": body_contains,
            "error": None,
        }
    except Exception as e:
        return {
            "ok": False,
            "status_code": None,
            "latency_ms": int((time.time() - started) * 1000),
            "body_contains": None,
            "error": str(e),
        }


def utm_parse(url: str) -> dict:
    """Parse UTM params from a URL. Returns missing/extra keys + values."""
    from urllib.parse import parse_qs, urlparse
    p = urlparse(url)
    qs = parse_qs(p.query)
    required = ["utm_source", "utm_medium", "utm_campaign"]
    present = {k: qs.get(k, [None])[0] for k in required}
    missing = [k for k, v in present.items() if not v]
    return {
        "host": p.netloc,
        "path": p.path,
        "present": present,
        "missing": missing,
        "well_formed": not missing,
    }


http_health_check_tool = FunctionTool(func=http_health_check)
utm_parse_tool = FunctionTool(func=utm_parse)

ops_qa_agent = make_llm_agent(
    name="ops_qa_agent",
    instructions=OPS_QA_INSTRUCTIONS,
    model=LIGHT,
    mode="write",
    output_key="ops_scan",
    skill_id="ops_qa_scan",
    action_type="ops_qa_op",
    extra_tools=[http_health_check_tool, utm_parse_tool],
)
