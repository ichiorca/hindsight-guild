---
name: ads-budget
description: "Budget allocation and bidding strategy gate. Invoke when the user says budget allocation, bidding strategy, ad spend, ROAS target, media budget, scaling, 'should I scale,' 'should I kill,' or '70/20/10.' The body specifies a 6-gate procedure (data sufficiency / 70-20-10 / platform-bidding / kill rule / scale rule / scaling readiness), a required ADS_BUDGET_PASS output object the Review Agent uses, and the platform selection matrix + budget floor table + bidding decision trees that you cannot reproduce from this description alone."
metadata:
  version: 1.6.0
user-invokable: false
---

<!--
v1.6.0 — Restructured from narrative rules to procedural budget gate. Each
gate emits a structured verdict + a kill/scale candidate list. Domain content
(70/20/10, platform matrix, floors, decision trees, 3× Kill Rule, 20%
Scaling Rule, scaling readiness checklist) preserved verbatim. Imported from
https://github.com/AgriciDaniel/claude-ads (MIT License).
-->

# Budget Allocation & Bidding Strategy — procedure

Collect last-14-day spend + performance across all active platforms. Run all
6 gates IN ORDER. Each gate updates a slot in `ADS_BUDGET_PASS`. Attach the
output object to the final response. The Paid Media Analyst Agent and the
Review Agent both depend on it — runs without it are auto-rejected.

## Required output schema

```
ADS_BUDGET_PASS = {
  "data_window_days":         <int>,
  "data_sufficient":          true | false,
  "allocation_70_20_10":      "pass" | "fail:<proven>%/<scaling>%/<testing>%",
  "bidding_strategy_audits":  [ {"platform": "...", "current": "...", "recommended": "...", "reason": "..."} ],
  "kill_candidates":          [ {"campaign": "...", "cpa_vs_target": <float>, "attempts": <int>} ],
  "scale_candidates":         [ {"campaign": "...", "stable_days": <int>, "next_budget_pct": <int>} ],
  "scaling_readiness_pass":   <int>,
  "scaling_readiness_fail":   <int>,
  "ops_incidents_opened":     <int>,
  "paid_variants_proposed":   <int>
}
```

Missing or malformed `ADS_BUDGET_PASS` is treated as skill not applied.

---

## Gate 1 — Data sufficiency

Confirm spend data covers **≥14 days**. Kill candidates must additionally
have **≥20 clicks OR ≥$100 spend** before recommending pause. If
insufficient, set `data_sufficient: false` and HALT the kill/scale gates;
still run allocation review on whatever data exists.

## Gate 2 — 70/20/10 allocation review

| Tier         | Share    | What                                                  |
|--------------|----------|-------------------------------------------------------|
| **Proven**   | **70%**  | Channels consistently meeting ROAS/CPA targets        |
| **Scaling**  | **20%**  | Showing promise, need more data                       |
| **Testing**  | **10%**  | New platforms, audiences, creatives                   |

Score the account's actual split. PASS if all three tiers are within ±5% of
the targets. Otherwise FAIL and record actual percentages.

### Platform selection matrix (cross-reference with detected business type)

| Business type    | Primary                    | Secondary           | Testing               |
|------------------|----------------------------|---------------------|-----------------------|
| SaaS B2B         | Google Search, LinkedIn    | Meta, YouTube       | TikTok, Microsoft     |
| E-commerce       | Google Shopping, Meta      | TikTok, YouTube     | Microsoft, LinkedIn   |
| Local Service    | Google Search, Google LSA  | Meta                | Microsoft, YouTube    |
| B2B Enterprise   | LinkedIn, Google Search    | Meta                | Microsoft, TikTok     |
| Info Products    | Meta, YouTube              | Google Search       | TikTok                |
| Mobile App       | Meta, Google UAC           | TikTok              | Apple Ads             |

Flag any platform outside the 70/20/10 bands for the weekly CMO Planner memo.

## Gate 3 — Per-platform bidding audit

For each active platform, confirm budget meets the floor AND bidding strategy
matches campaign maturity:

### Budget sufficiency floors

