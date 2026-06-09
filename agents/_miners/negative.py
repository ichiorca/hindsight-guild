"""Negative miner — PRD-03 §6.2.

Reads approvals where ``decision == "reject"`` in the lookback window,
groups by ``rejection_category``, and finds the most common 5-15 word
phrase fragment across rejected drafts within each category. When a
category has >= 3 rejections, propose adding the dominant phrase to
``negative_examples`` so the Review Agent's grounding picks it up.

For category == "claim_risk", also propose tightening review_agent's
claim-validation rules (text appended to its skill body).

Output: at most 5 proposals per run.
"""
from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta

log = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z'\-]*")

# 5-15 word phrase window per PRD §6.2.
_PHRASE_MIN = 5
_PHRASE_MAX = 15

DEFAULT_LOOKBACK_DAYS = 7
DEFAULT_MIN_CATEGORY_COUNT = 2  # lowered 3→2 for demo (surface w/ less data)
DEFAULT_MAX_PROPOSALS = 5


def _phrases(text: str) -> list[str]:
    """Sliding 5-to-15 word phrases over the text. Each emitted phrase
    is a lowercased, normalized substring of the input — close enough
    for clustering by frequency.

    NOTE: this iterates the full ``range(5, 16)`` window. The earlier
    ``for n in (_PHRASE_MIN, _PHRASE_MAX)`` only emitted 5- AND 15-grams,
    silently missing every 6-14 word repeated phrase — i.e. nearly all of
    them — so most recurring rejection patterns never clustered."""
    tokens = [t.lower() for t in _TOKEN_RE.findall(text or "")]
    out: list[str] = []
    for n in range(_PHRASE_MIN, _PHRASE_MAX + 1):
        for i in range(0, len(tokens) - n + 1):
            out.append(" ".join(tokens[i:i + n]))
    return out


def _dominant_phrase(rejected_drafts: list[str]) -> tuple[str | None, int]:
    """Across a list of rejected draft bodies, find the phrase that
    appears in the most distinct drafts. Returns (phrase, n_drafts)."""
    if not rejected_drafts:
        return None, 0
    drafts_by_phrase: dict[str, set[int]] = {}
    for idx, body in enumerate(rejected_drafts):
        for ph in set(_phrases(body)):
            drafts_by_phrase.setdefault(ph, set()).add(idx)
    if not drafts_by_phrase:
        return None, 0
    best_phrase, best_drafts = max(
        drafts_by_phrase.items(),
        key=lambda kv: (len(kv[1]), -len(kv[0].split())),
    )
    return best_phrase, len(best_drafts)


def mine(db, *,
         lookback_days: int = DEFAULT_LOOKBACK_DAYS,
         min_category_count: int = DEFAULT_MIN_CATEGORY_COUNT,
         max_proposals: int = DEFAULT_MAX_PROPOSALS) -> list[dict]:
    """Negative miner entry point. See module docstring."""
    cutoff = datetime.now(UTC) - timedelta(days=lookback_days)
    try:
        rejects = list(db["approvals"].find({
            "decision": "reject",
            "decided_at": {"$gte": cutoff},
        }, {
            "telemetry_id": 1, "rejection_category": 1, "decided_at": 1,
        }).limit(500))
    except Exception as e:
        log.warning("negative miner approvals query failed: %s", e)
        return []

    if not rejects:
        return []

    # Pull matching action draft bodies in one batch.
    tids = [r["telemetry_id"] for r in rejects if r.get("telemetry_id")]
    actions_by_tid: dict[str, dict] = {}
    if tids:
        try:
            for a in db["actions"].find(
                {"telemetry_id": {"$in": tids}},
                {"telemetry_id": 1, "raw": 1, "channel": 1},
            ):
                actions_by_tid[a["telemetry_id"]] = a
        except Exception as e:
            log.warning("negative miner actions batch failed: %s", e)

    # Group draft bodies by rejection_category.
    by_category: dict[str, list[tuple[str, str]]] = {}  # category → [(channel, body)]
    for rej in rejects:
        cat = rej.get("rejection_category") or "other"
        tid = rej.get("telemetry_id")
        action = actions_by_tid.get(tid) or {}
        raw = action.get("raw") or {}
        body = raw.get("draft") or raw.get("output_text") or ""
        if isinstance(body, dict):
            body = body.get("body_markdown") or body.get("body") or ""
        if not body:
            continue
        channel = action.get("channel") or "unknown"
        by_category.setdefault(cat, []).append((channel, body))

    proposals: list[dict] = []
    # Rank categories by reject count so high-volume categories surface first.
    sorted_cats = sorted(
        by_category.items(), key=lambda kv: -len(kv[1]),
    )

    for category, rows in sorted_cats:
        if len(rows) < min_category_count:
            continue
        phrase, n_drafts = _dominant_phrase([body for _, body in rows])
        if not phrase or n_drafts < 2:
            continue
        channels = sorted({ch for ch, _ in rows})

        # Build the proposal. The target_kind is "negative_example" — the
        # runner inserts a negative_examples row + (for claim_risk) also
        # writes a self_critique_proposal onto review_agent's skill.
        proposals.append({
            "kind": "negative",
            "target_kind": "negative_example",
            "target_id": category,
            "issue": (
                f"{len(rows)} rejections in category {category!r} "
                f"share the phrase {phrase!r} across {n_drafts} drafts "
                f"({', '.join(channels)})."
            ),
            "proposed_change": (
                f"Add to negative_examples:\n"
                f'  - phrase: "{phrase}"\n'
                f"  - rejection_category: {category}\n"
                f"  - channels: {', '.join(channels)}\n"
                f"Review Agent's grounding pulls the 3 most recent per "
                f"(channel, category); this becomes one of them."
            ),
            "confidence": "high" if len(rows) >= 5 else "medium",
            "evidence_count": len(rows),
            "evidence": {
                "category": category,
                "phrase": phrase,
                "distinct_drafts": n_drafts,
                "channels": channels,
                "rejects_in_window": len(rows),
            },
        })

        # claim_risk: also propose tightening review_agent's claim rules.
        if category == "claim_risk":
            proposals.append({
                "kind": "negative",
                "target_kind": "skill",
                "target_id": "review_agent",   # the rule skill; runner
                                                 # writes self_critique_proposal
                                                 # onto skills.review_agent
                "issue": (
                    f"{len(rows)} claim_risk rejections in the last "
                    f"{lookback_days}d share phrase {phrase!r}."
                ),
                "proposed_change": (
                    f"Append to review_agent's claim-risk rule set:\n"
                    f"  Reject phrases of the form: \"{phrase}\" unless an "
                    f"approved messaging_library claim grounds them.\n"
                    f"Currently this pattern slips past review and gets "
                    f"caught at founder decision time."
                ),
                "confidence": "medium",
                "evidence_count": len(rows),
                "evidence": {
                    "category": category,
                    "phrase": phrase,
                    "rejects_in_window": len(rows),
                },
            })

        if len(proposals) >= max_proposals:
            break

    return proposals[:max_proposals]
