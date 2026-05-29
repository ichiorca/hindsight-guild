# Edit-capture rationale for the 5 voice gates

This file is loaded only when an agent needs to justify a gate decision or
propose a new gate. The SKILL.md body has the operational checklist; this
deeper file documents WHY each gate exists, backed by edit-capture data.

## Methodology

Every founder edit landing in `training.edits` (BigQuery) is classified
by `services/edit_capture_handler` into one of these categories using a
Gemini-Flash classifier:

  - `softened_absolute`        — founder removed "guaranteed" / "always" / etc.
  - `cut_generic_opening`      — founder replaced a generic hook with specific
  - `anchored_unsupported_claim` — founder added an evidence link
  - `tightened_adverb_stack`   — founder cut redundant adverbs
  - `corrected_channel_tone`   — founder rewrote for the channel register
  - `other`                    — none of the above

Categories drive the gate list. A new gate is justified only when a
category exceeds **10 edits over a 28-day window** AND the founder
can articulate a deterministic rule for the fix.

## Gate 1 — Absolute language (47 edits, last 90d)

The 16 tokens in the gate's watchlist were derived by:

1. Pulling `before_text` + `after_text` for every `softened_absolute` edit.
2. Computing the token-level diff.
3. Filtering for tokens that appeared in ≥3 separate edits.

The replacement table in SKILL.md gate 1 comes from the same edit corpus —
each replacement is the founder's actual rewrite, generalized just enough
to apply to similar phrases.

**Why directional language?** The founder's stated rationale, captured
during a Q3 2025 retro: "Absolute claims feel like marketing-deck-speak.
Directional with a number ('typically X, see <experiment>') feels like a
peer talking, not a vendor pitching."

## Gate 2 — Adverb stacks (18 edits)

Pattern: any 2+ consecutive adverbs. Discovered by surfacing the most
common N-gram diffs in `tightened_adverb_stack` edits — the top 50 were
all 2+ adverbs.

## Gate 3 — Anchor verification (22 edits)

Rule: every numeric or factual claim needs an anchor in
`messaging_library`, `customer_voice`, or a decided experiment. Bare
claims pass the gate but with `unanchored_claims > 0` so the team sees
the count trend.

**Why not block on unanchored claims?** Earlier versions of the gate did.
The founder pushed back: "Some claims are obviously true and don't need a
citation in the draft. The count tells me when we're drifting." Tracking
without blocking is the compromise.

## Gate 4 — Channel tone (14 edits)

The tone-calibration table is built from:

- 3-5 founder-approved exemplars per channel
- The tone words the founder uses when describing the channel ("senior IC
  to peer", "founder to human", etc.)

Scoring is qualitative. A 4+ is required because edits below that score
landed in the founder's "rewrote tone" bucket disproportionately.

## Gate 5 — Customer problem lead (31 edits — highest!)

The single largest edit category. Founder's pattern, observed across
~80% of `cut_generic_opening` edits: rewrites the opening to either
quote a customer voice quote verbatim or paraphrase a problem from a
recent sales call.

**Why so aggressive on this one?** Because the second sentence of any
draft is the highest-attention real estate. The founder cares more about
this than any other gate. A draft that fails gate 5 but passes all others
still gets rewritten ~70% of the time.

## Proposing a new gate

If you (an agent) notice a recurring rejection pattern not covered by
the 5 gates, draft a `self_critique_proposal` with:

- The pattern (regex or rule statement)
- A backing edit count from `training.edits` (≥10 in 28d)
- A proposed replacement / fix
- An expected `VOICE_PASS` field name + values

The Self-Critique → Promotion-Gate loop will evaluate and either
promote the gate to SKILL.md v(next) or send it back for sharpening.

## Versioning

Bump SKILL.md `metadata.version` whenever you add/remove a gate.
References (this file) version independently — bump only if a gate's
rationale or threshold changes materially.
