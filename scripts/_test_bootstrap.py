"""Shared bootstrap for tests/e2e/e2e_*.py and scripts/local_seed.py.

This module deduplicates the ~30-40 lines of env+stdout boilerplate
every e2e script used to carry inline:

  - Loading the repo-root .env into os.environ (existing env wins).
  - Setting LOCAL_DEV / MONGO_URI_DIRECT / MONGO_DB / PROJECT_ID /
    GOOGLE_GENAI_USE_VERTEXAI defaults via os.environ.setdefault.
  - Reconfiguring sys.stdout / sys.stderr to UTF-8 so the unicode
    banners (the check / cross / warn glyphs) survive on Windows
    consoles (default cp1252 codepage).
  - The _banner / _sub / _ok / _fail / _warn / _info print helpers
    every script uses for its console output.

IMPORTANT — IMPORT HAS SIDE EFFECTS (BY DESIGN).

The module body calls ``bootstrap_env()`` at import time. This is
intentional: it lets every e2e script begin with a single line --
``from scripts._test_bootstrap import *`` -- and immediately have its
env populated before any ``from shared import mongo_tools`` or other
import that reads MONGO_URI_DIRECT / PROJECT_ID at module scope.

The side effects are bounded and idempotent:

  - .env is parsed once per process (os.environ.setdefault never
    overwrites already-set vars).
  - The same defaults applied twice produce the same end state.
  - stdout reconfigure is no-op on platforms / streams that don't
    support it.

If you need to opt out (e.g. a test that wants to inspect the raw
environment), import the module attributes directly without the
star import; bootstrap_env() will still run on first import, but
nothing else will be pulled into your module namespace.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

__all__ = [
    "REPO_ROOT",
    "bootstrap_env",
    "_banner",
    "_sub",
    "_ok",
    "_fail",
    "_warn",
    "_info",
]


REPO_ROOT = Path(__file__).resolve().parents[1]

_BOOTSTRAPPED = False


def _load_dotenv(env_path: Path) -> None:
    """Tiny stdlib-only .env loader. Existing env vars take precedence."""
    if not env_path.is_file():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        os.environ.setdefault(key, val)


def bootstrap_env() -> None:
    """Idempotently load .env, set LOCAL_DEV defaults, force UTF-8 stdio.

    Safe to call multiple times. Order matters: this MUST run before any
    import that reads env vars at module scope (e.g.
    ``from shared import mongo_tools``, which reads MONGO_URI_DIRECT).
    """
    global _BOOTSTRAPPED
    if _BOOTSTRAPPED:
        return

    _load_dotenv(REPO_ROOT / ".env")

    os.environ.setdefault("LOCAL_DEV", "1")
    os.environ.setdefault(
        "MONGO_URI_DIRECT",
        os.environ.get("MONGO_URI_DIRECT", "mongodb://localhost:27017"),
    )
    os.environ.setdefault("MONGO_DB", "hindsight_guild")
    os.environ.setdefault("PROJECT_ID", "local-dev")
    os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "0")

    # Force UTF-8 on stdout/stderr so the unicode banners render in
    # subprocess contexts too (Windows default codepage is cp1252 when
    # stdout isn't a TTY, which can't encode the symbols).
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass

    _BOOTSTRAPPED = True


# ---------------------------------------------------------------------------
# Output formatting helpers shared by every e2e script.
#
# The signatures here match the historical inline definitions in
# scripts/e2e_skill_evolution.py exactly. _banner additionally accepts an
# optional `level` kwarg for compatibility with the variant in
# scripts/e2e_experiment_lifecycle.py (level=2 uses dashes instead of
# equals signs).
# ---------------------------------------------------------------------------

def _banner(title: str, *, level: int = 1) -> None:
    bar = "=" * 72 if level == 1 else "-" * 72
    print(f"\n{bar}\n  {title}\n{bar}")


def _sub(title: str) -> None:
    print(f"\n  {title}\n  {'-' * len(title)}")


def _ok(msg: str) -> None:
    print(f"  ✓ {msg}")


def _fail(msg: str) -> None:
    print(f"  ✗ {msg}", file=sys.stderr)


def _warn(msg: str) -> None:
    print(f"  ⚠ {msg}")


def _info(msg: str) -> None:
    print(f"    {msg}")


# Run on import so `from scripts._test_bootstrap import *` is the only
# line a test script needs before it imports shared / agents / services.
bootstrap_env()
