"""Unit tests for shared.mongo_tools.

We patch the cached _client() so no real Mongo connection is ever opened.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from shared import mongo_tools


def _fake_client_factory(returns: dict):
    """Return a MagicMock that mimics MongoClient.

    `returns` is a dict mapping (collection, method) -> result. Defaults to empty.
    """
    db = MagicMock()
    for (coll, method), result in returns.items():
        m = getattr(db[coll], method)
        if method in ("find", "find_one"):
            # find returns a cursor-like; .limit().sort() chain is hard to fake.
            # Tests in this file patch the public helpers, not the raw client.
            pass
        m.return_value = result
    client = MagicMock()
    client.__getitem__.return_value = db
    return client


def test_use_secret_sets_default():
    """use_secret now sets a default secret name; per-call kwargs override it."""
    prev = mongo_tools._DEFAULT_SECRET
    try:
        mongo_tools.use_secret("mongo_uri_readonly")
        assert mongo_tools._DEFAULT_SECRET == "mongo_uri_readonly"
    finally:
        mongo_tools.use_secret(prev)


def test_per_secret_client_cache_isolated(monkeypatch):
    """A readonly and a writer client must coexist without thrashing the cache —
    that's why _CLIENTS is a dict and not an lru_cache(maxsize=1).

    ``secretmanager`` is lazy-imported inside ``_client()``, so we patch
    the module-level ``_sm`` cache with a pre-built mock — that short-
    circuits the construction path and feeds ``access_secret_version``
    through the mock. Also clears ``MONGO_URI_DIRECT`` so the LOCAL_DEV
    override doesn't bypass Secret Manager entirely.
    """
    monkeypatch.delenv("MONGO_URI_DIRECT", raising=False)

    sm_mock = MagicMock()
    sm_mock.access_secret_version.return_value.payload.data = b"mongodb://fake/"
    monkeypatch.setattr(mongo_tools, "_sm", sm_mock)
    # IMPORTANT: this test populates the module-global _CLIENTS cache with
    # FAKE clients. Without clearing it on the way out, a later test that
    # calls mongo_tools.db() gets a MagicMock instead of the real LOCAL_DEV
    # Mongo — which silently returns no rows (e.g. the paid-miner tests then
    # see zero variants). Snapshot + restore so the cache leak can't escape.
    saved_clients = dict(mongo_tools._CLIENTS)
    try:
        with patch.object(mongo_tools, "MongoClient", return_value=MagicMock()):
            mongo_tools._CLIENTS.clear()
            mongo_tools._client("mongo_uri_readonly")
            mongo_tools._client("mongo_uri_writer")
            assert set(mongo_tools._CLIENTS) == {
                "mongo_uri_readonly",
                "mongo_uri_writer",
            }
    finally:
        mongo_tools._CLIENTS.clear()
        mongo_tools._CLIENTS.update(saved_clients)


def test_find_one_passes_query():
    fake_db = MagicMock()
    fake_db.__getitem__.return_value.find_one.return_value = {"_id": "x"}
    with patch.object(mongo_tools, "db", return_value=fake_db):
        result = mongo_tools.find_one("skills", {"_id": "x"})
    assert result == {"_id": "x"}
    fake_db.__getitem__.assert_called_with("skills")


def test_find_sorted_applies_sort():
    """Critical: rubric grounding depends on ts DESC."""
    fake_db = MagicMock()
    cursor = MagicMock()
    cursor.sort.return_value.limit.return_value = [
        {"_id": "neg_1", "ts": "later"},
        {"_id": "neg_2", "ts": "earlier"},
    ]
    fake_db.__getitem__.return_value.find.return_value = cursor

    with patch.object(mongo_tools, "db", return_value=fake_db):
        result = mongo_tools.find_sorted(
            "negative_examples",
            {"channel": "linkedin", "rejection_category": "claim_risk"},
            sort=[("ts", -1)],
            limit=3,
        )

    cursor.sort.assert_called_with([("ts", -1)])
    cursor.sort.return_value.limit.assert_called_with(3)
    assert len(result) == 2


def test_upsert_uses_set_with_upsert_true():
    fake_db = MagicMock()
    with patch.object(mongo_tools, "db", return_value=fake_db):
        mongo_tools.upsert("skills", {"_id": "linkedin_post"}, {"current_version": "v4"})

    fake_db.__getitem__.return_value.update_one.assert_called_once_with(
        {"_id": "linkedin_post"},
        {"$set": {"current_version": "v4"}},
        upsert=True,
    )
