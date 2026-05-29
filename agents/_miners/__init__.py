"""PRD-03 self-critique miners.

Each miner is a pure function that reads recent telemetry/approvals,
finds patterns, and returns a list of ``Proposal`` dicts. The runner
in ``agents/self_critique_runner.py`` orchestrates them and persists
the proposals to the right Mongo location.

A miner NEVER:
  - Writes directly to Mongo (the runner does that).
  - Calls an LLM (deterministic clustering is the v1 contract).
  - Errors out the whole run (return empty list + log on failure).

Miner contract::

    def mine(db, *, lookback_days: int) -> list[Proposal]

where ``Proposal`` is a dict with at least::

    {
      "kind":          "voice|negative|paid|aeo|signal",
      "target_kind":   "skill" | "paid_action" | "signal_source",
      "target_id":     str,        # skill_id / variant_id / source_name
      "issue":         str,        # one-sentence
      "proposed_change": str,      # 3-5 sentence edit
      "confidence":    "high|medium|low",
      "evidence_count": int,
      "evidence":      dict,       # miner-specific
    }

v1 miners: ``voice``, ``negative``, ``paid``.
v1.5 miners (PRD-01 / PRD-02 dependent): ``aeo``, ``signal``.
"""
