"""Generate a Substack article via the local ADK pipeline.

Convenience wrapper around the same Research -> Content -> ImageBrief ->
Review -> Finalizer pipeline that powers /api/draft, hardcoded to the
substack channel and configured to save the resulting article to
drafts/YYYY-MM-DD-<slug>.md so you can open it in your editor.

Usage:
    python -m scripts.generate_substack_article \\
        --topic "Latest advancements in agentic commerce" \\
        [--icp seg_merchant_dtc]

Reads GOOGLE_API_KEY + MONGO_URI_DIRECT from .env via the same tiny
loader scripts/local_seed.py uses.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

# --- .env autoload (mirrors scripts/local_seed.py) ---------------------------

def _load_dotenv(env_path: Path) -> None:
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


REPO_ROOT = Path(__file__).resolve().parents[1]
_load_dotenv(REPO_ROOT / ".env")

if not os.environ.get("GOOGLE_API_KEY"):
    print("ERROR: GOOGLE_API_KEY not set in .env.", file=sys.stderr)
    sys.exit(1)

# ADK imports must come after env vars so the Vertex/genai switch is read.
from google.adk.runners import Runner  # noqa: E402
from google.adk.sessions import InMemorySessionService  # noqa: E402
from google.genai.types import Content, Part  # noqa: E402

from agents.pipeline import drafting_pipeline  # noqa: E402


def _slugify(s: str) -> str:
    s = re.sub(r"[^a-z0-9\s-]", "", s.lower()).strip()
    s = re.sub(r"[\s_-]+", "-", s)
    return s[:60].strip("-") or "untitled"


async def main():
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--topic", required=True,
                   help='Article topic, e.g. "Latest advancements in agentic commerce"')
    p.add_argument("--icp", default="seg_merchant_dtc",
                   help="ICP segment id (default: seg_merchant_dtc)")
    p.add_argument("--out-dir", default=str(REPO_ROOT / "drafts"),
                   help="Directory to write the .md file (default: ./drafts/)")
    args = p.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    telemetry_id = f"act_{uuid.uuid4().hex[:12]}"

    print("=" * 66)
    print("  Substack pipeline run")
    print(f"  topic         : {args.topic}")
    print(f"  icp           : {args.icp}")
    print(f"  telemetry_id  : {telemetry_id}")
    print(f"  draft_dir     : {out_dir}")
    print("=" * 66)
    print()

    session_service = InMemorySessionService()
    runner = Runner(
        agent=drafting_pipeline,
        app_name="hindsight-guild-local",
        session_service=session_service,
    )

    user_id = "local_user"
    session = await session_service.create_session(
        app_name="hindsight-guild-local",
        user_id=user_id,
        state={
            "telemetry_id": telemetry_id,
            "icp_segment": args.icp,
            "channel": "substack",
            "topic_hint": args.topic,
            "skill_id": "substack_post",
        },
    )

    message = Content(
        role="user",
        parts=[Part.from_text(
            text=(
                f"Draft a substack post targeting {args.icp}. "
                f"Focus on: {args.topic}"
            ),
        )],
    )

    seen = set()
    started = time.monotonic()
    print("[pipeline] starting...")
    async for event in runner.run_async(
        user_id=user_id, session_id=session.id, new_message=message,
    ):
        author = getattr(event, "author", None) or "?"
        if author not in seen:
            seen.add(author)
            elapsed = time.monotonic() - started
            print(f"[{elapsed:5.1f}s] {author} ran")

    elapsed = time.monotonic() - started
    print(f"[{elapsed:5.1f}s] pipeline done\n")

    # Re-read final session state.
    final = await session_service.get_session(
        app_name="hindsight-guild-local",
        user_id=user_id,
        session_id=session.id,
    )
    state = final.state if final else {}

    # ---- Save the article ---------------------------------------------------
    # LLM outputs can come back as either parsed JSON (dict) or raw text.
    # Try to parse string outputs as JSON; fall back to treating them as
    # plain text. Keeps the file save robust regardless of how the LLM
    # decided to format itself.
    def _as_dict(value) -> dict:
        if isinstance(value, dict):
            return value
        if not isinstance(value, str):
            return {}
        s = value.strip()
        # Strip ```json ... ``` fences. Regex over split() because LLMs
        # don't reliably terminate the closing fence with a newline.
        m = re.search(r"```(?:json|)\s*(\{.*\})\s*```", s, re.DOTALL)
        if m:
            s = m.group(1)
        # Or just find the first {...} block.
        if not s.lstrip().startswith("{"):
            start = s.find("{")
            if start != -1:
                s = s[start:]
        try:
            parsed = json.loads(s)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            pass
        # Last-ditch: walk back from the end looking for the largest
        # valid JSON prefix. Handles trailing prose/fences gracefully.
        for end in range(len(s), 0, -1):
            try:
                parsed = json.loads(s[:end])
                return parsed if isinstance(parsed, dict) else {}
            except Exception:
                continue
        return {}

    draft_raw = state.get("draft") or {}
    draft = _as_dict(draft_raw) if not isinstance(draft_raw, str) else (
        _as_dict(draft_raw) or {"body_markdown": draft_raw}
    )
    review = _as_dict(state.get("review"))
    image = _as_dict(state.get("image"))

    headline = draft.get("headline") or args.topic
    subtitle = draft.get("subtitle") or ""
    body = (draft.get("body_markdown")
            or draft.get("body")
            or draft.get("text") or "")

    today = datetime.now(UTC).strftime("%Y-%m-%d")
    slug = _slugify(headline or args.topic)
    out_path = out_dir / f"{today}-{slug}.md"

    md_parts = [
        f"# {headline}",
    ]
    if subtitle:
        md_parts.append(f"\n> {subtitle}\n")
    md_parts.append("")
    # Hero image, if ImageBrief produced a real URL (file:// for local-dev,
    # https://… for cloud). Embed inline so the markdown is self-rendering.
    if image and image.get("mode") == "api" and image.get("url"):
        alt = image.get("alt_text", "").replace("]", "").replace("[", "")
        md_parts.append(f"![{alt}]({image['url']})")
        md_parts.append("")
    md_parts.append(body or "*(no body produced — see review flags below)*")
    md_parts.append("")
    md_parts.append("---")
    md_parts.append("")
    md_parts.append("## Pipeline metadata")
    md_parts.append("")
    md_parts.append(f"- **topic requested**: {args.topic}")
    md_parts.append(f"- telemetry_id: `{telemetry_id}`")
    md_parts.append(f"- icp_segment: `{args.icp}`")
    md_parts.append("- channel: `substack`")
    md_parts.append(f"- generated_at: {datetime.now(UTC).isoformat()}")
    if image:
        md_parts.append(f"- image mode: `{image.get('mode')}` "
                          f"(alt: \"{image.get('alt_text', '')[:80]}\")")
    if review:
        rec = review.get("recommendation")
        flags = review.get("flags") or []
        md_parts.append(f"- review recommendation: `{rec}`")
        if flags:
            md_parts.append(f"- review flags ({len(flags)}):")
            for f in flags:
                md_parts.append(f"  - **{f.get('phrase')}**: {f.get('issue')}")
        if review.get("qualitative_notes"):
            md_parts.append(f"- review notes: {review['qualitative_notes']}")

    out_path.write_text("\n".join(md_parts), encoding="utf-8")

    # ---- Console summary ----------------------------------------------------
    print("=" * 66)
    print("  RESULT")
    print("=" * 66)
    print(f"  saved: {out_path}")
    print(f"  bytes: {out_path.stat().st_size}")
    if isinstance(draft, dict):
        print(f"  headline: {headline}")
        if subtitle:
            print(f"  subtitle: {subtitle}")
        print(f"  body length: {len(body)} chars")
    print(f"  image mode: {image.get('mode') if image else '(none)'}")
    print(f"  review recommendation: {review.get('recommendation') if review else '(none)'}")
    if review and review.get("flags"):
        print(f"  review flags: {len(review['flags'])}")
    print()
    print("Open the file to read the full article:")
    print(f"  code {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
