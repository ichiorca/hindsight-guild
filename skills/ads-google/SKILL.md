---
name: ads-google
description: "Google Ads deep analysis procedure across Search, PMax, AI Max, Display, YouTube, Demand Gen. Invoke when user says Google Ads, Google PPC, search ads, PMax, Performance Max, AI Max, AI Brief, broad match audit, Quality Score check, search terms audit, Smart Bidding, or Google campaign. The body specifies a 7-gate procedure (data sufficiency / 6 weighted categories / AI Max audit / PMax + DemandGen / threshold scoring / Health Score / deliverables), a required ADS_GOOGLE_PASS output object the Review Agent uses, and the 80-check rubric + category weights + DSA migration pre-flight + threshold table that you cannot reproduce from this description alone."
metadata:
  version: 1.6.0
user-invokable: false
tested_date: 2026-05-17
tested_with: claude-code v2.x
---

<!--
v1.6.0 — Restructured from narrative 80-check list to procedural Google-Ads
gate. Each gate emits a category score; weighted aggregation produces the
Health Score. Domain content (every check, AI Max rubric, DSA pre-flight
checklist, GAQL dedup rules, thresholds) preserved verbatim. Imported from
https://github.com/AgriciDaniel/claude-ads (MIT License).
-->

# Google Ads Deep Analysis — procedure

Collect account export, Change History, Search Terms Report. Run all 7
gates IN ORDER. Each gate updates `ADS_GOOGLE_PASS`. Attach the object to
the final response. The ads-audit orchestrator and the Review Agent both
read it — runs without it are auto-rejected.

## Required output schema

```
ADS_GOOGLE_PASS = {
  "data_window_days":          <int>,
  "data_sufficient":           true | false,
  "checks_evaluated":          <int>,
  "category_scores": {
    "conversion_tracking":     <int 0-100>,
    "wasted_spend":            <int>,
    "account_structure":       <int>,
    "keywords":                <int>,
    "ads":                     <int>,
    "settings":                <int>
  },
  "ai_max_audit":              "n/a" | "PASS" | "WARNING" | "FAIL",
  "dsa_migration_risk":        [ {"campaign": "...", "risk": "LOW|MEDIUM|HIGH"} ],
  "wasted_spend_monthly_usd":  <int>,
  "health_score":              <int 0-100>,
  "grade":                     "A" | "B" | "C" | "D" | "F",
  "report_written":            "GOOGLE-ADS-REPORT.md"
}
```

Missing or malformed `ADS_GOOGLE_PASS` is treated as skill not applied.

Before running gates, **read** `references/google-audit.md` (full 80-check
audit), `references/benchmarks.md`, and `references/scoring-system.md`. For
GAQL field incompatibilities, dedup patterns, and filter scope, read
`references/gaql-notes.md`. Key rules:

- Deduplicate keywords by `(ad_group_id + keyword_text + match_type)`
- Only analyze ENABLED campaigns and ad groups
- Filter to keywords with impressions > 0 for theme coherence (G03)
- Apply legacy BMM heuristic: BROAD + Manual CPC = legacy BMM, not intentional (G17)
- Only flag wasted spend on terms with >$10 spend AND 0 conversions (G16)
- Count shared negative keyword lists alongside campaign-level negatives (G14/G15)

If MCP available: use the Google Ads MCP server (`search`,
`list_accessible_customers`). Customer ID from CLAUDE.md > Accounts >
Google Ads, or ask. Fallback to manual exports.

---

## Gate 1 — Data sufficiency

Confirm data covers ≥30 days AND includes Search Terms Report. If not, set
`data_sufficient: false` and HALT. Do not fabricate scores.

## Gate 2 — Conversion Tracking (25% weight)

Evaluate each check PASS / WARNING / FAIL:

- Google tag (gtag.js) installed and firing on all pages
- Enhanced Conversions active (hashed first-party data)
- Consent Mode v2 implemented (EEA mandatory)
- Conversion actions mapped (primary vs secondary)
- Offline conversion import configured (lead gen)
- Server-side tagging via GTM (recommended)
- Attribution model: data-driven preferred (last-click only as fallback)
- Conversion lag analysis active

Score into `category_scores.conversion_tracking`.

## Gate 3 — Wasted Spend (20% weight)

- Search Terms Report reviewed (last 30 days minimum)
- Negative keyword coverage adequate (shared lists + campaign-level)
- Display placement audit (exclude low-quality sites)
- Invalid click rate within norms (<10%)
- Broad Match only used with Smart Bidding (NEVER without it)
- Brand/non-brand campaigns separated
- Geographic targeting precise (no wasted international spend)

