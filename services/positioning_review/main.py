"""Positioning weekly cron — invokes the Positioning Agent via A2A.

Runs Sunday evening so any proposals are waiting in the founder's Weekly
Review on Monday morning, alongside self-critique proposals and promotion
requests.
"""
from __future__ import annotations

import logging
import os

from agents.a2a_client import call_agent

log = logging.getLogger(__name__)
PROJECT_ID = os.environ["PROJECT_ID"]


def main():
    log.info("Invoking Positioning Agent via A2A...")
    try:
        result = call_agent(
            "positioning",
            "Run the weekly positioning review. Look at decided experiments, "
            "recent customer voice quotes, and recurring negative_examples "
            "categories. Propose updates to the messaging library where "
            "evidence supports it.",
            timeout=600.0,
        )
        log.info("Positioning Agent returned: %s", result)
    except Exception as e:
        log.exception("Positioning cron failed: %s", e)


if __name__ == "__main__":
    main()