| Platform        | Minimum daily            | Learning-phase budget                     |
|-----------------|--------------------------|-------------------------------------------|
| Google Search   | $20/day                  | Sufficient for 15+ conv/month             |
| Google PMax     | $50/day                  | Algorithm optimization                    |
| Meta            | $20/day per ad set       | ≥5× target CPA per ad set                 |
| LinkedIn        | $50/day Sponsored Content| 15+ conversions/month                     |
| TikTok          | $50/day campaign, $20/ad group | ≥50× target CPA per ad group        |
| Microsoft       | No strict minimum        | Sufficient for stable delivery            |

### Google bidding decision tree

```
< 30 conversions/month?   → Maximize Clicks (cap CPC at benchmark)
30-50 conversions/month?  → Maximize Conversions
> 50 conversions/month?   → Target CPA
Revenue tracking + > 50?  → Target ROAS
```

### Meta strategies
**Lowest Cost** (default, volume); **Cost Cap** (CPA ceiling); **Bid Cap**
(max bid per auction); **ROAS Goal**; **CBO vs ABO** — CBO for proven, ABO
for testing.

### LinkedIn
**Manual CPC** (start, cost control); **Cost Cap** (efficiency at scale);
**Maximum Delivery** (highest volume, most expensive — use only for scale);
**Target Cost** (predictable CPA).

### TikTok
**Lowest Cost** (maximize conversions within budget); **Cost Cap** (set max
CPA). Budget **≥50× CPA per ad group** for learning-phase exit.

### Microsoft
Mirror Google strategy at **20-35% lower bids**. Manual CPC for low-volume.
Target CPA / Target ROAS for automated (requires 15+ conv/30d).

### 2026 innovations to flag

- **Google AI Max for Search** — ~13-16% revenue/CPA lift with mature
  conversion tracking + strong negative lists
- **TikTok Smart+ Modular Control** — lock targeting/creative/budget/placement
  independently; ~53% lift over standard
- **Apple Ads Maximize Conversions** — GA Feb 26, 2026; daily budget ≥5×
  target CPA; two-week learning phase
- **Meta Advantage+** — automatic placement optimization; best with broad
  targeting + high creative volume

Record any mismatch in `bidding_strategy_audits`.

## Gate 4 — The 3× Kill Rule

If a campaign's **CPA is > 3× target** AND it has had **3+ optimization
attempts**, accept that the campaign is dead. **Open an ops_incident
(severity=high)** recommending pause. Don't keep tweaking.

Add to `kill_candidates`. Increment `ops_incidents_opened`.

## Gate 5 — The 20% Scaling Rule

Never increase a budget by more than **20% at a time** — Smart Bidding
relearns when budget changes are large, and you lose the optimization
history. Step in 15-20% increments with a 5-7 day stabilization between each
step.

For each scale candidate, record proposed `next_budget_pct` in
`scale_candidates`. Open as `paid_variants` proposals with `status="paused"`
so the founder activates explicitly. Increment `paid_variants_proposed`.

## Gate 6 — Scaling readiness checklist

Before recommending scaling, confirm ALL:

- Campaign has **≥30 days** of stable performance
- CPA within **20% of target** for 14+ days
- Frequency below saturation (Meta: <5.0 prospecting)
- No quality score / engagement decline trend
- Creative fatigue not imminent (see `ads-creative` skill)
- Landing page conversion rate stable

Pass-count → `scaling_readiness_pass`; fail-count → `scaling_readiness_fail`.
Any candidate failing the checklist is removed from `scale_candidates`.

---

## Why these specific gates

Founder-edit data + post-mortems on scaled-too-fast campaigns show these
gates correspond to the most expensive recurring mistakes:

- **70/20/10** prevents the most common reallocation error: over-investing in
  testing while starving proven channels.
- **3× Kill Rule** caps the "one more optimization" tax — most accounts have
  multiple zombie campaigns at >5× CPA still receiving budget.
- **20% Scaling Rule** is forced by Smart Bidding behavior. Larger steps
  trigger a relearn that costs 5-14 days of suboptimal delivery.
- **Scaling readiness checklist** mirrors the actual failure modes of
  prematurely scaled campaigns (frequency saturation, creative fatigue, LP
  conversion regression).

> **House style wins** for any copy generated in scaled or new variants.

See `references/bidding-deep-dive.md` for platform-specific edge cases
(e.g. Meta Andromeda budget shifts, Apple Ads learning-phase tuning). Load
only if a bidding audit is contested or a new strategy needs scoring.
