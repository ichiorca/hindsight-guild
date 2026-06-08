"""Fixtures for the Mongo-backed signal tests (Tier 2).

``signal_watcher`` / ``signal_router`` are exercised against a REAL local
MongoDB rather than a mock: the watcher's dedupe path depends on the unique
sparse index on ``signals.evidence_url`` raising ``DuplicateKeyError``, which a
mock can't reproduce. Both ``run_once`` functions accept an injected ``db``, so
the tests pass this throwaway test DB directly — no Secret Manager / ADC.

The fixture auto-skips when no local Mongo is reachable, so CI (which runs
``pytest tests/unit`` without a Mongo service) stays green. To exercise these
locally: ``docker compose up -d mongo``.
"""
from __future__ import annotations

import os

import pytest

_TEST_DB = "hindsight_guild_test"
_SIGNAL_COLLECTIONS = ("signals", "signal_sources", "customer_voice", "actions")


@pytest.fixture
def signal_db():
    from pymongo import ASCENDING, MongoClient

    uri = os.environ.get("MONGO_URI_DIRECT") or "mongodb://localhost:27017"
    client = MongoClient(uri, serverSelectionTimeoutMS=800)
    try:
        client.admin.command("ping")
    except Exception:
        client.close()
        pytest.skip(
            "local MongoDB not reachable (start it with "
            "`docker compose up -d mongo`) — skipping Mongo-backed signal tests"
        )

    db = client[_TEST_DB]

    def _clean() -> None:
        for c in _SIGNAL_COLLECTIONS:
            db[c].delete_many({})

    _clean()
    # Recreate the unique sparse index the watcher's dedupe relies on
    # (idempotent — same name + spec across runs).
    db["signals"].create_index(
        [("evidence_url", ASCENDING)],
        unique=True, sparse=True, name="idx_evidence_url_unique",
    )
    try:
        yield db
    finally:
        _clean()
        client.close()
