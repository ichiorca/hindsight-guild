"""Ops/QA sweep — invokes the Ops/QA Agent via A2A.

Daily. Checks landing pages, UTM hygiene on outbound links, pixel firing
where instrumented. Opens ops_incidents for any failures.
"""
from __future__ import annotations

import logging
import os

from agents.a2a_client import call_agent

log = logging.getLogger(__name__)
PROJECT_ID = os.environ["PROJECT_ID"]


def main():
    log.info("Invoking Ops/QA Agent via A2A...")
    try:
        result = call_agent(
            "ops_qa",
            "Run the daily Ops/QA sweep. Check every landing page in "
            "ops_targets. Verify UTM hygiene on the last 24h of "
            "attribution_map URLs. Open incidents for any failures.",
            timeout=600.0,
        )
        log.info("Ops/QA Agent returned: %s", result)
    except Exception as e:
        log.exception("Ops/QA cron failed: %s", e)


if __name__ == "__main__":
    main()
