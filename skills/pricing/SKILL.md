---
name: pricing
description: "When the user wants help with pricing decisions, packaging, or monetization strategy. Also use when the user mentions 'pricing,' 'pricing tiers,' 'freemium,' 'free trial,' 'packaging,' 'price increase,' 'value metric,' 'Van Westendorp,' 'willingness to pay,' 'monetization,' 'how much should I charge,' 'my pricing is wrong,' 'pricing page,' 'annual vs monthly,' 'per seat pricing,' or 'should I offer a free plan.' The body specifies a 6-gate pricing procedure (context check, value-metric selection, tier-axis spec, value-anchor floor/ceiling, raise-price signals, pricing-page checklist) and a required PRICING_PASS output object the Review Agent uses to verify pricing rigor. You cannot produce a passing pricing recommendation from the description alone — the value-metric table, good-better-best framework, Van Westendorp method, and output schema are only in the body. For in-app upgrade screens, see paywalls."
metadata:
  version: 2.1.0
---

<!--
v2.1.0 — Restructured from reference doc into procedural pricing gate.
Each gate produces a tracked output field. Domain content (value metrics,
tier structure, research methods, raise-price signals) preserved under the
gate it informs. Imported from https://github.com/iannuttall/marketingskills
(MIT License).
-->

# Pricing — strategy design procedure

Run all 6 gates IN ORDER when producing pricing recommendations. For
each gate: apply the criteria, fill the spec, record the result.
Attach a `PRICING_PASS` object to your final output. The Review Agent
reads it to verify the recommendation is grounded — proposals without
it are auto-rejected.

## Required output schema

Your final response MUST end with a JSON block in this exact shape:

```
PRICING_PASS = {
  "context_loaded":       "pass" | "fail:no_pm_context",
  "value_metric":         "<the metric you charge for>",
  "tier_axes":            { "packaging": "<spec>", "metric": "<spec>", "price_point": "<spec>" },
  "value_anchor":         { "floor": "<next best alternative>", "ceiling": "<perceived value>" },
  "raise_signal_score":   <int 0-9>,
  "pricing_page_checklist":"pass" | "fail:<missing items>"
}
```

---

## Gate 1 — Load product marketing context + business context

Check for `.agents/product-marketing.md`. Read it for ICP, positioning,
competitors, proof points.

- Found → `context_loaded = "pass"`.
- Not found → `context_loaded = "fail:no_pm_context"`. Ask the user
  minimally and proceed.

Always confirm or capture business context:
- Product type (SaaS, marketplace, e-commerce, service)
- Current pricing (if any)
- Target market (SMB, mid-market, enterprise)
- GTM motion (self-serve, sales-led, hybrid)
- Goal: growth, revenue, or profitability?
- Direction: upmarket or downmarket?

## Gate 2 — Choose the value metric

The value metric is what you charge for — it MUST scale with the value
the customer receives. Pick ONE primary metric:

| Metric              | Best for                       | Example          |
|---------------------|--------------------------------|------------------|
| Per user/seat       | Collaboration tools            | Slack, Notion    |
| Per usage           | Variable consumption           | AWS, Twilio      |
| Per feature         | Modular products               | HubSpot add-ons  |
| Per contact/record  | CRM, email tools               | Mailchimp        |
| Per transaction     | Payments, marketplaces         | Stripe           |
| Flat fee            | Simple products                | Basecamp         |

Validation test: "As a customer uses more of [metric], do they get more
value?" If no → reject and pick another metric. Good metrics: align
price with value, are easy to understand, scale with the customer, are
hard to game.

Record in `value_metric`.

## Gate 3 — Specify the three pricing axes

Every pricing proposal MUST specify all three axes explicitly:

**1. Packaging** — what's included at each tier (features, limits,
support level, how tiers differ)

**2. Pricing metric** — what you charge for (from Gate 2)

**3. Price point** — the actual dollar amounts (perceived value vs cost)

Use the Good-Better-Best framework as a default skeleton:

