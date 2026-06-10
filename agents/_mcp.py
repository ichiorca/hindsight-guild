"""MongoDB MCP toolset factory for ADK agents.

The MongoDB **MCP server** is the DEFAULT data-access path for agents. This
module wires the server (configured in ``mongo/mcp_server.py``) into an ADK
``McpToolset`` and hands agents the MCP read tools (``find``, ``aggregate``,
``count``, ``collection-schema``, …) under a ``mongodb_`` name prefix. The
server is always launched ``--readOnly``: canonical **writes** stay on the
provenance-capturing pymongo path (``agents/_mongodb_tools`` →
``mongo.history``) so the ``history.<coll>`` / ``_provenance`` audit trail is
never bypassed. Semantic search is the Atlas auto-embed ``mongodb_vector_search``
tool (also pymongo, read-only). So an agent's tool list is:

    read  mode → [ MCP read tools , mongodb_vector_search ]
    write mode → [ MCP read tools , mongodb_vector_search , <provenance writers> ]

Content + Review use the read-only Atlas user; Research, CMO Planner, and
workers use the writer for their writes. Atlas enforces roles server-side.

Schema sanitization (why MCP works now):
  ``mongodb-mcp-server`` emits tool ``inputSchema``\\s using JSON Schema
  2020-12 features (``const``, ``propertyNames``, ``$schema``,
  ``additionalProperties``, deeply-nested ``oneOf``) that ADK's tool-schema
  converter previously rejected with ``extra_forbidden``. ``_SanitizedMcpToolset``
  rewrites each tool's schema to Draft-7 (the subset ADK + Gemini accept)
  before ADK builds the function declaration. This mirrors the
  ``_GeminiSafeFunctionTool`` scrub already used on the pymongo tools.

Opting out:
  Set ``MONGODB_USE_MCP=0`` (or ``false``/``no``) to force the full pymongo
  tool surface instead of MCP. The MCP path also falls back to pymongo
  automatically if the server can't be launched (e.g. no Node on a laptop),
  so local pipeline runs keep working with no extra config.

Opting IN hard (demo recording):
  Set ``MONGODB_REQUIRE_MCP=1`` to forbid the silent fallback — if the MCP
  server can't launch, agent construction raises instead of quietly serving
  reads over pymongo. Use with ``scripts/preflight_demo.py``.

Binary resolution: by default we invoke the globally-installed
``mongodb-mcp-server`` entry script via ``node`` directly. This avoids the
npx cache fragility that bit Node 25 + mongodb-redact (the
``.esm-wrapper.mjs`` missing-file error). Production deploys install the
package globally via the agent Dockerfile; local devs run
``npm install -g mongodb-mcp-server`` once. To override (e.g. fall back to
npx, or pin a version), set ``MONGODB_MCP_COMMAND=npx`` (+ optional
``MONGODB_MCP_VERSION``).
"""
from __future__ import annotations

import logging
import os
import shutil
import sys
from typing import Any, Literal

from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset
from mcp import StdioServerParameters

from mongo.mcp_server import build_env

log = logging.getLogger(__name__)

# Curated MongoDB MCP read tools exposed to agents. The server is launched
# ``--readOnly`` (which already strips writes/destructive tools), and this
# filter further trims discovery/noise tools so the model sees a tight,
# task-relevant surface. Names match the mongodb-mcp-server tool catalog
# (see mongo/mcp_server.py::MCP_TOOLS["read"]).
_MCP_READ_TOOLS = [
    "find", "aggregate", "count", "collection-schema",
    "collection-indexes", "list-collections",
]

# How long to wait for the MCP stdio server to come up (npx/node cold start
# on first invocation can be slow).
_MCP_TIMEOUT_S = float(os.environ.get("MONGODB_MCP_TIMEOUT_S", "30"))


# ---------------------------------------------------------------------------
# JSON Schema 2020-12 → Draft-7 sanitization
# ---------------------------------------------------------------------------

# Keys we strip outright: either JSON-Schema dialect plumbing or 2020-12
# constructs ADK's schema converter / Gemini's validator don't accept.
_DROP_KEYS = frozenset({
    "$schema", "$id", "$anchor", "$comment", "$vocabulary",
    "$dynamicRef", "$dynamicAnchor",
    "additionalProperties", "unevaluatedProperties", "unevaluatedItems",
    "additionalItems", "propertyNames", "patternProperties",
    "dependentSchemas", "dependentRequired", "dependencies",
    "if", "then", "else", "not",
    "contentMediaType", "contentEncoding", "contentSchema",
    "readOnly", "writeOnly", "deprecated", "examples",
    "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
})

