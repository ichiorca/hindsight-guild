<!--
Adapted from claude-seo `skills/seo-sxo/SKILL.md` §"SERP Backwards
Analysis" + §"Search Intent". The upstream skill is a 9-step audit
that requires live SERP scraping (out of scope for our free tier).
This file extracts the intent / page-type framework as a static
reference the AeoScorer uses to choose the right voice + structure
per channel. Source basis: claude-seo v2.0.0 (MIT).
-->

# Intent + page type, per channel

Each channel has its own dominant intent + page-type expectation. The
AEO scorer reads `channel` from session state and applies the right
checklist below. When a draft mismatches its channel's expected
intent + page type, even a high-quality draft loses citability — AI
engines preferentially cite content that matches what users searched
for.

## Channel matrix

| Channel              | Dominant intent      | Page type the channel rewards                        |
|-----------------------|----------------------|------------------------------------------------------|
| `blog`               | Informational        | Long-form guide, listicle, comparison, case study     |
| `substack`           | Informational + opinion | First-person essay, post-mortem, framework explainer |
| `linkedin`           | Informational + commercial | Take, lessons, micro-case study, "three things"     |
| `lifecycle_email`    | Transactional        | Direct invitation, contextual reminder, next-step CTA |
| `google_ads`         | Transactional        | RSA — answer-focused, USP-led, verb-first             |
| `meta_ads`           | Awareness + commercial | Hook + proof point, image-led                         |
| `linkedin_ads`       | Commercial           | Peer-to-peer pitch, named-customer proof              |

## Intent classifications (Google framing)

- **Informational** — user wants to learn. AI search engines extract
  definitions, frameworks, step-by-steps. Citability lives in the
  passage blocks.
- **Commercial** — user is researching before buying. AI engines cite
  comparison tables, named-pro/con lists, ICP-matched recommendations.
- **Transactional** — user is ready to act. AI engines don't cite
  these heavily; they may surface them as direct actions
  (book/call/sign-up cards). Focus on conversion, not citation.
- **Navigational** — user is looking for a specific site. Out of
  scope for our drafting pipeline.

## Page-type adjustments

When `channel == "blog"` or `channel == "substack"`, the AEO scorer
factors in page type:

### Long-form guide
- Citability target: at least 3 self-contained answer blocks (one per
  major H2 section).
- Required: definition pattern for the central concept in the first
  block.
- Required: at least one numbered process or step list.

### Listicle
- Citability target: each list item is itself a self-contained
  passage in the 80-120-word range (shorter than the standard
  134-167 because lists are extracted per-item, not per-passage).
- Required: each item leads with the answer / takeaway, not the
  context.

### Comparison
- Citability target: at least one table (Markdown table). AI engines
  cite tables verbatim.
- Required: every comparison axis is defined (no "performance" without
  saying what's being measured).

### Case study
- Citability target: the narrative arc — situation, action, outcome —
  surfaces with named entities at each stage. Outcomes use named
  metrics with attribution.
- Required: at least one direct quote from a named customer.

### First-person essay
- Citability target: a stated conclusion in the first block. The
  reflective journey is the proof; the conclusion is the citable
  atom.
- Required: a "what I would do differently" or "what I learned" block
  near the end.

## How the scorer uses this

The AeoScorer reads `channel` and `page_type` (inferred from
`skill_id` if not explicit — see `agents/pipeline.py:_SKILL_BY_CHANNEL`).
If the draft's structure mismatches the page type for the channel, the
scorer lowers the relevant sub-signal:

| Mismatch                                          | Sub-signal hit                       |
|---------------------------------------------------|--------------------------------------|
| Long-form guide with < 2 self-contained blocks    | `self_contained_blocks` × 0.5        |
| Comparison without a table                        | `definition_patterns` × 0.7          |
| Case study without a named customer quote         | `entity_grounding` × 0.6             |
| Listicle with items > 200 words                   | `self_contained_blocks` × 0.6        |

## How the reviser uses this

When a sub-signal is hit specifically by a page-type mismatch, the
reviser's allowed rewrites are limited to:

- **Long-form guide:** `consolidate_to_block` to produce the missing
  self-contained block.
- **Comparison:** insert a table; do NOT manufacture comparison data.
  If the body lacks the data to fill a table, record
  `add_comparison_table: missing_data` and leave it.
- **Case study:** insert a `[needs_customer_quote]` tag in the
  position a quote would go. The reviser does not invent customer
  quotes.
- **Listicle:** split overlong list items into multiple items, only
  if the existing items split naturally.

The reviser will never rewrite the body across page types
(e.g., turn a case study into a guide). Page type is a channel +
skill_id decision made upstream.
