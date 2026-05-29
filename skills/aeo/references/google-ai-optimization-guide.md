<!--
Imported verbatim from claude-seo `skills/seo-geo/references/google-ai-optimization-guide.md`
(MIT). Trimmed: §"How claude-seo treats this guide" replaced with §"How
the aeo skill treats this guide" below to reflect our drafting-pipeline
context. Cross-refs to other claude-seo skills (seo-ecommerce, seo-images,
seo-technical) preserved as informational pointers — we don't ship those
audit paths but the cross-refs document where the guide's edge cases land.
Source last verified 2026-05-18; carried forward 2026-05-28.
-->

# Google AI Optimization Guide — primary-source synthesis (May 2026)

Google published a dedicated **AI optimization guide** under Search Central
docs. Its position is the most-cited primary source for how AI Overviews and
AI Mode interact with Search ranking. Every AEO scoring pass should treat
this doc as the canonical reference and reject community claims that
contradict it.

**Primary source:**
https://developers.google.com/search/docs/fundamentals/ai-optimization-guide

## TL;DR

> "Optimizing for generative AI search is **still SEO** from Google's
> perspective. AEO and GEO are rebranded labels for the same work."
> — Google, AI optimization guide

AI Overviews and AI Mode are grounded in the same ranking and quality systems
as classic Search. Two AI techniques layer on top:

1. **RAG / grounding** — retrieves indexed pages, generates a response with
   clickable source links.
2. **Query fan-out** — issues multiple related sub-queries and pulls in
   additional results before answering.

**Eligibility floor:** a page must be **indexed and eligible to be shown with
a snippet in Google Search** to appear in any AI feature. There is no separate
"AI index". Everything that follows is SEO fundamentals applied through this
lens.

## The myth-busting section (most important)

Google explicitly says you **do NOT need to**:

| Claim Google rejects | Source |
|---|---|
| Create `llms.txt` or AI-specific markup files | AI optimization guide §"Myths" |
| "Chunk" your content into small pieces for AI | Same |
| Rewrite content for AI with specific phrasings or long-tail keyword variations | Same |
| Chase inauthentic mentions across blogs / forums / videos | Same |
| Over-invest in structured data specifically for AI features | Same |

What **does** matter, per Google: unique, non-commodity, first-hand content.
Their example contrasts "7 Tips for First-Time Homebuyers" (commodity) with
"Why We Waived the Inspection & Saved Money: A Look Inside the Sewer Line"
(lived experience).

> **Cross-reference:** the llms.txt myth is independently confirmed by
> [[llmstxt-evidence]] (Mueller, Illyes, SE Ranking 300k-domain study,
> OtterlyAI server-log audit). Both files must stay aligned.

## The "creating helpful content" companion guide

The AI optimization guide links to Google's E-E-A-T guidance:

**Primary source:**
https://developers.google.com/search/docs/fundamentals/creating-helpful-content

Key actionable test — **Who / How / Why**:

- **Who** created it — bylines expected where readers expect them; author
  background pages required for YMYL.
- **How** it was created — especially for AI-assisted content; disclose
  process where readers would reasonably ask.
- **Why** it exists — "to help people," not "to attract search clicks."

YMYL ("Your Money or Your Life") topics get extra weight: health, finance,
safety. Sept 2025 QRG expanded YMYL to include political / social topics.

Google's listed warning signs to self-audit against:

- Writing to a target word count (there isn't one)
- Entering niches with no expertise just for traffic
- Faking publication-date freshness
- Mass content churn for "freshness" signals

## AI content policy

**Primary source:**
https://developers.google.com/search/blog/2023/02/google-search-and-ai-content
(plus the Search Essentials spam policies)

Generative AI content is fine if it meets Search Essentials. It crosses into
spam when used to **scale low-value pages** (QRG §4.6.5 scaled content abuse,
§4.6.6 low-effort main content).

Two operational requirements with concrete enforcement surfaces:

1. **Merchant Center — AI-generated product images:** must carry IPTC
   `DigitalSourceType: TrainedAlgorithmicMedia` metadata. Out of scope for
   marketing drafting; called out here because the same agentic-marketing
   system may publish product surfaces later.
2. **AI-generated product titles and descriptions:** must be separately
   specified and labeled as AI-generated in the merchant feed. Same scope
   note.

## Forward-looking: agent-friendly pages and WebMCP

The AI optimization guide pivots near the end to **AI agents** — not just
summarizers. Agents interact with sites through three channels: screenshots
plus a vision model, raw HTML/DOM, and the browser accessibility tree.

The guide also name-drops **WebMCP** (proposed standard for direct
site↔agent interaction) and **UCP** (Universal Commerce Protocol — Google +
Shopify + Etsy + Walmart + Visa/Mastercard). Both early-stage; relevant
when this system publishes commerce surfaces.

## How the aeo skill treats this guide

1. The AEO scorer cites this URL as the authoritative source whenever a
   sub-signal needs justification in its rationale.
2. The myth-busting list above gates community-sourced AI-SEO
   recommendations — if a recommendation contradicts Google's stated
   position (e.g., "you must publish an llms.txt"), the AEO scorer rejects
   it and notes the contradiction in its `rewrites` output.
3. Where a third-party claim and Google contradict, the AEO skill defers
   to Google and notes the contradiction explicitly.
4. The "Who / How / Why" test maps directly onto our `entity_grounding`
   sub-signal — author byline + brand grounding both check out under
   "Who".

## Last verified

2026-05-28 (imported from upstream verification 2026-05-18). Re-check the
source doc each quarter. Update this file whenever:

- Google publishes new myth-busting / clarification.
- Any of the linked policy docs revise eligibility or enforcement language.
- The UCP / WebMCP standards advance (currently early stage).