# Keys whose VALUE is a single subschema → recurse into it.
_SUBSCHEMA_KEYS = frozenset({"items", "contains"})
# Keys whose value is a list of subschemas → recurse into each.
_SUBSCHEMA_LIST_KEYS = frozenset({"anyOf", "oneOf", "allOf"})
# Keys whose value is a {name: subschema} map → recurse into each value.
_SUBSCHEMA_MAP_KEYS = frozenset({"properties", "$defs", "definitions"})


def _to_draft7(node: Any) -> Any:
    """Recursively rewrite a JSON Schema into the Draft-7 subset ADK accepts.

    - drops 2020-12 / dialect keys ADK's converter rejects (``extra_forbidden``)
    - converts ``const: X`` → ``enum: [X]`` (preserves the constraint)
    - normalizes union ``type: [...]`` (drops ``"null"``, takes the first
      concrete type) so the schema stays single-typed
    - keeps ``$ref`` / ``$defs`` so ADK's own dereferencer can resolve them

    Pure/structural: returns new containers, never mutates the input.
    """
    if isinstance(node, list):
        return [_to_draft7(x) for x in node]
    if not isinstance(node, dict):
        return node

    out: dict[str, Any] = {}
    for key, value in node.items():
        if key in _DROP_KEYS:
            continue
        if key == "const":
            out["enum"] = [value]
            continue
        if key == "type" and isinstance(value, list):
            concrete = [t for t in value if t != "null"]
            out["type"] = concrete[0] if concrete else "string"
            continue
        if key in _SUBSCHEMA_MAP_KEYS and isinstance(value, dict):
            out[key] = {k: _to_draft7(v) for k, v in value.items()}
            continue
        if key in _SUBSCHEMA_LIST_KEYS and isinstance(value, list):
            out[key] = [_to_draft7(v) for v in value]
            continue
        if key in _SUBSCHEMA_KEYS:
            out[key] = _to_draft7(value)
            continue
        out[key] = _to_draft7(value) if isinstance(value, (dict, list)) else value
    return out


class _SanitizedMcpToolset(McpToolset):
    """``McpToolset`` that Draft-7-sanitizes each tool's ``inputSchema``.

    ADK builds the Gemini function declaration from ``McpTool._mcp_tool.inputSchema``.
    We rewrite that schema (in place on the freshly-fetched tool objects)
    before ADK ever converts it, so the MongoDB MCP server's 2020-12 schemas
    load cleanly instead of failing with ``extra_forbidden``.

    It also PINS each tool's ``database`` parameter to the one database this
    deployment uses (enum with a single value). The MCP server requires
    ``database`` on every call; lighter models (gemini-2.5-flash) sometimes
    omitted it ("Invalid input: expected string, received undefined") or
    invented a name, which surfaced as apology text inside drafts. An enum
    makes the right value the only value any model can emit.
    """

    async def get_tools(self, readonly_context=None):  # type: ignore[override]
        from shared.mongo_tools import DB_NAME
        tools = await super().get_tools(readonly_context)
        for tool in tools:
            raw = getattr(tool, "_mcp_tool", None)
            schema = getattr(raw, "inputSchema", None)
            if isinstance(schema, dict):
                schema = _to_draft7(schema)
                props = schema.get("properties")
                if isinstance(props, dict) and "database" in props:
                    props["database"] = {
                        "type": "string",
                        "enum": [DB_NAME],
                        "description": f"Always '{DB_NAME}' on this deployment.",
                    }
                raw.inputSchema = schema
        return tools


def _resolve_command(mode: Literal["read", "write"]) -> tuple[str, list[str]]:
    override = os.environ.get("MONGODB_MCP_COMMAND")
    if override:
        # Pinned-version mode — e.g. MONGODB_MCP_COMMAND=npx with
        # MONGODB_MCP_VERSION=1.10.0 to pin a known-good release.
        version = os.environ.get("MONGODB_MCP_VERSION", "latest")
        args = ["-y", f"mongodb-mcp-server@{version}"]
        if mode == "read":
            args.append("--readOnly")
        return override, args

    # Default: call `node <entry-script>` directly. We deliberately avoid
    # `shutil.which("mongodb-mcp-server")` on Windows because that returns
    # a .CMD wrapper, and ADK's subprocess launcher can't pipe stdio
    # through .CMD reliably (the agent hangs at MCP handshake).
    node = shutil.which("node")
    if node is None:
        raise RuntimeError(
            "Node.js not found on PATH. Install Node 22 LTS, then\n"
            "    npm install -g mongodb-mcp-server"
        )

    # Find the global mongodb-mcp-server install. On Windows, npm puts it
    # under %APPDATA%\npm\node_modules\... On *nix, $(npm root -g).
    candidates: list[str] = []
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        if appdata:
            candidates.append(os.path.join(
                appdata, "npm", "node_modules",
                "mongodb-mcp-server", "dist", "esm", "index.js",
            ))
    # POSIX paths + manual override
    posix_override = os.environ.get("MONGODB_MCP_ENTRY")
    if posix_override:
        candidates.insert(0, posix_override)
    candidates += [
        "/usr/local/lib/node_modules/mongodb-mcp-server/dist/esm/index.js",
        "/usr/lib/node_modules/mongodb-mcp-server/dist/esm/index.js",
    ]
    entry = next((c for c in candidates if os.path.isfile(c)), None)
    if entry is None:
        raise RuntimeError(
            "mongodb-mcp-server entry script not found.\n"
            "Install it: npm install -g mongodb-mcp-server\n"
            f"Looked in: {candidates}"
        )

    args = [entry]
    if mode == "read":
        args.append("--readOnly")
    return node, args


