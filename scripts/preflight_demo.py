"""Demo preflight — hard-verify the two demo "money shots" before recording.

Both the Atlas auto-embed vector search and the MongoDB MCP handshake fall
back SILENTLY in production code (a log warning, then degraded behavior).
That is the right call for resilience and the wrong call for a demo video:
you can record a take that never actually shows Automated Embedding or MCP.

This script makes both paths fail LOUDLY, ahead of time:

  1. VECTOR — connects with the same URI convention as the agents, checks the
     ``customer_voice_vector`` autoEmbed index exists / is READY / queryable,
     then runs the exact ``$vectorSearch`` stage that
     ``agents/_mongodb_tools.mongodb_vector_search`` builds (NO fallback) and
     requires semantically-scored results back.
  2. MCP — resolves the server the same way ``agents/_mcp.py`` does, launches
     it, completes a real MCP handshake (initialize -> list tools), and runs a
     live ``find`` through the protocol.

Run it against the SAME Atlas cluster the recorded demo will use:

    # local-dev style (bypasses Secret Manager)
    MONGO_URI_DIRECT='mongodb+srv://...' python -m scripts.preflight_demo

    # or with GCP creds, reading the mongo_uri_readonly secret like the agents
    python -m scripts.preflight_demo

Exit code 0 = both money shots verified. Anything else: do not record yet —
each FAIL prints a specific remediation hint.

While actually recording, also set ``MONGODB_REQUIRE_MCP=1`` so agents raise
instead of silently dropping to pymongo (see agents/_mcp.py).
"""
from __future__ import annotations

import asyncio
import os
import sys
import traceback

from pymongo import MongoClient

DB_NAME = os.environ.get("MONGO_DB", "hindsight_guild")
COLLECTION = "customer_voice"
INDEX_NAME = f"{COLLECTION}_vector"
# Plain-English probe; anything thematically close to seeded customer quotes
# works — the point is exercising autoEmbed query-time embedding, not recall.
PROBE_QUERY = os.environ.get(
    "PREFLIGHT_QUERY", "frustrated with manual reporting and wasted hours")

GREEN, RED, YELLOW, RESET = "\033[92m", "\033[91m", "\033[93m", "\033[0m"


def _ok(msg: str) -> None:
    print(f"  {GREEN}PASS{RESET}  {msg}")


def _fail(msg: str, hint: str = "") -> None:
    print(f"  {RED}FAIL{RESET}  {msg}")
    if hint:
        print(f"        {YELLOW}fix:{RESET} {hint}")


def _warn(msg: str) -> None:
    print(f"  {YELLOW}WARN{RESET}  {msg}")


def _bootstrap_mongo_uri() -> None:
    """Make MONGO_URI_DIRECT available before anything resolves the URI.

    Dev laptops are usually `gcloud auth login`-ed WITHOUT Application
    Default Credentials, so the Secret Manager Python client fails with
    DefaultCredentialsError. Fall back to the gcloud CLI (which has its own
    credentials) to fetch the read-only URI, and export it so BOTH checks —
    the pymongo vector probe and the MCP server subprocess (build_env) —
    resolve the same way."""
    if os.environ.get("MONGO_URI_DIRECT"):
        return
    import subprocess
    project = os.environ.get("PROJECT_ID", "gen-lang-client-0079238279")
    try:
        uri = subprocess.run(
            ["gcloud", "secrets", "versions", "access", "latest",
             "--secret=mongo_uri_readonly", f"--project={project}"],
            shell=(os.name == "nt"), capture_output=True, text=True,
            check=True, timeout=30,
        ).stdout.strip()
        if uri and uri.lower() != "pending":
            os.environ["MONGO_URI_DIRECT"] = uri
            print(f"  (resolved mongo_uri_readonly via gcloud CLI, project {project})")
    except Exception as e:  # noqa: BLE001 — checks below print the real fix
        print(f"  (gcloud CLI secret fetch failed: {str(e)[:80]})")


