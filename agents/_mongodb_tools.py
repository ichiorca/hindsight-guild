"""pymongo-backed FunctionTools — the write surface, an Atlas vector-search
tool, and a full-surface local-dev fallback that complement the MongoDB MCP
server.

How this fits with MCP (see ``agents/_mcp.py``):
  The MongoDB **MCP server** is the default data-access path for every
  agent's READ/reasoning calls (``find``, ``aggregate``, ``count``,
  ``collection-schema``, …); it's launched ``--readOnly``. Two capabilities
  stay here as pymongo FunctionTools on purpose:

    1. **The write surface** (``mongodb_insert_one`` / ``_insert_many`` /
       ``_update_one``). Writes to canonical state collections MUST go
       through ``mongo.history`` so every mutation captures a pre-image in
       ``history.<coll>`` and every doc carries a ``_provenance`` block. The
       raw MCP write tools bypass that audit/time-travel layer, so writes
       stay on this provenance-capturing path even when MCP handles reads.

    2. **Vector search** (``mongodb_vector_search``). It runs an Atlas
       ``$vectorSearch`` with **Automated Embedding** — Atlas embeds both the
       indexed ``text`` field (at insert) and the query string (at query
       time) with a managed Voyage AI model. No client-side embedding, no
       ``VOYAGE_API_KEY``: text in, results out. See ``mongo/schema.py`` for
       the ``autoEmbed`` index definition.

Fallback:
  When the MCP server can't be launched (e.g. no Node on a dev laptop),
  ``agents/_mcp.py`` calls ``make_mongodb_tools`` here, which exposes the
  full pymongo read+vector+write surface under the same ``mongodb_*`` tool
  NAMES the agent prompts reference — so local runs behave identically minus
  the live MCP subprocess.

Why MCP was previously disabled (now fixed):
  ``mongodb-mcp-server`` emits tool schemas using JSON Schema 2020-12
  features (``const``, ``propertyNames``, deeply-nested ``oneOf``) that ADK's
  tool-schema validator rejected with ``extra_forbidden``. ``agents/_mcp.py``
  now sanitizes each MCP tool's ``inputSchema`` to Draft-7 before ADK
  converts it, so the live MCP toolset loads cleanly.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import UTC, datetime
from typing import Any, Literal

from bson import Decimal128, ObjectId
from google.adk.tools import FunctionTool
from google.genai import types

from shared import mongo_tools

log = logging.getLogger(__name__)

# Default page size — bounded so a careless agent can't pull the whole
# collection by accident.
_DEFAULT_LIMIT = 25
_MAX_LIMIT = 200

# The text field that Atlas Automated Embedding indexes on customer_voice
# (matches the autoEmbed `path` in mongo/schema.py). Atlas embeds this field
# at insert time and the query string at query time — no client-side vectors.
_AUTOEMBED_PATH = "text"

# Collections that legitimately bypass history capture:
#   - Event log mirrors (actions, outcomes) are append-only by contract;
#     their "provenance" is their telemetry_id + ts.
#   - skill_usage is append-only Tier-2/3 load telemetry.
#   - attribution_map is keyed on telemetry_id (unique); rewriting it on
#     re-publish is the documented behavior.
#   - paid_thresholds is config tweaked via the UI, not state mutation.
# Plus history.<coll> writes never recurse, and derived.<coll> writes are
# full-replace cron jobs that shouldn't try to history-capture either.
_APPEND_ONLY_OR_EXEMPT = {
    "actions", "outcomes", "skill_usage",
    "attribution_map", "paid_thresholds",
}


def _is_canonical(collection: str) -> bool:
    """True if writes to this collection MUST go through mongo/history.py."""
    if collection in _APPEND_ONLY_OR_EXEMPT:
        return False
    if collection.startswith("history.") or collection.startswith("derived."):
        return False
    return True


# Bare top-level keys the LLM emits when it's fumbling for ``$unset`` —
# ``_unset``, ``_unset_malformed``, ``_unset_another``, etc. Folding these
# into ``$set`` (the old behavior) wrote literal junk fields onto the
# canonical skill doc, so they are dropped instead. Observed empirically in
# the Self-Critique agent's tool calls (see tests/e2e/_probe_persist.py).
_PSEUDO_UNSET = re.compile(r"^_unset")


def _demangle_key(key: str) -> str:
    """Undo the quote/bracket wrapping LLMs put around dotted Mongo paths.

    Models frequently emit a ``$set`` key as the *string literal*
    ``'"versions.v2"'``, ``'["versions.v2"]'``, or ``'`versions.v2`'``
    (markdown backticks) instead of the bare field path ``versions.v2``.
    Mongo then creates a single top-level field whose name includes the
    quotes/brackets rather than the intended nested path, so the write
    silently lands in the wrong place. Strip the wrapping.
    """
    k = key.strip()
    m = re.fullmatch(r"\[\s*(['\"`])(.+?)\1\s*\]", k)  # ["a.b"] / ['a.b'] / [`a.b`]
    if m:
        return m.group(2)
    m = re.fullmatch(r"(['\"`])(.+?)\1", k)            # "a.b" / 'a.b' / `a.b`
    if m:
        return m.group(2)
    return k


def _clean_field_map(fields: dict) -> dict:
    """De-mangle every key in a ``$set`` / ``$unset`` field map."""
    if not isinstance(fields, dict):
        return fields
    return {_demangle_key(k): v for k, v in fields.items()}


def _resolve_path_conflicts(field_map: dict) -> dict:
    """Drop a parent path when a more specific dotted child is also present.

    Mongo aborts an update that targets both ``versions`` and
    ``versions.v2`` ("Updating the path 'versions' would create a conflict
    at 'versions'"). The LLM sometimes emits both — a full-object replace
    *and* the dotted add. The dotted child is the intended targeted write
    (and it preserves sibling versions like ``v1`` that promotion's diff
    needs), so keep the child and drop the broad parent instead of letting
    Mongo crash the whole agent run.
    """
    if not isinstance(field_map, dict):
        return field_map
    keys = list(field_map)
    drop = {
        k for k in keys
        if any(other != k and other.startswith(k + ".") for other in keys)
    }
    return {k: v for k, v in field_map.items() if k not in drop}


def _normalize_to_operator_update(update: dict) -> dict:
    """Coerce an LLM-supplied update into a well-formed operator update.

    LLM tool calls arrive in three shapes; all must end up valid:
      - all-operator   ``{"$set": {...}, "$push": {...}}``     → unchanged
      - all-bare       ``{"current_version": "v2"}``           → ``{"$set": {...}}``
      - MIXED          ``{"$set": {...}, "self_critique_proposal": {...}}``
        → the bare ``self_critique_proposal`` key is NOT a ``$``-operator, so
          Mongo rejects the whole update with "Unknown modifier:
          self_critique_proposal". Fold every bare top-level key into ``$set``
          (merging with any existing ``$set``).

    The mixed case is the one that bit the weekly Self-Critique agent: its
    prompt shows both a ``$set: {…}`` block and a sibling
    ``self_critique_proposal: {…}`` and the model occasionally emits them as
    two top-level keys.

    On top of shape-coercion this also repairs the three ways the LLM
    *corrupts* the operands (all observed in real Self-Critique tool calls):
      1. ``$set`` keys wrapped as string/array literals → de-mangled.
      2. hallucinated bare ``_unset*`` pseudo-operators → dropped (so they
         don't get written as junk fields).
      3. a parent path colliding with a dotted child (``versions`` +
         ``versions.v2``) → the parent is dropped so Mongo doesn't abort.
    """
    if not isinstance(update, dict) or not update:
        return update

    operators = {k: v for k, v in update.items() if k.startswith("$")}
    bare = {
        k: v for k, v in update.items()
        if not k.startswith("$") and not _PSEUDO_UNSET.match(k)
    }

    if bare:
        # Mixed / all-bare: merge bare keys into $set without dropping other
        # operators.
        operators["$set"] = {**operators.get("$set", {}), **bare}

    # Repair the operand maps of the field-level operators.
    for op in ("$set", "$unset"):
        if isinstance(operators.get(op), dict):
            operators[op] = _resolve_path_conflicts(_clean_field_map(operators[op]))

    # If cleaning emptied everything (e.g. the update was nothing but
    # pseudo-_unset junk), fall back to the original so the caller's own
    # zero-match / error handling runs rather than us inventing a write.
    return operators or update


def _upsert_synth_doc(filter: dict, update: dict) -> dict:
    """Synthesize a fresh canonical doc from a ``filter`` + ``$set`` payload.

    Used when ``mongodb_update_one(..., upsert=True)`` targeted a
    nonexistent _id on a canonical collection and we have to route to
    ``insert_with_provenance`` instead. Excludes operator-style filter
    conditions (``{"_id": {"$regex": ...}}``) since those can't be valid
    doc fields.
    """
    synth = {k: v for k, v in filter.items() if not isinstance(v, dict)}
    set_payload = update.get("$set", {}) if isinstance(update, dict) else {}
    if isinstance(set_payload, dict):
        synth.update(set_payload)
    return synth


def _bounded(n: int | None, default: int = _DEFAULT_LIMIT) -> int:
    if n is None or n <= 0:
        return default
    return min(int(n), _MAX_LIMIT)


def _strip_additional_properties(schema: types.Schema | None) -> None:
    """Recursively clear ``additional_properties`` from a genai Schema.

    google-genai 1.75 strips ``additional_properties`` from a *top-level*
    object schema but NOT from object schemas nested inside ``any_of`` — the
    exact shape an ``Optional[dict]`` parameter (``filter_expr: dict | None``)
    produces. Gemini's function-declaration validator then rejects the
    leftover field:

        400 INVALID_ARGUMENT: Unknown name "additional_properties" at
        '...function_declarations[N].parameters.properties[M].value.any_of[0]'

    Clearing it everywhere is semantically a no-op (a dict param is a
    free-form object with or without the flag) and makes the declaration
    Gemini-safe regardless of how deeply the union is nested.
    """
    if schema is None:
        return
    schema.additional_properties = None
    for branch in schema.any_of or []:
        _strip_additional_properties(branch)
    for prop in (schema.properties or {}).values():
        _strip_additional_properties(prop)
    _strip_additional_properties(schema.items)


class _GeminiSafeFunctionTool(FunctionTool):
    """``FunctionTool`` whose declaration is sanitized for the Gemini API.

    Behaves exactly like ``FunctionTool`` except it scrubs
    ``additional_properties`` out of the generated parameter schema (see
    ``_strip_additional_properties``). Needed because
    ``mongodb_vector_search``'s ``filter_expr: dict | None`` param otherwise
    serializes to an ``any_of`` branch Gemini's tool validator rejects. The
    override is the supported extension point — ``_get_declaration`` is
    rebuilt fresh on every call, so mutating its return value is safe.
    """

    def _get_declaration(self) -> types.FunctionDeclaration | None:
        decl = super()._get_declaration()
        if decl is not None:
            _strip_additional_properties(decl.parameters)
        return decl


def make_mongodb_tools(mode: Literal["read", "write"], *,
                       agent_name: str = "agent") -> list[FunctionTool]:
    """Return mongodb_* FunctionTools scoped to the appropriate Atlas user.

    Read mode exposes: find, find_one, aggregate, vector_search.
    Write mode exposes: all the above + insert_one, insert_many, update_one.

    Writes to canonical state collections (anything except the event log /
    append-only set listed in ``_APPEND_ONLY_OR_EXEMPT``) are AUTOMATICALLY
    routed through ``mongo.history.insert_with_provenance`` /
    ``update_with_history`` so every mutation captures a pre-image in
    ``history.<coll>`` and every inserted doc carries a ``_provenance`` /
    ``_workspace`` / ``_owner`` block. The shim synthesizes those if the
    LLM didn't supply them, keyed on ``agent_name`` (the calling agent's
    identity) so the audit trail names who wrote what.
    """
    secret_name = "mongo_uri_readonly" if mode == "read" else "mongo_uri_writer"

    def mongodb_find(collection: str, query: dict, limit: int = 10) -> list[dict]:
        """Find documents in a Mongo collection matching the query filter.

        Args:
            collection: Collection name. Examples: "customer_voice",
                "messaging_library", "negative_examples", "skills",
                "approvals", "attribution_map".
            query: MongoDB query filter. Standard operators allowed
                (``$gte``, ``$in``, ``$regex``, etc.). Empty ``{}`` returns
                arbitrary documents — prefer narrowing by icp_segment,
                channel, theme, or status.
            limit: Max documents to return (default 10, max 200).

        Returns:
            List of matching documents. ``_id`` is included as a string.
        """
        rows = mongo_tools.find(collection, query, _bounded(limit, 10),
                                 secret_name=secret_name)
        return [_stringify_id(r) for r in rows]

    def mongodb_find_one(collection: str, query: dict) -> dict | None:
        """Find a single document matching the query. Returns None if no match.

        Args:
            collection: Collection name.
            query: MongoDB filter (e.g. ``{"_id": "linkedin_post"}``).
        """
        row = mongo_tools.find_one(collection, query, secret_name=secret_name)
        return _stringify_id(row) if row else None

    def mongodb_aggregate(collection: str, pipeline: list[dict],
                          limit: int = 50) -> list[dict]:
        """Run an aggregation pipeline. Use for grouped counts, distinct
        values, lookups across collections, or rolling stats.

        Args:
            collection: Collection name.
            pipeline: List of aggregation stages. The function appends a
                ``$limit`` stage with the limit kwarg, so the agent doesn't
                have to add one explicitly.
            limit: Max documents in the result (default 50, max 200).
        """
        db = mongo_tools.db(secret_name)
        capped = list(pipeline) + [{"$limit": _bounded(limit, 50)}]
        return [_stringify_id(r) for r in db[collection].aggregate(capped)]

    def mongodb_vector_search(collection: str, query_text: str,
                               top_k: int = 5,
                               filter_expr: dict | None = None) -> list[dict]:
        """Semantic-search a collection by meaning, not keywords.

        Runs an Atlas ``$vectorSearch`` with **Automated Embedding**: Atlas
        embeds ``query_text`` server-side with the same managed Voyage AI
        model that embedded the indexed ``text`` field at insert time. No
        client-side embedding and no Voyage API key — you pass
        natural-language text, Atlas returns the nearest documents.

        If the auto-embed index isn't queryable yet (still building, or the
        Automated Embedding preview isn't enabled on the cluster tier), this
        falls back to a plain field-filter ``find()`` so the call still
        returns useful rows.

        Args:
            collection: Typically "customer_voice".
            query_text: The query in plain English.
            top_k: How many results to return (default 5, max 20).
            filter_expr: Optional pre-filter (e.g.
                ``{"icp_segment": "seg_merchant_dtc"}``). Filtered fields must
                be declared as ``filter`` fields in the autoEmbed index.
        """
        k = max(1, min(int(top_k or 5), 20))
        db = mongo_tools.db(secret_name)
        stage: dict[str, Any] = {
            "$vectorSearch": {
                "index": f"{collection}_vector",
                "path": _AUTOEMBED_PATH,
                # Automated Embedding: pass text, not a queryVector. Atlas
                # embeds it with the index's Voyage model at query time.
                "query": query_text,
                "numCandidates": max(k * 10, 50),
                "limit": k,
            },
        }
        if filter_expr:
            stage["$vectorSearch"]["filter"] = filter_expr
        try:
            return [_stringify_id(r) for r in db[collection].aggregate([stage])]
        except Exception as e:
            log.warning("vector_search via Atlas auto-embed failed (%s); "
                        "falling back to find()", e)
            return mongodb_find(collection, filter_expr or {}, limit=k)

    tools: list[FunctionTool] = [
        _GeminiSafeFunctionTool(func=mongodb_find),
        _GeminiSafeFunctionTool(func=mongodb_find_one),
        _GeminiSafeFunctionTool(func=mongodb_aggregate),
        _GeminiSafeFunctionTool(func=mongodb_vector_search),
    ]

    if mode == "write":
        # Lazy import — keeps the read-only path free of the mongo.history
        # module load (and its insert/update side effects) for agents that
        # never write.
        from mongo.history import DocumentNotFound, insert_with_provenance, update_with_history

        def _synth_provenance(doc: dict) -> dict:
            """Fill _provenance / _workspace / _owner if the LLM omitted them.

            Caller-supplied values are preserved — this only adds what's
            missing. Defaults to trust_tier='inferred' because agent-emitted
            docs are derivative, not founder-verified.
            """
            doc = dict(doc)  # don't mutate the LLM's payload in place
            now = datetime.now(UTC)
            doc.setdefault("_workspace",
                           os.environ.get("MONGO_WORKSPACE", "default"))
            doc.setdefault("_owner", agent_name)
            prov = doc.setdefault("_provenance", {})
            prov.setdefault("kind", "agent")
            prov.setdefault("actor_id", agent_name)
            prov.setdefault("source", {
                "kind": "agent_tool_call",
                "ref_id": None,
                "ref_collection": None,
            })
            prov.setdefault("created_at", now)
            prov.setdefault("updated_at", now)
            prov.setdefault("confidence", 0.7)
            prov.setdefault("evidence_count", 0)
            prov.setdefault("trust_tier", "inferred")
            prov.setdefault("supersedes", [])
            prov.setdefault("ttl", None)
            return doc

        def mongodb_insert_one(collection: str, document: dict) -> str:
            """Insert one document. Returns the inserted ``_id`` as a string.

            On canonical state collections the write is routed through
            ``insert_with_provenance`` — a matching row is appended to
            ``history.<coll>`` and the doc gets a synthesized ``_provenance``
            block if the caller didn't supply one. Append-only event-log
            collections (actions/outcomes/skill_usage/etc.) use raw insert.
            """
            if not _is_canonical(collection):
                db = mongo_tools.db(secret_name)
                result = db[collection].insert_one(document)
                return str(result.inserted_id)
            doc = _synth_provenance(document)
            inserted = insert_with_provenance(
                collection, doc,
                actor_id=agent_name,
                change_kind="agent_insert",
                secret_name=secret_name,
            )
            return str(inserted.get("_id", ""))

        def mongodb_insert_many(collection: str, documents: list[dict]) -> list[str]:
            """Insert multiple documents in one round-trip.

            Canonical collections: iterates ``insert_with_provenance`` per
            doc (so each gets its own history row and synthesized provenance).
            Append-only collections: single-batch ``insert_many``.
            """
            if not documents:
                return []
            if not _is_canonical(collection):
                db = mongo_tools.db(secret_name)
                result = db[collection].insert_many(documents)
                return [str(i) for i in result.inserted_ids]
            ids: list[str] = []
            for d in documents:
                inserted = insert_with_provenance(
                    collection, _synth_provenance(d),
                    actor_id=agent_name,
                    change_kind="agent_insert",
                    secret_name=secret_name,
                )
                ids.append(str(inserted.get("_id", "")))
            return ids

        def mongodb_update_one(collection: str, filter: dict, update: dict,
                                upsert: bool = False) -> dict:
            """Update one document matching ``filter`` with ``update`` ops.

            Behavior depends on the target collection:
              - **Canonical state**: routes through ``update_with_history``,
                which captures the pre-image into ``history.<coll>``. On a
                zero-match with ``upsert=True``, falls back to
                ``insert_with_provenance`` (synthesizing a doc from filter +
                $set).
              - **Append-only / event log**: raw ``update_one`` matching the
                prior shim semantics for ``actions`` / ``outcomes``.

            ``update`` may arrive in either operator form
            (``{"$set": {...}}``) or as a plain dict (``{"current_version":
            "v2"}``); the latter is auto-wrapped in ``$set`` so LLM tool
            calls don't trip pymongo's "update only works with $ operators"
            check.

            Args:
                collection: Collection name.
                filter: MongoDB query selecting the doc to update.
                update: Update operators (``$set``, ``$push``, ``$unset``),
                    or a plain field dict that we'll wrap as ``$set``.
                upsert: Insert if no match (default False).

            Returns:
                ``{"matched_count": N, "modified_count": N, "upserted_id": str | None}``.
            """
            update = _normalize_to_operator_update(update)

            if not _is_canonical(collection):
                # Append-only / event log: raw write, no history capture.
                db = mongo_tools.db(secret_name)
                result = db[collection].update_one(filter, update, upsert=upsert)
                return {
                    "matched_count": result.matched_count,
                    "modified_count": result.modified_count,
                    "upserted_id": (
                        str(result.upserted_id) if result.upserted_id else None
                    ),
                }

            # Canonical path. update_with_history raises DocumentNotFound on
            # zero matches — translate into a match-zero return value so the
            # LLM doesn't have to handle exceptions.
            try:
                update_with_history(
                    collection, filter, update,
                    actor_id=agent_name,
                    change_kind="agent_update",
                    secret_name=secret_name,
                )
                return {"matched_count": 1, "modified_count": 1,
                        "upserted_id": None}
            except DocumentNotFound:
                if not upsert:
                    return {"matched_count": 0, "modified_count": 0,
                            "upserted_id": None}
                # Upsert: synthesize a doc and route through
                # insert_with_provenance so the new doc gets history-captured
                # too.
                inserted = insert_with_provenance(
                    collection,
                    _synth_provenance(_upsert_synth_doc(filter, update)),
                    actor_id=agent_name,
                    change_kind="agent_upsert",
                    secret_name=secret_name,
                )
                return {"matched_count": 0, "modified_count": 0,
                        "upserted_id": str(inserted.get("_id", ""))}

        tools.extend([
            _GeminiSafeFunctionTool(func=mongodb_insert_one),
            _GeminiSafeFunctionTool(func=mongodb_insert_many),
            _GeminiSafeFunctionTool(func=mongodb_update_one),
        ])

    return tools


def _jsonable(value: Any) -> Any:
    """Recursively coerce Mongo/BSON values into JSON-serializable forms.

    ADK passes tool results back to the model as JSON; a raw ``ObjectId`` (or
    ``datetime`` / ``Decimal128`` / bytes) ANYWHERE in a document — not just at
    the top-level ``_id`` — makes that serialization raise
    ``PydanticSerializationError`` and the agent run dies with an empty result.
    (Hit in prod: ``customer_voice.signal_id`` is an ObjectId, surfaced by
    ``mongodb_vector_search`` on signal-triggered drafts.) Walk nested
    dicts/lists and convert the offenders."""
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal128):
        return str(value)
    if isinstance(value, (bytes, bytearray)):
        return None  # don't ship binary blobs back to the model
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    return value


def _stringify_id(doc: dict | None) -> dict | None:
    """JSON-safe a Mongo document for return to the LLM. Recursively converts
    ObjectId / datetime / Decimal128 / bytes, including nested and non-``_id``
    fields (named for history; it does more than ``_id`` now)."""
    if not doc:
        return doc
    return _jsonable(doc)


# ---------------------------------------------------------------------------
# Subset builders for the MCP-default path (agents/_mcp.py)
#
# When the MongoDB MCP server provides reads, agents still need the
# provenance-capturing writers and the Atlas vector-search tool. We reuse the
# exact closures built by ``make_mongodb_tools`` (so the write/provenance and
# auto-embed logic lives in one place) and select the relevant tools by name,
# avoiding any duplicate ``mongodb_find`` / ``mongodb_aggregate`` that would
# collide with the MCP read tools.
# ---------------------------------------------------------------------------

_WRITE_TOOL_NAMES = ("mongodb_insert_one", "mongodb_insert_many",
                     "mongodb_update_one")


def make_vector_search_tool(mode: Literal["read", "write"]) -> list[FunctionTool]:
    """The Atlas auto-embed ``mongodb_vector_search`` tool, on its own.

    Always scoped to the read-only Atlas user (vector search never writes),
    regardless of the agent's ``mode``. Used alongside the MongoDB MCP read
    tools in ``agents/_mcp.py``.
    """
    _ = mode  # vector search is read-only regardless of the agent's mode
    return [t for t in make_mongodb_tools("read")
            if t.name == "mongodb_vector_search"]


def make_write_tools(*, agent_name: str = "agent") -> list[FunctionTool]:
    """The provenance-capturing write tools (insert_one/many, update_one).

    Scoped to the writer Atlas user. These stay on the pymongo /
    ``mongo.history`` path even when the MCP server handles reads, so every
    canonical mutation keeps its ``history.<coll>`` pre-image + ``_provenance``.
    """
    return [t for t in make_mongodb_tools("write", agent_name=agent_name)
            if t.name in _WRITE_TOOL_NAMES]
