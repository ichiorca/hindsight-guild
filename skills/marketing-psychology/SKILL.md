---
name: marketing-psychology
description: Mental-model application procedure. Invoke when the user wants to apply psychological principles, mental models, or behavioral science to marketing. Also use when the user mentions "psychology," "mental models," "cognitive bias," "persuasion," "behavioral science," "why people buy," "decision-making," "consumer behavior," "anchoring," "social proof," "scarcity," "loss aversion," "framing," or "nudge." The body specifies a 5-gate procedure (challenge-classify, model-select, mechanism-explain, application-design, ethics-check) keyed off a challenge -> models lookup table, and a required MARKETING_PSYCHOLOGY_PASS output object the Review Agent uses. You cannot produce a passing recommendation from this description alone — the model catalog, the challenge table, the ethics gate, and the output schema are only in the body. For page-level CRO, see cro; for pricing tactics, see pricing; for copy framing, see copywriting.
metadata:
  version: 2.1.0
---

<!--
v2.1.0 — Restructured from mental-model encyclopedia to procedural
application gate. The full model catalog is preserved as the source-of-
truth lookup; gates require selecting from it deliberately. Imported
from https://github.com/iannuttall/marketingskills (MIT License).
-->

# Marketing Psychology — application procedure

Run all 5 gates IN ORDER. Each gate forces you to use the model
catalog deliberately rather than namedrop "social proof" or
"scarcity" reflexively. Attach a `MARKETING_PSYCHOLOGY_PASS` object so
the Review Agent can verify the skill was applied.

## Required output schema

Your final response MUST end with a JSON block in this exact shape:

```
MARKETING_PSYCHOLOGY_PASS = {
  "challenge_class":      "low_conv" | "price_objection" | "trust" | "urgency" | "retention" | "growth_stall" | "decision_paralysis" | "onboarding" | "other:<short>",
  "models_selected":      [<model name>, ...],   // must be 2-4
  "primary_model":        "<name>",
  "mechanism_explained":  "pass" | "fail:no_mechanism",
  "application_concrete": "pass" | "fail:vague",
  "ethics_check":         "pass" | "fail:manipulation_risk",
  "testable_hypothesis":  "<one-sentence hypothesis>"
}
```

`models_selected` outside the 2-4 range or `ethics_check = "fail:..."`
without a documented mitigation is a failed recommendation. Missing
or malformed `MARKETING_PSYCHOLOGY_PASS` is treated as the entire
skill not having been applied.

---

## Gate 1 — Classify the challenge

Identify the challenge from this table FIRST. The class drives which
models are candidates.

| Challenge class      | Symptoms                                              | Candidate models                                                |
|----------------------|-------------------------------------------------------|-----------------------------------------------------------------|
| Low conversions      | Visitors arrive, don't act                            | Hick's Law, Activation Energy, BJ Fogg, Friction reduction      |
| Price objection      | "Too expensive" recurring                             | Anchoring, Framing, Mental Accounting, Loss Aversion, Decoy     |
| Trust gap            | Skepticism, "is this real"                            | Authority, Social Proof, Reciprocity, Pratfall Effect           |
| Urgency missing      | "I'll come back later" -> never                       | Scarcity, Loss Aversion, Zeigarnik Effect                       |
| Retention / churn    | High signup, low stick                                | Endowment Effect, Switching Costs, Status-Quo Bias              |
| Growth stalling      | Plateau on a previously working channel               | Theory of Constraints, Local vs Global Optima, Compounding      |
| Decision paralysis   | High traffic to pricing, low pick-rate                | Paradox of Choice, Default Effect, Nudge Theory                 |
| Onboarding drop-off  | Trials don't activate                                 | Goal-Gradient, IKEA Effect, Commitment & Consistency            |

If the challenge doesn't fit, use `"other:<short>"` and justify
candidate models inline.

Record `challenge_class`.

## Gate 2 — Select 2-4 models (no more, no fewer)

From the candidate list for the challenge class, pick 2-4 models.

