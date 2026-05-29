"""Weighted-random allocator. Vizier-swap-compatible interface.

Reads `variants[].allocation_pct` from the experiment doc. report_outcome is a
no-op for round-robin; the signature stays so a Vizier swap-in doesn't touch
caller code.
"""
from __future__ import annotations

import random

from shared import mongo_tools


def pick_variant(experiment_id: str, rng: random.Random | None = None) -> str:
    """Returns the variant_id to use for the next action."""
    rng = rng or random
    exp = mongo_tools.find_one("experiments", {"_id": experiment_id},
                               secret_name="mongo_uri_readonly")
    if not exp:
        raise ValueError(f"Experiment {experiment_id} not found")
    if exp.get("state") != "running":
        raise ValueError(
            f"Experiment {experiment_id} is in state {exp.get('state')}, not running"
        )

    variants = exp["variants"]
    weights = [v["allocation_pct"] for v in variants]
    ids = [v["id"] for v in variants]
    return rng.choices(ids, weights=weights, k=1)[0]


def report_outcome(experiment_id: str, variant_id: str, metric: str, value: float) -> None:
    """No-op for weighted-random. Vizier would update its posterior here.

    Kept in the API so a Phase-3 swap-in (Vertex AI Vizier study) doesn't
    require touching the agents or the outcome attacher.
    """
    _ = experiment_id, variant_id, metric, value
