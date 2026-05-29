---
name: aeo
description: >
  Answer Engine Optimization — score and optimize a marketing draft for
  citation by ChatGPT, Perplexity, Google AI Overviews, and Claude web
  search. Invoke from the aeo_agent between content_agent and
  review_agent for blog / substack / linkedin drafts. Produces an
  `answer_extractability` score (0-1) and, when below threshold, a
  rewritten draft that lifts citability without losing voice or claims.
applies_to:
  channel: [blog, substack, linkedin]
metadata:
  version: 1.0.0
  source: https://github.com/AgriciDaniel/claude-seo (skills/seo-geo + seo-content)
  license: MIT
---

<!--
v1.0.0 — Adapted from claude-seo `seo-geo` (audit framing) into a
drafting-pipeline skill. Removed: URL fetching, crawler-access checks,
brand-mention-on-Reddit recommendations (out of scope for draft scoring),
DataForSEO/Ahrefs MCP integrations (not on our free tier). Kept: passage
citability rules, structural readability rules, authority signals
recipe, platform-specific optimization table.
-->

# AEO — Answer Engine Optimization (drafting-time)

## Primary source

Google's AI Optimization Guide is the canonical reference. Its TL;DR:

> "Optimizing for generative AI search is **still SEO** from Google's
> perspective. AEO and GEO are rebranded labels for the same work."

AI Overviews and AI Mode are grounded in the same ranking and quality
systems as classic Search. See
`references/google-ai-optimization-guide.md` for the full
primary-source synthesis (Google's myth-busting list,
Who/How/Why test, AI content policy).

For why **llms.txt is NOT a citation lever**, see
`references/llmstxt-evidence.md`. We do not advise the drafter to
chase it.

---

## When you run

You are invoked by `agents/aeo_agent.py` (a LoopAgent with two
sub-agents: AeoScorer and AeoReviser). Pipeline order:
`content_agent → critique_loop → aeo_agent → image_brief → review_agent`.

The draft you receive has already passed `house-style` voice gates and
the critique-loop revision. Your job is to add the AEO dimension
without re-litigating those gates.

**Skip when:** `channel not in {"blog", "substack", "linkedin"}`. Paid
and email channels don't have answer-engine surfaces; return
`answer_extractability: null` (rendered as "—" on the rubric card).

**Skip reviser when:** `word_count < 200` (stubs). Score only.

---

## Required output schema

```json
AEO_SCORE = {
  "answer_extractability": 0.66,
  "sub_signals": {
    "answer_first":           0.0..1.0,
    "question_form_h2s":      0.0..1.0,
    "self_contained_blocks":  0.0..1.0,
    "specific_stats":         0.0..1.0,
    "definition_patterns":    0.0..1.0,
    "entity_grounding":       0.0..1.0
  },
  "rewrites": [
    {"kind": "answer_first|h2_to_question|...",
     "before": "<excerpt>", "after": "<excerpt>"}
  ]
}
```

