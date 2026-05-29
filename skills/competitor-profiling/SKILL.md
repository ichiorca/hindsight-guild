---
name: competitor-profiling
description: Competitor-profile production procedure. Invoke when the user wants to research, profile, or analyze competitors from their URLs. Also use when the user mentions "competitor profile," "competitor research," "competitor analysis," "profile this competitor," "analyze competitor," "competitive intelligence," "competitor deep dive,"  "who are my competitors," "competitor landscape," "competitor dossier," "competitive audit," or "research these competitors." The body specifies a 5-gate procedure (scope, raw-persist, scrape, SEO-pull, synthesize), the raw-data directory layout, the per-page extraction matrix, the depth-level table, and a required COMPETITOR_PROFILING_PASS output object the Review Agent uses. You cannot produce a passing profile from this description alone — the layout rules, page-extraction matrix, DataForSEO tool list, and output schema are only in the body. Input is a list of competitor URLs. Output is structured competitor profile markdown files. For comparison/alternative pages, see competitors. For sales battle cards, see sales-enablement.
metadata:
  version: 2.1.0
---

<!--
v2.1.0 — Restructured from research walkthrough to procedural profiling
gate. Raw-data layout, page-extraction matrix, and depth table preserved.
Imported from https://github.com/iannuttall/marketingskills (MIT License).
-->

# Competitor Profiling — production procedure

Run all 5 gates IN ORDER per competitor. Each gate produces a tracked
artifact (raw data on disk OR a section of the synthesized profile).
Attach a `COMPETITOR_PROFILING_PASS` object so the Review Agent can
verify the skill was applied.

## Required output schema

Your final response MUST end with a JSON block in this exact shape:

```
COMPETITOR_PROFILING_PASS = {
  "depth":              "quick_scan" | "deep_profile",
  "raw_dir":            "<competitor-profiles/raw/<slug>/<YYYY-MM-DD>/>",
  "scrapes_saved":      <int>,
  "seo_endpoints_saved":<int>,
  "reviews_saved":      <int>,
  "profile_path":       "<competitor-profiles/<slug>.md>",
  "summary_updated":    "yes" | "no:single_competitor" | "n/a",
  "unsourced_claims":   <int>
}
```

`unsourced_claims > 0` is allowed but surfaces in telemetry — every
claim should trace to a saved raw file. Missing or malformed
`COMPETITOR_PROFILING_PASS` is treated as the entire skill not having
been applied.

---

## Core principles (enforced by gates)

1. **Facts over opinions.** Every claim must trace to a source (scraped
   page, review data, SEO metric). Label inferences clearly.
2. **Structured and comparable.** All profiles use the same template.
3. **Current data.** Profiles are dated snapshots; flag stale data.
4. **Honest assessment.** Don't exaggerate weaknesses or downplay
   strengths.

---

## Gate 1 — Scope

Before scraping anything, confirm:

1. **Competitor URLs** — explicit list.
2. **Your product context** — read `.agents/product-marketing.md`
   (or `.claude/product-marketing.md`, or legacy
   `product-marketing-context.md`). Don't re-ask what context already
   answers.
3. **Depth level** — quick scan or deep profile (default to quick scan
   unless user requests deep or the list is <=3 competitors).
4. **Focus areas** — pricing / positioning / SEO strength / content
   strategy (if user prioritized any).

Record `depth`.

## Gate 2 — Raw-data persistence layout

Before any expensive API call, create this directory tree:

```
competitor-profiles/
|-- raw/
|   |-- <competitor-slug>/
|       |-- <YYYY-MM-DD>/
|           |-- scrapes/    # one .md per scraped page
|           |-- seo/        # one .json per DataForSEO endpoint
|           |-- reviews/    # one .md or .json per review source
|-- <competitor-slug>.md    # final synthesized profile
|-- _summary.md             # cross-competitor summary
```

Rules:
- `<competitor-slug>`: lowercase, hyphenated (`responsehub`, `safe-base`).
- `<YYYY-MM-DD>`: date the data was pulled — never overwrite a prior
  date's folder; create fresh on a new run (this is what enables
  diff-over-time).
- Save BEFORE synthesizing. The synthesized profile cites the raw dir
  in its "Raw Data Sources" section.

Record `raw_dir`.

## Gate 3 — Site scrape (Firecrawl)

**Step 3a — Map the site.** `firecrawl_map -> competitor URL`. From the
map, prioritize:

- Homepage
- Pricing page
- Features / product pages
- About / company page
- Blog (top-level — content strategy signals)
- Customers / case studies
- Integrations
- Changelog / what's new (if exists)

**Step 3b — Scrape key pages.** `firecrawl_scrape -> each page URL`.
Save each result to `raw/<slug>/<date>/scrapes/<page-name>.md` BEFORE
extracting fields.

**Per-page extraction matrix:**

| Page          | Extract                                                                              |
|---------------|--------------------------------------------------------------------------------------|
| Homepage      | Headline, subheadline, value prop, primary CTA, social proof, target audience signals|
| Pricing       | Tiers, prices, feature breakdown, billing, free tier/trial, enterprise signals       |
| Features      | Feature categories, key capabilities, how each is described, demo signals            |
| About         | Founding story, team size, funding, mission, HQ                                      |
| Customers     | Named customers, logos, industries, case study themes                                |
| Integrations  | Integration count, key integrations, categories                                      |
| Changelog     | Release velocity, recent focus areas, product direction                              |

