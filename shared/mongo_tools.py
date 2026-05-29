"""Thin pymongo wrappers for non-LLM callers AND the rubric harness.

Two distinct callers use this module:
  1. Non-LLM workers — outcome_attach, drift_detect, self_critique, seed_demo,
     edit_capture handler. Default secret: ``mongo_uri_writer``.
  2. The rubric scoring path in shared/rubrics.py — runs inside agent callbacks
     to pull the 3 most-recent negatives for grounding. Read-only: passes
     ``secret_name="mongo_uri_readonly"`` explicitly.

Role enforcement is **per-call** via the ``secret_name`` kwarg. There is no
mutable process global — multi-agent processes (e.g., the A2A server that
imports all 13 agents) cannot rely on a "last import wins" default. The
``use_secret()`` setter still exists for service entry-points that have a
single, unambiguous role.

LLM-driven agent reasoning (when the model itself decides to look something up)
still goes through MCP via MCPToolset — never through these wrappers.
"""
from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from pymongo import MongoClient

# google.cloud.secretmanager is imported lazily inside _client() — only the
# GCP-deployed path (no MONGO_URI_DIRECT) needs it, so a fully-local
# laptop dev can run without installing google-cloud-secret-manager. The
# TYPE_CHECKING import below resolves the annotation name for static
# analysis without pulling the dependency in at runtime.
if TYPE_CHECKING:
    from google.cloud import secretmanager

PROJECT_ID = os.environ.get("PROJECT_ID", "agentic-marketing-mvp")
DB_NAME = os.environ.get("MONGO_DB", "agentic_marketing")
_DEFAULT_SECRET = os.environ.get("MONGO_SECRET_NAME", "mongo_uri_writer")

# Cache one MongoClient per Secret Manager secret name. This avoids the
# connection-pool churn that the previous lru_cache+cache_clear pattern caused
# every time use_secret() flipped, and crucially keeps role separation intact
# when a single process holds both a reader and a writer client (e.g., the
# a2a_server hosting multiple agents).
_CLIENTS: dict[str, MongoClient] = {}
_sm: secretmanager.SecretManagerServiceClient | None = None


def use_secret(secret_name: str) -> None:
    """Set the *process-wide default* mongo secret.

    Safe for single-role processes (cron workers, the seed_demo script). In
    a multi-agent process, last-import-wins, which is why every helper also
    accepts an explicit ``secret_name`` kwarg — pass it at the callsites that
    must enforce a specific role (see shared/rubrics.py for an example).
    """
    global _DEFAULT_SECRET
    _DEFAULT_SECRET = secret_name


def _resolve(secret_name: str | None) -> str:
    return secret_name or _DEFAULT_SECRET


def _client(secret_name: str | None = None) -> MongoClient:
    name = _resolve(secret_name)
    if name not in _CLIENTS:
        # Local-dev override: if MONGO_URI_DIRECT is set, bypass Secret
        # Manager entirely and connect to that URI. Every secret_name
        # resolves to the same local client — good enough for laptop
        # development where readonly/writer separation isn't enforced.
        direct = os.environ.get("MONGO_URI_DIRECT")
        if direct:
            _CLIENTS[name] = MongoClient(direct)
            return _CLIENTS[name]
        # Production path — lazy-import Secret Manager so local dev
        # doesn't need google-cloud-secret-manager installed.
        from google.cloud import secretmanager
        global _sm
        if _sm is None:
            _sm = secretmanager.SecretManagerServiceClient()
        path = f"projects/{PROJECT_ID}/secrets/{name}/versions/latest"
        uri = _sm.access_secret_version(name=path).payload.data.decode()
        _CLIENTS[name] = MongoClient(uri)
    return _CLIENTS[name]


def db(secret_name: str | None = None):
    return _client(secret_name)[DB_NAME]


def find(collection: str, query: dict, limit: int = 10,
         secret_name: str | None = None) -> list[dict]:
    return list(db(secret_name)[collection].find(query).limit(limit))


def find_sorted(collection: str, query: dict, sort: list[tuple[str, int]],
                limit: int = 10, secret_name: str | None = None) -> list[dict]:
    """find() with an explicit sort. Used for time-sensitive lookups —
    e.g., 'most recent N negatives' for rubric grounding, where a just-added
    negative must appear at the top of results. The default find() does not
    guarantee any particular order.
    """
    return list(db(secret_name)[collection].find(query).sort(sort).limit(limit))


def find_one(collection: str, query: dict,
             secret_name: str | None = None) -> dict | None:
    return db(secret_name)[collection].find_one(query)


def insert_many(collection: str, docs: list[dict],
                secret_name: str | None = None) -> list:
    if not docs:
        return []
    return db(secret_name)[collection].insert_many(docs).inserted_ids


def upsert(collection: str, key: dict, update: dict,
           secret_name: str | None = None) -> None:
    db(secret_name)[collection].update_one(key, {"$set": update}, upsert=True)


def push(collection: str, key: dict, field: str, value: Any,
         secret_name: str | None = None) -> None:
    db(secret_name)[collection].update_one(key, {"$push": {field: value}})


def transition_experiment_state(experiment_id: str, new_state: str,
                                 result: dict | None = None,
                                 lesson: str | None = None,
                                 secret_name: str | None = None,
                                 actor_id: str = "system") -> None:
    """Move an experiment to a new state, recording the pre-image in
    history.experiments so the audit trail survives. Use this — never poke
    the experiments collection directly — so 'why did this experiment get
    decided?' is always recoverable.
    """
    from datetime import datetime

    from mongo.history import update_with_history

    update: dict[str, Any] = {"state": new_state}
    if new_state == "decided":
        update["decided_at"] = datetime.utcnow()
        if result:
            update["result"] = result
        if lesson:
            update["lesson"] = lesson
    update_with_history(
        "experiments",
        {"_id": experiment_id},
        {"$set": update},
        actor_id=actor_id,
        change_kind=f"state_transition_to_{new_state}",
    )
