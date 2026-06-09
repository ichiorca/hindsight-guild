"""Voice miner — PRD-03 §6.1.

Reads approvals where ``decision == "edit"`` in the lookback window.
For each, joins the corresponding action's draft_text vs the approval's
approved_text, computes a token-level diff, and clusters recurring
removed / added phrases. Patterns hitting both ``>=3 frequency`` AND
``>=3 distinct drafts`` become proposals.

Output: at most 5 voice proposals per run (rank by frequency).
Each proposal lives at ``skills.{id}.self_critique_proposal`` slot;
the runner upserts. Target skills:

  - Adds/removes flagged on ``house-style``: bullet-style "always use X"
    / "never use Y" lines added to the body.
  - Token substitutions flagged on ``copy-editing``: "replace X with Y".
"""
from __future__ import annotations

import logging
import re
from collections import Counter
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

log = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z'\-]*")
# n-gram window for clustering — 1-5 tokens.
_MIN_NGRAM = 1
_MAX_NGRAM = 5

# Thresholds per PRD-03 §6.1.
# Lowered 3→2 so the closed-loop surfaces proposals with less edit history
# (demo-friendly). A pattern needs to recur across >=2 distinct edited drafts.
DEFAULT_MIN_FREQUENCY = 2
DEFAULT_MIN_DRAFTS = 2
DEFAULT_MAX_PROPOSALS = 5


def _tokens(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text or "")]


def _ngrams(tokens: list[str], n_min: int = _MIN_NGRAM, n_max: int = _MAX_NGRAM) -> Iterable[str]:
    for n in range(n_min, n_max + 1):
        for i in range(0, len(tokens) - n + 1):
            yield " ".join(tokens[i:i + n])


def _diff_buckets(before_text: str, after_text: str) -> tuple[list[str], list[str]]:
    """Returns (removed_ngrams, added_ngrams).

    Crude but stable: we compute the multiset difference of n-grams from
    before vs after. Anything in before-only is "removed", anything in
    after-only is "added". This catches most patterns we care about
    (banned phrases dropped, evidence anchors added) without depending
    on a real diff library.
    """
    before = list(_ngrams(_tokens(before_text)))
    after = list(_ngrams(_tokens(after_text)))
    bc = Counter(before)
    ac = Counter(after)
    removed: list[str] = []
    for ngram, cnt in bc.items():
        net = cnt - ac.get(ngram, 0)
        if net > 0:
            removed.extend([ngram] * net)
    added: list[str] = []
    for ngram, cnt in ac.items():
        net = cnt - bc.get(ngram, 0)
        if net > 0:
            added.extend([ngram] * net)
    return removed, added


# Words that are too common to be useful as standalone signals — we
# never flag bare stop-words even if they appear N times. Keeping the
# list tiny + biased toward English filler so we don't accidentally
# suppress real signals like "we" (which IS a meaningful voice token).
_STOPWORDS = {"the", "a", "an", "of", "and", "or", "to", "in", "on", "for",
              "with", "is", "are", "was", "were", "be", "been"}


def _is_noise(ngram: str) -> bool:
    """Filter spurious 1-grams from the proposal pool."""
    parts = ngram.split()
    if len(parts) == 1 and parts[0] in _STOPWORDS:
        return True
    if all(p in _STOPWORDS for p in parts):
        return True
    return False


