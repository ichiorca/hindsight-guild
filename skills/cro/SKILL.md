---
name: cro
description: "When the user wants to optimize, improve, or increase conversions on any marketing page or form. Also use when the user says 'CRO,' 'conversion rate optimization,' 'this page isn't converting,' 'improve conversions,' 'low conversion rate,' or 'this page needs work.' Use even if the user just shares a URL and asks for feedback. The body specifies a 7-dimension scoring procedure (value-prop, headline, CTA, hierarchy, trust, objections, friction) with a required CRO_PASS output object the Review Agent uses to verify the audit ran. You cannot produce a passing audit from the description alone — the per-dimension scoring rubric, the weak/strong CTA examples, the output buckets, and the schema are only in the body. For signup/registration flows specifically, see signup. For post-signup activation, see onboarding."
metadata:
  version: 2.1.0
---

<!--
v2.1.0 — Restructured from analysis framework to procedural audit. Each
dimension now scores into a tracked output field. Domain content (weak/strong
CTA examples, trust signal taxonomy, friction list) preserved under the gate
it informs. Imported from https://github.com/iannuttall/marketingskills
(MIT License).
-->

# CRO — page audit procedure

Run all 7 dimensions IN ORDER on the target page. For each: scan the
page against the criteria, score it 1-5, and feed it into the output
object. Bucket every finding into Quick Wins / High-Impact / Test
Ideas / Copy Alternatives. Attach a `CRO_PASS` object to your final
output. The Review Agent reads it to verify the audit ran — drafts
without it are auto-rejected.

## Required output schema

Your final response MUST end with a JSON block in this exact shape:

```
CRO_PASS = {
  "value_prop_5s":        <int 1-5>,
  "headline_score":       <int 1-5>,
  "cta_score":            <int 1-5>,
  "hierarchy_score":      <int 1-5>,
  "trust_score":          <int 1-5>,
  "objections_score":     <int 1-5>,
  "friction_score":       <int 1-5>,
  "quick_wins":           <int count>,
  "high_impact":          <int count>,
  "test_ideas":           <int count>
}
```

Score = 5 (no issues) down to 1 (broken). Any dimension scoring ≤3 MUST
produce at least one recommendation in the appropriate bucket.

---

## Dimension 1 — Value proposition clarity (HIGHEST IMPACT)

Check (5-second test):
- Can a visitor understand what this is and why they should care in 5s?
- Is the primary benefit specific and differentiated?
- Is it in the customer's language, not company jargon?

Common failure modes (downscore for each):
- Feature-focused instead of benefit-focused
- Too vague or too clever
- Trying to say everything instead of the most important thing

## Dimension 2 — Headline effectiveness

Check:
- Communicates the core value prop
- Specific enough to be meaningful (not "Powerful tools for modern teams")
- Matches the traffic source's messaging (ad → headline continuity)

## Dimension 3 — CTA placement, copy, hierarchy

Check:
- ONE clear primary action (count the CTAs above the fold; >1 primary = downscore)
- Visible without scrolling
- Button copy communicates value, not just action:

| Weak       | Strong                |
|------------|-----------------------|
| Submit     | Start Free Trial      |
| Sign Up    | Get My Report         |
| Learn More | See Pricing           |

Every weak CTA found → Copy Alternatives bucket with 2-3 stronger options.

## Dimension 4 — Visual hierarchy & scannability

Check:
- Scanning gets the main message
- Most important elements are visually prominent
- Enough white space

## Dimension 5 — Trust signals & social proof

Check for presence AND placement (near CTAs and after benefit claims):
- Customer logos (especially recognizable)
- Testimonials — specific, attributed, with photos
- Case study snippets with real numbers
- Review scores and counts

Score 1 if no trust signals appear. Score 5 only if signals appear next
to the primary CTA.

## Dimension 6 — Objection handling

Common objections to look for and check the page addresses:
- Price/value concerns
- "Will this work for my situation?"
- Implementation difficulty
- "What if it doesn't work?"

Handled via: FAQ sections, guarantees, comparison content. Each
unaddressed objection in the top 4 → downscore one step.

## Dimension 7 — Friction points

Scan for:
- Too many form fields (>5 above the fold = downscore)
- Unclear next steps
- Mobile experience issues
- Long load times (>3s = downscore)

Every friction item found → Quick Wins bucket (these are usually fast
fixes).

---

## Output buckets

Every recommendation MUST land in exactly one bucket. Count goes into
the output object:

- **Quick Wins (Implement Now)** — easy changes, likely immediate impact.
  E.g., fix weak CTA copy, remove a form field, add a customer logo row.
- **High-Impact Changes (Prioritize)** — bigger changes worth the effort.
  E.g., rewrite the headline, restructure hierarchy, add a testimonial section.
- **Test Ideas** — hypotheses worth A/B testing rather than assuming.
  Hand off to the `ab-testing` skill (it produces the test plan).
- **Copy Alternatives** — for headlines/CTAs, deliver 2-3 alternatives
  with rationale, not just one.

Set `quick_wins`, `high_impact`, `test_ideas` to the bucket counts.

---

## Why these specific dimensions

These seven dimensions are ordered by impact on conversion — value-prop
clarity at the top is the single biggest lever; friction at the bottom
is usually fast to fix but lower-magnitude. The framework is run in
order so the highest-leverage issues surface first in the audit report
and Quick Wins don't crowd out High-Impact findings.

See `references/experiments.md` for specific test ideas per page type
(homepage, landing, pricing, feature, blog). See `references/form.md`
for form-specific CRO (field optimization, multi-step forms, error
handling). Load only when the page is a form OR when generating test
ideas after the audit.
