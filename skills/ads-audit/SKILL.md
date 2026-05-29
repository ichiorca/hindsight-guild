---
name: ads-audit
description: "Full multi-platform ads audit procedure with parallel subagent delegation across Google, Meta, LinkedIn, TikTok, Microsoft, Apple. Invoke when user says audit, full ad check, analyze my ads, account health check, paid media audit, paid advertising audit, ad spend audit, advertising audit, or PPC audit. The body specifies an 8-step procedure (collect / validate / detect / dispatch / aggregate / score / prioritize / report), a required ADS_AUDIT_PASS output object the Review Agent uses, and the per-platform weighting table + aggregate scoring formula that you cannot reproduce from this description alone."
metadata:
  version: 1.6.0
user-invokable: false
tested_date: 2026-05-17
tested_with: claude-code v2.x
---

<!--
v1.6.0 — Restructured from narrative checklist to procedural orchestration
gate. Each step updates the audit output object. Domain content (per-platform
weights, aggregate formula, priority + quick-win definitions) preserved
verbatim. Imported from https://github.com/AgriciDaniel/claude-ads
(MIT License).
-->

# Full Multi-Platform Ads Audit — orchestration procedure

This audit dispatches up to 6 platform subagents in parallel and aggregates
their verdicts into an account-wide health score. Operates under the
**10-Principle Thinking Framework** (see `references/thinking-framework.md`):
OBSERVE dominates collection; THINK + CONNECT (Lateral) dominate analysis;
CONNECT (System) + ACCEPT dominate synthesis. If the audit feels mechanical,
you are skipping a principle.

Run all 8 steps IN ORDER. Attach `ADS_AUDIT_PASS` to the final output. The
Review Agent reads it — audits without it are auto-rejected.

## Required output schema

```
ADS_AUDIT_PASS = {
  "business_type":       "saas_b2b|ecommerce|local|b2b_enterprise|info|mobile_app",
  "active_platforms":    ["google", "meta", ...],
  "platform_scores":     { "google": <int 0-100>, "meta": <int>, ... },
  "aggregate_score":     <int 0-100>,
  "grade":               "A" | "B" | "C" | "D" | "F",
  "critical_count":      <int>,
  "high_count":          <int>,
  "quick_wins_count":    <int>,
  "deliverables_written":["ADS-AUDIT-REPORT.md", "ADS-ACTION-PLAN.md", "ADS-QUICK-WINS.md"]
}
```

Missing or malformed `ADS_AUDIT_PASS` is treated as the skill not applied.

---

## Step 1 — Collect account data

Request exports, screenshots, or API access. Accept any combination:
- Google Ads: account export, Change History, Search Terms Report
- Meta Ads: Ads Manager export, Events Manager screenshot, EMQ scores
- LinkedIn Ads: Campaign Manager export, Insight Tag status
- TikTok Ads: Ads Manager export, Pixel/Events API status
- Microsoft Ads: account export, UET tag status, import validation results

If no exports, audit from screenshots or manual entry.

## Step 2 — Validate data availability

Confirm at least one platform has usable data. If zero, halt and ask the
user. Do not proceed with empty audit.

## Step 3 — Detect business type

Analyze account signals per the ads orchestrator. Record into
`ADS_AUDIT_PASS.business_type`.

## Step 4 — Identify active platforms

List every platform with delivered impressions in the last 30 days. Record
into `active_platforms`.

## Step 5 — Dispatch subagents (parallel if available, else sequential)

Per the Wave 3 plan, dispatch these in parallel:

- `audit-google`: Conversion, waste, structure, keywords, ads, settings (80 checks; G01-G61 + 19 hyphenated v1.5+ IDs incl. AI Max)
- `audit-meta`: Pixel/CAPI, creative fatigue, structure, audience (50 checks; M01-M40 + 10 hyphenated v1.5+ IDs incl. Andromeda)
- `audit-creative`: LinkedIn, TikTok, Microsoft creative + cross-platform synthesis
- `audit-tracking`: LinkedIn, TikTok, Microsoft tracking + cross-platform tracking health
- `audit-budget`: LinkedIn, TikTok, Microsoft budget/bidding + cross-platform allocation
- `audit-compliance`: All-platform compliance, settings, benchmarks

Amazon, cross-platform attribution, and server-side tracking are covered by
their standalone sub-skills (`ads-amazon`, `ads-attribution`,
`ads-server-side-tracking`) — Wave 3 will pair these with agents for
parallel dispatch.

## Step 6 — Validate subagent returns

Confirm each subagent returned valid scores with required fields before
aggregating. Re-dispatch on malformed returns. Do not silently swallow.

## Step 7 — Score per-platform and aggregate

### Per-platform weights

| Platform  | Category weights                                                                       |
|-----------|----------------------------------------------------------------------------------------|
| Google    | Conversion 25%, Waste 20%, Structure 15%, Keywords 15%, Ads 15%, Settings 10%          |
| Meta      | Pixel/CAPI 30%, Creative 30%, Structure 20%, Audience 20%                              |
| LinkedIn  | Tech 25%, Audience 25%, Creative 20%, Lead Gen 15%, Budget 15%                         |
| TikTok    | Creative 30%, Tech 25%, Bidding 20%, Structure 15%, Performance 10%                    |
| Microsoft | Tech 25%, Syndication 20%, Structure 20%, Creative 20%, Settings 15%                   |

### Aggregate formula

```
Aggregate = Sum(Platform_Score × Platform_Budget_Share)
Grade: A (90-100), B (75-89), C (60-74), D (40-59), F (<40)
```

Full algorithm: `references/scoring-system.md`.

Record `platform_scores`, `aggregate_score`, `grade`.

## Step 8 — Write deliverables and prioritize

Write all three files:

- `ADS-AUDIT-REPORT.md` — exec summary (aggregate + per-platform scores, top 5 critical, top 5 quick wins), per-platform sections (score, category breakdown, findings), cross-platform analysis (budget allocation, tracking consistency, creative consistency, attribution overlap), strategic recommendations (platform prioritization, budget reallocation, scaling opportunities, kill list)
- `ADS-ACTION-PLAN.md` — recommendations sorted Critical > High > Medium > Low
- `ADS-QUICK-WINS.md` — items fixable in <15 minutes with high impact

### Priority definitions

- **Critical**: Revenue/data loss risk (fix immediately)
- **High**: Significant performance drag (fix within 7 days)
- **Medium**: Optimization opportunity (fix within 30 days)
- **Low**: Best practice, minor impact (backlog)

### Quick-win criteria

```
IF severity == "Critical" OR severity == "High"
AND estimated_fix_time < 15 minutes
THEN flag as Quick Win
SORT BY (severity_multiplier × estimated_impact) DESC
```

Record `critical_count`, `high_count`, `quick_wins_count`,
`deliverables_written`.

---

## Why this orchestration

The 10-Principle Framework + parallel-subagent pattern exists because
sequential single-agent audits routinely missed cross-platform issues
(double-counted conversions, inconsistent creative messaging, budget
misallocation). Per-platform weights mirror where each platform's
performance lever actually lives — e.g. Meta is Creative-dominated post-
Andromeda; Google is Conversion-tracking + Waste dominated. Aggregating by
budget share (not by platform count) ensures the score reflects where the
money is.

See `references/scoring-system.md` for the per-platform algorithm and
`references/thinking-framework.md` for the 10 principles. Load only if a
weight is contested or a new platform needs scoring.