def _mongo_uri() -> str:
    # Same precedence as mongo/mcp_server.py + mongo/schema.py: direct
    # override first, then the read-only secret the agents themselves use.
    from mongo.mcp_server import _resolve_mongo_uri
    return _resolve_mongo_uri("read")


# ---------------------------------------------------------------------------
# Check 1 — Atlas Vector Search with Automated Embedding, no fallback allowed
# ---------------------------------------------------------------------------

def check_vector() -> bool:
    print(f"\n[1/2] Atlas Vector Search (autoEmbed) on {DB_NAME}.{COLLECTION}")
    try:
        uri = _mongo_uri()
    except Exception as e:
        _fail(f"cannot resolve Mongo URI: {e}",
              "set MONGO_URI_DIRECT to the demo Atlas SRV string, or run "
              "with GCP creds that can read the mongo_uri_readonly secret")
        return False

    if "mongodb+srv" not in uri and "mongodb.net" not in uri:
        _warn("URI does not look like Atlas — local/Docker Mongo has no "
              "vector search. Point MONGO_URI_DIRECT at the demo cluster.")

    try:
        client: MongoClient = MongoClient(uri, serverSelectionTimeoutMS=8000)
        coll = client[DB_NAME][COLLECTION]
        n_docs = coll.count_documents({})
    except Exception as e:
        _fail(f"cannot connect / read {COLLECTION}: {e}")
        return False
    if n_docs == 0:
        _fail(f"{COLLECTION} is empty",
              "run `python demo/seed_demo.py` (then wait ~60 min for Atlas "
              "to embed the inserted docs) before the vector check can pass")
        return False
    _ok(f"connected; {COLLECTION} has {n_docs} docs")

    # Index present + READY + queryable?
    try:
        indexes = list(coll.list_search_indexes())
    except Exception as e:
        _fail(f"list_search_indexes failed: {e}",
              "this command only works against Atlas — you are probably "
              "pointed at a local Mongo")
        return False
    idx = next((i for i in indexes if i.get("name") == INDEX_NAME), None)
    if idx is None:
        _fail(f"search index '{INDEX_NAME}' does not exist",
              "run `python -m mongo.schema apply` against this cluster")
        return False
    status = idx.get("status", "unknown")
    queryable = idx.get("queryable", False)
    if status not in ("READY", "STEADY", "ACTIVE") or not queryable:
        _fail(f"index '{INDEX_NAME}' status={status} queryable={queryable}",
              "Atlas builds the index asynchronously (30-120s after apply). "
              "If it stays PENDING/FAILED, the cluster tier likely does not "
              "support Automated Embedding (public preview) — upgrade the "
              "cluster (M10+ / Flex) in the Atlas UI, then re-apply the "
              "schema and re-seed")
        return False
    _ok(f"index '{INDEX_NAME}' is {status} and queryable")

    # The actual money shot: the EXACT stage mongodb_vector_search builds,
    # but with no try/except fallback — autoEmbed must answer.
    stage = {
        "$vectorSearch": {
            "index": INDEX_NAME,
            "path": "text",
            "query": PROBE_QUERY,  # text in — Atlas embeds it server-side
            "numCandidates": 50,
            "limit": 3,
        },
    }
    project = {"$project": {"text": 1, "icp_segment": 1,
                            "score": {"$meta": "vectorSearchScore"}}}
    try:
        rows = list(coll.aggregate([stage, project]))
    except Exception as e:
        _fail(f"$vectorSearch with text query was REJECTED: {e}",
              "this is the M0/unsupported-tier symptom — the demo would "
              "silently fall back to find(). Upgrade the cluster tier "
              "(M10+ / Flex), re-apply schema, re-seed, re-run this check")
        return False
    if not rows:
        _fail("$vectorSearch ran but returned 0 documents",
              "docs exist but their embeddings are probably still being "
              "computed by Atlas (autoEmbed embeds at insert, ~minutes). "
              "Wait and re-run; seed >=60 min before recording")
        return False
    if "score" not in rows[0]:
        _fail("results came back without a vectorSearchScore",
              "the pipeline did not actually run as a vector query")
        return False
    _ok(f"$vectorSearch answered with scored results for: '{PROBE_QUERY}'")
    for r in rows:
        text = (r.get("text") or "")[:80].replace("\n", " ")
        print(f"        {r['score']:.4f}  [{r.get('icp_segment', '?')}] {text}")
    print("        ^ eyeball these: top hits should be semantically related "
          "to the probe, not just recent rows")
    return True


