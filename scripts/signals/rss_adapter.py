"""Generic RSS / Atom source adapter.

Polls a feed URL configured per signal_sources doc. Cursor is the
``published`` timestamp (Unix-seconds) of the newest entry we wrote.

We avoid taking on ``feedparser`` as a hard dep — it's a 500K install
with significant transitive deps. The XML format we consume is small
and regular enough that ``xml.etree.ElementTree`` from the stdlib
covers both RSS 2.0 and Atom for our purposes.

Polite-polling: respects ``<ttl>`` on RSS feeds when present (caps our
effective poll interval to MAX(source.poll_interval_sec, ttl_minutes*60)
on the watcher side — see signal_watcher_agent).
"""
from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx

log = logging.getLogger(__name__)

_TIMEOUT = 20.0
_MAX_ENTRIES_PER_POLL = 40
_USER_AGENT = "auto-gtm-signals/0.1 (RSS reader; +https://github.com/)"

# Namespaces used by Atom feeds.
_ATOM_NS = "{http://www.w3.org/2005/Atom}"


def poll(source_doc: dict, mongo_db=None) -> list[dict]:
    """Pull new entries from an RSS / Atom feed.

    Returns a list of normalized event dicts::

        {"source": "rss",
         "evidence_url": "<entry link>",
         "evidence_excerpt": "<title — summary>",
         "published_ts": int (unix seconds),
         "raw": {...}}

    Updates ``source_doc['cursor']`` in place with the newest
    ``published_ts`` seen this tick.

    Detects format (RSS vs Atom) by inspecting the root element.
    ``mongo_db`` kept for interface uniformity (unused).
    """
    cfg = source_doc.get("config") or {}
    feed_url = (cfg.get("feed_url") or "").strip()
    if not feed_url:
        log.warning("rss adapter: source %s has no config.feed_url — skipping",
                    source_doc.get("name"))
        return []
    cursor = source_doc.get("cursor")

    try:
        r = httpx.get(
            feed_url,
            headers={"User-Agent": _USER_AGENT},
            timeout=_TIMEOUT,
        )
    except httpx.HTTPError as e:
        log.warning("rss adapter HTTP error for %s: %s", feed_url, e)
        return []
    if r.status_code != 200:
        log.warning("rss adapter HTTP %s: %s", r.status_code, r.text[:200])
        return []

    try:
        root = ET.fromstring(r.content)
    except ET.ParseError as e:
        log.warning("rss adapter XML parse error for %s: %s", feed_url, e)
        return []

    # RSS 2.0: <rss><channel><item>...</item></channel></rss>
    # Atom: <feed xmlns="..."><entry>...</entry></feed>
    if root.tag.endswith("rss"):
        entries = _parse_rss(root)
    elif root.tag.endswith("feed") or root.tag == f"{_ATOM_NS}feed":
        entries = _parse_atom(root)
    else:
        log.warning("rss adapter: unknown feed root %s at %s",
                    root.tag, feed_url)
        return []

    events: list[dict] = []
    newest_cursor: int | None = None

    for e in entries[:_MAX_ENTRIES_PER_POLL]:
        ts = e["published_ts"]
        if cursor is not None and ts <= int(cursor):
            continue
        if newest_cursor is None or ts > newest_cursor:
            newest_cursor = ts

        title = e["title"]
        summary = e["summary"]
        excerpt = (title + " — " + summary) if summary else title
        excerpt = excerpt[:400]

        events.append({
            "source": "rss",
            "evidence_url": e["link"],
            "evidence_excerpt": excerpt,
            "published_ts": ts,
            "raw": {"title": title, "link": e["link"],
                     "published": ts, "summary": summary,
                     "feed_url": feed_url},
        })

    if newest_cursor:
        source_doc["cursor"] = newest_cursor

    return events


def base_score(raw: dict) -> float:
    """RSS base recipe from PRD-02 §6: flat 0.5. RSS feeds don't expose
    engagement signals — ICP-keyword regex on the title + summary is
    what differentiates one entry from another."""
    return 0.5


# ---------------------------------------------------------------------------
# Format parsers
# ---------------------------------------------------------------------------

def _parse_rss(root: ET.Element) -> list[dict]:
    out: list[dict] = []
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub_str = (item.findtext("pubDate") or "").strip()
        summary = _strip_html(item.findtext("description") or "")

        if not link:
            continue

        ts = _rss_date_to_ts(pub_str)
        out.append({
            "title": title, "link": link, "summary": summary,
            "published_ts": ts,
        })
    return out


def _parse_atom(root: ET.Element) -> list[dict]:
    ns = {"a": "http://www.w3.org/2005/Atom"}
    out: list[dict] = []
    for entry in root.findall("a:entry", ns):
        title = (entry.findtext("a:title", default="", namespaces=ns) or "").strip()
        link_elem = entry.find("a:link", ns)
        link = link_elem.get("href") if link_elem is not None else ""
        pub_str = (entry.findtext("a:published", default="", namespaces=ns)
                   or entry.findtext("a:updated", default="", namespaces=ns)
                   or "").strip()
        summary = _strip_html(
            entry.findtext("a:summary", default="", namespaces=ns)
            or entry.findtext("a:content", default="", namespaces=ns)
            or ""
        )

        if not link:
            continue

        ts = _atom_date_to_ts(pub_str)
        out.append({
            "title": title, "link": link, "summary": summary,
            "published_ts": ts,
        })
    return out


def _rss_date_to_ts(s: str) -> int:
    """RFC 822 — e.g., ``Tue, 27 May 2026 14:00:00 GMT``."""
    if not s:
        return 0
    try:
        dt = parsedate_to_datetime(s)
        if dt is None:
            return 0
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return int(dt.timestamp())
    except Exception:
        return 0


def _atom_date_to_ts(s: str) -> int:
    """RFC 3339 — e.g., ``2026-05-27T14:00:00Z``."""
    if not s:
        return 0
    try:
        # Handle both "Z" and "+00:00" suffixes.
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return int(dt.timestamp())
    except Exception:
        return 0


def _strip_html(s: str) -> str:
    return re.sub(r"<[^>]+>", " ", s or "").strip()
