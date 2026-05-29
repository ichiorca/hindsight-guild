"""Paid Media sweep — invokes the Paid Media Analyst Agent via A2A.

Every 6 hours. Reads campaign performance, drafts new variants in PAUSED
state, opens ops_incidents for any campaign that hits the stop-loss
threshold.
"""
from __future__ import annotations

import logging
import os

from agents.a2a_client import call_agent

log = logging.getLogger(__name__)
PROJECT_ID = os.environ["PROJECT_ID"]


def main():
    log.info("Invoking Paid Media Analyst Agent via A2A...")
    try:
        result = call_agent(
            "paid_media",
            "Run the paid-media sweep. Review the last 24h of campaign spend "
            "and conversion data. Draft new variants in paused state. Open "
            "stop-loss incidents where the rules trigger.",
            timeout=600.0,
        )
        log.info("Paid Media Agent returned: %s", result)
    except Exception as e:
        log.exception("Paid Media cron failed: %s", e)


if __name__ == "__main__":
    main()