**Step 3c — Reviews (deep profile only).** Use `firecrawl_scrape` or
`firecrawl_search` for G2, Capterra, Product Hunt, TrustRadius. Save
to `raw/<slug>/<date>/reviews/<source>.md`. Extract: overall rating,
review count, common praise themes, common complaint themes, 3-5
representative quotes.

Record `scrapes_saved` and `reviews_saved`.

## Gate 4 — SEO / market data (DataForSEO)

Save EACH raw response as JSON to `raw/<slug>/<date>/seo/<endpoint>.json`
BEFORE parsing into the profile.

**Endpoints to call (quick scan):**

| Endpoint                                       | What it provides                                      |
|------------------------------------------------|-------------------------------------------------------|
| `backlinks_summary`                            | Domain rank, total backlinks, referring domains, spam |
| `dataforseo_labs_google_domain_rank_overview`  | Domain-level organic metrics, traffic value           |
| `dataforseo_labs_google_ranked_keywords`       | Total ranking keywords, top 3/10/100, est. traffic    |

**Additional endpoints (deep profile):**

| Endpoint                                       | What it provides                                      |
|------------------------------------------------|-------------------------------------------------------|
| `backlinks_referring_domains`                  | Top referring domains, link acquisition pattern       |
| `dataforseo_labs_google_keywords_for_site`     | Targeted keywords, content gaps vs your site          |
| `dataforseo_labs_google_competitors_domain`    | Closest organic competitors, market overlap           |
| `dataforseo_labs_google_relevant_pages`        | Highest-traffic pages, top organic value content      |

Cross-reference site claims against SEO data (e.g., a "10,000 customers"
claim with very low traffic / few backlinks deserves a flag).

Record `seo_endpoints_saved`.

## Gate 5 — Synthesize

Combine scraped content + SEO data into the profile template. Save to
`competitor-profiles/<slug>.md`. Every claim must reference its source
file in `raw/<slug>/<date>/`.

**Profile template (sections required):**

1. `# [Name] - Competitor Profile` with URL, Generated date, Depth.
2. `## At a Glance` table: Tagline, Founded, HQ, Team size, Funding,
   Domain rank, Est. organic traffic, Referring domains, Organic keywords.
3. `## Positioning & Messaging`: primary value prop, target audience,
   positioning angle, 3 key messaging themes (with source page).
4. `## Product & Features`: core capabilities, differentiators,
   integrations, product-direction signals from changelog.
5. `## Pricing`: tier table, billing, free trial, notable quirks.
6. `## Customers & Social Proof`: named customers, industries, case
   study themes, review ratings (G2 / Capterra).
7. `## SEO & Content Strategy`: organic strength, top organic pages,
   content strategy signals, backlink profile.
8. `## Strengths & Weaknesses`: each with evidence source.
9. `## Competitive Implications for [Your Product]`: where they're
   strong vs us, where we're strong vs them, opportunities, threats.
10. `## Raw Data Sources`: list of scraped pages + SEO pulls + review
    sources with dates.

**Summary doc (`_summary.md`)** — only when multiple competitors:
landscape overview paragraph, comparison table of key metrics,
positioning map, 3-5 key takeaways, gaps and opportunities.

Count claims in the profile that lack a raw-file reference; record as
`unsourced_claims`.

Record `profile_path` and `summary_updated`.

---

## Quick Scan vs Deep Profile

| Element                | Quick Scan                  | Deep Profile                          |
|------------------------|-----------------------------|---------------------------------------|
| Scrape pages           | Homepage + Pricing only     | All key pages + review sites          |
| SEO endpoints          | 3 (Gate 4 quick scan list)  | All 7 (quick + deep lists combined)   |
| Reviews                | Skipped                     | G2 + Capterra + Product Hunt          |
| Output                 | Abbreviated profile         | Full template                         |

Default to **quick scan** unless user requests deep or competitor list
is <=3.

---

## Handling Multiple Competitors

1. **Parallelize scraping** — homepages first across all, then pricing
   across all, etc.
2. **Use consistent metrics** — same DataForSEO endpoints per competitor.
3. **Build the summary last** — after all individual profiles complete.
4. **Prioritize** — if user lists 10+ competitors, suggest top 5 by
   domain overlap / market similarity.

## Updating Profiles

Profiles are snapshots. On re-run:
- Re-check pricing first (most volatile).
- Re-pull SEO metrics (shift monthly).
- Scan changelog for product changes.
- Update Generated date; add a `## Change Log` section noting what
  changed since the prior snapshot.
- ALWAYS create a fresh date folder under `raw/<slug>/`.

---

## Why these gates

Profiles fail Review most often in two ways the gates catch:

- **Claims without raw-file references** (Gates 2 + 5) — the profile
  cannot be audited or re-used if the raw data is gone.
- **Inconsistent metrics across competitors** (Gate 4) — different
  endpoints called per competitor make profiles non-comparable, which
  defeats the comparison-table use case downstream.

See `references/tool-reference.md` for the full Firecrawl + DataForSEO
MCP tool list with example calls. See `references/templates.md` for
the full profile and summary markdown templates. Load only when the
in-skill tables don't cover your case.
