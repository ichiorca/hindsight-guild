"""C10c — Weekly self-critique cron.

Thin shim: invokes the Self-Critique ADK agent via A2A. The agent reads
last-14-day telemetry + edits, identifies systematic issues, and writes
self_critique_proposal entries to the skills collection. Same code path
whether the cron fires it (this module) or the founder triggers it from
the UI.

The previous Gemini-direct implementation lived here too; that drift between
"cron logic" and "agent logic" is gone now — the agent is the single source
of truth for self-critique behavior. Promotion gating still happens in
services/promotion_gate.
"""
from __future__ import annotations

import logging
import os

from agents.a2a_client import call_agent

PROJECT_ID = os.environ["PROJECT_ID"]
log = logging.getLogger(__name__)


def main():
    log.info("Invoking Self-Critique Agent via A2A...")
    try:
        result = call_agent(
            "self_critique",
            "Run the weekly self-critique pass. Pull the last 14 days of "
            "telemetry.actions and training.edits, find systematic issues "
            "per (skill_id, skill_version), and write self_critique_proposal "
            "entries onto the matching skill docs for any pattern that has "
            "enough evidence to act on.",
            timeout=600.0,
        )
        log.info("Self-Critique Agent returned: %s", result)
    except Exception as e:
        log.exception("Self-Critique cron failed: %s", e)


if __name__ == "__main__":
    main()
