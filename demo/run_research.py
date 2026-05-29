"""C15 — Demo runner: invoke the Research Agent. Used in Moment 3 to feed
the prompt-injection string and demonstrate Model Armor blocking.
"""
from __future__ import annotations

import argparse
import asyncio
import json

from google.adk.runner import Runner

from agents.research import research_agent


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--prompt", required=True,
                   help="Arbitrary text to feed the Research Agent. Use a known "
                        "injection string to demo Model Armor.")
    args = p.parse_args()

    runner = Runner(agent=research_agent, verbose=True)
    print(f"=== Running Research Agent with prompt ===\n{args.prompt}\n")
    result = await runner.run(input={"prompt": args.prompt})
    print("\n--- OUTPUT ---")
    print(result.output)
    print("\n--- MODEL ARMOR ---")
    print(json.dumps(result.state.get("model_armor", {}), indent=2))


if __name__ == "__main__":
    asyncio.run(main())
