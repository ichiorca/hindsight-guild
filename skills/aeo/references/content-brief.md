<!--
Adapted from claude-seo `skills/seo-content-brief/SKILL.md`. The
upstream skill produces full SEO briefs from competitor SERP analysis
(out of scope for our free tier). This file extracts the *output
template* the AeoReviser uses when proposing structural rewrites — a
common shape for "here's how the draft should restructure". Source
basis: claude-seo v2.0.0 (MIT).
-->

# Rewrite template — the structural recipe

When the AeoReviser proposes a restructure (`answer_extractability <
0.5`), it produces a short "rewrite plan" before applying changes.
This template defines the plan's shape so the closed-loop learner
(PRD-03 `aeo_miner`) can mine recurring rewrite patterns later.

## Plan shape

```json
REWRITE_PLAN = {
  "target_score":  0.7,
  "current_score": 0.42,
  "sub_signals_to_lift": ["answer_first", "self_contained_blocks"],
  "ordered_actions": [
    {
      "kind": "answer_first",
      "current": "<excerpt of current opener>",
      "proposed": "<excerpt of proposed opener>",
      "rationale": "Opener buries the answer behind 3 sentences of context.",
      "preserves": ["all stats", "voice", "claim attribution"]
    },
    {
      "kind": "consolidate_to_block",
      "current": "<excerpt of fragmented section>",
      "proposed": "<excerpt of consolidated block>",
      "rationale": "Three short paragraphs that together answer the H2's implied question; consolidating to one 152-word block.",
      "preserves": ["all stats", "voice", "claim attribution"]
    }
  ],
  "skipped_actions": [
    {
      "kind": "add_comparison_table",
      "reason": "Body lacks comparable data — would require fabrication."
    }
  ]
}
```

`ordered_actions` runs in order. Each must verify its `preserves`
list holds AFTER the rewrite. If any does not hold, the action is
moved to `skipped_actions` and the next is attempted.

## Action contracts

### `answer_first`

- Preserves: every fact, every claim attribution, the H1, the byline.
- Allowed changes: paragraph order in the opener (up to the first
  H2), sentence-level rephrasing of throat-clearing sentences only.
- Forbidden: adding new facts, restating opinions stronger.

### `h2_to_question`

- Preserves: every fact and claim under the H2.
- Allowed changes: the H2 text itself.
- Forbidden: changing what's under the H2.

### `consolidate_to_block`

- Preserves: every sentence's factual content.
- Allowed changes: merging 2-3 paragraphs, light reordering of
  sentences within the merged block, inlining of any
  "above" / "below" references.
- Forbidden: dropping or summarizing facts; if a fact would be lost,
  skip and record.

### `add_definition`

- Preserves: the existing body.
- Allowed changes: inserting one sentence in the form "X is …" near
  the term's first mention.
- Forbidden: defining terms speculatively (the definition must come
  from the body's own usage — if the body doesn't establish what X
  is, the definition can't be invented).

### `attribute_stat`

- Preserves: the number and the claim.
- Allowed changes: appending an inline anchor — `[source: …]` or
  `[exp_…]`.
- Forbidden: making up a source; if no source is available in the
  draft or in the messaging library, skip and tag the number
  `[needs_evidence]` instead.

### `ground_entity`

- Preserves: the meaning of the sentence.
- Allowed changes: replacing "we" / "our team" with a named referent
  on **first mention only** (subsequent uses can stay pronoun).
- Forbidden: inventing person names; the referent must come from the
  draft byline or the publishing organization.

## How the closed-loop learner mines this

PRD-03 `aeo_miner` reads `aeo_audits` rows (which contain
`rewrites: [...]` lists drawn from this template) and looks for
recurring `kind` values across drafts. The top 2 kinds become
proposals to add a prescriptive bullet to the AEO skill's "Quick
Wins" section, lifting the score floor over time.

The `rationale` and `preserves` fields are what the miner clusters
on — not just the `kind`. A `consolidate_to_block` triggered by
"opener buries answer" is a different pattern than one triggered by
"three sibling paragraphs each answer half the question".
