"""JSON-LD generator for AI-citation grounding.

Emits an ``Article`` blob with embedded ``Person`` (author) and
``Organization`` (publisher) so AI search engines can resolve the
entities. Used by the publish step — Dev.to accepts JSON-LD via its
front-matter; substack server-renders it; LinkedIn UGC doesn't accept
it (LinkedIn grounds via Person URN instead).

Adapted from ``claude-seo/scripts/schema_generate.py`` (MIT). The
upstream module handles 20+ schema types; we emit the 6 active types
listed in ``skills/aeo/references/schema-for-ai.md``. The reduced
surface avoids carrying the upstream's full schema-org type taxonomy
into our drafting pipeline — anything not in those 6 types isn't
useful for AI citation grounding at draft time.

Public API:

    emit_article_jsonld(meta: dict) -> dict
        Returns a JSON-LD-shaped dict (caller json.dumps it).
"""
from __future__ import annotations

from datetime import datetime


def _today_iso() -> str:
    """ISO-8601 date, no time component — matches schema.org's
    ``datePublished`` convention."""
    return datetime.utcnow().date().isoformat()


def emit_article_jsonld(meta: dict) -> dict:
    """Emit a JSON-LD ``Article`` blob with embedded Person + Organization.

    Args:
        meta: A dict with these keys (all optional except ``headline``):
            - ``headline``        (str, required)
            - ``description``     (str)
            - ``url``             (str — canonical URL)
            - ``date_published``  (str, ISO-8601 — defaults to today)
            - ``date_modified``   (str, ISO-8601 — defaults to today)
            - ``author``          (dict with ``name``, ``url``,
                                   ``sameAs`` list)
            - ``publisher``       (dict with ``name``, ``url``, ``logo_url``)
            - ``image``           (dict with ``url`` and ``caption``)
            - ``breadcrumbs``     (list of {name, url}, root → leaf)

    Returns:
        A dict matching the shape of schema.org Article with embedded
        sub-types. Caller serializes via ``json.dumps``.
    """
    headline = (meta.get("headline") or "").strip()
    if not headline:
        raise ValueError("emit_article_jsonld: 'headline' is required")

    date_published = meta.get("date_published") or _today_iso()
    date_modified = meta.get("date_modified") or date_published
    url = (meta.get("url") or "").strip() or None

    article: dict = {
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": headline,
        "datePublished": date_published,
        "dateModified": date_modified,
    }

    description = (meta.get("description") or "").strip()
    if description:
        article["description"] = description

    if url:
        article["mainEntityOfPage"] = {
            "@type": "WebPage",
            "@id": url,
        }

    author = _build_person(meta.get("author") or {})
    if author:
        article["author"] = author

    publisher = _build_organization(meta.get("publisher") or {})
    if publisher:
        article["publisher"] = publisher

    image = _build_image(meta.get("image") or {})
    if image:
        article["image"] = image

    breadcrumbs = _build_breadcrumb_list(meta.get("breadcrumbs") or [])
    if breadcrumbs:
        # BreadcrumbList is a sibling, not nested under the article.
        # Callers that need both should request a graph; we keep it
        # simple by putting it in @graph when present.
        return {
            "@context": "https://schema.org",
            "@graph": [article, breadcrumbs],
        }

    return article


def _build_person(author: dict) -> dict | None:
    """Build a Person sub-blob. Returns None when there isn't enough
    data — we never emit an unattributed author (defeats the purpose
    of entity grounding)."""
    name = (author.get("name") or "").strip()
    if not name:
        return None
    person: dict = {
        "@type": "Person",
        "name": name,
    }
    url = (author.get("url") or "").strip()
    if url:
        person["url"] = url
    same_as = [s for s in (author.get("sameAs") or []) if s]
    if same_as:
        person["sameAs"] = same_as
    return person


def _build_organization(publisher: dict) -> dict | None:
    """Build an Organization sub-blob."""
    name = (publisher.get("name") or "").strip()
    if not name:
        return None
    org: dict = {
        "@type": "Organization",
        "name": name,
    }
    url = (publisher.get("url") or "").strip()
    if url:
        org["url"] = url
    logo_url = (publisher.get("logo_url") or "").strip()
    if logo_url:
        org["logo"] = {
            "@type": "ImageObject",
            "url": logo_url,
        }
    return org


def _build_image(image: dict) -> dict | None:
    """Build an ImageObject blob."""
    url = (image.get("url") or "").strip()
    if not url:
        return None
    img: dict = {
        "@type": "ImageObject",
        "url": url,
    }
    caption = (image.get("caption") or "").strip()
    if caption:
        img["caption"] = caption
    return img


def _build_breadcrumb_list(crumbs: list) -> dict | None:
    """Build a BreadcrumbList blob from an ordered list of (name, url)
    dicts. Returns None when the list is empty."""
    if not crumbs:
        return None
    items = []
    for i, crumb in enumerate(crumbs, start=1):
        name = (crumb.get("name") or "").strip()
        url = (crumb.get("url") or "").strip()
        if not name or not url:
            continue
        items.append({
            "@type": "ListItem",
            "position": i,
            "name": name,
            "item": url,
        })
    if not items:
        return None
    return {
        "@type": "BreadcrumbList",
        "itemListElement": items,
    }
