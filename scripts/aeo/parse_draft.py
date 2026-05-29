"""Draft structure extractor — markdown-first, HTML-tolerant.

Adapted from ``claude-seo/scripts/parse_html.py`` but rewritten for our
drafting-pipeline use case: the input is the **markdown body** emitted
by ``content_agent`` (occasionally a substack-style JSON envelope or
raw HTML for the LinkedIn UGC path). The upstream module was 700+
lines focused on live-page audit (lazy-load detection, schema parsing,
SEO link inventory) — none of that applies at drafting time.

This module exposes one function:

    extract_structure(text: str) -> dict

returning:

    {
      "h1":            "Title text" | None,
      "h2_list":       ["Section 1", "Section 2", ...],
      "h3_list":       [...],
      "paragraphs":    [{"text": str, "word_count": int, "h2_index": int|None}],
      "word_count":    int,
      "has_lists":     bool,    # at least one markdown bullet / number list
      "has_tables":    bool,    # at least one markdown table
      "has_code_blocks": bool,
      "format":        "markdown" | "html"
    }

``h2_index`` on each paragraph is the index of the H2 the paragraph
falls under (so the AEO scorer can compute self-contained-blocks per
H2). Paragraphs above the first H2 carry ``h2_index: None``.

The HTML path uses BeautifulSoup if installed; otherwise it falls back
to a regex-based H2/paragraph extractor. The upstream lazy-load /
plugin-detection logic was removed — it has no role at drafting time.
"""
from __future__ import annotations

import re

# Markdown headings: leading hashes + space. We tolerate up to 6 # per spec.
_H1_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
_H2_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
_H3_RE = re.compile(r"^###\s+(.+?)\s*$", re.MULTILINE)
# Heading boundary for paragraph segmentation — any H1-H6.
_ANY_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
# Markdown lists: bullet (- * +) OR numbered (1. 2.).
_LIST_RE = re.compile(r"^(?:[\-\*\+]|\d+\.)\s+\S", re.MULTILINE)
# Markdown tables: a line with two or more pipe characters.
_TABLE_RE = re.compile(r"^\s*\|.*\|.*$", re.MULTILINE)
_CODE_FENCE_RE = re.compile(r"^```", re.MULTILINE)

# Token = word-shaped sequence; matches content_quality's token model
# for consistency.
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z'\-]*")


def _looks_like_html(text: str) -> bool:
    """Cheap probe: text starts with a tag or contains common block tags."""
    head = text.lstrip()[:64].lower()
    return head.startswith("<!doctype") or head.startswith("<html") or bool(
        re.search(r"<(h1|h2|h3|p|article|section)\b", text.lower())
    )


def _word_count(text: str) -> int:
    return len(_TOKEN_RE.findall(text))


def _extract_markdown(text: str) -> dict:
    """Markdown-shaped body — the common case from content_agent.

    Paragraph segmentation is two-phase: (1) split on heading lines so
    a "## Heading\\nparagraph" block (no blank line between) doesn't
    drop the paragraph; (2) within each section, split on blank lines.
    """
    h1_match = _H1_RE.search(text)
    h1 = h1_match.group(1).strip() if h1_match else None
    h2_list = [m.group(1).strip() for m in _H2_RE.finditer(text)]
    h3_list = [m.group(1).strip() for m in _H3_RE.finditer(text)]

    # Walk the text line-by-line, tracking the current H2 index. Strip
    # code fences and tables (we don't want their contents counted as
    # prose paragraphs). Group remaining non-blank lines into
    # paragraphs (blank-line separated).
    paragraphs: list[dict] = []
    current_h2_idx: int | None = None
    in_code_fence = False
    buffer: list[str] = []

    def flush() -> None:
        """Emit the accumulated buffer as one paragraph (if any prose)."""
        if not buffer:
            return
        block = "\n".join(buffer).strip()
        if not block:
            buffer.clear()
            return
        # Skip pure-list / pure-table blocks — they're not citable
        # passages on their own.
        if all(
            line.lstrip().startswith(("|", "-", "*", "+")) or
            re.match(r"^\d+\.\s", line.lstrip())
            for line in block.splitlines()
        ):
            buffer.clear()
            return
        paragraphs.append({
            "text": block,
            "word_count": _word_count(block),
            "h2_index": current_h2_idx,
        })
        buffer.clear()

    for line in text.splitlines():
        # Code-fence toggle — never emit fence content as a paragraph.
        if line.startswith("```"):
            flush()
            in_code_fence = not in_code_fence
            continue
        if in_code_fence:
            continue

        heading_match = _ANY_HEADING_RE.match(line)
        if heading_match:
            flush()
            # Track H2 boundary for downstream sub-signal aggregation.
            if heading_match.group(1) == "##":
                # h2_list was built earlier in document order; advancing
                # the index here matches that order.
                current_h2_idx = (current_h2_idx + 1) if current_h2_idx is not None else 0
            continue

        if line.strip() == "":
            flush()
            continue

        buffer.append(line)

    flush()

    return {
        "h1": h1,
        "h2_list": h2_list,
        "h3_list": h3_list,
        "paragraphs": paragraphs,
        "word_count": _word_count(text),
        "has_lists": bool(_LIST_RE.search(text)),
        "has_tables": bool(_TABLE_RE.search(text)),
        "has_code_blocks": bool(_CODE_FENCE_RE.search(text)),
        "format": "markdown",
    }


