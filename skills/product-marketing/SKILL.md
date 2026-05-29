---
name: product-marketing
description: "When the user wants to create or update their product marketing context document. Also use when the user mentions 'product context,' 'marketing context,' 'set up context,' 'positioning,' 'who is my target audience,' 'describe my product,' 'ICP,' 'ideal customer profile,' or wants to avoid repeating foundational information across marketing tasks. Use this at the start of any new project before using other marketing skills — it creates .agents/product-marketing.md that all other skills reference. The body specifies a 4-gate procedure (existence check, draft path, section coverage, verbatim-language enforcement) and a required PRODUCT_MARKETING_PASS output object the Review Agent uses to verify the doc is complete and grounded. You cannot produce a passing document from the description alone — the 12-section spec, the doc template, the verbatim rule, and the output schema are only in the body."
metadata:
  version: 2.1.0
---

<!--
v2.1.0 — Restructured from interview script into procedural context-doc
gate. Each gate produces a tracked output field. Domain content (12
sections, document template, JTBD four forces) preserved under the gate
it informs. Imported from https://github.com/iannuttall/marketingskills
(MIT License).
-->

# Product Marketing — context document procedure

Run all 4 gates IN ORDER when creating or updating `.agents/product-
marketing.md`. For each gate: apply the criteria, gather/draft content,
record the result. Attach a `PRODUCT_MARKETING_PASS` object to your
final output. The Review Agent reads it to verify the doc is complete
and grounded — outputs without it are auto-rejected.

## Required output schema

Your final response MUST end with a JSON block in this exact shape:

```
PRODUCT_MARKETING_PASS = {
  "existence_check":      "exists" | "missing" | "legacy_path:<path>",
  "draft_path":           "auto_from_codebase" | "from_scratch" | "update_existing",
  "sections_covered":     <int 0-12>,
  "verbatim_quotes":      <int count of verbatim customer phrases captured>,
  "saved_to":             ".agents/product-marketing.md"
}
```

---

## Gate 1 — Existence check

Check in this exact order:
1. `.agents/product-marketing.md` (canonical)
2. `.claude/product-marketing.md`
3. `.agents/product-marketing-context.md` (legacy)
4. `.claude/product-marketing-context.md` (legacy)

If found at a non-canonical path: OFFER to move it to
`.agents/product-marketing.md`. Set
`existence_check = "legacy_path:<found path>"`.

If found at canonical: `existence_check = "exists"`. Read it, summarize
what's captured, and ask which sections to update.

If not found anywhere: `existence_check = "missing"`. Proceed to Gate 2
to pick the draft path.

## Gate 2 — Draft path selection

If the doc is missing, present TWO options to the user:

1. **Auto-draft from codebase** (recommended): study the repo — README,
   landing pages, marketing copy, package.json, about pages, meta
   descriptions, any existing docs — and draft a V1. Then ask: "What
   needs correcting? What's missing?" Iterate to satisfaction.

2. **Start from scratch:** walk through each of the 12 sections (Gate 3)
   conversationally, one at a time. Don't dump all questions at once.

Most users prefer option 1. Record selection in `draft_path`. If the doc
exists, use `"update_existing"` and ask which sections to refresh.

## Gate 3 — 12-section coverage

The doc MUST cover all 12 sections. Record how many are actually filled
in `sections_covered` (0-12). Sections that genuinely don't apply
(e.g., Personas for a single-buyer B2C product) count as covered if
explicitly noted "n/a — single-buyer product."

### 1. Product Overview
One-liner; what it does (2-3 sentences); product category (the "shelf"
customers search on); product type (SaaS, marketplace, e-commerce,
service); business model + pricing.

### 2. Target Audience
Target company type (industry, size, stage); decision-makers (roles,
departments); primary use case; jobs to be done (2-3 things customers
"hire" you for); specific use cases or scenarios.

### 3. Personas (B2B only)
For each stakeholder (User, Champion, Decision Maker, Financial Buyer,
Technical Influencer): what they care about, their challenge, the
value you promise them.

### 4. Problems & Pain Points
Core challenge before finding you; why current solutions fall short;
what it costs them (time, money, opportunities); emotional tension
(stress, fear, doubt).

### 5. Competitive Landscape
- **Direct competitors:** same solution, same problem (Calendly vs SavvyCal)
- **Secondary competitors:** different solution, same problem (Calendly vs Superhuman scheduling)
- **Indirect competitors:** conflicting approach (Calendly vs personal assistant)

How each falls short for customers.

### 6. Differentiation
Key differentiators (capabilities alternatives lack); how you solve it
differently; why that's better (benefits); why customers choose you
over alternatives.

