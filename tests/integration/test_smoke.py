"""Smoke test stub for the headline demo integration.

Runs against a real project + seeded data. Skipped by default; set
INTEGRATION_TEST=1 to enable.

The full reject-then-redraft scenario:
  1. seed_demo.main()
  2. Run Content Agent → draft 1
  3. Insert a negative_examples row (simulating Sheet rejection)
  4. Re-run Content Agent → draft 2 must avoid absolute language AND show
     the just-added negative in its rubric grounding logs.

This test is the spec's headline integration test (plan §5.2). Implementation
is left as a stub here because it requires:
  - A live GCP project with billing
  - Atlas M0 with the vector index in READY state
  - All Cloud Run services deployed
  - Apps Script trigger active (or the test substitutes a direct DB insert)

When the cloud is available, fill in the body following the contract in the
docstring above.
"""
from __future__ import annotations

import os

import pytest


@pytest.mark.skipif(
    os.environ.get("INTEGRATION_TEST") != "1",
    reason="Set INTEGRATION_TEST=1 to run against real cloud resources.",
)
def test_reject_then_redraft():
    pytest.skip("Implement once cloud env is provisioned.")