def _extract_html_bs4(text: str) -> dict | None:
    """Try BeautifulSoup. Returns None if bs4 isn't installed so caller can
    fall back to the regex path. We don't depend on bs4 as a hard
    requirement — the drafting pipeline runs on markdown 99% of the time."""
    try:
        from bs4 import BeautifulSoup  # type: ignore[import-not-found]
    except ImportError:
        return None
    soup = BeautifulSoup(text, "html.parser")
    h1 = soup.h1.get_text(strip=True) if soup.h1 else None
    h2_list = [h.get_text(strip=True) for h in soup.find_all("h2")]
    h3_list = [h.get_text(strip=True) for h in soup.find_all("h3")]

    # Paragraph segmentation by <p> tags; H2 index by document order.
    h2_tags = soup.find_all("h2")
    h2_positions = {id(h): i for i, h in enumerate(h2_tags)}

    paragraphs: list[dict] = []
    for p in soup.find_all("p"):
        body = p.get_text(" ", strip=True)
        if not body:
            continue
        # Find the most recent H2 preceding this paragraph.
        prev_h2 = None
        for h in h2_tags:
            if h.sourceline is not None and p.sourceline is not None:
                if h.sourceline < p.sourceline:
                    prev_h2 = h
                else:
                    break
        h2_idx = h2_positions.get(id(prev_h2)) if prev_h2 else None
        paragraphs.append({
            "text": body,
            "word_count": _word_count(body),
            "h2_index": h2_idx,
        })

    all_text = soup.get_text(" ", strip=True)
    return {
        "h1": h1,
        "h2_list": h2_list,
        "h3_list": h3_list,
        "paragraphs": paragraphs,
        "word_count": _word_count(all_text),
        "has_lists": bool(soup.find(["ul", "ol"])),
        "has_tables": bool(soup.find("table")),
        "has_code_blocks": bool(soup.find(["pre", "code"])),
        "format": "html",
    }


def _extract_html_regex(text: str) -> dict:
    """Fallback HTML extractor when bs4 isn't installed."""
    h1_match = re.search(r"<h1[^>]*>(.*?)</h1>", text, re.IGNORECASE | re.DOTALL)
    h1 = re.sub(r"<[^>]+>", "", h1_match.group(1)).strip() if h1_match else None
    h2_list = [re.sub(r"<[^>]+>", "", m.group(1)).strip()
               for m in re.finditer(r"<h2[^>]*>(.*?)</h2>", text,
                                    re.IGNORECASE | re.DOTALL)]
    h3_list = [re.sub(r"<[^>]+>", "", m.group(1)).strip()
               for m in re.finditer(r"<h3[^>]*>(.*?)</h3>", text,
                                    re.IGNORECASE | re.DOTALL)]
    p_blocks = [re.sub(r"<[^>]+>", "", m.group(1)).strip()
                for m in re.finditer(r"<p[^>]*>(.*?)</p>", text,
                                     re.IGNORECASE | re.DOTALL)]
    paragraphs = [{"text": p, "word_count": _word_count(p), "h2_index": None}
                  for p in p_blocks if p]
    # All-text body for word count and has_* flags.
    body = re.sub(r"<[^>]+>", " ", text)
    return {
        "h1": h1,
        "h2_list": h2_list,
        "h3_list": h3_list,
        "paragraphs": paragraphs,
        "word_count": _word_count(body),
        "has_lists": bool(re.search(r"<(ul|ol)\b", text, re.IGNORECASE)),
        "has_tables": bool(re.search(r"<table\b", text, re.IGNORECASE)),
        "has_code_blocks": bool(re.search(r"<(pre|code)\b", text, re.IGNORECASE)),
        "format": "html",
    }


def extract_structure(text: str) -> dict:
    """Single public entry. Detects markdown vs HTML and dispatches.

    Drafts emitted by ``content_agent`` are markdown. The HTML branch
    exists for paths where the publishing integration returns HTML the
    AEO post-publish check would want to score (out of scope in v1; the
    branch is here so v2's "did the published HTML preserve our
    structure?" check doesn't require a second module).
    """
    if not text or not text.strip():
        return {
            "h1": None, "h2_list": [], "h3_list": [], "paragraphs": [],
            "word_count": 0, "has_lists": False, "has_tables": False,
            "has_code_blocks": False, "format": "markdown",
        }
    if _looks_like_html(text):
        bs = _extract_html_bs4(text)
        return bs if bs is not None else _extract_html_regex(text)
    return _extract_markdown(text)
