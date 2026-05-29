---
name: content-strategy
description: Content-strategy planning procedure. Invoke when the user wants to plan a content strategy, decide what content to create, or figure out what topics to cover. Also use when the user mentions "content strategy," "what should I write about," "content ideas," "blog strategy," "topic clusters," "content planning," "editorial calendar," "content marketing," "content roadmap," "what content should I create," "blog topics," "content pillars," or "I don't know what to write." The body specifies a 6-gate procedure (context-gather, pillar-identification, ideation, buyer-stage mapping, scoring, output-shape) plus the weighted scoring formula (Customer Impact 40 / Content-Market Fit 30 / Search Potential 20 / Resources 10) and a required CONTENT_STRATEGY_PASS output object the Review Agent uses. You cannot produce a passing plan from this description alone — the pillar criteria, modifier tables, scoring weights, and output schema are only in the body. For writing individual pieces, see copywriting. For SEO audits, see seo-audit. For social media content, see social.
metadata:
  version: 2.1.0
---

<!--
v2.1.0 — Restructured from strategy guide to procedural planning gate.
Pillar criteria, keyword modifier tables, ideation sources, and scoring
matrix preserved under the gate that consumes them. Imported from
https://github.com/iannuttall/marketingskills (MIT License).
-->

# Content Strategy — planning procedure

Run all 6 gates IN ORDER. Each gate produces a tracked artifact. The
final output is a prioritized content plan with pillars, scored topics,
and a topic-cluster map. Attach a `CONTENT_STRATEGY_PASS` object so the
Review Agent can verify the skill was applied.

## Required output schema

Your final response MUST end with a JSON block in this exact shape:

```
CONTENT_STRATEGY_PASS = {
  "context_gathered":     "pass" | "fail:missing_<field>",
  "pillars_proposed":     <int — must be between 3 and 5>,
  "ideation_sources_used":<int — must be >= 2>,
  "topics_scored":        <int>,
  "topics_above_7":       <int>,
  "buyer_stages_covered": ["awareness" | "consideration" | "decision" | "implementation", ...],
  "cluster_map_included": "yes" | "no:rationale"
}
```

`pillars_proposed` outside the 3-5 range or `topics_above_7 = 0` is
treated as a failed plan. Missing or malformed
`CONTENT_STRATEGY_PASS` is treated as the entire skill not having
been applied.

---

## Gate 1 — Context gather

Read product-marketing context first if it exists:
`.agents/product-marketing.md`, `.claude/product-marketing.md`, or
legacy `product-marketing-context.md`. Only ASK for what's missing.

Required buckets:

1. **Business context** — what the company does, ICP, primary content
   goal (traffic / leads / brand awareness / thought leadership),
   problems product solves.
2. **Customer research** — questions customers ask before buying,
   objections in sales calls, recurring support topics, language they
   use.
