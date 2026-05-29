---
name: ads-math
description: "PPC financial calculator and modeling procedure. Invoke when the user says PPC math, ad calculator, break-even, budget forecast, ROAS calculator, CPA calculator, impression share, LTV CAC, MER, payback period, or 'do the math on this campaign.' The body specifies an 8-calculator procedure (CPA / ROAS / break-even / IS opportunity / budget forecast / LTV:CAC / MER / payback), a required ADS_MATH_PASS output object the Review Agent uses, and the formulas + interpretation tables that you cannot reproduce from this description alone."
metadata:
  version: 1.6.0
user-invokable: false
---

<!--
v1.6.0 — Restructured from narrative calculator list to procedural math
gate. Each calculator emits a tracked result + interpretation. Domain
content (every formula, every interpretation table, payback targets, MER
brackets) preserved verbatim. Imported from
https://github.com/AgriciDaniel/claude-ads (MIT License).
-->

# PPC Financial Calculator & Modeling — procedure

Detect (or ask) which calculation the user needs. Collect inputs from
pasted data, exports, or verbal description. **Always show the formula
BEFORE the result** — marketers don't trust math they can't audit. Present
results with interpretation, benchmark comparison, and a specific
recommendation. Flag any concerning metric. Attach `ADS_MATH_PASS` to the
final response. Runs without it are auto-rejected.

## Required output schema

```
ADS_MATH_PASS = {
  "calculator_used":     "cpa" | "roas" | "break_even" | "is_opportunity"
                       | "budget_forecast" | "ltv_cac" | "mer" | "payback",
  "formula_shown":       true | false,
  "inputs":              { ... },
  "result":              <number or object>,
  "benchmark_compared":  true | false,
  "interpretation":      "...",
  "recommendation":      "scale" | "maintain" | "cut" | "investigate" | "increase_budget" | "raise_pricing" | "improve_quality",
  "concerning_flags":    [ "..." ]
}
```

If multiple calculators run in one turn, emit one `ADS_MATH_PASS` object
per calculation. Missing or malformed → skill not applied.

`formula_shown` MUST be `true`. Never present a result without first
showing the formula.

---

## 1. CPA Calculator

```
CPA = Total Spend / Total Conversions
```

**Inputs**: spend + conversions over a period.
**Output**: CPA, trend (if historical), benchmark comparison.

## 2. ROAS Calculator

```
ROAS  = Revenue from Ads / Ad Spend
ROAS% = (Revenue − Spend) / Spend × 100
```

**Inputs**: ad spend + revenue attributed to ads.
**Output**: ratio (e.g. 3.5×) and percentage (250%), break-even ROAS if
margins provided, comparison to platform benchmarks.

## 3. Break-Even Analysis

```
Break-Even CPA  = Average Order Value × Profit Margin
Break-Even ROAS = 1 / Profit Margin
```

**Inputs**: AOV (or average deal value), profit margin, current CPA/ROAS.
**Output**: max profitable CPA, min profitable ROAS, headroom above/below
break-even, **recommendation: scale / maintain / cut**.

## 4. Impression Share Opportunity (Google)

```
Revenue Opportunity = Current Revenue × (1 / Current IS − 1)
```

**Inputs**: current impression share, IS lost to budget %, IS lost to rank %,
current spend + conversions.
**Output**: estimated additional conversions from full IS, budget needed
for full IS, priority (budget increase vs quality improvement).

## 5. Budget Forecasting (with 3 scenarios)

```
Projected Spend       = Daily Budget × Days in Period
Projected Conversions = Projected Spend / Historical CPA
Projected Revenue     = Projected Conversions × AOV
```

Scenarios:
- **Conservative**: +20% budget (no diminishing returns)
- **Moderate**: +50% budget (mild diminishing returns)
- **Aggressive**: +100% budget (significant diminishing returns)

Always include the **20% scaling rule** reminder — never increase >20% at
a time without losing optimization history (see `ads-budget`). If the
scenario exceeds 20%, flag in `concerning_flags`.

## 6. LTV:CAC Ratio

```
CAC = Total Marketing Spend / New Customers Acquired
LTV = Average Revenue per Customer × Average Customer Lifespan
LTV:CAC = LTV / CAC
```

**Inputs**: total marketing spend, new customers, ARPU, lifespan, optional
gross margin.
**Interpretation**:

| Ratio        | Interpretation                                       |
|--------------|------------------------------------------------------|
| < 1:1        | Losing money per customer                            |
| 1:1 to 2:1   | Marginal — investigate before scaling                |
| 3:1          | Healthy — the SaaS-industry default target           |
| > 5:1        | Likely under-investing in marketing — could scale    |

## 7. MER (Marketing Efficiency Ratio)

```
MER = Total Revenue / Total Marketing Spend
```

The blended, cross-channel version of ROAS. Use when attribution is fuzzy
(post-iOS 14.5, Consent Mode V2, cookie deprecation) and channel-level
ROAS is unreliable.

| MER      | Interpretation                                       |
|----------|------------------------------------------------------|
| < 2×     | Marketing isn't profitable at the unit level         |
| 2-3×     | OK for established brands; weak for growth-stage     |
| 3-5×     | Healthy growth-stage range                           |
| > 5×     | Either highly efficient or under-investing           |

## 8. Payback Period

```
Payback Period (months) = CAC / (ARPU × Gross Margin)
```

**Targets**:
- **B2C subscription**: < 6 months
- **B2B SaaS SMB**: < 12 months
- **B2B SaaS mid-market**: < 18 months
- **B2B SaaS enterprise**: < 24 months

A payback period longer than these targets → CAC is too high OR pricing
needs to move up. Recommendation: `improve_quality` (lower CAC) or
`raise_pricing`.

---

## Cross-check rule (mandatory)

**Don't trust platform-attributed ROAS as ground truth.** Derive CAC,
LTV:CAC, payback, and MER from raw data. Cross-check against the platform's
number — disagreement of **>30%** is a signal to invoke `ads-attribution`.
If disagreement exceeds 30%, append `"platform_roas_divergence_>30%"` to
`concerning_flags`.

## Why these specific calculators

The Analytics Agent and Paid Media Analyst Agent call this skill in three
recurring contexts, each mapped to a specific calculator:

- **Weekly performance snapshot for the CMO memo** → CPA, ROAS, MER
- **Justifying kill/scale recommendations with first-principles unit
  economics** (not platform-attributed ROAS) → break-even, LTV:CAC, payback
- **Sizing the budget impact of a paid_variant before activation** →
  impression-share opportunity, budget forecast

Every calculator surfaces the formula because founder edits have repeatedly
caught math that "felt right" but used the wrong denominator (e.g. MER
divided by paid-spend only instead of total marketing spend; ROAS computed
on attributed-not-incremental revenue).

See `references/calculator-edge-cases.md` for advanced patterns (multi-
touch incrementality, contribution margin variants, cohort LTV). Load only
if the standard calculator can't model the situation.
