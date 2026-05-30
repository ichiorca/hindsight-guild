"""Drafting — invoke the A2A pipeline (or single agents), async job queue,
live-ops WebSocket broadcaster, and synthetic LOCAL_DEV fallbacks.

Owns ALL the in-memory job state (_JOBS) and the WebSocket fan-out
(_LiveBroadcaster). The live REST endpoints in routers/live.py share the
same _live_now_payload() / _JOBS / _live_broadcaster module-level objects
imported from here, so there's a single source of truth.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from collections import OrderedDict
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from shared import mongo_tools

router = APIRouter()
log = logging.getLogger(__name__)


class DraftRequest(BaseModel):
    icp_segment: str
    channel: str  # linkedin | email | blog | substack | lifecycle_email | google_ads | meta_ads | linkedin_ads
    topic_hint: str | None = ""
    experiment_id: str | None = None
    # Optional single-agent handoff. When None or "pipeline", the full
    # drafting team runs (Research → Critique → Reviser → … → Finalizer).
    # When set to an agent_id (e.g., "lifecycle_email_agent"), the UI's
    # POST routes to that agent's A2A port instead. See _AGENT_A2A_PORTS.
    agent_id: str | None = None
    # PRD-02: when set, the signal_router originated this draft. The job
    # runner writes the resulting telemetry_id back onto the signal row's
    # ``triggered_telemetry_id`` so the queue card chip + /signals
    # surfaces can link draft ↔ source.
    triggered_by_signal_id: str | None = None


# ---------------------------------------------------------------------------
# Async job queue for /api/draft.
#
# Why a queue:
# The real ADK pipeline takes 30–60s end-to-end. Holding a synchronous
# FastAPI request that long blocks a worker, swallows browser timeouts on
# flaky networks, and gives the UI no way to show progress. The queue
# pattern fixes all three:
#   POST /api/draft       -> {job_id, status}        (returns instantly)
#   GET  /api/draft/{id}  -> {status, result?, error?, started_at, ...}
#
# Storage: in-memory OrderedDict capped at 100 jobs. Fine for local dev +
# single-instance Cloud Run. Production scale would persist to Mongo
# under `state.draft_jobs`. Reload of uvicorn wipes jobs — also fine
# locally; the UI just kicks off a fresh request.
# ---------------------------------------------------------------------------

_JOBS_MAX = 100
_JOBS: OrderedDict[str, dict] = OrderedDict()


def _job_set(job_id: str, **fields) -> None:
    job = _JOBS.get(job_id, {})
    job.update(fields)
    _JOBS[job_id] = job
    _JOBS.move_to_end(job_id)
    while len(_JOBS) > _JOBS_MAX:
        _JOBS.popitem(last=False)
    # Push the new snapshot to anyone subscribed to /api/ws/live. We
    # broadcast on EVERY job state change (pending → running → done|failed)
    # so the UI reflects the transition immediately. Failures here are
    # swallowed because telemetry-style pushes must never break the
    # job they're reporting on.
    try:
        _live_broadcaster.publish_nowait()
    except Exception as e:
        log.debug("live broadcast skipped: %s", e)


# ---------------------------------------------------------------------------
# /api/ws/live — WebSocket push for the live-ops ticker.
#
# Replaces the 4-second polling path. Clients connect once, receive an
# initial snapshot, then receive a fresh snapshot every time:
#   - A draft job changes state (kicked off by _job_set above)
#   - A 5-second wall-clock tick fires (catches agent activity that
#     happened outside the UI — cron, A2A direct calls)
#
# Wire format: each message is a JSON-encoded LiveNowPayload — same shape
# as the /api/live/now REST endpoint, so the client renderer doesn't care
# which transport it came from. The REST endpoint stays for SSR / curl /
# fallback when WS isn't available.
# ---------------------------------------------------------------------------

class _LiveBroadcaster:
    """Minimal pub/sub for the live ticker. Holds a set of WebSocket
    connections and a single asyncio.Event used to coalesce burst updates.

    Why an Event (not a queue per client): the broadcast payload is
    derived from current state at fan-out time, so even if 50 _job_set
    calls happen in 5ms, every subscriber gets exactly one fresh snapshot
    when the broadcast loop wakes up. Avoids queue backpressure entirely.
    """

    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._tick_event: asyncio.Event | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._tick_event = asyncio.Event()

    async def subscribe(self, ws: WebSocket) -> None:
        await ws.accept()
        self._clients.add(ws)

    def unsubscribe(self, ws: WebSocket) -> None:
        self._clients.discard(ws)

    def publish_nowait(self) -> None:
        """Threadsafe trigger from any sync caller (e.g., _job_set)."""
        if not self._tick_event or not self._loop:
            return  # broadcaster not yet attached to a loop
        # call_soon_threadsafe is safe from any thread / coroutine.
        self._loop.call_soon_threadsafe(self._tick_event.set)

    async def run(self) -> None:
        """Long-running broadcast loop. Wakes on either the tick event
        (job state change) or a 5-second timeout (so external activity
        like cron-driven telemetry still reaches the UI). Sends one
        snapshot per wake-up to every connected client. Dead clients
        are pruned silently."""
        assert self._tick_event is not None
        while True:
            try:
                await asyncio.wait_for(self._tick_event.wait(), timeout=5.0)
            except TimeoutError:
                pass  # periodic refresh
            self._tick_event.clear()

            if not self._clients:
                continue  # no subscribers; skip the Mongo round-trip

            try:
                payload = _live_now_payload()
            except Exception as e:
                log.warning("live broadcast payload failed: %s", e)
                continue

            dead: list[WebSocket] = []
            for ws in list(self._clients):
                try:
                    await ws.send_json(payload)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                self._clients.discard(ws)


_live_broadcaster = _LiveBroadcaster()


def _live_now_payload() -> dict:
    """Build the JSON payload shared by the WS push + REST /api/live/now
    endpoint. Same shape either way so the client renderer is transport-agnostic."""
    now = datetime.now(UTC)
    active_jobs: list[dict] = []
    for job_id, job in _JOBS.items():
        if job.get("status") not in ("pending", "running"):
            continue
        active_jobs.append({
            "job_id": job_id,
            "status": job.get("status"),
            "agent_id": job.get("agent_id") or "pipeline",
            "channel": job.get("channel") or "",
            "icp_segment": job.get("icp_segment") or "",
            "topic_hint": job.get("topic_hint") or "",
            "started_at": job.get("started_at"),
        })

    recent_cutoff = now - timedelta(seconds=60)
    recent_agents: list[dict] = []
    try:
        rows = mongo_tools.db()["actions"].aggregate([
            {"$match": {"ts": {"$gte": recent_cutoff}}},
            {"$group": {
                "_id": "$agent",
                "last_seen": {"$max": "$ts"},
                "last_action": {"$last": "$action_type"},
                "last_channel": {"$last": "$channel"},
                "n": {"$sum": 1},
            }},
            {"$sort": {"last_seen": -1}},
        ])
        for r in rows:
            recent_agents.append({
                "agent_id": r["_id"],
                "last_seen": r["last_seen"].isoformat() if r.get("last_seen") else None,
                "last_action": r.get("last_action"),
                "last_channel": r.get("last_channel"),
                "count_60s": r.get("n", 0),
            })
    except Exception as e:
        log.warning("live payload: actions aggregation failed: %s", e)

    return {
        "server_time": now.isoformat(),
        "active_jobs": active_jobs,
        "recent_agents": recent_agents,
    }


@router.websocket("/api/ws/live")
async def ws_live(ws: WebSocket) -> None:
    """Push-based live-ops feed. Sends an initial snapshot immediately on
    connect, then a fresh snapshot every time a draft job changes state
    or every 5s (server tick), whichever comes first.

    Client should treat any send as the new ground truth — fields are
    full-replacement, not patches."""
    await _live_broadcaster.subscribe(ws)
    try:
        # Send an initial snapshot so the UI doesn't have to wait for the
        # next broadcast cycle to render anything.
        await ws.send_json(_live_now_payload())
        # Then just wait — _LiveBroadcaster.run() does the actual sending.
        # We do need to keep this coroutine alive so the WS doesn't close
        # from our side; reading is the natural way (browser closing the
        # tab will fire WebSocketDisconnect).
        while True:
            await ws.receive_text()  # ignore client messages; we're push-only
    except WebSocketDisconnect:
        pass
    finally:
        _live_broadcaster.unsubscribe(ws)


def _pipeline_url(agent_id: str | None = None) -> str | None:
    """Resolve an A2A URL — for the full pipeline by default, or for a
    specific agent when ``agent_id`` is set.

    Resolution order, for each candidate:
      1. ``A2A_URL_<NAME>`` env var (LOCAL_DEV path)
      2. ``a2a_url_<name>`` Secret Manager secret (cloud path)
      3. ``A2A_URL_BASE`` env var + the agent's known port (LOCAL_DEV convenience)

    Examples:
      - agent_id=None        → A2A_URL_PIPELINE or a2a_url_pipeline
      - agent_id="positioning_agent" → A2A_URL_POSITIONING_AGENT or
        a2a_url_positioning_agent or {A2A_URL_BASE}:8007
    """
    from services.web_api.main import _secret_optional
    # Default = full pipeline.
    key = "pipeline" if (agent_id is None or agent_id == "pipeline") else agent_id

    env_url = os.environ.get(f"A2A_URL_{key.upper()}")
    if env_url:
        return env_url.rstrip("/")

    secret_url = _secret_optional(f"a2a_url_{key.lower()}")
    if secret_url:
        return secret_url.rstrip("/")

    # LOCAL_DEV convenience: A2A_URL_BASE=http://localhost lets us derive
    # http://localhost:<port> for each agent without per-agent env vars.
    base = os.environ.get("A2A_URL_BASE", "").rstrip("/")
    port = _AGENT_A2A_PORTS.get(key)
    if base and port:
        return f"{base}:{port}"

    return None


@router.post("/api/draft")
async def draft(req: DraftRequest):
    """Enqueue a drafting job. Returns immediately with a job_id; the UI
    polls GET /api/draft/{job_id} until status == 'done' or 'failed'."""
    import asyncio
    job_id = f"job_{uuid.uuid4().hex[:12]}"
    _job_set(
        job_id,
        status="pending",
        started_at=datetime.now(UTC).isoformat(),
        channel=req.channel,
        icp_segment=req.icp_segment,
        topic_hint=req.topic_hint,
        experiment_id=req.experiment_id,
        result=None,
        error=None,
    )
    # Fire-and-forget — runs in the same event loop as the FastAPI server.
    asyncio.create_task(_run_draft_job(job_id, req))
    return {"job_id": job_id, "status": "pending"}


@router.get("/api/draft/{job_id}")
async def get_draft_status(job_id: str):
    job = _JOBS.get(job_id)
    if not job:
        raise HTTPException(404, f"draft job {job_id} not found "
                                  "(may have been evicted from the in-memory "
                                  "cap of 100)")
    return {"job_id": job_id, **job}


def _build_agent_message(req: DraftRequest) -> str:
    """Translate a DraftRequest into the natural-language brief each agent
    expects in the first user-role message.

    The drafting pipeline + Content Agent both parse channel/ICP from this
    string. Single-agent handoffs target the agent's specific output: a
    Positioning agent gets a "Propose…" brief, Customer Voice gets a
    "Ingest the following…" brief, etc. The ICP and topic stay constant.
    """
    icp = req.icp_segment
    topic = req.topic_hint or ""
    exp = req.experiment_id

    agent = req.agent_id or "pipeline"
    if agent in ("pipeline", "content_agent"):
        # Full pipeline OR Content-only handoff — original message shape.
        return (
            f"Draft a {req.channel} post targeting {icp}. "
            f"{('Focus on: ' + topic) if topic else ''}"
            f"{(' Experiment: ' + exp) if exp else ''}"
        ).strip()

    if agent == "lifecycle_email_agent":
        return (
            f"Draft a lifecycle nurture email sequence (3-5 steps) targeting "
            f"{icp}. {('Theme: ' + topic) if topic else ''}"
            f" Status='draft'; do not send."
        )

    if agent == "paid_media_agent":
        # The channel field selects the ad platform for paid runs.
        platform_map = {
            "google_ads":   "Google Ads (RSA)",
            "meta_ads":     "Meta Ads",
            "linkedin_ads": "LinkedIn Ads",
        }
        platform = platform_map.get(req.channel, req.channel)
        return (
            f"Review last-24h spend and draft 2-3 new {platform} variants "
            f"targeting {icp}. {('Test angle: ' + topic) if topic else ''} "
            f"All variants paused. Open stop-loss incidents per the loaded "
            f"paid_thresholds config."
        )

    if agent == "positioning_agent":
        return (
            f"Propose a positioning update for {icp}. "
            f"{('Topic: ' + topic) if topic else ''} "
            f"Validate every claim via validate_claim before inserting "
            f"into positioning_proposals."
        )

    if agent == "customer_voice_agent":
        return (
            f"Ingest the following raw text into customer_voice for {icp}. "
            f"{topic if topic else 'Sales-call transcript (paste below).'}"
        )

    if agent == "research_agent":
        return (
            f"Run a research pass for {req.channel} targeting {icp}. "
            f"{('Topic: ' + topic) if topic else ''}"
        )

    if agent == "review_agent":
        return (
            f"Review the most recent {req.channel} draft for {icp}. "
            f"Validate every claim via validate_claim + web_search; "
            f"flag specifically, don't blanket-flag."
        )

    if agent == "image_brief_agent":
        return (
            f"Generate a hero image brief for the most recent {req.channel} "
            f"draft targeting {icp}. {('Theme: ' + topic) if topic else ''}"
        )

    if agent == "ops_qa_agent":
        return (
            "Run the ops/QA sweep. Check ops_targets uptime, UTM hygiene on "
            "last-24h outbound links, and open ops_incidents for any failures."
        )

    if agent == "cmo_planner":
        return (
            f"Compose the weekly CMO memo. {('Focus area: ' + topic) if topic else ''} "
            f"Self-verify every claim before slack_approval."
        )

    if agent == "self_critique_agent":
        return (
            "Run the self-critique sweep across all skills with >=20 actions "
            "in the last 14 days. Surface patterns; propose where the data "
            "is clear."
        )

    if agent == "analytics_agent":
        return (
            f"Pull the weekly analytics snapshot for {icp} on {req.channel}. "
            f"{('Topic: ' + topic) if topic else ''}"
        )

    # Unknown agent — best-effort generic brief.
    return f"Run {agent} for {icp} on {req.channel}. {topic}".strip()


def _id_token_headers(target_url: str) -> dict:
    """Authorization header for invoking a PRIVATE Cloud Run service.

    Cloud Run service-to-service auth requires a Google-signed ID token whose
    audience is the receiving service's URL; on Cloud Run it's minted from the
    metadata server for the running service account (sa-agents). Without it,
    a private A2A service returns 403. In LOCAL_DEV the A2A servers are plain
    local processes with no auth, so no header is added.
    """
    if os.environ.get("LOCAL_DEV"):
        return {}
    try:
        from urllib.parse import urlsplit

        import google.auth.transport.requests
        import google.oauth2.id_token

        parts = urlsplit(target_url)
        audience = f"{parts.scheme}://{parts.netloc}"
        token = google.oauth2.id_token.fetch_id_token(
            google.auth.transport.requests.Request(), audience)
        return {"Authorization": f"Bearer {token}"}
    except Exception as e:  # noqa: BLE001 — auth is best-effort; surface as 403 if it fails
        log.warning("could not mint ID token for %s: %s", target_url, e)
        return {}


async def _run_draft_job(job_id: str, req: DraftRequest) -> None:
    """Background runner. Translates the UI's DraftRequest into the A2A
    JSON-RPC envelope, posts to the resolved service, unwraps the response,
    and stores the flat result on the job. Errors are captured, not raised."""
    import asyncio
    _job_set(job_id, status="running", agent_id=req.agent_id or "pipeline")
    try:
        url = _pipeline_url(req.agent_id)
        if not url:
            # No live A2A service for this target; synthetic fallback or 503.
            if os.environ.get("DRAFTING_FALLBACK") == "synthetic":
                result = _synthetic_draft(req)
                _job_set(job_id, status="done", result=result,
                         completed_at=datetime.now(UTC).isoformat())
                _record_signal_backref(req, result)
                return
            target = req.agent_id or "pipeline"
            _job_set(
                job_id, status="failed",
                error=(f"A2A service for {target!r} not configured. Either "
                       f"set A2A_URL_{target.upper()}=http://localhost:<port> "
                       f"in .env (port from agents/a2a_server.py) and start "
                       f"the matching uvicorn process, or set "
                       f"A2A_URL_BASE=http://localhost as a shortcut, "
                       f"or DRAFTING_FALLBACK=synthetic for the mock path."),
                completed_at=datetime.now(UTC).isoformat(),
            )
            return

        # Per-agent message shaping. The full pipeline parses the channel +
        # icp from text — single-agent calls get a more targeted brief
        # because each agent has its own task surface.
        msg = _build_agent_message(req)
        # A2A 0.3.x JSON-RPC: method is "message/send" (was "tasks/send" in the
        # 0.1 draft), parts use "kind" (not "type"), and the message carries a
        # messageId + kind. ADK's to_a2a server speaks this shape.
        payload = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "message/send",
            "params": {
                "message": {
                    "role": "user",
                    "parts": [{"kind": "text", "text": msg}],
                    "messageId": str(uuid.uuid4()),
                    "kind": "message",
                },
            },
        }

        # Connect timeout is intentionally short (3s) so we fail FAST to
        # synthetic in LOCAL_DEV when the A2A server isn't actually running.
        # Read timeout is 300s: the full pipeline (Research → Content →
        # Critique loop ×2 → AEO restructure → ImageBrief → Review →
        # Finalizer) runs every stage to completion on Gemini API and can
        # exceed 120s end-to-end; the job runs in the background and the UI
        # polls, so a longer read budget never blocks a request thread.
        timeout = httpx.Timeout(connect=3.0, read=300.0, write=10.0, pool=5.0)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.post(url, json=payload, headers=_id_token_headers(url))
                r.raise_for_status()
                envelope = r.json()
        except (httpx.ConnectError, httpx.ConnectTimeout) as conn_err:
            # A2A server isn't reachable. Fall back to synthetic IF the
            # operator opted in via DRAFTING_FALLBACK=synthetic.
            # Otherwise re-raise so the failure is visible.
            if os.environ.get("DRAFTING_FALLBACK") == "synthetic":
                log.warning(
                    "A2A %s unreachable (%s); falling back to synthetic preview",
                    url, conn_err,
                )
                result = _synthetic_draft(req)
                _job_set(job_id, status="done", result=result,
                         completed_at=datetime.now(UTC).isoformat())
                _record_signal_backref(req, result)
                return
            raise

        result = _unwrap_a2a_pipeline_response(envelope)
        _job_set(job_id, status="done", result=result,
                 completed_at=datetime.now(UTC).isoformat())
        _record_signal_backref(req, result)
    except asyncio.CancelledError:
        _job_set(job_id, status="failed", error="cancelled")
        raise
    except Exception as e:
        # Print full traceback to stderr — log.exception is silenced under
        # some uvicorn log configs, and we need the real failure to debug
        # silently-failing draft jobs.
        import traceback as _tb
        _tb.print_exc()
        log.exception("draft job %s failed: %s", job_id, e)
        _job_set(job_id, status="failed", error=f"{type(e).__name__}: {e}",
                 completed_at=datetime.now(UTC).isoformat())


def _record_signal_backref(req: DraftRequest, result: dict | None) -> None:
    """PRD-02: when a draft was triggered by signal_router, persist the
    resulting telemetry_id back onto the signal row so the queue card
    can render the "Triggered by" chip and /signals can deep-link.

    Best-effort: any failure here is logged and swallowed — the draft
    itself already succeeded; telemetry must never fail downstream
    state."""
    if not req.triggered_by_signal_id or not isinstance(result, dict):
        return
    tid = result.get("telemetry_id")
    if not tid:
        return
    try:
        from bson import ObjectId

        from shared import mongo_tools
        try:
            oid = ObjectId(req.triggered_by_signal_id)
        except Exception:
            return
        mongo_tools.db()["signals"].update_one(
            {"_id": oid},
            {"$set": {"triggered_telemetry_id": tid}},
        )
    except Exception as e:
        log.warning("signal back-ref update failed for %s: %s",
                    req.triggered_by_signal_id, e)


def _unwrap_a2a_pipeline_response(envelope: dict) -> dict:
    """Translate the A2A JSON-RPC envelope into the UI's flat draft shape.

    The pipeline ends with a Finalizer agent whose only job is to emit one
    JSON object containing every state slot the UI needs (telemetry_id,
    channel, icp_segment, draft, research_findings, image, review). That
    JSON arrives as the last agent-role message's text. We parse it.

    Falls back to walking all message parts if the Finalizer's JSON isn't
    cleanly the last one (LLM may add stray whitespace, code fences, etc.).
    Returns the same flat keys as _synthetic_draft so the UI's useDraft()
    hook can render either path uniformly.
    """
    result = envelope.get("result") or {}
    final: dict = {}

    # Collect every text part the agent produced. A2A 0.3.x returns a Task
    # whose output lives in `artifacts` and/or `history`; the 0.1 shape used
    # `messages`; a bare Message reply puts parts at the top level. Gather
    # from all of them so the Finalizer's JSON is found wherever it lands.
    def _texts_from_parts(parts: Any) -> list[str]:
        return [p["text"] for p in (parts or [])
                if isinstance(p, dict) and p.get("text")]

    candidates: list[str] = []
    candidates += _texts_from_parts(result.get("parts"))            # bare Message reply
    for msg in (result.get("history") or result.get("messages") or []):
        candidates += _texts_from_parts(msg.get("parts"))
    for art in result.get("artifacts") or []:
        candidates += _texts_from_parts(art.get("parts"))
    status_msg = (result.get("status") or {}).get("message") or {}
    candidates += _texts_from_parts(status_msg.get("parts"))

    # Walk newest-to-oldest so the Finalizer's output (last) wins.
    for text in reversed(candidates):
        parsed = _try_parse_json(text)
        if isinstance(parsed, dict) and "telemetry_id" in parsed:
            final = parsed
            break

    # Defensive fallback: if no message contained the finalizer JSON, merge
    # any parseable JSON dicts from all messages (older ADK shape, or a run
    # that died before the finalizer).
    if not final:
        for text in candidates:
            parsed = _try_parse_json(text)
            if isinstance(parsed, dict):
                final.update(parsed)

    draft_text = final.get("draft") or ""
    if isinstance(draft_text, dict):
        draft_text = draft_text.get("body_markdown") or draft_text.get("body") or str(draft_text)

    return {
        "synthetic": False,
        "telemetry_id": final.get("telemetry_id"),
        "channel": final.get("channel"),
        "icp_segment": final.get("icp_segment"),
        "draft": draft_text,
        "research_findings": final.get("research_findings") or {},
        "review": final.get("review") or {},
        "image": final.get("image") or None,
        "eval_scores": final.get("eval_scores") or {},
        # Surface the raw envelope under a stable key so the UI can dig deeper
        # without us promising a schema for it.
        "_a2a_envelope": envelope,
    }


def _try_parse_json(text: str) -> Any:
    """Parse JSON tolerantly — strip code fences, leading prose, etc."""
    if not text:
        return None
    s = text.strip()
    # Strip ```json ... ``` fences the LLM may add despite instructions.
    if s.startswith("```"):
        s = s.split("```", 2)[-1] if s.count("```") >= 2 else s.lstrip("`")
        if s.lower().startswith("json"):
            s = s[4:].lstrip()
    try:
        return json.loads(s)
    except Exception:
        # Last-ditch: find the first { … } that parses
        start = s.find("{")
        if start == -1:
            return None
        for end in range(len(s), start, -1):
            try:
                return json.loads(s[start:end])
            except Exception:
                continue
        return None


_SKILL_BY_CHANNEL = {
    "linkedin": "linkedin_post",
    "substack": "substack_post",
    "blog": "blog_outline",
    "email": "nurture_email",
    "lifecycle_email": "nurture_email_sequence",
    "google_ads": "paid_variant_google",
    "meta_ads": "paid_variant_meta",
    "linkedin_ads": "paid_variant_linkedin",
}


def _skill_for(channel: str | None) -> str:
    """Map a channel to its canonical skill_id. Keep in sync with
    agents/pipeline.py's _SKILL_BY_CHANNEL."""
    return _SKILL_BY_CHANNEL.get(channel or "linkedin", "linkedin_post")


