"""Unit tests for shared.allocator."""
from __future__ import annotations

import random
from unittest.mock import patch

import pytest

from shared import allocator


def _fake_exp(weights=(50, 50)):
    return {
        "_id": "exp_test",
        "state": "running",
        "variants": [
            {"id": f"v{i}", "allocation_pct": w}
            for i, w in enumerate(weights)
        ],
    }


def test_pick_variant_returns_valid_id():
    with patch.object(allocator.mongo_tools, "find_one", return_value=_fake_exp()):
        choice = allocator.pick_variant("exp_test", rng=random.Random(7))
    assert choice in ("v0", "v1")


def test_pick_variant_respects_weights():
    """1000 samples at 90/10 must put >800 picks on the heavier arm."""
    counts = {"v0": 0, "v1": 0}
    rng = random.Random(1)
    with patch.object(allocator.mongo_tools, "find_one",
                       return_value=_fake_exp(weights=(90, 10))):
        for _ in range(1000):
            counts[allocator.pick_variant("exp_test", rng=rng)] += 1
    assert counts["v0"] > 800, counts


def test_pick_variant_raises_on_missing_experiment():
    with patch.object(allocator.mongo_tools, "find_one", return_value=None):
        with pytest.raises(ValueError, match="not found"):
            allocator.pick_variant("exp_missing")


def test_pick_variant_raises_on_not_running():
    exp = _fake_exp()
    exp["state"] = "decided"
    with patch.object(allocator.mongo_tools, "find_one", return_value=exp):
        with pytest.raises(ValueError, match="state decided"):
            allocator.pick_variant("exp_test")
