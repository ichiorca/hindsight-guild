"""Self-contained answer-block detector.

The AEO scorer's most important sub-signal is `self_contained_blocks`
(weight 20% of `answer_extractability`). AI search engines (ChatGPT,
Perplexity, Google AI Overviews, Claude web search) extract single
passages when they cite a source — they don't assemble multi-paragraph
answers. The Ahrefs December 2025 study of 75,000 brands measured the
median citation passage length at **134-167 words** across all four
engines.

This module reads a draft body (markdown or HTML), segments it into
paragraph blocks (delegating to ``parse_draft.extract_structure``),
scores each block on word-count + self-containment, and returns a
per-H2-section view the AEO scorer uses to compute the sub-signal.

See ``skills/aeo/references/passage-blocks.md`` for the full prescriptive
rule set this module implements.
"""
from __future__ import annotations

import re

from . import parse_draft

# Citation passage window — calibrated from Ahrefs Dec 2025 study of
# 75k brands' AI citations across Perplexity / ChatGPT / AIO / Claude.
TARGET_MIN_WORDS = 134
TARGET_MAX_WORDS = 167

# Listicle items are extracted per-item (not per-passage) so their
# citation window is shorter.
LISTICLE_TARGET_MIN_WORDS = 80
LISTICLE_TARGET_MAX_WORDS = 120


# Cross-reference markers that signal a block is NOT self-contained.
# Each match knocks the self-containment score down — see the table in
# skills/aeo/references/passage-blocks.md for weights.
_REFERS_BACKWARD = re.compile(
    r"\b(above|previously|earlier|as (?:we )?(?:discussed|mentioned|noted|saw|covered)|"
    r"in the (?:previous|prior) (?:section|paragraph)|"
    r"the (?:framework|chart|table|figure) (?:above|earlier))\b",
    re.IGNORECASE,
)
_REFERS_FORWARD = re.compile(
    r"\b(below|later|we'll (?:see|cover|discuss|show)|"
    r"in the (?:next|following) section|"
    r"as (?:we'll|we will) (?:see|cover|discuss)|"
    r"the (?:chart|table|figure) below)\b",
    re.IGNORECASE,
)
_TRANSITIONAL_OPENER = re.compile(
    r"^\s*(also|similarly|furthermore|moreover|additionally|likewise|in addition|"
    r"on the other hand|by contrast|conversely|in turn|consequently|therefore|"
    r"thus|hence|thereby|building on|continuing|next,)\b",
    re.IGNORECASE,
)
# Numeric-claim detector — same shape as content_quality's, used here to
# spot floating numbers (numbers that are present without inline source).
_NUMBER_RE = re.compile(r"\b\d+(?:[.,]\d+)?(?:%|st|nd|rd|th|×|x)?\b")
# Inline source markers we accept as attribution. ALL alternates use
# explicit word boundaries — without them, "per" matched inside
# "experiments", "source" matched inside "outsourced", etc., causing
# unattributed blocks to score as attributed.
_ATTRIBUTION_HINTS = re.compile(
    # "per" alone is too loose — it matches "per-channel", "per capita",
    # etc. Require "per <Capitalized>" or "per <year>" — i.e., an actual
    # attribution target follows.
    r"\bper\s+(?:[A-Z][a-z]+|\d{4})|"
    r"\baccording to\b|"
    r"\bsource:|\(source[:\s]|\[source[:\s]|"
    r"\bexp_[a-z0-9_]+|"                            # internal experiment ID
    r"\bsee\s+(?:exp_[a-z0-9_]+|[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)|"  # see X
    r"\b\d{4}\s+(?:study|report|paper|analysis)\b|" # "2025 study"
    r"\b(?:ahrefs|google|gartner|hubspot|mckinsey|forrester|jasper|openai|anthropic)\b",
    re.IGNORECASE,
)


def _score_self_containment(block_text: str) -> float:
    """Heuristic 0-1. See skills/aeo/references/passage-blocks.md."""
    score = 1.0

    if _REFERS_BACKWARD.search(block_text):
        score *= 0.6
    if _REFERS_FORWARD.search(block_text):
        score *= 0.6
    if _TRANSITIONAL_OPENER.search(block_text):
        score *= 0.8

    # Floating numbers: contains a number AND no attribution within block.
    has_number = bool(_NUMBER_RE.search(block_text))
    has_attribution = bool(_ATTRIBUTION_HINTS.search(block_text))
    if has_number and not has_attribution:
        score *= 0.7

    return round(score, 3)


