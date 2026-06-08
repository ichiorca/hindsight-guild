"""PRD-02 signal_router_agent.

Reads unprocessed signals where ``score >= threshold``, picks the top N
(sorted by score then ts), and enqueues a drafting job for each via the
existing ``POST /api/draft`` endpoint. Marks each signal ``processed=true``
with a back-reference to the spawned ``telemetry_id``.

Pure-Python decision function (no LLM). Same author pattern as
``agents/_critique_factory.py``: callable from a scheduled job, from a
manual ``POST /api/signals/route-now``, or from tests.

Rate limits (PRD-02 §8):
  - Max 5 auto-drafts per tick.
  - Max 20 auto-drafts per 24h per icp_segment (avoid flooding).
  - Skip if there's already an action with the same triggered_by_signal_id
    in the last 7 days (duplicate suppression beyond Mongo's own
    evidence_url uniqueness).

Kill switch: env var ``SIGNAL_AUTO_DRAFT=0`` (or ``SIGNAL_ROUTER_DISABLED=1``)
makes ``run_once`` return ``{status: "disabled"}``. Useful when the
founder wants the watcher running but no auto-drafts firing.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import UTC, datetime, timedelta

import httpx

from shared import mongo_tools

log = logging.getLogger(__name__)

# Rate-limit ceilings — match PRD-02 §8. The per-tick cap is configurable per
# environment via SIGNAL_MAX_PER_TICK (default 3) so you can throttle/burst
# without a code change (e.g. lower while testing, higher in prod).
DEFAULT_MAX_PER_TICK = 3
MAX_PER_24H_PER_ICP = 20
DUPLICATE_SUPPRESSION_DAYS = 7


def _max_per_tick() -> int:
    """Max auto-drafts enqueued per router tick (env SIGNAL_MAX_PER_TICK, default 3)."""
    try:
        return max(1, int(os.environ.get("SIGNAL_MAX_PER_TICK",
                                         str(DEFAULT_MAX_PER_TICK))))
    except ValueError:
        return DEFAULT_MAX_PER_TICK

# Default API URL for the in-process FastAPI server. Tests override via
# the ``api_url`` argument to run_once().
_DEFAULT_API_URL = os.environ.get(
    "SIGNAL_ROUTER_API_URL",
    "http://localhost:8080",
)
_HTTP_TIMEOUT = 30.0


# ---------------------------------------------------------------------------
# Channel decision heuristics (PRD-02 §8 table)
# ---------------------------------------------------------------------------

_QUESTION_RE = re.compile(r"\b(how|what|why|when|where|which|who|is|are|do|does|can)\b", re.IGNORECASE)


def _decide_channel(signal: dict, source_doc: dict) -> str:
    """Pick a channel. Honors source_doc.default_channel if set; else
    falls back to the §8 heuristic."""
    default = (source_doc or {}).get("default_channel")
    if default:
        return default

    src_kind = signal.get("source") or ""
    excerpt = signal.get("evidence_excerpt") or ""
    excerpt_first = excerpt.split(".")[0] if excerpt else ""

    # Long-form Q-shaped thread on HN / Reddit → blog
    if src_kind in ("hn", "reddit") and _QUESTION_RE.search(excerpt_first):
        return "blog"
    # RSS items default to linkedin (short reactions)
    if src_kind == "rss":
        return "linkedin"
    # Reddit personal/anecdotal → linkedin
    if src_kind == "reddit":
        return "linkedin"
    return "blog"


def _build_topic_hint(signal: dict, source_doc: dict) -> str:
    """If source_doc.topic_hint_template is set, format it with a simple
    {pain} placeholder pulled from the excerpt. Else fall back to the
    first 80 chars of the excerpt."""
    template = (source_doc or {}).get("topic_hint_template")
    excerpt = signal.get("evidence_excerpt") or ""
    if template:
        pain = _extract_pain(excerpt) or excerpt[:40]
        try:
            return template.format(pain=pain)
        except Exception:
            # Template referenced a placeholder we don't know about.
            # Fall through to excerpt-based hint.
            pass
    return excerpt[:80]


_PAIN_PATTERNS = [
    re.compile(r"(?:struggling with|leaking|losing|missing|stalled on)\s+(\w[\w\s\-]{2,40})", re.IGNORECASE),
    re.compile(r"(\w[\w\s\-]{2,40})\s+(?:is broken|slipping|drift)", re.IGNORECASE),
]


def _extract_pain(text: str) -> str | None:
    """Best-effort pull of a noun phrase representing a pain point.
    None when no pattern matched."""
    for pat in _PAIN_PATTERNS:
        m = pat.search(text or "")
        if m:
            return m.group(1).strip()
    return None


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_once(db=None, api_url: str | None = None) -> dict:
    """Single router tick. Returns a dict summarizing what was enqueued.

    Args:
        db: Optional Mongo DB. Defaults to ``mongo_tools.db()``.
        api_url: Base URL of the local web_api (so we can hit /api/draft).
            Defaults to ``SIGNAL_ROUTER_API_URL`` env var or http://localhost:8080.

    Returns::

        {"status": "ok" | "disabled" | "no_pending",
         "enqueued": [{"signal_id": str, "job_id": str, ...}],
         "suppressed": [{"signal_id": str, "reason": str}],
         "total_enqueued": int}
    """
    if os.environ.get("SIGNAL_AUTO_DRAFT", "1").lower() in ("0", "false", "no"):
        return {"status": "disabled", "reason": "SIGNAL_AUTO_DRAFT=0",
                "enqueued": [], "suppressed": [], "total_enqueued": 0}
    if os.environ.get("SIGNAL_ROUTER_DISABLED", "").lower() in ("1", "true", "yes"):
        return {"status": "disabled", "reason": "SIGNAL_ROUTER_DISABLED=1",
                "enqueued": [], "suppressed": [], "total_enqueued": 0}

    # pymongo Database objects don't support bool() — explicit None check.
    db = db if db is not None else mongo_tools.db()
    api_url = (api_url or _DEFAULT_API_URL).rstrip("/")
    cap = _max_per_tick()

    pending = list(db["signals"].find({
        "processed": False,
        "suppressed_reason": None,
    }).sort([("score", -1), ("ts", 1)]).limit(max(cap * 4, 100)))
    # Pull a generous window (>= 100) so the channel round-robin below can see
    # lower-scoring channels too — otherwise a high-volume high-score channel
    # would crowd them out of a tiny cap*4 pull. Still bounded so a large
    # backlog can't load unboundedly; dedup/over-quota headroom is included.

    if not pending:
        return {"status": "no_pending", "enqueued": [], "suppressed": [], "total_enqueued": 0}

    # 24h-per-icp quota count.
    since = datetime.now(UTC) - timedelta(hours=24)
    quota_by_icp: dict[str | None, int] = {}
    for r in db["actions"].aggregate([
        {"$match": {
            "ts": {"$gte": since},
            "triggered_by_signal_id": {"$ne": None},
        }},
        {"$group": {"_id": "$icp_segment", "n": {"$sum": 1}}},
    ]):
        quota_by_icp[r["_id"]] = int(r["n"])

    enqueued: list[dict] = []
    suppressed: list[dict] = []

    # Source docs are needed for default_channel + topic_hint_template
    # decisions. Pull them once.
    source_docs = {s["name"]: s for s in db["signal_sources"].find({})}

    def _source_doc_for(signal: dict) -> dict:
        # Exact match by the source NAME the watcher stamped on the signal.
        # Required now that several sources can share a (kind, icp) pair
        # (e.g. multiple rss/seg_merchant_dtc subreddits), each with its own
        # default_channel + topic_hint_template. Fall back to the (kind, icp)
        # heuristic for legacy signals written before source_name existed.
        name = signal.get("source_name")
        if name and name in source_docs:
            return source_docs[name]
        s = signal.get("source")
        icp = signal.get("icp_segment")
        for doc in source_docs.values():
            if doc.get("source") == s and doc.get("icp_segment") == icp:
                return doc
        return {}

    # Channel-diverse selection: group the score-sorted pending by the channel
    # each signal would draft to, then round-robin across channels so a single
    # tick spans draft types (LinkedIn + Substack + blog + …) instead of N of
    # the same type. Score order is preserved within each channel, so the
    # highest-scoring signal of each type is taken first.
    from itertools import zip_longest
    chan_of: dict = {}
    by_channel: dict[str, list] = {}
    for _sig in pending:
        _ch = _decide_channel(_sig, _source_doc_for(_sig))
        chan_of[_sig["_id"]] = _ch
        by_channel.setdefault(_ch, []).append(_sig)
    ordered = [s for grp in zip_longest(*by_channel.values())
               for s in grp if s is not None]

    dup_since = datetime.now(UTC) - timedelta(days=DUPLICATE_SUPPRESSION_DAYS)

    for signal in ordered:
        if len(enqueued) >= cap:
            break

        sid = signal["_id"]
        icp = signal.get("icp_segment")

        # Quota check
        if quota_by_icp.get(icp, 0) >= MAX_PER_24H_PER_ICP:
            suppressed.append({
                "signal_id": str(sid),
                "reason": f"24h_quota_full_for_icp:{icp}",
            })
            db["signals"].update_one(
                {"_id": sid},
                {"$set": {"suppressed_reason": "rate_limit",
                          "processed": True,
                          "processed_at": datetime.now(UTC)}},
            )
            continue

        # Duplicate suppression: prior action with the same signal back-ref
        # in the last DUPLICATE_SUPPRESSION_DAYS.
        prior = db["actions"].find_one({
            "triggered_by_signal_id": sid,
            "ts": {"$gte": dup_since},
        })
        if prior:
            suppressed.append({
                "signal_id": str(sid),
                "reason": "duplicate_within_7d",
            })
            db["signals"].update_one(
                {"_id": sid},
                {"$set": {"suppressed_reason": "duplicate_url",
                          "processed": True,
                          "processed_at": datetime.now(UTC)}},
            )
            continue

        source_doc = _source_doc_for(signal)
        channel = chan_of[signal["_id"]]
        topic_hint = _build_topic_hint(signal, source_doc)

        # Enqueue via /api/draft. The triggered_by_signal_id back-ref
        # lets the drafting job runner update the signal row with the
        # resulting telemetry_id on completion — that's what the queue
        # chip + /signals deep-link rely on.
        payload = {
            "channel": channel,
            "icp_segment": icp or "seg_merchant_dtc",
            "topic_hint": topic_hint,
            "triggered_by_signal_id": str(sid),
        }
        try:
            r = httpx.post(f"{api_url}/api/draft", json=payload,
                           timeout=_HTTP_TIMEOUT)
            r.raise_for_status()
            body = r.json()
            job_id = body.get("job_id")
        except Exception as e:
            log.warning("router: /api/draft enqueue failed for signal %s: %s",
                        sid, e)
            suppressed.append({"signal_id": str(sid),
                                "reason": f"enqueue_failed:{e!s}"[:200]})
            continue

        # Mark signal processed; the back-ref to telemetry_id will be
        # filled in by the draft pipeline's before_agent_callback when
        # it knows the telemetry_id (which is a new uuid each run).
        # We store the job_id now so the dashboard can link to the
        # pending draft.
        db["signals"].update_one(
            {"_id": sid},
            {"$set": {
                "processed": True,
                "processed_at": datetime.now(UTC),
                "router_job_id": job_id,
            }},
        )
        quota_by_icp[icp] = quota_by_icp.get(icp, 0) + 1
        enqueued.append({
            "signal_id": str(sid),
            "job_id": job_id,
            "channel": channel,
            "icp_segment": icp,
            "topic_hint": topic_hint,
        })

    return {
        "status": "ok",
        "enqueued": enqueued,
        "suppressed": suppressed,
        "total_enqueued": len(enqueued),
    }
