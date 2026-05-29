---
name: ab-testing
description: When the user wants to plan, design, or implement an A/B test, or build a growth experimentation program. Also use when the user says "A/B test," "split test," "experiment," "variant copy," "hypothesis," "should I test this," "statistical significance," "ICE score," "experiment backlog," or "experimentation program." The body specifies a 6-gate procedure (hypothesis quality, single-variable, sample-size lock, metric trio, ICE score, peeking guard) and a required AB_TESTING_PASS output object the Review Agent uses to verify rigor. You cannot produce a passing test plan from the description alone — the hypothesis template, sample-size table, metric trio rules, ICE rubric, and output schema are only in the body. For tracking implementation, see analytics. For page-level conversion optimization, see cro.
metadata:
  version: 2.1.0
---

<!--
v2.1.0 — Restructured from reference guide to procedural gate. Each gate now
produces a tracked output field for the Review Agent. Domain content
(sample-size tables, ICE rubric, hypothesis framework) preserved under the
gate it informs. Imported from https://github.com/iannuttall/marketingskills
(MIT License).
-->

# A/B Testing — experiment design procedure

Run all 6 gates IN ORDER before submitting a test plan or experiment
backlog item. For each gate: apply the criteria, REWRITE the spec if it
fails, record the result. Attach an `AB_TESTING_PASS` object to your
final output. The Review Agent reads it to verify rigor — plans without
it are auto-rejected.

## Required output schema

Your final response MUST end with a JSON block in this exact shape:

```
AB_TESTING_PASS = {
  "hypothesis_quality":   "pass" | "fail:<reason>",
  "single_variable":      "pass" | "fail:<N>_vars_detected",
  "sample_size_locked":   <int per variant>,
  "metric_trio":          "pass" | "fail:<missing>",
  "ice_score":            <float 1-10>,
  "peeking_guard":        "pass" | "fail:<reason>"
}
```

Missing or malformed `AB_TESTING_PASS` is treated as the skill not having
been applied.

---

## Gate 1 — Hypothesis quality

The hypothesis MUST match this exact template:

```
Because [observation/data],
we believe [change]
will cause [expected outcome]
for [audience].
We'll know this is true when [metrics].
```

Fail if ANY of:
- Missing "because" clause (no observation/data grounding)
- Uses hedge words: "might," "could," "may," "possibly"
- No specific outcome magnitude (e.g., "by ≥15%")
- No audience specified

**Weak** (fail): "Changing the button color might increase clicks."

**Strong** (pass): "Because users report difficulty finding the CTA
(heatmaps + feedback), we believe making the button larger with a
contrasting color will increase CTA clicks by ≥15% for new visitors.
We'll measure click-through rate from page view to signup start."

Set `hypothesis_quality = "pass"` or `"fail:<specific reason>"`.

## Gate 2 — Single-variable enforcement

Count the number of distinct elements changing between control and
variant. Variant copy + variant button color + variant layout = 3 vars.

- 1 variable → `pass`
- ≥2 variables → `fail:<N>_vars_detected`, then either split into
  separate tests or convert to a multivariate design (and re-scope MDE).

## Gate 3 — Sample-size lock

Look up sample size from this table by baseline conversion rate × MDE
(minimum detectable effect):

| Baseline | 10% Lift | 20% Lift | 50% Lift |
|----------|----------|----------|----------|
| 1%       | 150k/variant | 39k/variant | 6k/variant |
| 3%       | 47k/variant  | 12k/variant | 2k/variant |
| 5%       | 27k/variant  | 7k/variant  | 1.2k/variant |
| 10%      | 12k/variant  | 3k/variant  | 550/variant |

Pre-commit the number to `sample_size_locked = <int>`. If traffic can't
hit this in ≤4 weeks, EITHER raise the MDE (smaller table value) OR
reject the test — do not start an underpowered experiment.

For granular tables (other baselines, segment-aware sizing), see
`references/sample-size-guide.md`.

## Gate 4 — Metric trio

Every test MUST declare three metric classes:

- **Primary** — single metric, tied to the hypothesis, used to call the test
- **Secondary** — supports primary interpretation
- **Guardrail** — must not get worse; stops the test if significantly negative

Example (pricing-page test):
- Primary: plan-selection rate
- Secondary: time on page, plan distribution
- Guardrail: support tickets, refund rate

Fail if any class is missing. Set `metric_trio = "pass"` or
`"fail:missing_<primary|secondary|guardrail>"`.

## Gate 5 — ICE score

Score the hypothesis 1-10 on each axis:

| Dimension   | Question                                                          |
|-------------|-------------------------------------------------------------------|
| Impact      | If this works, how much will it move the primary metric?          |
| Confidence  | How sure are we this will work? (Data-driven, not gut.)           |
| Ease        | How fast and cheap can we ship and measure?                       |

`ice_score = (Impact + Confidence + Ease) / 3`.

Tests with ICE < 5 should be DEPRIORITIZED, not rejected — record the
score and let the backlog ranker decide. Re-score monthly.

## Gate 6 — Peeking guard

Looking at results before the locked sample size is reached and stopping
early **leads to false positives**. State explicitly in the test plan:

- "Will not check significance until N=<sample_size_locked> per variant."
- "Will not stop early on guardrail unless P<0.01 of harm."

Fail if the plan allows mid-flight peeking or doesn't pre-commit to
sample size. Set `peeking_guard = "pass"` or `"fail:<reason>"`.

---

## Velocity & analysis context (informational, not gated)

Track these org-level numbers to calibrate the program:

| Metric                          | Target                                  |
|---------------------------------|-----------------------------------------|
| Experiments launched per month  | 4-8                                     |
| Win rate                        | 20-30% (higher suggests conservative bias) |
| Average duration                | 2-4 weeks                               |
| Backlog depth                   | 20+ hypotheses queued                   |

Analysis checklist (run AFTER sample size reached):
1. Sample size reached? If not, result is preliminary.
2. Statistically significant? Check confidence intervals.
3. Effect size meaningful vs. MDE?
4. Secondary metrics consistent?
5. Guardrail breaches?
6. Segment differences (mobile/desktop, new/returning)?

## Documentation template (for completed tests)

```markdown
## [Experiment Name]
**Date**:
**Hypothesis**: [from Gate 1]
**Sample size**: [from Gate 3]
**Result**: [winner/loser/inconclusive] — [primary] by [X%] (95% CI: [range], p=[value])
**Guardrails**: [outcomes]
**Segment deltas**: [notable differences]
**Why it worked/failed**:
**Pattern**: [the reusable insight]
**Apply to**: [other pages/flows]
```

---

## Why these specific gates

These six gates are the failure modes most often caught when reviewing
test plans: hedge-word hypotheses (gate 1), multi-variable creep (gate
2), underpowered tests started anyway (gate 3), missing guardrails
(gate 4), gut-feel prioritization (gate 5), and early stopping on
flutters (gate 6). Each gate kills a known-recurring rejection class
before founder review.

See `references/sample-size-guide.md` for granular sizing tables and
`references/test-templates.md` for plan / report / decision-log
templates. Load only if you need a baseline not in Gate 3's table or
detailed documentation formatting.