- **Fewer than 2**: monocausal — you'll miss the leverage point.
- **More than 4**: kitchen-sinking — the user can't act on it.

Designate ONE primary model and 1-3 supporting models.

**The full model catalog (source of truth) lives in this skill as the
"Model catalog" section below.** Use it to select; don't reach for
models outside it without naming why.

Record `models_selected` (list of 2-4) and `primary_model`.

## Gate 3 — Explain the mechanism

For the primary model, in 1-3 sentences, explain WHY it works at the
psychological level. Not "anchoring works because people anchor" —
that's not a mechanism. A mechanism is:

> "The brain processes the first numeric value as a reference point.
> Subsequent valuations are computed as adjustments from that anchor,
> not from scratch. Anchors that are too high pull subsequent
> valuations higher even when the anchor is obviously irrelevant."

For each supporting model, one sentence on how it reinforces the
primary.

Set `mechanism_explained = "pass"` if you stated a real mechanism;
`"fail:no_mechanism"` if you only stated the model's name.

## Gate 4 — Concrete application

Turn the models into a specific, implementable recommendation. Not:

> "Use anchoring to address price objections."

But:

> "On the pricing page, lead the tier table with the Enterprise tier
> at $499/mo. Position the recommended Pro tier ($79/mo) second so it
> reads as 84% cheaper than the anchor. Add a strikethrough $129
> 'regular price' on Pro for a secondary anchor."

The application must specify: WHERE in the journey, WHAT the change
is, and WHAT signal will tell us it worked.

Record `application_concrete = "pass"` if WHERE + WHAT + SIGNAL are
all present; `"fail:vague"` otherwise.

## Gate 5 — Ethics check

Many of these models border on manipulation. The skill is to use them
ethically. Auto-fail any application that:

- Manufactures false scarcity ("only 3 left" when there are 3,000).
- Uses fake testimonials, fake counts, or fabricated reviews.
- Uses dark-pattern defaults that opt users into charges they
  wouldn't consent to.
- Hides fees in mental accounting that resurface as billing surprises.
- Triggers loss aversion via fear-mongering about outcomes the
  product can't actually prevent.

Set `ethics_check = "pass"` only if the application meets ALL these:

1. Genuine — the scarcity, social proof, or anchor is real.
2. Reversible — the user can change their mind without penalty.
3. Disclosed — anything that could surprise the user is surfaced
   upfront.