### 7. Objections & Anti-Personas
Top 3 sales objections + how to address them. Who is NOT a good fit
(anti-persona).

### 8. Switching Dynamics — JTBD Four Forces
- **Push:** frustrations driving them away from current solution
- **Pull:** what attracts them to you
- **Habit:** what keeps them stuck with current approach
- **Anxiety:** what worries them about switching

### 9. Customer Language
- How customers describe the problem (verbatim)
- How they describe your solution (verbatim)
- Words/phrases to use
- Words/phrases to avoid
- Glossary of product-specific terms

### 10. Brand Voice
Tone (professional, casual, playful…); communication style (direct,
conversational, technical); brand personality (3-5 adjectives).

### 11. Proof Points
Key metrics/results to cite; notable customers/logos; testimonial
snippets; main value themes + supporting evidence.

### 12. Goals
Primary business goal; key conversion action; current metrics (if known).

## Gate 4 — Verbatim language enforcement

The single most valuable content in this doc is VERBATIM customer
language — exact phrases from customers, not polished restatements.

Required minima:
- Section 9 (Customer Language): ≥3 verbatim phrases for "how they
  describe the problem"
- Section 11 (Proof Points): ≥1 testimonial quote (attributed)
- Section 4 (Problems): if possible, ≥1 verbatim phrase for the core
  challenge

If the user offers polished phrasing only, push back: "Do you have an
exact phrase a customer said? — those resonate more in copy than our
own wording."

Count total verbatim phrases captured across the doc into
`verbatim_quotes`. <3 = insufficient grounding; ask for more.

---

## Document template (use this exact structure)

After Gates 1-4, write to `.agents/product-marketing.md`:

```markdown
# Product Marketing Context

*Last updated: [date]*

## Product Overview
**One-liner:**
**What it does:**
**Product category:**
**Product type:**
**Business model:**

## Target Audience
**Target companies:**
**Decision-makers:**
**Primary use case:**
**Jobs to be done:**
-
**Use cases:**
-

## Personas
| Persona | Cares about | Challenge | Value we promise |
|---------|-------------|-----------|------------------|
|         |             |           |                  |

## Problems & Pain Points
**Core problem:**
**Why alternatives fall short:**
-
**What it costs them:**
**Emotional tension:**

## Competitive Landscape
**Direct:** [Competitor] — falls short because...
**Secondary:** [Approach] — falls short because...
**Indirect:** [Alternative] — falls short because...

## Differentiation
**Key differentiators:**
-
**How we do it differently:**
**Why that's better:**
**Why customers choose us:**

## Objections
| Objection | Response |
|-----------|----------|
|           |          |

**Anti-persona:**

## Switching Dynamics
**Push:**
**Pull:**
**Habit:**
**Anxiety:**

## Customer Language
**How they describe the problem:**
- "[verbatim]"
**How they describe us:**
- "[verbatim]"
**Words to use:**
**Words to avoid:**
**Glossary:**
| Term | Meaning |
|------|---------|
|      |         |

## Brand Voice
**Tone:**
**Style:**
**Personality:**

## Proof Points
**Metrics:**
**Customers:**
**Testimonials:**
> "[quote]" — [who]

**Value themes:**
| Theme | Proof |
|-------|-------|
|       |       |

## Goals
**Business goal:**
**Conversion action:**
**Current metrics:**
```

## Confirm and save

After drafting:
1. Show the completed document.
2. Ask if anything needs adjustment.
3. Save to `.agents/product-marketing.md` (record in `saved_to`).
4. Tell the user: "Other marketing skills will now use this context
   automatically. Run /product-marketing anytime to update it."

## Interview tips

- **Be specific:** "What's the #1 frustration that brings them to you?"
  not "What problem do they solve?"
- **Capture exact words:** customer language beats polished descriptions.
- **Ask for examples:** "Can you give me an example?" unlocks better
  answers.
- **Validate as you go:** summarize each section and confirm before
  moving on.
- **Skip what doesn't apply:** not every product needs all sections
  (e.g., Personas for B2C).

---

## Why these specific gates

These four gates exist because context docs fail in predictable ways:
overwriting an existing doc the user didn't realize was there (gate 1),
no draft and conversational exhaustion before the doc is built (gate 2),
sections silently skipped that other skills depend on (gate 3), and
"polished" company-voice copy passed off as customer language — which
strips the doc of the verbatim phrases that make downstream copy
resonate (gate 4).

This skill is upstream of nearly every other marketing skill. Skills
like `emails`, `launch`, `pricing`, `cro`, `onboarding` all start by
loading `.agents/product-marketing.md`. Incomplete coverage here
degrades every downstream output.
