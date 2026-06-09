"""AEO miner — PRD-03 §6.4 (depends on PRD-01).

Reads ``aeo_audits`` rows from the last lookback window. Counts the
recurring ``rewrites[].kind`` values across audits; ranks by
(occurrence count, mean score_lift). For the top 2 kinds, proposes
adding a prescriptive bullet to the AEO skill's Quick Wins section
so the AEO scorer pre-empts the rewrite next time.

Gated on the ``aeo_audits`` collection existing AND having rows;
degrades to ``[]`` when PRD-01 isn't deployed.
"""
from __future__ import annotations

import logging
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta

log = logging.getLogger(__name__)

DEFAULT_LOOKBACK_DAYS = 14
DEFAULT_MIN_OCCURRENCES = 2  # lowered 3→2 for demo (surface w/ less data)
DEFAULT_MAX_PROPOSALS = 2

# Human-readable bullets per allowed rewrite kind. Matches the AEO
# SKILL.md's documented reviser actions.
_QUICK_WIN_BY_KIND: dict[str, str] = {
    "answer_first":
        "Lead the first paragraph with the answer to the implied query. "
        "If the title is a question, the first 40-60 words must contain "
        "the direct answer (not setup, not context).",
    "h2_to_question":
        "Phrase H2s as the question a reader would type. Noun-phrase H2s "
        "('The Renewal Problem') lose AI-search citability — use the "
        "interrogative form ('How do you stop renewal slip?').",
    "consolidate_to_block":
        "Each H2 section must contain at least one 134-167 word "
        "self-contained block — a passage that stands alone as a "
        "citable answer. Avoid spreading a single answer across 3-4 "
        "short paragraphs.",
    "add_definition":
        "Introduce every key term with a definition pattern ('X is …', "
        "'X refers to …'). AI engines preferentially extract definition "
        "blocks for 'What is X?' queries.",
    "attribute_stat":
        "Every numeric claim must carry inline attribution — a named "
        "source ('per Ahrefs Dec 2025') or an internal experiment ID "
        "('see exp_csm_handoff_v2'). Floating numbers don't get cited.",
    "ground_entity":
        "Replace 'we' / 'our team' with a named referent on first "
        "mention (author + role, brand + descriptor). AI engines can't "
        "cite content they can't attribute.",
}


def mine(db, *, lookback_days: int = DEFAULT_LOOKBACK_DAYS,
         min_occurrences: int = DEFAULT_MIN_OCCURRENCES,
         max_proposals: int = DEFAULT_MAX_PROPOSALS) -> list[dict]:
    """AEO miner entry point. See module docstring."""
    try:
        cutoff = datetime.now(UTC) - timedelta(days=lookback_days)
        audits = list(db["aeo_audits"].find(
            {"ts": {"$gte": cutoff}},
            {"rewrites": 1, "score_before": 1, "score_after": 1},
        ).limit(1000))
    except Exception as e:
        log.warning("aeo miner audits query failed: %s", e)
        return []

    if not audits:
        return []

    # Count rewrite kinds; track mean score lift per kind.
    occ: Counter[str] = Counter()
    lifts: dict[str, list[float]] = defaultdict(list)
    for a in audits:
        rewrites = a.get("rewrites") or []
        if not isinstance(rewrites, list):
            continue
        before = a.get("score_before")
        after = a.get("score_after")
        try:
            lift = float(after) - float(before)
        except (TypeError, ValueError):
            lift = None
        for rw in rewrites:
            kind = (rw or {}).get("kind") if isinstance(rw, dict) else None
            if not kind:
                continue
            occ[kind] += 1
            if lift is not None:
                lifts[kind].append(lift)

    # Filter + rank.
    ranked: list[tuple[str, int, float]] = []   # (kind, count, mean_lift)
    for kind, count in occ.items():
        if count < min_occurrences:
            continue
        mean_lift = (sum(lifts[kind]) / len(lifts[kind])) if lifts[kind] else 0.0
        ranked.append((kind, count, mean_lift))

    ranked.sort(key=lambda t: (-t[1], -t[2]))

    proposals: list[dict] = []
    for kind, count, mean_lift in ranked[:max_proposals]:
        bullet = _QUICK_WIN_BY_KIND.get(kind)
        if not bullet:
            # Unknown rewrite kind — record but use a generic message.
            bullet = (f"Quick Win: pre-empt {kind} restructures at draft "
                       "time by following the rules in skills/aeo/SKILL.md "
                       "§Reviser rules.")

        proposals.append({
            "kind": "aeo",
            "target_kind": "skill",
            "target_id": "aeo",
            "issue": (
                f"AEO reviser applied {kind!r} on {count} drafts in the "
                f"last {lookback_days}d "
                f"(mean answer_extractability lift "
                f"{mean_lift:+.3f}). Bake the rule into the AEO playbook "
                f"so the scorer pre-empts the rewrite next time."
            ),
            "proposed_change": (
                f"Append to skills/aeo/SKILL.md → §Quick Wins:\n"
                f"  - {bullet}"
            ),
            "confidence": "high" if count >= 6 else "medium",
            "evidence_count": count,
            "evidence": {
                "rewrite_kind": kind,
                "occurrence_count": count,
                "mean_score_lift": round(mean_lift, 3),
                "audits_window_days": lookback_days,
            },
        })

    return proposals
