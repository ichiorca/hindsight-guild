"""Reddit source adapter — public JSON by default, OAuth when enabled.

Two fetch paths, selected by the ``REDDIT_USE_OAUTH`` flag:

  * OFF (default): the unauthenticated ``https://www.reddit.com/r/<sub>/new.json``
    surface. Free, no creds — but Reddit increasingly rate-limits / blocks
    unauthenticated reads (sustained 429/403), so this often returns nothing.
  * ON  (``REDDIT_USE_OAUTH=1``): authenticated ``https://oauth.reddit.com``
    with a cached bearer token. Reliable, higher rate limit (~60 req/min).

OAuth credentials (env first, then Secret Manager fallback):
    REDDIT_CLIENT_ID         (required)   secret: reddit_client_id
    REDDIT_CLIENT_SECRET     (required)   secret: reddit_client_secret
    REDDIT_USERNAME          (optional)   secret: reddit_username
    REDDIT_PASSWORD          (optional)   secret: reddit_password
    REDDIT_USER_AGENT        (optional)   — descriptive UA Reddit requires

Grant type is chosen automatically: ``password`` (script app) when a
username + password are supplied, else ``client_credentials`` (app-only,
read-only) which needs only the client id + secret. Create an app at
https://www.reddit.com/prefs/apps (type "script" or "web app").

Cursor is the ``created_utc`` of the newest event we wrote. No backoff/retry
inside the adapter — the watcher handles per-tick skipping.
"""
from __future__ import annotations

import logging
import os
import time

import httpx

log = logging.getLogger(__name__)

_PUBLIC_FMT = "https://www.reddit.com/r/{subreddit}/new.json"
_OAUTH_FMT = "https://oauth.reddit.com/r/{subreddit}/new"
_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
_TIMEOUT = 15.0
_MAX_LISTINGS_PER_POLL = 50
_DEFAULT_USER_AGENT = "hindsight-guild/0.1 (signal_watcher; +https://orcaqubits-ai.com)"

# Module-level bearer-token cache: {"token": str|None, "expires_at": epoch}.
_TOKEN_CACHE: dict = {"token": None, "expires_at": 0.0}


# ---------------------------------------------------------------------------
# OAuth helpers
# ---------------------------------------------------------------------------

def _oauth_enabled() -> bool:
    return os.environ.get("REDDIT_USE_OAUTH", "").lower() in ("1", "true", "yes")


def _user_agent() -> str:
    return os.environ.get("REDDIT_USER_AGENT") or _DEFAULT_USER_AGENT


def _cred(env_name: str, secret_name: str) -> str | None:
    """Credential lookup: env var first, then Secret Manager (best-effort).
    Returns None when neither is available (incl. LOCAL_DEV, where
    secret_value raises and we swallow it)."""
    v = os.environ.get(env_name)
    if v:
        return v
    try:
        from shared.clients import secret_value
        return secret_value(secret_name)
    except Exception:
        return None


def reset_token_cache() -> None:
    """Drop the cached bearer token (used by tests)."""
    _TOKEN_CACHE["token"] = None
    _TOKEN_CACHE["expires_at"] = 0.0


def _get_oauth_token() -> str | None:
    """Fetch + cache a Reddit bearer token. Returns None if creds are missing
    or the token request fails (caller then falls back to the public path)."""
    now = time.time()
    if _TOKEN_CACHE["token"] and now < _TOKEN_CACHE["expires_at"] - 60:
        return _TOKEN_CACHE["token"]

    client_id = _cred("REDDIT_CLIENT_ID", "reddit_client_id")
    client_secret = _cred("REDDIT_CLIENT_SECRET", "reddit_client_secret")
    if not (client_id and client_secret):
        log.warning("reddit OAuth enabled but REDDIT_CLIENT_ID/SECRET missing")
        return None

    username = _cred("REDDIT_USERNAME", "reddit_username")
    password = _cred("REDDIT_PASSWORD", "reddit_password")
    if username and password:
        data = {"grant_type": "password", "username": username, "password": password}
    else:
        data = {"grant_type": "client_credentials"}

    try:
        r = httpx.post(
            _TOKEN_URL,
            data=data,
            auth=(client_id, client_secret),
            headers={"User-Agent": _user_agent()},
            timeout=_TIMEOUT,
        )
    except httpx.HTTPError as e:
        log.warning("reddit OAuth token request failed: %s", e)
        return None
    if r.status_code != 200:
        log.warning("reddit OAuth token HTTP %s: %s", r.status_code, r.text[:200])
        return None

    body = r.json() or {}
    token = body.get("access_token")
    if not token:
        log.warning("reddit OAuth token response missing access_token")
        return None
    _TOKEN_CACHE["token"] = token
    _TOKEN_CACHE["expires_at"] = now + float(body.get("expires_in") or 3600)
    return token