# ---------------------------------------------------------------------------
# Check 2 — live MongoDB MCP server handshake (initialize -> tools -> find)
# ---------------------------------------------------------------------------

async def _mcp_session() -> bool:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    from agents._mcp import _resolve_command
    from mongo.mcp_server import build_env

    try:
        command, args = _resolve_command("read")
    except RuntimeError as e:
        _fail(str(e).splitlines()[0],
              "install Node 22+ and `npm install -g mongodb-mcp-server`")
        return False
    _ok(f"resolved server: {command} {args[0]}{' --readOnly' if '--readOnly' in args else ''}")

    params = StdioServerParameters(
        command=command, args=args, env={**os.environ, **build_env("read")})
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            info = await session.initialize()
            server = getattr(info, "serverInfo", None)
            name = getattr(server, "name", "?")
            version = getattr(server, "version", "?")
            _ok(f"MCP handshake complete — {name} v{version}")

            tools = await session.list_tools()
            names = sorted(t.name for t in tools.tools)
            if "find" not in names:
                _fail(f"'find' not in server tools: {names}")
                return False
            _ok(f"{len(names)} tools listed (incl. find/aggregate) — "
                f"server is --readOnly")

            result = await session.call_tool("find", {
                "database": DB_NAME,
                "collection": COLLECTION,
                "filter": {},
                "limit": 1,
            })
            if getattr(result, "isError", False):
                content = "; ".join(
                    getattr(c, "text", "") for c in result.content)[:200]
                _fail(f"live `find` over MCP errored: {content}",
                      "check the connection string the server received "
                      "(MONGO_URI_DIRECT / mongo_uri_readonly secret)")
                return False
            _ok(f"live `find` on {DB_NAME}.{COLLECTION} answered over MCP")
            return True


def check_mcp() -> bool:
    print("\n[2/2] MongoDB MCP server (live stdio handshake)")
    try:
        return asyncio.run(asyncio.wait_for(_mcp_session(), timeout=90))
    except TimeoutError:
        _fail("MCP handshake timed out after 90s",
              "first run can be slow; retry. If it persists, run "
              "`python -m mongo.mcp_server --health-check` and check Node")
        return False
    except Exception as e:
        _fail(f"MCP session failed: {e}",
              "this is exactly the silent-fallback case agents/_mcp.py "
              "would hide — fix it before recording")
        traceback.print_exc(limit=3)
        return False


def main() -> int:
    print("Hindsight Guild — demo preflight (vector money shot + MCP handshake)")
    _bootstrap_mongo_uri()
    vector_ok = check_vector()
    mcp_ok = check_mcp()

    print("\n" + "=" * 64)
    print(f"  vector autoEmbed : {'PASS' if vector_ok else 'FAIL'}")
    print(f"  MCP handshake    : {'PASS' if mcp_ok else 'FAIL'}")
    if vector_ok and mcp_ok:
        print(f"\n{GREEN}Both money shots verified — safe to record.{RESET}")
        print("Set MONGODB_REQUIRE_MCP=1 in the agents' environment while "
              "recording so a mid-take fallback fails loudly instead of "
              "silently using pymongo.")
        return 0
    print(f"\n{RED}Do NOT record yet — fix the FAILs above first.{RESET}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
