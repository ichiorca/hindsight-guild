"""Hacker News source adapter (Algolia search API).

Polls https://hn.algolia.com/api/v1/search_by_date with the configured
query string. Cursor is the ``created_at_i`` (Unix-seconds) of the
newest event we wrote — the next poll asks for ``numericFilters=
created_at_i>{cursor}``.

Free, no auth, no rate limit at reasonable polling intervals
(the 30-min default in signal_sources is comfortably below Algolia's
informal ~10K-req/day-per-IP soft cap).

API reference: https://hn.algolia.com/api
"""
from __future__ import annotations

import logging

import httpx

log = logging.getLogger(__name__)

_BASE = "https://hn.algolia.com/api/v1/search_by_date"
_TIMEOUT = 15.0
_MAX_HITS_PER_POLL = 50    # Algolia's default page is 20; cap our pull.


def poll(source_doc: dict, mongo_db=None) -> list[dict]:
    """Pull new HN stories + comments matching the configured query.

    Returns a list of normalized event dicts::

        {"source": "hn",
         "evidence_url": "https://news.ycombinator.com/item?id=…",
         "evidence_excerpt": "<title or comment text, ≤ 400 chars>",
         "created_at_i": int,
         "raw": {…full Algolia hit…}}

    Updates ``source_doc['cursor']`` in place with the newest
    ``created_at_i`` seen this tick — the watcher persists it.

    ``mongo_db`` is accepted for interface uniformity with reddit /
    rss adapters; HN doesn't need it.
    """
    cfg = source_doc.get("config") or {}
    query = (cfg.get("query") or "").strip()
    if not query:
        log.warning("hn adapter: source %s has no config.query — skipping",
                    source_doc.get("name"))
        return []
    min_points = int(cfg.get("min_points") or 0)
    cursor = source_doc.get("cursor")

    params = {
        "query": query,
        "tags": "(story,comment)",
        "hitsPerPage": _MAX_HITS_PER_POLL,
    }
    if cursor:
        params["numericFilters"] = f"created_at_i>{int(cursor)}"

    try:
        r = httpx.get(_BASE, params=params, timeout=_TIMEOUT)
    except httpx.HTTPError as e:
        log.warning("hn adapter HTTP error: %s", e)
        return []
    if r.status_code != 200:
        log.warning("hn adapter HTTP %s: %s", r.status_code, r.text[:200])
        return []

    hits = (r.json() or {}).get("hits") or []
    events: list[dict] = []
    newest_cursor: int | None = None

    for hit in hits:
        # min_points filter on stories (comments have null points).
        points = hit.get("points")
        if points is not None and min_points and points < min_points:
            continue

        story_id = hit.get("objectID")
        if not story_id:
            continue
        evidence_url = f"https://news.ycombinator.com/item?id={story_id}"

        # Stories use ``title``; comments use ``comment_text`` (HTML-escaped).
        excerpt = (hit.get("title")
                   or _strip_html(hit.get("comment_text") or "")
                   or hit.get("story_title")
                   or "")
        excerpt = excerpt[:400]

        created_at_i = int(hit.get("created_at_i") or 0)
        if created_at_i:
            if newest_cursor is None or created_at_i > newest_cursor:
                newest_cursor = created_at_i

        events.append({
            "source": "hn",
            "evidence_url": evidence_url,
            "evidence_excerpt": excerpt,
            "created_at_i": created_at_i,
            "raw": hit,
        })

    if newest_cursor:
        source_doc["cursor"] = newest_cursor

    return events


def base_score(raw: dict) -> float:
    """HN base recipe from PRD-02 §6::

        0.4 + 0.05 * min(num_comments, 20) + 0.01 * min(points, 30)
        capped at 1.0
    """
    n_comments = int(raw.get("num_comments") or 0)
    points = int(raw.get("points") or 0)
    score = 0.4 + 0.05 * min(n_comments, 20) + 0.01 * min(points, 30)
    return round(min(1.0, score), 3)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _strip_html(s: str) -> str:
    """HN's Algolia API returns comment_text as escaped HTML
    (``<p>foo</p>`` shape). Strip tags so the queue tooltip reads
    cleanly. We intentionally don't decode HTML entities; the
    evidence_excerpt is for display only, not for indexing.
    Collapse multi-space runs that the tag-strip leaves behind."""
    import re
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s)).strip()
