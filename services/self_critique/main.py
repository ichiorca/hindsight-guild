"""Self-critique cron — runs the PRD-03 deterministic miners.

Repointed from the legacy self-critique LlmAgent (which it used to invoke via
A2A) to the miner runner. This matters because the Self-Learning page
(``/learning``) reads the ``self_critique_runs`` collection + the proposals the
miners emit — and ONLY ``agents/self_critique_runner.run_once()`` writes those.
The old LlmAgent path wrote proposals straight to ``skills`` and never touched
``self_critique_runs``, so the page stayed empty even though this job ran fine.

Same code path whether the cron fires it (this module) or the founder clicks
"Run miners now" in the UI (``POST /api/self-critique/run-now`` →
``self_critique_runner.run_once``). Promotion gating still happens in
``services/promotion_gate``.

The 5 miners (voice / negative / paid / aeo / signal) are deterministic and
mechanical; they need decision history to emit proposals (approvals for voice,
``training.edits`` for negative, etc.), so early runs may legitimately emit 0
proposals while still recording a run row that proves the loop is alive.
"""
from __future__ import annotations

import json
import logging

from agents.self_critique_runner import run_once

log = logging.getLogger(__name__)


def main():
    log.info("Running PRD-03 self-critique miners...")
    try:
        result = run_once()
        log.info("Self-critique miners complete: %s",
                 json.dumps(result, default=str)[:1000])
    except Exception as e:
        log.exception("Self-critique miner run failed: %s", e)


if __name__ == "__main__":
    main()
