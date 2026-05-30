"""Demo runner — invoke the CMO Planner for a weekly plan.

The CMO Planner uses Analytics and Research as AgentTools, queries Mongo
for experiments and self-critique proposals, drafts the weekly memo, and
posts to Slack for founder approval. End-to-end multi-agent flow.
"""
from __future__ import annotations

import argparse
import asyncio
import json

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai.types import Content, Part

from agents.cmo_planner import cmo_planner


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--week", default=None,
                   help="ISO week label, e.g. 2026-W22. Default: this week.")
    args = p.parse_args()

    session_service = InMemorySessionService()
    runner = Runner(
        agent=cmo_planner,
        app_name="hindsight-guild-cmo",
        session_service=session_service,
    )

    user_id = "founder"
    session = await session_service.create_session(
        app_name="hindsight-guild-cmo", user_id=user_id,
        state={"current_week": args.week or "current"},
    )

    print("=== CMO Planner weekly cycle ===\n")
    msg = Content(role="user", parts=[Part.from_text(
        f"Produce the weekly growth memo and experiment slate for "
        f"week={args.week or 'current'}. Post to Slack for approval."
    )])

    async for event in runner.run_async(
        user_id=user_id, session_id=session.id, new_message=msg
    ):
        if event.is_final_response() and event.content and event.content.parts:
            for part in event.content.parts:
                if getattr(part, "text", None):
                    print(part.text)

    final = await session_service.get_session(
        app_name="hindsight-guild-cmo", user_id=user_id, session_id=session.id
    )
    state = final.state if final else {}
    print("\n--- WEEKLY PLAN ---")
    print(json.dumps(state.get("weekly_plan", {}), indent=2))


if __name__ == "__main__":
    asyncio.run(main())