# Maps an agent_id → A2A port. Used by /api/draft when ``agent_id`` is
# passed to route the run to a single agent instead of the full pipeline.
# Order must match agents/a2a_server.py. ``pipeline`` is the default (full
# Research→Critique→…→Finalizer chain).
_AGENT_A2A_PORTS: dict[str, int] = {
    "pipeline":              8005,
    "research_agent":        8001,
    "content_agent":         8002,
    "review_agent":          8003,
    "analytics_agent":       8004,
    "cmo_planner":           8006,
    "positioning_agent":     8007,
    "customer_voice_agent":  8008,
    "lifecycle_email_agent": 8009,
    "paid_media_agent":      8010,
    "ops_qa_agent":          8011,
    "self_critique_agent":   8012,
    "image_brief_agent":     8013,
}


def _synthetic_draft(req: DraftRequest) -> dict:
    """Local fallback when no live A2A service is configured.

    Agent-aware: each agent has its own output shape, so the synthetic
    response must mimic it. The UI's per-agent result renderer reads
    ``shape`` to decide which view to mount.

    Real Mongo data is used wherever available — voice quotes, approved
    claims, ICP descriptions all come from the local DB. We do NOT
    fabricate eval scores, review verdicts, or experimental lifts — the
    user's hard rule is "no fake numbers". When the field would require
    a real LLM call to populate honestly, it's left null / empty.

    Side effect: when this path produces a draft (pipeline / content),
    we also emit a Mongo ``actions`` row so the Approval Queue + Live
    Ops + Capabilities heatmap all reflect the draft as real activity.
    Without this, the UI's draft-to-queue flow breaks in LOCAL_DEV.
    """
    from mongo import queries
    agent = req.agent_id or "pipeline"

    # ICP-scoped lookup with a graceful fallback: agent-driven inserts
    # occasionally tag voice quotes / approved claims under variant ICP
    # slugs (``seg_saas_founder`` vs ``seg_founder_b2b``). Without a
    # fallback, the synthetic preview shows an empty research section
    # for the "wrong" slug — the UI looks broken even though we have
    # plenty of data. Try the requested ICP first, then widen.
    voice = mongo_tools.find("customer_voice",
                              {"icp_segment": req.icp_segment}, limit=3)
    if not voice:
        voice = mongo_tools.find("customer_voice", {}, limit=3)
    claims = queries.approved_claims_for(req.icp_segment)
    if not claims:
        # No exact-ICP match — return whatever approved claims exist.
        try:
            claims = mongo_tools.find(
                "messaging_library", {"status": "approved"}, limit=5,
            )
        except Exception:
            claims = []

    # Emit real ``actions`` rows so the UI surfaces synthetic work as
    # activity. ``emit_for`` lets us write under a specific agent name —
    # crucial for SequentialAgent flows (lifecycle_email_drafter,
    # paid_media_drafter, …) so the parent agent's roll-up sees its
    # sub-agents' contributions on the Agents page.
    def _emit_synth_action(
        action_type: str,
        raw: dict,
        emit_for: str | None = None,
    ) -> str:
        import uuid as _uuid
        tid = f"act_synth_{_uuid.uuid4().hex[:12]}"
        agent_for = emit_for or (
            agent if agent != "pipeline" else "content_agent"
        )
        try:
            mongo_tools.db()["actions"].insert_one({
                "telemetry_id": tid,
                "ts": datetime.now(UTC),
                "agent": agent_for,
                "action_type": action_type,
                "channel": req.channel,
                "icp_segment": req.icp_segment,
                "skill_id": _skill_for(req.channel),
                "skill_version": "synthetic_preview",
                "raw": raw,
                "_synthetic": True,
            })
        except Exception as e:
            log.warning("synth action emit failed: %s", e)
        return tid

    def _emit_for_agent(action_type: str, raw: dict) -> str | None:
        """Emit one action row tagged with the right (sub-)agent name so
        the parent agent's recent_actions surface on the Agents page.
        Maps each top-level agent_id to the row name our SUB_AGENT_ALIASES
        in /api/agents rolls up to."""
        sub_agent = {
            "lifecycle_email_agent": "lifecycle_email_drafter",
            "paid_media_agent":      "paid_media_drafter",
        }.get(agent, agent)
        return _emit_synth_action(action_type, raw, emit_for=sub_agent)

    if agent in ("pipeline", "content_agent"):
        # Pipeline / Content — full draft with research_findings but NO
        # synthetic eval_scores (the user said: no fake numbers).
        # Defensive: customer_voice rows may have ``text`` or legacy ``raw_quote``.
        sample_voice = ((voice[0].get("text") or voice[0].get("raw_quote", ""))
                        if voice else "")
        sample_claim = claims[0]["claim_text"] if claims else ""
        body = (
            f"{sample_voice}\n\n"
            f"That's what we hear from teams trying to fix the handoff. "
            f"And it's why we built this: {sample_claim}\n\n"
            f"What's the one bottleneck in your workflow you keep meaning to fix?"
        ) if (sample_voice or sample_claim) else (
            "(Synthetic preview — no customer_voice or approved_claims "
            "available for this ICP. Seed Mongo to see real grounding.)"
        )
        action_type = f"draft_{req.channel}" if req.channel else "draft_linkedin"
        raw_envelope = {
            "draft": body,
            "topic_hint": req.topic_hint,
        }
        tid = _emit_synth_action(action_type, raw_envelope)
        return {
            "synthetic": True,
            "shape": "pipeline",
            "telemetry_id": tid,
            "draft": body,
            "research_findings": {
                "customer_voice": [(v.get("text") or v.get("raw_quote", ""))
                                    for v in voice],
                "approved_claims": [c["claim_text"] for c in claims[:3]],
            },
            "review": None,        # real review needs an LLM pass; don't fake
            "eval_scores": None,   # rubric scores need Vertex Eval; don't fake
        }

    if agent == "lifecycle_email_agent":
        sample_voice = ((voice[0].get("text") or voice[0].get("raw_quote", ""))
                        if voice else "(no voice quotes yet)")
        _emit_for_agent("draft_email_sequence", {"sequence_name": req.topic_hint})
        return {
            "synthetic": True,
            "shape": "lifecycle_email",
            "email_sequence": {
                "_id": "preview_seq",
                "sequence_name": (req.topic_hint or "Preview sequence"),
                "icp_segment": req.icp_segment,
                "status": "draft",
                "steps": [
                    {
                        "step_num": 1,
                        "subject": "the handoff problem you mentioned",
                        "body": (
                            f"{{first_name}} — one customer described it like this: "
                            f"\"{sample_voice}\". The pattern we built for that "
                            f"workflow saves them about a half-day per account. "
                            f"15 min next week to walk through?"
                        ),
                        "cta": "https://cal.com/team/15min",
                        "delay_days": 0,
                    },
                    {
                        "step_num": 2,
                        "subject": "(quick) the 1-page version",
                        "body": (
                            "Just sent a 1-pager showing how the auto-handoff "
                            "would map to your stack. Worth a quick look?"
                        ),
                        "cta": "https://docs.example.com/handoff",
                        "delay_days": 3,
                    },
                ],
            },
        }

    if agent == "paid_media_agent":
        platform_label = {
            "google_ads": "Google Ads",
            "meta_ads": "Meta Ads",
            "linkedin_ads": "LinkedIn Ads",
        }.get(req.channel, "Paid")
        _emit_for_agent("paid_media_op", {"platform": platform_label})
        return {
            "synthetic": True,
            "shape": "paid_media",
            "paid_media_action": {
                "variants_proposed": [
                    {
                        "platform": req.channel,
                        "headline": "Stop losing renewals at the CSM handoff",
                        "body": "When the handoff doc misses context, NRR slips. Auto-generate from telemetry.",
                        "cta": "Try free for 14 days",
                        "test_axis": "specificity-of-pain",
                        "rationale": "Names the specific moment of failure",
                    },
                    {
                        "platform": req.channel,
                        "headline": f"How RevOps teams cut handoff time on {platform_label}",
                        "body": "Three case studies from peer companies. 15-min read.",
                        "cta": "Read the case studies",
                        "test_axis": "proof-by-customer-name",
                        "rationale": "Tests social proof using ICP-peer logos",
                    },
                ],
                "stop_loss_incidents": [],
                "campaigns_reviewed": 0,
                "variants_dropped": 0,
                "confidence": "medium",
            },
        }

    if agent == "positioning_agent":
        _emit_for_agent("positioning_op", {"topic_hint": req.topic_hint})
        return {
            "synthetic": True,
            "shape": "positioning",
            "positioning_proposals": [
                {
                    "_id": "preview_prop",
                    "kind": "new_claim",
                    "claim_text": (req.topic_hint or "Cut handoff time by automating the brief"),
                    "applies_to_icp": [req.icp_segment],
                    "rationale": "Seeded preview — wire a real run to validate via validate_claim + web_search.",
                    "status": "proposed",
                    "confidence": "medium",
                },
            ],
        }

    if agent == "customer_voice_agent":
        _emit_for_agent("customer_voice_ingest", {"topic_hint": req.topic_hint})
        return {
            "synthetic": True,
            "shape": "customer_voice",
            "inserted": 0,
            "skipped_low_value": 0,
            "summary": (
                "(Preview — no raw text was processed.) Run the real "
                "customer_voice_agent via A2A to ingest sales transcripts, "
                "support tickets, or NPS responses for "
                f"{req.icp_segment}."
            ),
        }

    if agent == "research_agent":
        _emit_for_agent("research_op", {"topic_hint": req.topic_hint})
        return {
            "synthetic": True,
            "shape": "research",
            "research_findings": {
                "icp_segment": req.icp_segment,
                "channel": req.channel,
                "customer_voice": [(v.get("text") or v.get("raw_quote", ""))
                                    for v in voice],
                "approved_claims": [c["claim_text"] for c in claims[:5]],
                "negative_examples": {"claim_risk": [], "tone": []},
                "web_findings": [],
                "past_lessons": [],
                "confidence": "low" if not (voice or claims) else "medium",
            },
        }

    if agent == "ops_qa_agent":
        _emit_for_agent("ops_qa_op", {})
        return {
            "synthetic": True,
            "shape": "ops_qa",
            "checked": 0,
            "incidents_opened": 0,
            "confidence": "low",
            "summary": "(Preview — no real ops_targets were probed. Live run hits http_health_check on configured URLs.)",
        }

    if agent == "cmo_planner":
        _emit_for_agent("cmo_plan_op", {"topic_hint": req.topic_hint})
        return {
            "synthetic": True,
            "shape": "cmo_memo",
            "memo_markdown": (
                "# Weekly CMO memo — preview\n\n"
                "_(This is a synthetic preview. The real CMO Planner runs "
                "Research + Analytics + self-verification before drafting.)_\n\n"
                "## What played last week\n"
                "(connect to BigQuery telemetry.actions to populate)\n\n"
                "## Experiment slate\n"
                "(connect to mongodb.experiments to populate)\n"
            ),
            "proposed_experiments": [],
            "approval_id": None,
        }

    if agent == "review_agent":
        _emit_for_agent("review_op", {})
        # Pull the most recent draft from actions and produce a synthetic
        # review. No fake eval scores — we only flag what's deterministically
        # detectable in plain text (e.g., absolute language, lone percentages).
        db = mongo_tools.db()
        recent_draft_row = db["actions"].find_one(
            {"action_type": {"$regex": "^draft_"}},
            sort=[("_id", -1)],
        )
        draft_text = ""
        draft_channel = req.channel
        if recent_draft_row:
            raw = recent_draft_row.get("raw") or {}
            d = raw.get("draft") if isinstance(raw, dict) else None
            if isinstance(d, dict):
                draft_text = d.get("body_markdown") or d.get("body") or d.get("text") or ""
            elif isinstance(d, str):
                draft_text = d
            draft_channel = recent_draft_row.get("channel") or req.channel

        flags = []
        if draft_text:
            import re as _re
            # Crude pattern: lone numerical claims (e.g. "30%", "2x faster").
            for m in _re.finditer(r"\b\d+(?:\.\d+)?\s*%|\b\d+x\b", draft_text):
                flags.append({
                    "phrase": m.group(0),
                    "issue": "needs_evidence",
                    "evidence_source": None,
                })
            # Absolute language often flagged by review.
            for word in ("guaranteed", "always", "never", "100%", "completely"):
                if _re.search(rf"\b{word}\b", draft_text, _re.IGNORECASE):
                    flags.append({
                        "phrase": word,
                        "issue": "off_brand",
                        "evidence_source": None,
                    })

        return {
            "synthetic": True,
            "shape": "review",
            "review": {
                "flags": flags[:5],
                "qualitative_notes": (
                    f"(Preview review — deterministic pattern check only, no "
                    f"LLM grading. {len(flags)} pattern hits found. The live "
                    f"Review agent additionally calls validate_claim and "
                    f"web_search on every numeric / named claim, and applies "
                    f"a Vertex AI Eval rubric.)"
                ),
                "recommendation": "edit" if flags else "pass",
                "confidence": "low",
            },
            "draft": draft_text or (
                "(no draft found in actions — generate one via the full "
                "pipeline first, then re-run review)"
            ),
            "channel": draft_channel,
        }

    if agent == "image_brief_agent":
        _emit_for_agent("image_brief_op", {"topic_hint": req.topic_hint})
        # Generate a real image using the local-dev Imagen path (already
        # wired in shared/imagen.py). No fake stubs — produce an actual
        # PNG so the UI's ChannelPreview renders the result.
        import uuid as _uuid

        from shared.imagen import aspect_for_channel, generate_image

        # Channel may map to a known aspect, else default 16:9.
        aspect = aspect_for_channel(req.channel) or "16:9"
        prompt_text = (
            f"clean editorial illustration for {req.icp_segment} on "
            f"{req.channel}: {req.topic_hint or 'business concept'}. "
            f"Minimal flat design, no text, no logos, no real people."
        )
        try:
            img = generate_image(
                prompt=prompt_text,
                telemetry_id=f"act_preview_{_uuid.uuid4().hex[:8]}",
                aspect_ratio=aspect,
                alt_text=req.topic_hint or "Editorial illustration",
            )
            return {
                "synthetic": True,
                "shape": "image_brief",
                "image": {
                    "mode": img.mode,
                    "url": img.public_url,
                    "alt_text": img.alt_text,
                    "prompt": img.prompt,
                    "aspect_ratio": img.aspect_ratio,
                    "width": img.width,
                    "height": img.height,
                    "reason": img.reason,
                },
            }
        except Exception as e:
            log.warning("synthetic image_brief generate_image failed: %s", e)
            return {
                "synthetic": True,
                "shape": "image_brief",
                "image": {
                    "mode": "stub",
                    "url": None,
                    "alt_text": req.topic_hint or "Editorial illustration",
                    "prompt": prompt_text,
                    "aspect_ratio": aspect,
                    "reason": f"local imagen failed: {e}",
                },
            }

    if agent == "analytics_agent":
        _emit_for_agent("analytics_op", {})
        # Real Mongo counts — no fake metrics. The UI's renderer treats this
        # like a mini-dashboard. Real BQ analytics needs ADC.
        db = mongo_tools.db()
        from datetime import timedelta as _td
        week_ago = datetime.now(UTC) - _td(days=7)
        total_actions = db["actions"].count_documents({"ts": {"$gte": week_ago}})
        drafts_7d = db["actions"].count_documents({
            "ts": {"$gte": week_ago},
            "action_type": {"$regex": "^draft_"},
        })
        # Distinct channels touched
        channels = list(db["actions"].distinct(
            "channel",
            {"ts": {"$gte": week_ago}, "channel": {"$exists": True, "$ne": None}},
        ))
        # By-agent counts
        by_agent_rows = list(db["actions"].aggregate([
            {"$match": {"ts": {"$gte": week_ago}}},
            {"$group": {"_id": "$agent", "n": {"$sum": 1}}},
            {"$sort": {"n": -1}},
            {"$limit": 8},
        ]))
        return {
            "synthetic": True,
            "shape": "analytics",
            "analytics_snapshot": {
                "window": "7d",
                "total_actions": total_actions,
                "drafts": drafts_7d,
                "channels_touched": channels,
                "by_agent": [
                    {"agent": r["_id"], "count": r["n"]} for r in by_agent_rows
                ],
                "note": (
                    "Local-dev counts from the Mongo ``actions`` mirror. The "
                    "live analytics_agent additionally pulls from BigQuery "
                    "telemetry.actions for full-fidelity attribution + drift."
                ),
            },
        }

    if agent == "self_critique_agent":
        _emit_for_agent("self_critique_op", {})
        # List skills currently flagged for review + recent sweep counts.
        db = mongo_tools.db()
        from datetime import timedelta as _td
        week_ago = datetime.now(UTC) - _td(days=7)
        proposals = list(db["skills"].find(
            {"self_critique_proposal.status": "awaiting_human_review"}
        ).limit(5))
        # Strip ObjectIds and project just the proposal slice
        clean_props: list[dict] = []
        for p in proposals:
            sc = p.get("self_critique_proposal") or {}
            clean_props.append({
                "skill_id": p.get("_id"),
                "issue": sc.get("issue"),
                "proposed_change": sc.get("proposed_change"),
                "confidence": sc.get("confidence"),
                "evidence_count": sc.get("evidence_count"),
            })
        n_sweeps = db["actions"].count_documents({
            "agent": "self_critique_agent",
            "ts": {"$gte": week_ago},
        })
        return {
            "synthetic": True,
            "shape": "self_critique",
            "self_critique": {
                "awaiting_review": clean_props,
                "sweeps_7d": n_sweeps,
                "note": (
                    "Live self-critique reads BigQuery telemetry.actions + "
                    "training.edits to cluster founder-edit patterns. In "
                    "LOCAL_DEV the BQ source returns [], so no new patterns "
                    "are surfaced — only previously-stored proposals show up."
                ),
            },
        }

    # Truly unknown agent — fall through.
    return {
        "synthetic": True,
        "shape": "generic",
        "agent_id": agent,
        "note": (
            f"No synthetic preview implemented for {agent}. Start the "
            f"matching A2A process (see agents/a2a_server.py) and set "
            f"A2A_URL_BASE=http://localhost in .env."
        ),
    }