def _fetch_listing(subreddit: str, params: dict) -> dict | None:
    """GET the subreddit 'new' listing as parsed JSON. Uses OAuth when enabled
    (with a public-path fallback if the token can't be obtained). Returns the
    body dict, or None on any failure (rate-limit / blocked / network)."""
    headers = {"User-Agent": _user_agent()}
    url = _PUBLIC_FMT.format(subreddit=subreddit)

    if _oauth_enabled():
        token = _get_oauth_token()
        if token:
            url = _OAUTH_FMT.format(subreddit=subreddit)
            headers["Authorization"] = f"bearer {token}"
        else:
            log.warning("reddit: OAuth on but no token — falling back to public JSON")

    try:
        r = httpx.get(url, params=params, headers=headers, timeout=_TIMEOUT)
    except httpx.HTTPError as e:
        log.warning("reddit adapter HTTP error: %s", e)
        return None
    if r.status_code == 429:
        log.warning("reddit adapter rate-limited (429); skipping tick")
        return None
    if r.status_code != 200:
        log.warning("reddit adapter HTTP %s: %s", r.status_code, r.text[:200])
        return None
    return r.json() or {}


# ---------------------------------------------------------------------------
# Adapter contract
# ---------------------------------------------------------------------------

def poll(source_doc: dict, mongo_db=None) -> list[dict]:
    """Pull new posts from a subreddit.

    Returns a list of normalized event dicts::

        {"source": "reddit",
         "evidence_url": "https://reddit.com/r/{sub}/comments/{id}/…",
         "evidence_excerpt": "<title; or title + selftext preview>",
         "created_utc": int,
         "raw": {…full Reddit listing data…}}

    Updates ``source_doc['cursor']`` in place with the newest
    ``created_utc`` seen this tick.

    ``mongo_db`` kept for interface uniformity (unused).
    """
    cfg = source_doc.get("config") or {}
    subreddit = (cfg.get("subreddit") or "").strip().lstrip("r/").lstrip("/")
    if not subreddit:
        log.warning("reddit adapter: source %s has no config.subreddit — skipping",
                    source_doc.get("name"))
        return []
    min_upvotes = int(cfg.get("min_upvotes") or 0)
    cursor = source_doc.get("cursor")

    body = _fetch_listing(subreddit, {"limit": _MAX_LISTINGS_PER_POLL})
    if body is None:
        return []

    listings = (body.get("data") or {}).get("children") or []
    events: list[dict] = []
    newest_cursor: int | None = None

    for entry in listings:
        post = (entry or {}).get("data") or {}
        post_id = post.get("id")
        if not post_id:
            continue

        # Reddit's API marks rate-limited / removed posts with
        # selftext == "[removed]"; we drop those — they have no signal.
        if (post.get("selftext") or "").strip() == "[removed]":
            continue

        created_utc = int(post.get("created_utc") or 0)
        if cursor is not None and created_utc <= int(cursor):
            continue
        if newest_cursor is None or created_utc > newest_cursor:
            newest_cursor = created_utc

        score = int(post.get("score") or 0)
        if min_upvotes and score < min_upvotes:
            continue

        # Build a readable evidence_excerpt: title, plus the first chunk
        # of selftext if present.
        title = (post.get("title") or "").strip()
        selftext = (post.get("selftext") or "").strip()
        excerpt = (title + " — " + selftext) if selftext else title
        excerpt = excerpt[:400]

        # Reddit's permalink is the relative path; prepend the host.
        permalink = post.get("permalink") or f"/r/{subreddit}/comments/{post_id}/"
        evidence_url = "https://www.reddit.com" + permalink

        events.append({
            "source": "reddit",
            "evidence_url": evidence_url,
            "evidence_excerpt": excerpt,
            "created_utc": created_utc,
            "raw": post,
        })

    if newest_cursor:
        source_doc["cursor"] = newest_cursor

    return events


def base_score(raw: dict) -> float:
    """Reddit base recipe from PRD-02 §6::

        0.3 + 0.02 * upvotes + 0.05 * num_comments
        capped at 1.0
    """
    upvotes = int(raw.get("score") or raw.get("ups") or 0)
    num_comments = int(raw.get("num_comments") or 0)
    score = 0.3 + 0.02 * upvotes + 0.05 * num_comments
    return round(min(1.0, score), 3)
