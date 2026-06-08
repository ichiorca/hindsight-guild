"""Demo runner — invoke the Research -> Content -> ImageBrief -> Review ->
Finalizer pipeline.

Auto-loads .env from the repo root so a single command works:

    python -m demo.run_pipeline --icp seg_merchant_dtc --channel substack \\
        --topic "post-LLM GTM motion"

Requires (local-dev path):
  - Mongo via `docker compose up -d` + `python -m scripts.local_seed`
  - GOOGLE_API_KEY in .env (free key at https://aistudio.google.com/app/apikey)
  - GOOGLE_GENAI_USE_VERTEXAI=0 in .env (route ADK to Gemini API, not Vertex)

The pipeline degrades the GCP-only pieces automatically:
  - Imagen image gen -> stub URL
  - BigQuery telemetry -> skipped (TELEMETRY_DISABLED=1)
  - Memory Bank -> no-op (AGENT_ENGINE_ID unset)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from pathlib import Path


def _load_dotenv(env_path: Path) -> None:
    """Tiny stdlib-only .env loader (same as scripts/local_seed.py).
    Existing env vars take precedence over the file."""
    if not env_path.is_file():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        os.environ.setdefault(key, val)


_load_dotenv(Path(__file__).resolve().parents[1] / ".env")

if not os.environ.get("GOOGLE_API_KEY"):
    print("ERROR: GOOGLE_API_KEY not set in .env.", file=sys.stderr)
    print("  1. Get a free key: https://aistudio.google.com/app/apikey",
          file=sys.stderr)
    print("  2. Paste it into .env on the GOOGLE_API_KEY= line.",
          file=sys.stderr)
    sys.exit(1)

# ADK imports must come after env vars are set so the Vertex/genai switch
# is read correctly.
from google.adk.runners import Runner  # noqa: E402
from google.adk.sessions import InMemorySessionService  # noqa: E402
from google.genai.types import Content, Part  # noqa: E402

from agents.pipeline import drafting_pipeline  # noqa: E402

_SKILL_BY_CHANNEL = {
    "linkedin": "linkedin_post",
    "email":    "nurture_email",
    "blog":     "blog_outline",
    "substack": "substack_post",
}


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--icp", required=True, help="e.g. seg_merchant_dtc")
    p.add_argument("--channel", default="linkedin",
                   choices=("linkedin", "email", "blog", "substack"))
    p.add_argument("--experiment_id", default=None)
    p.add_argument("--topic", default="",
                   help="topic hint for Research Agent")
    args = p.parse_args()

    session_service = InMemorySessionService()
    runner = Runner(
        agent=drafting_pipeline,
        app_name="hindsight-guild-demo",
        session_service=session_service,
    )

    user_id = "demo_user"
    telemetry_id = f"act_{uuid.uuid4().hex[:12]}"
    session = await session_service.create_session(
        app_name="hindsight-guild-demo",
        user_id=user_id,
        state={
            "telemetry_id": telemetry_id,
            "icp_segment": args.icp,
            "channel": args.channel,
            "experiment_id": args.experiment_id,
            "topic_hint": args.topic,
            "skill_id": _SKILL_BY_CHANNEL[args.channel],
        },
    )

    print("=== Pipeline run ============================")
    print(f"  telemetry_id : {telemetry_id}")
    print(f"  channel      : {args.channel}")
    print(f"  icp          : {args.icp}")
    print(f"  topic        : {args.topic or '(none)'}")
    print("=============================================\n")

    message = Content(
        role="user",
        parts=[Part.from_text(
            text=(
                f"Draft a {args.channel} post targeting {args.icp}. "
                f"{('Focus on: ' + args.topic) if args.topic else ''}"
            ),
        )],
    )

    # Per-agent status as the pipeline progresses.
    seen_agents: set[str] = set()
    async for event in runner.run_async(
        user_id=user_id, session_id=session.id, new_message=message,
    ):
        author = getattr(event, "author", None) or "?"
        if author not in seen_agents:
            seen_agents.add(author)
            print(f"[{author}] running...")

    # Re-read final session state.
    final = await session_service.get_session(
        app_name="hindsight-guild-demo", user_id=user_id, session_id=session.id,
    )
    state = final.state if final else {}

    print("\n=== Pipeline output =========================")
    print(_pretty_state(state, args.channel))


def _pretty_state(state: dict, channel: str) -> str:
    """Channel-aware pretty-print of the final session state."""
    out = []

    # Research findings
    rf = state.get("research_findings")
    if isinstance(rf, dict):
        out.append("--- Research findings ---")
        if rf.get("customer_voice"):
            out.append(f"  customer_voice ({len(rf['customer_voice'])} quotes):")
            for v in rf["customer_voice"][:3]:
                text = v.get("text") if isinstance(v, dict) else str(v)
                out.append(f"    - {text[:140]}")
        if rf.get("approved_claims"):
            out.append(f"  approved_claims ({len(rf['approved_claims'])} claims):")
            for c in rf["approved_claims"][:3]:
                text = c.get("claim_text") if isinstance(c, dict) else str(c)
                out.append(f"    - {text[:140]}")
        if rf.get("past_lessons"):
            out.append(f"  past_lessons ({len(rf['past_lessons'])}):")
            for ls in rf["past_lessons"][:2]:
                text = ls.get("content") if isinstance(ls, dict) else str(ls)
                out.append(f"    - {text[:140]}")
        out.append("")

    # Draft — substack is a structured dict, everything else is plain text.
    draft = state.get("draft")
    if draft:
        out.append(f"--- Draft (channel={channel}) ---")
        if isinstance(draft, dict):
            if draft.get("headline"):
                out.append(f"  HEADLINE: {draft['headline']}")
            if draft.get("subtitle"):
                out.append(f"  SUBTITLE: {draft['subtitle']}")
            body = draft.get("body_markdown") or draft.get("body") or ""
            out.append("")
            out.append(body)
        else:
            out.append(str(draft))
        out.append("")

    # Image (stub URL in local mode)
    img = state.get("image")
    if isinstance(img, dict):
        out.append(f"--- Image (mode={img.get('mode')}) ---")
        out.append(f"  url: {img.get('url')}")
        out.append(f"  alt_text: {img.get('alt_text')}")
        if img.get("reason"):
            out.append(f"  reason: {img['reason']}")
        out.append("")

    # Review
    review = state.get("review")
    if isinstance(review, dict):
        out.append("--- Review ---")
        out.append(f"  recommendation: {review.get('recommendation')}")
        flags = review.get("flags") or []
        if flags:
            out.append(f"  flags ({len(flags)}):")
            for f in flags[:6]:
                out.append(f"    - {f.get('phrase')}: {f.get('issue')}")
        if review.get("qualitative_notes"):
            out.append(f"  notes: {review['qualitative_notes']}")
        out.append("")

    # Finalizer envelope — what an A2A caller would see.
    final = state.get("_final")
    if final is not None:
        out.append("--- Finalizer envelope (A2A response payload) ---")
        try:
            out.append(json.dumps(
                final if isinstance(final, dict) else json.loads(final),
                indent=2, default=str,
            ))
        except Exception:
            out.append(str(final))

    return "\n".join(out) if out else "(empty state)"


if __name__ == "__main__":
    asyncio.run(main())
