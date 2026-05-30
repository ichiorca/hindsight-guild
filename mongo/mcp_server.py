"""MongoDB MCP server lifecycle + tool catalog reference.

The MongoDB MCP server (`mongodb-mcp-server` npm package) is run as an
in-process stdio subprocess by each agent's MCPToolset. This module holds:

  1. The env-var configuration that the MCP server reads
     (MDB_MCP_CONNECTION_STRING). Embeddings are handled by Atlas Automated
     Embedding server-side, so no client embedding-provider env is set here.
  2. The MCP tool catalog (what's available + per-agent scope)
  3. A local launcher (`python -m mongo.mcp_server`) for testing the MCP
     server outside an agent process — useful for verifying the connection
     before deploying agents.
  4. A health-check that connects, runs a trivial find, exits.

`agents/_mcp.py` is where the production wiring lives (it builds the
MCPToolset that ADK uses). This module is documentation + dev-time tooling.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from typing import Literal

# google.cloud.secretmanager is imported lazily inside _secret() — local-dev
# callers with MONGO_URI_DIRECT set never need the package.


# ---------------------------------------------------------------------------
# MCP tool catalog — what the server exposes
# ---------------------------------------------------------------------------

MCP_TOOLS = {
    "read": [
        "find", "find-one", "aggregate", "count", "distinct",
        "vector-search", "list-collections", "list-indexes", "list-databases",
    ],
    "write": [
        "insert-one", "insert-many", "update-one", "update-many",
        "delete-one", "delete-many",
    ],
    "admin": [
        "create-collection", "drop-collection", "create-search-index",
        "drop-search-index",
        # atlas-* tools — Tool Hub policy denies for hackathon SAs:
        "atlas-list-orgs", "atlas-list-projects", "atlas-create-cluster",
    ],
}


AGENT_SCOPES = {
    "content_agent": MCP_TOOLS["read"],
    "review_agent": MCP_TOOLS["read"],
    "research_agent": MCP_TOOLS["read"] + ["insert-many", "insert-one"],
    "cmo_planner": MCP_TOOLS["read"] + MCP_TOOLS["write"],
    "analytics_agent": [],  # no Mongo access; uses BigQuery only
}


# ---------------------------------------------------------------------------
# Env var configuration for the MCP server subprocess
# ---------------------------------------------------------------------------

PROJECT_ID = os.environ.get("PROJECT_ID", "hindsight-guild-mvp")


def _secret(name: str) -> str:
    # Lazy import — local-dev callers (MONGO_URI_DIRECT set) never hit this.
    from google.cloud import secretmanager
    sm = secretmanager.SecretManagerServiceClient()
    full = f"projects/{PROJECT_ID}/secrets/{name}/versions/latest"
    return sm.access_secret_version(name=full).payload.data.decode()


def _resolve_mongo_uri(mode: Literal["read", "write"]) -> str:
    """Return the MongoDB connection string for the MCP server.

    Local-dev path: MONGO_URI_DIRECT bypasses Secret Manager. Same
    convention as shared/mongo_tools.py and mongo/schema.py so all three
    agree on the URI source.
    """
    direct = os.environ.get("MONGO_URI_DIRECT")
    if direct:
        return direct
    secret = "mongo_uri_readonly" if mode == "read" else "mongo_uri_writer"
    return _secret(secret)


def build_env(mode: Literal["read", "write"]) -> dict:
    """Env vars passed to the MongoDB MCP server subprocess.

    Returned dict goes into StdioServerParameters(env=...). This is the
    canonical configuration — agents/_mcp.py uses this same shape.

    Embeddings are NOT configured here. We use Atlas **Automated Embedding**
    (the ``autoEmbed`` index in ``mongo/schema.py``): Atlas embeds the indexed
    ``text`` field on insert and the query string on ``$vectorSearch`` with a
    managed Voyage AI model, server-side. So there is no client-side Voyage
    key and no ``MDB_MCP_EMBEDDING_*`` provider config to set — the MCP server
    just needs the connection string.
    """
    return {
        "MDB_MCP_CONNECTION_STRING": _resolve_mongo_uri(mode),
    }


def server_args(mode: Literal["read", "write"]) -> list[str]:
    """argv for the MCP server subprocess."""
    args = ["-y", "mongodb-mcp-server@latest"]
    if mode == "read":
        args.append("--readOnly")
    return args


# ---------------------------------------------------------------------------
# Local launcher / health-check
# ---------------------------------------------------------------------------

def launch_local(mode: Literal["read", "write"]) -> None:
    """Run the MCP server on stdio for local manual testing.

    The server speaks the MCP protocol over stdio. To exercise it manually,
    use a tool like `mcp-cli` (`npx @modelcontextprotocol/cli`) and point
    it at the spawned process, or just run an agent locally with `adk run`.
    """
    env = {**os.environ, **build_env(mode)}
    cmd = ["npx", *server_args(mode)]
    print(f"Launching MCP server (mode={mode}): {' '.join(cmd)}", file=sys.stderr)
    subprocess.run(cmd, env=env, check=True)


def health_check() -> bool:
    """Connect via pymongo (same secret the MCP server reads), run a trivial
    query, exit. If this works the MCP server will work too.
    """
    from pymongo import MongoClient
    try:
        uri = _secret("mongo_uri_readonly")
    except Exception as e:
        print(f"FAIL: cannot read mongo_uri_readonly secret: {e}", file=sys.stderr)
        return False
    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=5000)
        names = client[os.environ.get("MONGO_DB", "hindsight_guild")].list_collection_names()
        print(f"OK: connected. Collections: {names}")
        return True
    except Exception as e:
        print(f"FAIL: cannot connect: {e}", file=sys.stderr)
        return False


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--launch", choices=["read", "write"],
                   help="Launch the MCP server on stdio (read or write mode).")
    p.add_argument("--health-check", action="store_true",
                   help="Connect and list collections; exit 0 on success.")
    p.add_argument("--show-config", choices=["read", "write"],
                   help="Print the env config that agents pass to MCPToolset.")
    args = p.parse_args()

    if args.show_config:
        import json
        cfg = {"command": "npx", "args": server_args(args.show_config),
               "env_keys": sorted(build_env(args.show_config).keys())}
        print(json.dumps(cfg, indent=2))
        sys.exit(0)
    if args.health_check:
        sys.exit(0 if health_check() else 1)
    if args.launch:
        launch_local(args.launch)
        sys.exit(0)

    p.print_help()
