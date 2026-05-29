<!--
Distilled from claude-seo `skills/seo-geo/SKILL.md` §"Citability Score (25%)".
The upstream skill embeds these rules inside a 270-line audit playbook;
this file extracts the prescriptive subset needed at drafting time so the
AeoReviser sub-agent can apply them without loading the full upstream
context. Source basis: claude-seo v2.0.0 (MIT).
-->

# Passage blocks — the 134-167 word self-contained answer

## What gets cited

AI search engines (ChatGPT, Perplexity, Google AI Overviews, Claude
web search) extract **single passages** when they cite a source. Multi-
paragraph answers requiring assembly are summarized — and the summary
strips attribution back to the source.

The Ahrefs December 2025 study of 75,000 brands tracked passage
length across all four engines and found the median citation passage
was **134-167 words**. Shorter passages got cited (mean 142 words).
Longer passages got summarized (and lost attribution).

## What a self-contained block looks like

A block in the 134-167-word window that:

1. **Answers a single question.** The block can be read in isolation
   and the reader gets the answer.
2. **Defines its own terms.** Any jargon, named entity, or
   abbreviation introduced in the block is also defined in the block.
3. **Stands free of "above" or "below" references.** No "as discussed
   above", "see the chart below", "as we'll cover next".
4. **Carries its own attribution.** If the block contains numbers, the
   source / experiment ID is in the block, not earlier in the post.

## What a NON-self-contained block looks like

Same word count, but:

- "Building on the framework we introduced in section 2…" → fails (1).
- "Using the same dataset…" without naming it → fails (2).
- "X is the best approach (here's the chart)" → fails (3).
- "47% of cohort A converted" without `cohort A` defined and the
  source named → fails (4).

These passages perform fine for readers reading top-to-bottom but lose
citability — an AI engine extracting a snippet won't have the
preceding context.

## How the scorer uses this

`scripts/aeo/passage_blocks.py` segments the draft body into
paragraph-level blocks, counts words, and reports each block as:

```python
{"start": <char_offset>, "end": <char_offset>, "word_count": <int>,
 "in_target_range": <bool>,    # 134 <= word_count <= 167
 "self_contained_score": <0-1>  # heuristic; see below
}
```

The `self_contained_score` is a heuristic the script computes from:

| Signal                                             | Effect on score |
|----------------------------------------------------|------------------|
| Contains "above" / "below" / "previous" / "next"   | × 0.6            |
| Contains "as discussed" / "as mentioned"           | × 0.5            |
| Contains a number AND no attribution within block  | × 0.7            |
| Contains a named entity introduced earlier         | × 0.7            |
| Opens with a transitional word (also, similarly)   | × 0.8            |
| Otherwise                                          | × 1.0            |

The sub-signal `self_contained_blocks` (weight 20% of `answer_extractability`)
is the fraction of H2 sections that contain at least one block with both
`in_target_range == true` and `self_contained_score >= 0.8`.

## How the reviser uses this

When `self_contained_blocks < 0.4`, the reviser MAY apply rewrite kind
`consolidate_to_block` — merging 2-3 short paragraphs into one block
in the target range. The reviser must:

1. Preserve every fact verbatim.
2. Inline-grounder any external references ("as discussed above" →
   re-state the referenced point in the block).
3. Verify the resulting block is 134-167 words via the script.

If consolidation would push the block past 167 words or require
rewording any fact, leave the section alone and record
`consolidate_to_block: skipped_too_long` in `AEO_SCORE.rewrites`.

## Why this isn't just SEO advice

Traditional SEO valued **comprehensive coverage** — long-form posts
that answered every related question. AI search prefers
**extractable atoms** — short self-contained answers that can be
cited verbatim. The same content can be both, but only when each H2
section contains at least one block that stands alone.

This is the structural difference between writing for Google's
top-10-blue-links era and writing for the citation era.