`answer_extractability` is the weighted composite (see §"Composite
score" below). The reviser populates `rewrites` only when it actually
restructured the draft. Empty `rewrites` means scoring-only pass.

---

## The 6 sub-signals you must score

### 1. Answer-first (weight 25%)

**Strong:** the first 40-60 words of the body contain the answer to the
implied query (the post's title/H1). A reader who reads only the
opening paragraph already has the takeaway.

**Weak:** the opening is throat-clearing (history, definitions, "in
today's market…", a question the body will eventually answer).

Score this 1.0 if the opener IS the answer, 0.5 if the answer appears
within the first H2 section, 0.0 if buried beyond.

### 2. Question-form H2s (weight 15%)

**Strong:** at least 30% of H2s are phrased as a question matching the
ICP's likely query ("How do I stop renewal slip at the CSM handoff?").

**Weak:** H2s are noun-phrase chapter titles ("The Renewal Problem").
These work for SEO but lose AI-search citability — AI engines extract
Q→A pairs.

Score = fraction of H2s in question form, capped at 1.0 when ≥ 30%.

### 3. Self-contained answer blocks (weight 20%)

**Strong:** each H2 section contains at least one **134-167-word**
paragraph that stands alone — could be quoted verbatim and the reader
would understand it without surrounding context.

**Weak:** answers are spread across multiple short paragraphs requiring
the reader to assemble them. AI engines extract single passages, not
threads.

The 134-167-word target comes from the Ahrefs December 2025 study of
AI citation passages — it's the median citation length across Google
AIO + Perplexity + ChatGPT. Shorter blocks get cited; longer blocks
get summarized (which strips attribution).

Use `scripts/aeo/passage_blocks.py` to segment. Score = fraction of H2
sections that contain at least one block in the target range.

### 4. Specific stats with attribution (weight 15%)

**Strong:** every quantitative claim ("3× faster", "47% reduction")
has an inline attribution — either to a named source ("per Ahrefs
December 2025") OR to an internal experiment ID
("see exp_csm_handoff_v2"). AI engines preferentially cite
attribution-backed numbers.

**Weak:** numbers float unattached. Even if true, AI engines won't
cite them because they can't verify.

Score = fraction of numeric claims with anchored attribution.

### 5. Definition patterns (weight 10%)

**Strong:** key terms are introduced with definition patterns —
"X is …", "X refers to …", "X means …". These are the prompts AI
engines preferentially extract for "What is X?" style queries.

**Weak:** key terms are used assuming the reader knows them.

Score = 1.0 if ≥ 2 definition patterns present for the post's key
entities, 0.5 if 1, 0.0 if none.

### 6. Entity grounding (weight 15%)

**Strong:** brand, author, and key concepts are clearly named with
unambiguous referents. For author bylines, the byline links to a
page identifying the person (LinkedIn, About page, etc.). For named
companies, the first mention uses the full legal name before any
shorthand.

**Weak:** "we", "the team", "our client" without resolving identity.
AI engines won't cite content they can't attribute.

Score = 1.0 if brand + author both grounded, 0.5 if one, 0.0 if neither.

---

## Composite score

```
answer_extractability =
    0.25 * answer_first
  + 0.15 * question_form_h2s
  + 0.20 * self_contained_blocks
  + 0.15 * specific_stats
  + 0.10 * definition_patterns
  + 0.15 * entity_grounding
```

**Threshold:** `answer_extractability >= 0.7` → exit reviser, ship as-is.

**Below 0.5** → reviser MUST restructure. **Between 0.5 and 0.7** →
reviser MAY restructure if a low-cost rewrite is available; otherwise
return score-only.

---

## Reviser rules

When the AeoReviser sub-agent restructures, it MUST:

1. **Preserve every factual claim verbatim.** Numbers, named entities,
   experiment IDs, customer quotes don't move and don't get reworded.
2. **Preserve voice.** The `house-style` VOICE_PASS object already
   validated tone. If the rewrite would drop `brand_voice` below 0.7
   on a re-grade, revert to original and surface the AEO score
   without a rewrite. (Same revert pattern as `self_critique`.)
3. **Document each change.** Every rewrite appended to
   `AEO_SCORE.rewrites` with `kind`, `before` excerpt, `after` excerpt.
   This feeds the closed-loop learner (PRD-03 §6.4 `aeo_miner`).

Allowed rewrite kinds:

| kind                       | Action                                                                  |
|-----------------------------|--------------------------------------------------------------------------|
| `answer_first`              | Reorder the opener to lead with the answer.                              |
| `h2_to_question`            | Convert a noun-phrase H2 to its question form.                           |
| `consolidate_to_block`      | Merge short paragraphs into a 134-167-word self-contained block.         |
| `add_definition`            | Insert a "X is …" definition for an unintroduced key term.               |
| `attribute_stat`            | Add a source / experiment-ID anchor to a floating number.                |
| `ground_entity`             | Replace "we" / "our team" with a named referent on first mention.        |

If a sub-signal scores < 0.4 and none of the above kinds applies
without breaking the voice gate, leave it. Recording the low score is
useful; mangling the draft is not.

---

## Platform-specific notes (informational)

Different AI engines weight different signals. We don't optimize for
each separately — the composite catches the high-overlap rules. For
reference:

| Platform                | Key citation sources                                  |
|-------------------------|--------------------------------------------------------|
| Google AI Overviews     | Top-10 ranking pages (92% per Ahrefs Dec 2025)        |
| ChatGPT web search      | Wikipedia (47.9%), Reddit (11.3%)                     |
| Perplexity              | Reddit (46.7%), Wikipedia                             |
| Claude web search       | Diverse — favors structured + attributed content      |

The signals above (definition patterns, attribution, entity grounding)
overlap heavily with what ChatGPT and Claude both prefer. Reddit
presence and Wikipedia entity work happen outside the drafting
pipeline — see `references/intent-and-page-type.md`.

---

## Tools available

The aeo_agent registers these tools (defined in `scripts/aeo/`):

- `content_quality(text)` → `{filler_score, ai_pattern_score,
  information_density, repetition_score, overall_quality, flags}`.
  Use to flag low-effort drafts that would fail Google's QRG §4.6.5
  scaled-content-abuse check.
- `passage_blocks(text)` → list of `{start, end, word_count}` segments
  in the 134-167 range. Use for §3 self-contained-blocks scoring.
- `schema_for_article(meta)` → JSON-LD blob for Article + Person +
  Organization. Embed in the draft body for the final publish step.

---

## Error handling

| Scenario                                       | Action                                                          |
|-------------------------------------------------|------------------------------------------------------------------|
| Draft is empty / < 50 words                    | Return `answer_extractability: null`. Don't run reviser.        |
| Reviser would drop `brand_voice` < 0.7         | Revert; return score-only with note `reviser_reverted: true`.    |
| `passage_blocks` script raises                 | Score `self_contained_blocks: null`. Compute composite without. |
| Channel not in [blog, substack, linkedin]      | Return null score (rubric card shows "—"). No work.              |

---

## Last verified

2026-05-28 against `agentic_marketing_functional_spec.md §7.4` (rubric
set) and the claude-seo `seo-geo` v2.0.0 reference. Update on next
quarterly judge recalibration.
