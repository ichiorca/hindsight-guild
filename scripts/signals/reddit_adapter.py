"""Reddit source adapter (public JSON).

Polls https://www.reddit.com/r/<subreddit>/new.json — Reddit's
unauthenticated public surface for new posts. Cursor is the
``created_utc`` (Unix-seconds) of the newest event we wrote.

Free for read; Reddit's ToS requires a meaningful ``User-Agent``
header. Their published rate limit for OAuth-less requests is
60 req/min — comfortably above our 30-min poll cadence.

If our IP gets rate-limited (sustained 429s) the watcher will skip
this source for the tick and try again on the next interval. No
backoff/retry inside the adapter — the watcher handles that.

API note: the legacy ``.json`` surface returns up to 100 listings per
request. We cap at 50 to match HN's behavior.
"""
from __future__ import annotations

import logging

import httpx

log = logging.getLogger(__name__)

_BASE_FMT = "https://www.reddit.com/r/{subreddit}/new.json"
_TIMEOUT = 15.0
_MAX_LISTINGS_PER_POLL = 50
_USER_AGENT = "auto-gtm-signals/0.1 (+https://github.com/)"


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

    url = _BASE_FMT.format(subreddit=subreddit)
    params = {"limit": _MAX_LISTINGS_PER_POLL}

    try:
        r = httpx.get(
            url,
            params=params,
            headers={"User-Agent": _USER_AGENT},
            timeout=_TIMEOUT,
        )
    except httpx.HTTPError as e:
        log.warning("reddit adapter HTTP error: %s", e)
        return []
    if r.status_code == 429:
        log.warning("reddit adapter rate-limited (429); skipping tick")
        return []
    if r.status_code != 200:
        log.warning("reddit adapter HTTP %s: %s", r.status_code, r.text[:200])
        return []

    listings = ((r.json() or {}).get("data") or {}).get("children") or []
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