- **Good (Entry):** core features, limited usage, low price
- **Better (Recommended):** full features, reasonable limits, anchor price
- **Best (Premium):** everything + advanced, 2-3× Better price

Tier differentiation levers:
- Feature gating (basic vs. advanced)
- Usage limits (same features, different limits)
- Support level (Email → Priority → Dedicated)
- Access controls (API, SSO, custom branding)

Record each axis in `tier_axes`. For persona-based packaging detail,
see `references/tier-structure.md`.

## Gate 4 — Value anchor (floor / ceiling)

Price MUST sit between the floor and ceiling. State both explicitly:

- **Floor:** the next best alternative (what they'd do/use instead)
- **Ceiling:** customer's perceived value of YOUR solution

Cost-to-serve is a baseline only — not the basis.

Record in `value_anchor`. If floor ≥ ceiling, the positioning is broken
— either reframe the value (raise ceiling via stronger proof) or
reposition vs. a different alternative (lower floor).

**Pricing research methods** (use when ceiling is uncertain):

*Van Westendorp* — four questions identify the acceptable range:
1. Too expensive (wouldn't consider)
2. Too cheap (question quality)
3. Expensive but might consider
4. A bargain
Analyze intersections to find the optimal zone.

*MaxDiff* — shows feature sets, asks most/least important. Results
inform tier packaging.

For detailed methods, see `references/research-methods.md`.

## Gate 5 — Raise-price signal score

If the user is considering raising prices, score these 9 signals (1
point each):

**Market signals (3 points possible):**
- [ ] Competitors have raised prices
- [ ] Prospects don't flinch at price
- [ ] "It's so cheap!" feedback in calls/reviews

**Business signals (3 points):**
- [ ] Very high conversion rate (>40%)
- [ ] Very low churn (<3% monthly)
- [ ] Strong unit economics

**Product signals (3 points):**
- [ ] Significant value added since last pricing
- [ ] Product is more mature/stable
- [ ] Customers actively asking for higher-tier features

Record `raise_signal_score` (0-9). Recommended actions:
- 0-3: don't raise; fix conversion/churn first
- 4-6: raise with grandfathering of existing customers
- 7-9: raise broadly, consider plan restructure

Raise-price strategies:
1. Grandfather existing (new price for new customers only)
2. Delayed increase (announce 3-6 months out)
3. Tied to value (raise but add features)
4. Plan restructure (change plans entirely)

If not a raise scenario, set `raise_signal_score = 0` and ignore.

## Gate 6 — Pricing-page checklist

If the deliverable includes a pricing page, it MUST contain all of:

Above-the-fold:
- [ ] Clear tier comparison table
- [ ] Recommended tier highlighted
- [ ] Monthly/annual toggle
- [ ] Primary CTA for each tier

Common elements:
- [ ] Feature comparison table (full)
- [ ] Who each tier is for
- [ ] FAQ section
- [ ] Annual discount callout (17-20%)
- [ ] Money-back guarantee
- [ ] Customer logos / trust signals

Pricing psychology levers (apply where applicable):
- **Anchoring:** show higher-priced option first
- **Decoy effect:** middle tier should be best value
- **Charm pricing:** $49 vs. $50 (value-focused)
- **Round pricing:** $50 vs. $49 (premium)

Fail = any required item missing.
`pricing_page_checklist = "pass"` or `"fail:<missing items>"`.

If the deliverable is not a pricing page, set
`pricing_page_checklist = "pass"` and note "n/a" in your output.

---

## Why these specific gates

These six gates target the failure modes most often seen in pricing
proposals: recommending prices without PM/ICP context (gate 1),
charging on a metric that doesn't scale with value (gate 2),
hand-waving on packaging or metric while focusing only on price point
(gate 3), pricing without an explicit floor/ceiling anchor (gate 4),
raising prices on vibes instead of signals (gate 5), and shipping
pricing pages that miss conversion essentials (gate 6).

See `references/tier-structure.md` for persona-based packaging and
`references/research-methods.md` for detailed Van Westendorp / MaxDiff
execution. Load only when packaging gets persona-complex or research
is being commissioned.