def _is_listicle(structure: dict, page_type_hint: str | None) -> bool:
    """Listicle heuristic: explicit hint OR (has lists AND list items
    dominate the body)."""
    if page_type_hint == "listicle":
        return True
    if not structure.get("has_lists"):
        return False
    # Count paragraphs whose word count is < 30 — proxy for list-item-like
    # blocks. If they make up > 60% of paragraphs, treat as listicle.
    paras = structure.get("paragraphs", [])
    if not paras:
        return False
    short_paras = sum(1 for p in paras if p["word_count"] < 30)
    return short_paras > 0.6 * len(paras)


def detect_blocks(text: str, *, page_type_hint: str | None = None) -> dict:
    """Segment a draft body into blocks + score each.

    Args:
        text: Draft body (markdown or HTML).
        page_type_hint: Optional "guide" | "listicle" | "comparison" |
            "case_study" | "essay". When provided, switches the target
            word window (listicle uses 80-120; everything else 134-167).
            When omitted, listicle is auto-detected from structure.

    Returns:
        {
          "format":        "markdown" | "html",
          "target_min":    int,
          "target_max":    int,
          "h2_sections":   [
            {
              "h2_index":  int,
              "h2_title":  str,
              "blocks":    [
                {"text": str, "word_count": int,
                 "in_target_range": bool,
                 "self_contained_score": float,
                 "qualifies": bool}   # in_target AND score >= 0.8
              ]
            }
          ],
          "intro_blocks":  [...],   # blocks BEFORE first H2 (same shape)
          "n_qualifying_sections": int,
          "n_h2_sections": int,
          "self_contained_blocks_signal": 0.0..1.0,
        }
    """
    structure = parse_draft.extract_structure(text)
    listicle = _is_listicle(structure, page_type_hint)
    target_min = LISTICLE_TARGET_MIN_WORDS if listicle else TARGET_MIN_WORDS
    target_max = LISTICLE_TARGET_MAX_WORDS if listicle else TARGET_MAX_WORDS

    # Bucket paragraphs by their h2_index (None = intro).
    by_h2: dict[int | None, list[dict]] = {}
    for p in structure["paragraphs"]:
        by_h2.setdefault(p["h2_index"], []).append(p)

    def score_block(p: dict) -> dict:
        wc = p["word_count"]
        in_range = target_min <= wc <= target_max
        sc_score = _score_self_containment(p["text"])
        return {
            "text": p["text"],
            "word_count": wc,
            "in_target_range": in_range,
            "self_contained_score": sc_score,
            "qualifies": in_range and sc_score >= 0.8,
        }

    intro_blocks = [score_block(p) for p in by_h2.get(None, [])]

    h2_sections: list[dict] = []
    n_qualifying = 0
    for idx, title in enumerate(structure["h2_list"]):
        section_blocks = [score_block(p) for p in by_h2.get(idx, [])]
        if any(b["qualifies"] for b in section_blocks):
            n_qualifying += 1
        h2_sections.append({
            "h2_index": idx,
            "h2_title": title,
            "blocks": section_blocks,
        })

    n_h2 = len(structure["h2_list"])
    if n_h2 == 0:
        # No H2s — fall back to "does the body contain at least one
        # qualifying block?". Score: 1.0 if yes, 0.0 if no.
        # Same denominator semantics: signal is the fraction of "sections"
        # that have a qualifying block; with 0 sections we treat the
        # whole body as one section.
        whole_body_qualifies = any(b["qualifies"] for b in intro_blocks)
        signal = 1.0 if whole_body_qualifies else 0.0
    else:
        signal = round(n_qualifying / n_h2, 3)

    return {
        "format": structure["format"],
        "target_min": target_min,
        "target_max": target_max,
        "h2_sections": h2_sections,
        "intro_blocks": intro_blocks,
        "n_qualifying_sections": n_qualifying,
        "n_h2_sections": n_h2,
        "self_contained_blocks_signal": signal,
    }