def mine(db, *, lookback_days: int = 7,
         min_frequency: int = DEFAULT_MIN_FREQUENCY,
         min_drafts: int = DEFAULT_MIN_DRAFTS,
         max_proposals: int = DEFAULT_MAX_PROPOSALS) -> list[dict]:
    """Voice miner entry point. See module docstring."""
    cutoff = datetime.now(UTC) - timedelta(days=lookback_days)
    try:
        edits = list(db["approvals"].find({
            "decision": "edit",
            "decided_at": {"$gte": cutoff},
        }, {
            "telemetry_id": 1, "approved_text": 1, "original_draft": 1,
            "decided_at": 1,
        }).limit(500))
    except Exception as e:
        log.warning("voice miner approvals query failed: %s", e)
        return []

    if not edits:
        return []

    # Pull matching actions in one batch — cheaper than per-edit lookups.
    tids = [e["telemetry_id"] for e in edits if e.get("telemetry_id")]
    actions_by_tid: dict[str, dict] = {}
    if tids:
        try:
            for a in db["actions"].find(
                {"telemetry_id": {"$in": tids}},
                {"telemetry_id": 1, "raw": 1, "channel": 1, "skill_id": 1},
            ):
                actions_by_tid[a["telemetry_id"]] = a
        except Exception as e:
            log.warning("voice miner actions batch failed: %s", e)

    # Aggregate removed / added n-grams across all edits.
    removed_counter: Counter[str] = Counter()
    added_counter: Counter[str] = Counter()
    drafts_by_ngram_removed: dict[str, set[str]] = {}
    drafts_by_ngram_added: dict[str, set[str]] = {}

    for edit in edits:
        tid = edit.get("telemetry_id")
        if not tid:
            continue
        action = actions_by_tid.get(tid) or {}
        raw = action.get("raw") or {}
        # Prefer the approval's OWN original_draft — it's always present and is
        # the exact pre-edit text. Fall back to the action's stored draft.
        # (Signal-triggered drafts have several action rows per telemetry_id
        # and the one we join often carries raw=None, so the action-only path
        # silently misses the "before" and the miner never sees the edit.)
        before = edit.get("original_draft") or raw.get("draft") or raw.get("output_text") or ""
        if isinstance(before, dict):
            before = before.get("body_markdown") or before.get("body") or ""
        after = edit.get("approved_text") or ""
        if not before or not after:
            continue

        removed, added = _diff_buckets(before, after)
        for ngram in removed:
            if _is_noise(ngram):
                continue
            removed_counter[ngram] += 1
            drafts_by_ngram_removed.setdefault(ngram, set()).add(tid)
        for ngram in added:
            if _is_noise(ngram):
                continue
            added_counter[ngram] += 1
            drafts_by_ngram_added.setdefault(ngram, set()).add(tid)

    target_skill = "house-style"

    # --- Swap detection (the case the v1 _classify_pattern proxied as a
    # bare "remove"): when a removed phrase and an added phrase recur
    # together in the SAME drafts, the founder is SUBSTITUTING one for the
    # other. "replace X with Y" is far more actionable than two disconnected
    # add/remove rules, and emitting both halves separately double-reports
    # one behaviour. We consume the removed half so it isn't also emitted as
    # a plain remove; an added phrase may anchor more than one swap.
    qualifying_removed = [
        (ng, freq) for ng, freq in removed_counter.most_common()
        if freq >= min_frequency
        and len(drafts_by_ngram_removed.get(ng, set())) >= min_drafts
    ]
    qualifying_added = [
        (ng, freq) for ng, freq in added_counter.most_common()
        if freq >= min_frequency
        and len(drafts_by_ngram_added.get(ng, set())) >= min_drafts
    ]

    swapped_removed: set[str] = set()
    swap_props: list[tuple[int, int, dict]] = []  # (freq, shared, proposal)
    for r_ng, r_freq in qualifying_removed:
        r_drafts = drafts_by_ngram_removed.get(r_ng, set())
        best = None  # (shared, added_ngram)
        for a_ng, _ in qualifying_added:
            shared = len(r_drafts & drafts_by_ngram_added.get(a_ng, set()))
            if shared >= min_drafts and (best is None or shared > best[0]):
                best = (shared, a_ng)
        if best is None:
            continue
        shared, a_ng = best
        swapped_removed.add(r_ng)
        swap_props.append((r_freq, shared, {
            "kind": "voice",
            "target_kind": "skill",
            "target_id": target_skill,
            "issue": (f"Founder replaced {r_ng!r} with {a_ng!r} in "
                       f"{r_freq} edits across {shared} drafts."),
            "proposed_change": (
                f'Add to "replace" list:\n  - replace "{r_ng}" with "{a_ng}"\n'
                f"The founder consistently swaps these; the drafter should "
                f"use the preferred form on first pass."
            ),
            "confidence": "high" if r_freq >= 5 else "medium",
            "evidence_count": r_freq,
            "evidence": {
                "ngram": r_ng,
                "pattern_kind": "swap",
                "added": a_ng,
                "distinct_drafts": shared,
                "frequency": r_freq,
            },
        }))

    # --- Plain remove / add for everything not already explained by a swap.
    candidates: list[tuple[int, str, str, int]] = []   # (freq, ngram, kind, drafts)
    for ngram, freq in qualifying_removed:
        if ngram in swapped_removed:
            continue
        candidates.append((freq, ngram, "remove",
                           len(drafts_by_ngram_removed.get(ngram, set()))))
    for ngram, freq in qualifying_added:
        candidates.append((freq, ngram, "add",
                           len(drafts_by_ngram_added.get(ngram, set()))))

    # Rank by frequency, then by SPECIFICITY (more words = more actionable:
    # "free protocol audit" beats a bare "your") so the top proposals aren't
    # generic single common words, then by distinct-draft coverage.
    candidates.sort(key=lambda x: (-x[0], -x[1].count(" "), -x[3]))
    plain_props: list[tuple[int, dict]] = []
    for freq, ngram, kind, drafts in candidates:
        if kind == "remove":
            issue = f"Founder removed {ngram!r} in {freq} edits across {drafts} drafts."
            proposed = (
                f'Add to "never use" list:\n  - "{ngram}"\n'
                f"The founder consistently strips this phrase from drafts; "
                f"flagging it pre-emptively saves the round-trip."
            )
        else:
            issue = f"Founder added {ngram!r} in {freq} edits across {drafts} drafts."
            proposed = (
                f'Add to "always include where applicable" list:\n  - "{ngram}"\n'
                f"The founder consistently inserts this; the drafter should "
                f"propose it on first pass when context warrants."
            )
        plain_props.append((freq, {
            "kind": "voice",
            "target_kind": "skill",
            "target_id": target_skill,
            "issue": issue,
            "proposed_change": proposed,
            "confidence": "high" if freq >= 5 else "medium",
            "evidence_count": freq,
            "evidence": {
                "ngram": ngram,
                "pattern_kind": kind,
                "distinct_drafts": drafts,
                "frequency": freq,
            },
        }))

    # Swaps first (highest-value signal), then plain remove/add by frequency.
    swap_props.sort(key=lambda x: (-x[0], -x[1]))
    proposals = [p for _, _, p in swap_props] + [p for _, p in plain_props]
    return proposals[:max_proposals]