**Negative keyword rules (critical: bad negatives kill campaigns):**
- NEVER suggest Broad Match negatives unless explicitly justified
- Default to **Exact Match** `[keyword]` for specific irrelevant queries
- Use **Phrase Match** `"keyword"` for irrelevant intent patterns
- Source negatives from actual Search Terms Report, NOT guesses
- Group into themed lists: Informational (how-to, DIY, what is), Job-seeker (jobs, careers, salary), Competitor (only if intentionally excluded), Free-intent (free, crack, torrent)
- Recommend **Shared Negative Lists** at account level, not just campaign-level
- Review existing negatives for over-blocking

Compute `wasted_spend_monthly_usd` from terms with >$10 spend AND 0 conv.
Score into `category_scores.wasted_spend`.

## Gate 4 — Structure / Keywords / Ads / Settings (15+15+15+10%)

### Account Structure (15%)
- Campaign-level organization follows business logic
- Ad groups themed tightly (15-20 keywords max)
- RSA ad groups have ≥3 active ads
- PMax campaigns structured correctly (asset groups, signals)
- SKAGs evaluated (migrate to themed groups if present)
- Campaign labels/naming conventions consistent

### Keywords (15%)
- Match type strategy appropriate (Exact → Phrase → Broad progression)
- Quality Score distribution (aim ≥7 average)
- Low QS keywords flagged (<5 = FAIL, 5-6 = WARNING)
- Keyword cannibalization check
- Impression share tracked for top keywords
- Keyword bid adjustments set for devices/locations/audiences

### Ads (15%)
- RSA: ≥8 unique headlines, ≥3 descriptions per ad group
- RSA ad strength: "Good" or "Excellent" (not "Poor"/"Average")
- Pin usage minimal and strategic
- Extensions: sitelinks (≥4), callouts (≥4), structured snippets, image
- Dynamic keyword insertion used appropriately
- Ad copy includes CTA, value prop, differentiators

### Settings (10%)
- ECPC flagged as deprecated. Migrate to Smart Bidding (tCPA/tROAS/Maximize)
- Bid strategy appropriate for campaign maturity and goals
- Budget pacing: no campaigns limited by budget (unless intentional)
- Ad schedule aligned with business hours / conversion patterns
- Device bid adjustments set based on performance data
- Location targeting: "Presence" not "Presence or Interest"
- Network settings: Search Partners reviewed, Display opt-out for Search

Score each category 0-100 into `category_scores`.

## Gate 5 — AI Max for Search audit (if active)

AI Max layers broad match + keywordless targeting on existing Search campaigns.
14% avg conversion lift for non-retail brands at similar CPA/ROAS (Google Ads
blog, May 2025). **DSA, ACA, and campaign-level broad match auto-migrate
into AI Max by end of September 2026**; new DSA campaign creation via API
ends Sept 2026. Strong negative keyword lists are a hard prerequisite.
Independent data across 250+ campaigns: +13% median revenue, +16% median
CPA — set expectations accordingly.

### Detection
`campaign.ai_max_setting.enable_ai_max` (Google Ads API v21+).

### Audit checklist (if active or eligible)
- **Field check** — `enable_ai_max = true` for eligible Search campaigns (or documented opt-out)
- **Broad Match + Smart Bidding combo verified**
- **Search Term Matching** — FAIL if broader-match share >60% on non-Smart-Bidding campaign
- **AI Brief configured** — business name, value prop (≤200 chars), target audience, forbidden topics, disclaimers
- **Text customization rules** — locked legal phrases, banned competitor names, approved disclaimers, pin discipline
- **Final URL Expansion (FUE) controls** — include/exclude lists prevent routing to checkout-skip, password-gated, 404 pages
- **Brand exclusions applied** (same mechanism as PMax)
- **Text disclaimers** (rolling out May 2026+) — populated for health/finance/legal/crypto verticals
- **Budget impact** — AI Max can shift spend ±30% in first 7 days
- **Negative keyword coverage** — AI Max broadens reach 3-5×; scale negatives accordingly

### DSA Migration Pre-Flight Checklist

The Sept 2026 auto-migration moves DSA / ACA / campaign-level broad-match
Search into AI Max whether or not the advertiser is ready. Pre-flight:

- [ ] Inventory DSA campaigns (`campaign.advertising_channel_sub_type IN (SEARCH_DYNAMIC, ...)`)
- [ ] Inventory ACA-enabled campaigns (`campaign.ad_strength_settings` w/ auto-generated headlines)
- [ ] Inventory campaign-level broad-match Search campaigns w/o AI Max enabled
- [ ] Tracking template audit — verify `{lpurl}` ValueTrack params resolve under FUE
- [ ] Negative keyword pre-staging — last 90 days of DSA search terms staged as Exact/Phrase negatives
- [ ] AI Brief drafted per migrating campaign
- [ ] URL controls staged — FUE include/exclude lists per campaign
- [ ] Brand exclusion lists prepared (PMax format)
- [ ] Bidding strategy migration — Manual CPC / ECPC must move to Smart Bidding pre-migration
- [ ] Conversion tracking pre-flight — Enhanced Conversions + Consent Mode V2 active and verified
- [ ] Reporting baseline — 28-day pre-migration metrics captured (CTR, CVR, CPA, ROAS, Search Lost IS)

Per-campaign risk: **LOW** (Smart Bidding + strong negatives + good Brief),
**MEDIUM** (Smart Bidding but weak negatives OR no Brief), **HIGH** (Manual
CPC, weak negatives, generic Brief). Stage LOW → HIGH; pause MEDIUM/HIGH if
conversion volume drops >25% in first 7 days.

Record into `dsa_migration_risk[]` and set `ai_max_audit` overall verdict.

## Gate 6 — PMax + Demand Gen deep dive (if active)

### PMax additional checks
- Asset group diversity (text, images, video, feeds)
- Audience signals configured (custom segments, lists, demographics)
- URL expansion settings reviewed
- Brand exclusions applied (campaign-level, all advertisers)
- Campaign-level negative keywords (now available for ALL advertisers)
- Search themes utilized (2024 feature)
- Final URL expansion: enabled or disabled w/ justification
- Insights tab reviewed (search categories, audience segments)

### Demand Gen (replaced Video Action Campaigns, auto-upgrade Jul 2025)
- Video + image asset mix present (combined drives 20% more conversions vs video-only at same CPA)
- Audience signals configured (custom segments, lookalikes)
- Conversion tracking aligned with upper/mid-funnel goals
- Note: frequency capping NOT available; monitor reach vs frequency manually

## Gate 7 — Threshold scoring, Health Score, deliverables

### Key thresholds

| Metric              | Pass    | Warning      | Fail    |
|---------------------|---------|--------------|---------|
| Quality Score (avg) | ≥7      | 5-6          | <5      |
| CTR (Search)        | ≥6.66%  | 3-6.66%      | <3%     |
| CVR (Search)        | ≥7.52%  | 3-7.52%      | <3%     |
| CPC (Search)        | ≤$5.26  | $5.26-8.00   | >$8.00  |
| Wasted Spend        | <10%    | 10-20%       | >20%    |
| Ad Strength         | Good+   | Average      | Poor    |
| Invalid Clicks      | <5%     | 5-10%        | >10%    |

### Confirm all 80 checks evaluated

Set `checks_evaluated`. If <80, HALT and complete remaining checks.

### Compute Health Score

Weighted aggregate of `category_scores` using the 25/20/15/15/15/10 weights.
Grade: A (90-100), B (75-89), C (60-74), D (40-59), F (<40). Record into
`health_score` + `grade`.

### Write `GOOGLE-ADS-REPORT.md`

Include: per-category bars with weight markers, full 80-check findings,
wasted spend monthly $, Quick Wins sorted by impact, PMax recommendations,
keyword health matrix (QS, CTR, CVR per keyword group).

Format the score block:

```
Google Ads Health Score: XX/100 (Grade: X)

Conversion Tracking: XX/100  ████████░░  (25%)
Wasted Spend:        XX/100  ██████████  (20%)
Account Structure:   XX/100  ███████░░░  (15%)
Keywords:            XX/100  █████░░░░░  (15%)
Ads:                 XX/100  ████████░░  (15%)
Settings:            XX/100  ██████████  (10%)
```

---

## Why these specific gates

The 80-check rubric and category weights correspond to where Google Ads
performance is actually lost: conversion tracking (25%) is where 30%+ of
accounts mis-attribute; wasted spend (20%) is where broad-match-without-
Smart-Bidding bleeds 20-40% of budget; AI Max + DSA migration (gate 5) is
the single largest 2026 disruption — accounts that don't pre-flight will
lose conversion volume at the Sept 2026 auto-migration. PMax + Demand Gen
deep dives (gate 6) are conditional because both campaign types have
distinct audit rubrics that don't apply to Search-only accounts.

See `references/google-audit.md` for the full 80-check definitions and
`references/gaql-notes.md` for GAQL dedup/filter rules. Load only if a
check's threshold is contested or a new feature needs encoding.
