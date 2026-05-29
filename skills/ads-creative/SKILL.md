---
name: ads-creative
description: "Cross-platform creative quality audit procedure. Invoke when the user says creative audit, ad creative, creative fatigue, creative diversity score, ad variation audit, ad copy, ad design, or creative review. The body specifies a 5-gate procedure (platform quality bar / fatigue detection / refresh cadence / format diversity / variant drafting), a required ADS_CREATIVE_PASS output object the Review Agent uses, and the per-platform quality bars + fatigue thresholds + refresh cadence table that you cannot reproduce from this description alone."
metadata:
  version: 1.6.0
user-invokable: false
---

<!--
v1.6.0 — Restructured from narrative checklist to procedural creative gate.
Each gate updates a structured verdict + drafts new paid_variants where
fatigue thresholds are crossed. Domain content (per-platform quality bars,
fatigue thresholds, refresh cadence, format matrix) preserved verbatim.
Imported from https://github.com/AgriciDaniel/claude-ads (MIT License).
-->

# Cross-Platform Creative Quality Audit — procedure

The Paid Media Analyst Agent invokes this skill on every active platform.
Pull last-14-day performance from BigQuery (`telemetry.outcomes` joined with
`attribution_map` by `platform`). Run all 5 gates IN ORDER. Each gate
updates `ADS_CREATIVE_PASS`. Attach the object to the final output. Runs
without it are auto-rejected.

## Required output schema

```
ADS_CREATIVE_PASS = {
  "platforms_audited":      ["google", "meta", "linkedin", "tiktok", ...],
  "quality_bar_pass":       { "google": true|false, "meta": ..., ... },
  "fatigue_incidents":      <int>,
  "refresh_overdue":        [ {"platform": "...", "creative_id": "...", "days_overdue": <int>} ],
  "format_diversity_score": { "meta": "<count>/4", "linkedin": "<count>/4", ... },
  "variants_drafted":       <int>,
  "ops_incidents_opened":   <int>,
  "house_style_pass_count": <int>
}
```

Missing or malformed `ADS_CREATIVE_PASS` is treated as skill not applied.

---

## Gate 1 — Per-platform quality bar

For each active platform, score the live creative set against its quality
bar. Mark pass/fail into `quality_bar_pass[platform]`.

### Google Ads
- RSA: ≥8 unique headlines, ≥3 descriptions per ad group
- RSA ad strength: "Good" or "Excellent"
- Pin usage: minimal and strategic
- Extensions: sitelinks (≥4), callouts (≥4), structured snippets, image
- PMax asset groups: text + image + video + optional product feed

### Meta Ads
- Format diversity: ≥3 formats active (image, video, carousel, collection)
- Creative volume: ≥5 creatives per ad set
- Video length: 15s max Stories/Reels, 30s max Feed
- Headline under 40 chars, primary text under 125 chars
- UGC/testimonial content tested
- Advantage+ Creative enhancements enabled
- For Entity-ID clustering analysis, see `ads-meta`

### LinkedIn
- Thought Leader Ads active, ≥30% budget for B2B
- Format diversity: ≥2 formats tested (single image, carousel, video, document)
- Creative refresh: every 4-6 weeks
- Professional tone (NOT casual)

### TikTok
- ≥6 creatives per ad group (critical)
- All video 9:16 vertical 1080x1920 (non-negotiable)
- Native-looking content (not corporate)
- Hook in first 1-2 seconds
- No creative active >7 days with declining CTR
- Sound-on optimization (never silent)
- Safe zone compliance: X:40-940, Y:150-1470

## Gate 2 — Cross-platform fatigue detection

For each live creative, score against the fatigue thresholds. Open an
`ops_incident` (severity=high) on every creative that crosses a threshold.

| Signal                       | Threshold              | Action                       |
|------------------------------|------------------------|------------------------------|
| CTR declining                | >20% over 14 days      | Refresh creative             |
| Frequency (Meta prospecting) | >5.0                   | New audience or creative     |
| Frequency (Meta retargeting) | >12.0                  | New creative                 |
| Watch time declining (TikTok)| <3s average            | New hook needed              |
| QS declining (Google)        | Drop of 2+ points      | Refresh ad copy              |
| Engagement rate drop         | >30% decline           | Full creative overhaul       |

Increment `fatigue_incidents` and `ops_incidents_opened`.

## Gate 3 — Refresh cadence check

For each platform's active creative, compare days-since-launch to the
recommended refresh cadence. Any creative past its cadence goes into
`refresh_overdue`.

| Platform      | Recommended refresh                 |
|---------------|-------------------------------------|
| TikTok        | Every 7-10 days (fastest fatigue)   |
| Meta          | Every 14-21 days                    |
| LinkedIn      | Every 4-6 weeks                     |
| Google Search | Every 8-12 weeks                    |
| Microsoft     | Every 8-12 weeks                    |
| YouTube       | Every 4-8 weeks                     |

## Gate 4 — Format diversity matrix

Score each active platform on format coverage (count formats actually live).
Record into `format_diversity_score`.

| Format       | Google         | Meta | LinkedIn | TikTok        | Microsoft   |
|--------------|----------------|------|----------|---------------|-------------|
| Static Image | RSA image ext  | YES  | YES      | NO            | Multimedia  |
| Video        | YouTube, PMax  | YES  | YES      | YES (required)| YES (9:16)  |
| Carousel     | NO             | YES  | YES      | NO            | NO          |
| Collection   | NO             | YES  | NO       | NO            | NO          |
| Document     | NO             | NO   | YES      | NO            | NO          |

Aim for ≥3/4 applicable formats per platform.

## Gate 5 — Draft new variants

For every fatigued / refresh-overdue creative, draft a replacement variant
and land it in `paid_variants` with `status="paused"`. The **ImageBrief
Agent** renders. Each variant's copy MUST pass `house-style` voice rules and
cite an approved `messaging_library` claim or `customer_voice` quote.

Increment `variants_drafted` and `house_style_pass_count` (drafts that
cleared the voice gate). If platform is Meta, also load
`read_skill("ads-meta")` for the Entity-ID clustering predictor before
finalizing variants.

---

## Deep dives (call only when relevant)

- For **exhaustive per-platform creative specs** (resolutions, aspect ratios,
  file sizes, character limits per format, TikTok safe-zone overlays), call
  `read_skill_reference("ads-creative", "references/platform-specs.md")`.
- For **CTR / engagement / CPC / CPA benchmarks** by platform × industry
  (2026 data), call `read_skill_reference("ads-creative",
  "references/benchmarks.md")`.

## Why these specific gates

Creative is the highest-leverage performance lever in 2026 — Meta's
Andromeda + GEM + Lattice stack made creative the new targeting; Google AI
Max + DemandGen made creative diversity a budget multiplier; TikTok's
algorithm rewards refresh frequency. The five gates correspond to the five
recurring sources of paid-media waste:

- Wrong-format/wrong-spec creative — gate 1
- Late-detected fatigue — gate 2
- Stale-creative drag — gate 3
- Format mono-culture (and thus retrieval suppression) — gate 4
- Variant pipeline starvation — gate 5

> **House style wins.** All ad copy follows `house-style` voice rules.

See references above. Load only if a gate's threshold is contested or new
platform specs need encoding.