If `ethics_check = "fail:..."`, document the risk and propose a
mitigation (e.g., "switch from 'only 3 left' to 'low stock' with
real inventory feed").

Also produce one testable hypothesis the team can run as an
experiment (Record `testable_hypothesis`).

---

## Model catalog

The full library, organized so Gate 2 can select from it.

### Foundational thinking models

- **First Principles** — break problems to basic truths; 5 Whys.
- **Jobs to Be Done** — customers "hire" products for outcomes.
- **Circle of Competence** — stay where you have genuine expertise.
- **Inversion** — ask what guarantees failure, then avoid it.
- **Occam's Razor** — simplest explanation is usually right.
- **Pareto (80/20)** — find the 20% driving 80% of results.
- **Local vs Global Optima** — don't optimize the wrong thing.
- **Theory of Constraints** — fix the bottleneck before optimizing.
- **Opportunity Cost** — every choice has a cost in what you skip.
- **Law of Diminishing Returns** — additional input yields less.
- **Second-Order Thinking** — effects of effects (flash sale trains
  customers to wait for discounts).
- **Map != Territory** — your dashboard is not your customer.
- **Probabilistic Thinking** — plan for multiple outcomes.
- **Barbell Strategy** — extreme safety + small high-risk bets.

### Buyer-psychology models

- **Fundamental Attribution Error** — customers don't convert because
  of YOUR process, not their character.
- **Mere Exposure Effect** — familiarity breeds liking.
- **Availability Heuristic** — vivid examples feel more probable.
- **Confirmation Bias** — people seek confirming evidence.
- **Lindy Effect** — fundamentals outlast fads.
- **Mimetic Desire** — people want what desirable people want.
- **Sunk Cost Fallacy** — past spend should not drive future spend.
- **Endowment Effect** — people overvalue what they own.
- **IKEA Effect** — effort invested raises perceived value.
- **Zero-Price Effect** — "free" is psychologically distinct.
- **Hyperbolic Discounting** — immediate beats future.
- **Status-Quo Bias** — current state is preferred.
- **Default Effect** — pre-selection drives choice.
- **Paradox of Choice** — more options paralyze.
- **Goal-Gradient Effect** — effort accelerates near the finish.
- **Peak-End Rule** — experiences judged by peak + end.
- **Zeigarnik Effect** — open loops nag.
- **Pratfall Effect** — small flaws raise likability.
- **Curse of Knowledge** — experts can't unsee what they know.
- **Mental Accounting** — money perceived differently by source/use.
- **Regret Aversion** — avoid regret-prone choices.
- **Bandwagon / Social Proof** — popularity = quality signal.

### Persuasion models

- **Reciprocity Principle** — give first.
- **Commitment & Consistency** — small commitments lead to large.
- **Authority Bias** — credentials create trust.
- **Liking / Similarity** — "one of us" sells.
- **Unity Principle** — shared identity drives influence.
- **Scarcity / Urgency** — limited beats unlimited (when genuine).
- **Foot-in-the-Door** — small ask first, scale up.
- **Door-in-the-Face** — big ask first, retreat to real ask.
- **Loss Aversion / Prospect Theory** — losses ~2x as painful as
  equivalent gains.
- **Anchoring Effect** — first number dominates.
- **Decoy Effect** — adding inferior option lifts target.
- **Framing Effect** — positive vs negative phrasing flips perception.
- **Contrast Effect** — before/after vividness.

### Pricing psychology

- **Charm Pricing / Left-Digit Effect** — $99 reads much cheaper than
  $100.
- **Rounded-Price (Fluency) Effect** — round numbers feel premium.
- **Rule of 100** — under $100 use % discount; over $100 use $ off.
- **Price Relativity / Good-Better-Best** — middle tier seems
  reasonable.
- **Mental Accounting (Pricing)** — "$1/day" feels cheaper than
  "$30/month."

### Design & delivery models

- **Hick's Law** — more choices = slower decisions.
- **AIDA Funnel** — Attention -> Interest -> Desire -> Action.
- **Rule of 7** — ~7 touchpoints before conversion.
- **Nudge Theory / Choice Architecture** — small framing changes,
  big behavioral shifts.
- **BJ Fogg Behavior Model** — Behavior = Motivation x Ability x
  Prompt.
- **EAST Framework** — Easy, Attractive, Social, Timely.
- **COM-B Model** — Capability, Opportunity, Motivation.
- **Activation Energy** — first step must be trivial.
- **North Star Metric** — one metric aligns the org.
- **Cobra Effect** — incentives that backfire.

### Growth & scaling models

- **Feedback Loops** — outputs become inputs.
- **Compounding** — small consistent gains accumulate.
- **Network Effects** — value scales with users.
- **Flywheel Effect** — hard to start, easy to maintain.
- **Switching Costs** — retention via ethical lock-in.
- **Exploration vs Exploitation** — optimize known, test new.
- **Critical Mass / Tipping Point** — sustained-growth threshold.
- **Survivorship Bias** — study failures, not just winners.

---

## Why these gates

Recommendations using psychology fail in three ways the gates catch:

- **Reflexive model name-drop** (Gate 2 + Gate 3) — "use social proof"
  with no mechanism explanation produces shallow recommendations that
  don't transfer.
- **Vague application** (Gate 4) — without WHERE / WHAT / SIGNAL the
  recommendation is unimplementable.
- **Ethics drift** (Gate 5) — the line from "anchoring" to "manipulation"
  is shorter than it looks. The ethics check is non-optional.

This skill is paired most often with **cro** (page-level
application), **copywriting** (frame-level application), **pricing**
(tier-level application), and **ab-testing** (testing the
hypothesis Gate 5 produces).