def _mcp_read_toolset() -> _SanitizedMcpToolset:
    """Build the read-only, schema-sanitized MongoDB MCP toolset.

    Always ``--readOnly`` and scoped to the read-only Atlas user — agents
    read via MCP and write via the provenance path. Raises if the MCP server
    can't be located (caller falls back to pymongo).
    """
    command, args = _resolve_command("read")
    return _SanitizedMcpToolset(
        connection_params=StdioConnectionParams(
            server_params=StdioServerParameters(
                command=command,
                args=args,
                # Merge os.environ so `node` keeps its PATH etc.; build_env
                # layers in MDB_MCP_CONNECTION_STRING (read-only user).
                env={**os.environ, **build_env("read")},
            ),
            timeout=_MCP_TIMEOUT_S,
        ),
        tool_filter=_MCP_READ_TOOLS,
        tool_name_prefix="mongodb",
        errlog=sys.stderr,
    )


def _use_pymongo_fallback() -> bool:
    """True if the caller explicitly opted out of MCP (``MONGODB_USE_MCP=0``)."""
    val = os.environ.get("MONGODB_USE_MCP", "").strip().lower()
    return val in ("0", "false", "no", "off")


def _require_mcp() -> bool:
    """True if silent pymongo fallback is forbidden (``MONGODB_REQUIRE_MCP=1``).

    Set this while recording the demo: a draft that quietly runs on pymongo
    instead of the live MCP server is worse than one that fails loudly,
    because the video's "agents read Atlas via MCP" claim stops being true.
    """
    val = os.environ.get("MONGODB_REQUIRE_MCP", "").strip().lower()
    return val in ("1", "true", "yes", "on")


def _pymongo_tools(mode: Literal["read", "write"], agent_name: str) -> list:
    from agents._mongodb_tools import make_mongodb_tools
    return make_mongodb_tools(mode, agent_name=agent_name)


def mongodb_toolset(mode: Literal["read", "write"], *,
                    agent_name: str = "agent") -> list:
    """Return the MongoDB tools for an ADK ``LlmAgent.tools=[...]``.

    DEFAULT (MCP): the live MongoDB MCP server provides the read surface
    (prefixed ``mongodb_find`` / ``mongodb_aggregate`` / …); the Atlas
    auto-embed ``mongodb_vector_search`` tool is added in both modes; write
    mode additionally gets the provenance-capturing writers
    (``mongodb_insert_one`` / ``_insert_many`` / ``_update_one``).

    FALLBACK (pymongo): if ``MONGODB_USE_MCP=0`` or the MCP server can't be
    launched, return the full pymongo tool surface from ``make_mongodb_tools``
    under the same ``mongodb_*`` tool names — agents behave identically minus
    the live MCP subprocess.

    Callers spread the result:
        tools=[*mongodb_toolset(mode="write", agent_name=name), other_tool, ...]
    """
    if _use_pymongo_fallback():
        if _require_mcp():
            raise RuntimeError(
                "MONGODB_USE_MCP=0 conflicts with MONGODB_REQUIRE_MCP=1 — "
                "unset one of them.")
        return _pymongo_tools(mode, agent_name)

    try:
        read_toolset = _mcp_read_toolset()
    except Exception as exc:
        if _require_mcp():
            raise RuntimeError(
                "MongoDB MCP server required (MONGODB_REQUIRE_MCP=1) but it "
                f"could not be launched: {exc}") from exc
        log.warning(
            "MongoDB MCP server unavailable (%s); falling back to pymongo "
            "FunctionTools. Set MONGODB_USE_MCP=0 to silence this.", exc,
        )
        return _pymongo_tools(mode, agent_name)

    from agents._mongodb_tools import make_vector_search_tool, make_write_tools
    tools: list = [read_toolset, *make_vector_search_tool(mode)]
    if mode == "write":
        tools.extend(make_write_tools(agent_name=agent_name))
    return tools