3. **Current state** — existing content (what's working), resources
   (writers, budget, time), formats available (written / video / audio).
4. **Competitive landscape** — main competitors, known content gaps.

If a bucket is empty and the user expects a plan anyway, mark
`context_gathered = "fail:missing_<field>"` and proceed with stated
assumptions inline.

## Gate 2 — Pillar identification (3-5 pillars)

Content pillars are the 3-5 core topics the brand will own. Each
pillar spawns a cluster.

**Identify pillars using all four lenses:**

| Lens             | Question                                        |
|------------------|-------------------------------------------------|
| Product-led      | What problems does your product solve?          |
| Audience-led     | What does your ICP need to learn?               |
| Search-led       | What topics have volume in your space?          |
| Competitor-led   | What are competitors ranking for?               |

**Pillar criteria (each pillar must meet all four):**
- Aligns with product/service.
- Matches what the audience cares about.
- Has search volume AND/OR social interest.
- Broad enough to spawn many subtopics.

**Structural note.** Most content lives under `/blog` with internal
linking. Dedicated hub/spoke URL structures (`/guides/topic`) only
when building layered resources (Atlassian's `/agile`-scale guides).
Default to `/blog/post-title`.

Record `pillars_proposed` (must be 3-5).

## Gate 3 — Ideation (use at least 2 sources)

Pull topic ideas from at least 2 of these 6 sources. More is better.

**1. Keyword data** (Ahrefs / SEMrush / GSC exports): cluster related
keywords, tag by buyer stage and intent, find quick wins (low
competition + decent volume + high relevance), identify content gaps
(competitor ranks, you don't).

Output table:
| Keyword | Volume | Difficulty | Buyer Stage | Content Type | Priority |

**2. Call transcripts** (sales / customer calls): extract questions
asked, pain points, objections, language patterns, competitor mentions.

**3. Survey responses**: open-ended themes (30%+ mention = high
priority), resource requests, format preferences.

**4. Forum research**:
- Reddit: `site:reddit.com [topic]`
- Quora: `site:quora.com [topic]`
- Indie Hackers, Hacker News, Product Hunt, industry Slack/Discord
Extract FAQs, misconceptions, debates.

**5. Competitor analysis**: `site:competitor.com/blog` — top posts,
recurring topics, gaps, case-study patterns. Identify topics you can
cover better and angles they're missing.

**6. Sales and support input**: common objections, repeated questions,
ticket patterns, success stories, feature requests.

Record `ideation_sources_used` (must be >= 2).

## Gate 4 — Buyer-stage mapping

Map every proposed topic to a buyer stage using these modifier tables:

**Awareness** — "what is," "how to," "guide to," "introduction to"
Example: "What is Agile Project Management," "Guide to Sprint Planning."

**Consideration** — "best," "top," "vs," "alternatives," "comparison"
Example: "Best Project Management Tools for Remote Teams," "Asana vs
Trello vs Monday."

**Decision** — "pricing," "reviews," "demo," "trial," "buy"
Example: "Project Management Tool Pricing Comparison," "[Product]
Reviews."

**Implementation** — "templates," "examples," "tutorial," "how to use,"
"setup"
Example: "Project Template Library," "Step-by-Step Setup Tutorial."

**Searchable vs Shareable framing.** Every piece must be searchable,
shareable, or both:
- Searchable captures existing demand (keyword targeting, intent match,
  comprehensive coverage).
- Shareable creates demand (novel insight, original data, counter-
  intuitive take, story-driven).
Prioritize searchable first — it's the foundation.

Record `buyer_stages_covered` (which stages appear in your plan).

## Gate 5 — Scoring (weighted formula)

Score each topic on 4 weighted factors. Total = weighted sum.

| Factor                     | Weight | What to assess                                                |
|----------------------------|--------|---------------------------------------------------------------|
| Customer Impact            | 40%    | Frequency in research, % of customers, emotional charge, LTV  |
| Content-Market Fit         | 30%    | Product alignment, unique insights, customer stories, drives interest |
| Search Potential           | 20%    | Monthly volume, competition, long-tail opportunities, growth  |
| Resource Requirements      | 10%    | Expertise available, research needed, asset cost (inverse)    |

Each factor scored 1-10. Weighted total = (CI*0.4)+(CMF*0.3)+(SP*0.2)+(R*0.1).

Output table:
| Idea | Customer Impact (40%) | Content-Market Fit (30%) | Search Potential (20%) | Resources (10%) | Total |

Topics scoring >= 7.0 go into the priority list. Topics 5.0-6.9 go on
the bench. Topics < 5.0 are cut (or surface why if the user wanted
them retained).

Record `topics_scored` (total scored) and `topics_above_7` (count
making the priority list).

## Gate 6 — Output shape (pillars + topics + cluster map)

Final deliverable has three sections:

**1. Content pillars (3-5)** — name + rationale + how it connects to
product.

**2. Priority topics** — for each topic that scored >= 7:
- Topic / working title
- Searchable, shareable, or both
- Content type (use-case, hub/spoke, thought leadership, data-driven,
  expert roundup, case study, meta content)
- Target keyword + buyer stage
- Why this topic (customer-research backing — cite the source)

**3. Topic cluster map** — visual or structured representation of how
content interconnects (which spokes belong to which pillar, which
posts link to which).

```
Pillar Topic (Hub)
|-- Subtopic Cluster 1
|   |-- Article A
|   |-- Article B
|-- Subtopic Cluster 2
|   |-- Article C
|   |-- Article D
```

Record `cluster_map_included`.

---

## Content type quick reference

| Type                | When to use                                          |
|---------------------|------------------------------------------------------|
| Use-case content    | "[persona] + [use-case]" long-tail targeting         |
| Hub and spoke       | Layered authoritative coverage on a single topic     |
| Template libraries  | High-intent + product-adoption play                  |
| Thought leadership  | Articulating a felt-but-unnamed concept              |
| Data-driven         | Original analysis, anonymized product data           |
| Expert roundups     | 15-30 experts, one question, built-in distribution   |
| Case studies        | Challenge -> Solution -> Results -> Learnings        |
| Meta content        | Behind-the-scenes transparency                       |

For programmatic content at scale, see the **programmatic-seo** skill.

---

## Why these gates

Content plans fail when they:
- Skip the pillar criteria (Gate 2) and end up with a flat list of
  topics that don't compound.
- Ideate from only one source (Gate 3) — usually keyword tools — and
  miss customer-research signal.
- Score topics by gut (Gate 5) instead of the weighted formula, which
  is why the formula leads with **Customer Impact at 40%**.
- Lack a cluster map (Gate 6) — the plan reads as a list, not a system.

See `references/headless-cms.md` for CMS selection, content modeling,
editorial workflows, and platform comparison (Sanity, Contentful,
Strapi). Load only when CMS / tooling decisions are in scope.
