---
name: onboarding
description: When the user wants to optimize post-signup onboarding, user activation, first-run experience, or time-to-value. Also use when the user mentions "onboarding flow," "activation rate," "user activation," "first-run experience," "empty states," "onboarding checklist," "aha moment," "new user experience," "users aren't activating," "nobody completes setup," "low activation rate," "users sign up but don't use the product," "time to value," or "first session experience." The body specifies a 6-gate activation procedure (context check, aha-moment definition, first-30s approach, checklist sizing, empty-state coverage, multi-channel coordination) and a required ONBOARDING_PASS output object the Review Agent uses to verify activation rigor. You cannot produce a passing flow from the description alone — the approach matrix, checklist rules, empty-state template, funnel format, and output schema are only in the body. For signup/registration optimization, see signup. For ongoing email sequences, see emails.
metadata:
  version: 2.1.0
---

<!--
v2.1.0 — Restructured from playbook into procedural activation gate.
Each gate produces a tracked output field. Domain content (aha examples,
approach matrix, checklist patterns) preserved under the gate it informs.
Imported from https://github.com/iannuttall/marketingskills (MIT License).
-->

# Onboarding — activation design procedure

Run all 6 gates IN ORDER when designing or auditing a post-signup flow.
For each gate: apply the criteria, REWRITE if it fails, record the
result. Attach an `ONBOARDING_PASS` object to your final output. The
Review Agent reads it to verify the flow is activation-grade — designs
without it are auto-rejected.

## Required output schema

Your final response MUST end with a JSON block in this exact shape:

```
ONBOARDING_PASS = {
  "context_loaded":       "pass" | "fail:no_pm_context",
  "aha_moment":           "<one-sentence definition of the activation event>",
  "first_30s_approach":   "product_first" | "guided_setup" | "value_first",
  "checklist_items":      <int 0-7>,
  "empty_states_covered": "pass" | "fail:<N>_uncovered",
  "channel_coordination": "pass" | "fail:<reason>"
}
```

---

## Gate 1 — Load product marketing context

Check for `.agents/product-marketing.md` (or legacy `.claude/...` or
`product-marketing-context.md`). Need: product type, ICP, core value
prop, B2B/B2C distinction.

- Found → `context_loaded = "pass"`.
- Not found → `context_loaded = "fail:no_pm_context"`. Ask the user for
  product type, B2B/B2C, core value proposition, and current flow.

## Gate 2 — Define the aha moment

The "aha" is the action that correlates most strongly with retention —
what retained users do that churned users don't. It MUST be:

- A single user action (not a sequence)
- Measurable as an event
- Achievable in the first session

Examples by product type:

| Product type        | Aha action                              |
|---------------------|-----------------------------------------|
| Project management  | Create first project + add team member  |
| Analytics           | Install tracking + see first report     |
| Design tool         | Create first design + export/share      |
| Marketplace         | Complete first transaction              |
| B2B SaaS            | Setup wizard → first value action       |
| Mobile app          | Permissions → quick win                 |
| Content platform    | Follow/customize → consume → create     |

Record the aha as a one-sentence definition in `aha_moment`. Every
subsequent gate routes the user TOWARD this event.

Required tracking: % of signups reaching activation, time to activation,
steps to activation, activation by cohort/source.

## Gate 3 — First-30-seconds approach

Pick ONE of three approaches based on product type. Recording into
`first_30s_approach`:

| Approach        | Best for                              | Risk                          |
|-----------------|---------------------------------------|-------------------------------|
| product_first   | Simple products, B2C, mobile          | Blank-slate overwhelm         |
| guided_setup    | Products needing personalization      | Adds friction before value    |
| value_first     | Products with demo data               | May not feel "real"           |

Whichever approach is chosen:
- Clear single next action
- No dead ends
- Progress indication if multi-step

## Gate 4 — Onboarding checklist sizing

If using a checklist (recommended for self-serve B2B and products with
multiple setup steps), it MUST:

- Have 3-7 items (not overwhelming; >7 = fail)
- Order by value — most impactful action first (quick wins lead)
- Show progress bar / completion %
- Celebrate completion
- Be dismissable (don't trap users)

If you choose not to use a checklist (e.g., very simple B2C product),
set `checklist_items = 0` — that is valid.

## Gate 5 — Empty-state coverage

For EVERY screen a new user sees before activation, the empty state
MUST include:

- Brief copy explaining what this area is for
- A preview/illustration of what it looks like with data
- Clear primary action to add the first item
- *Optional:* pre-populated example data

Count screens with no/broken empty state. If ≥1, set
`empty_states_covered = "fail:<N>_uncovered"`.

Tooltips and guided tours (if used): max 3-5 steps, dismissable any
time, do not repeat for returning users.

## Gate 6 — Multi-channel coordination

Email and in-app onboarding MUST coordinate, not duplicate. Required
trigger-based emails:

- Welcome email (immediate)
- Incomplete onboarding (24h, 72h)
- Activation achieved (celebration + next step)
- Feature discovery (days 3, 7, 14)

Each email MUST:
- Reinforce in-app actions, not repeat them
- Drive back to product with specific CTA
- Be personalized based on actions taken

Stalled-user re-engagement plan required:
- Detection: define "stalled" (X days inactive, incomplete setup)
- Email sequence: value reminder + blocker address + help offer
- In-app recovery: welcome-back + resume where left off
- Human touch for high-value accounts

Fail if emails duplicate in-app or no stalled-user plan exists. Set
`channel_coordination`.

---

## Output deliverables (always include)

Beyond the schema, the response MUST include:

**Onboarding audit** (if auditing existing flow):
For each issue → Finding → Impact → Recommendation → Priority

**Onboarding flow design** (if designing):
- Activation goal (the aha from Gate 2)
- Step-by-step flow
- Checklist items (if Gate 4 applies)
- Empty-state copy (from Gate 5)
- Email sequence triggers (from Gate 6)
- Metrics plan

**Funnel analysis format:**
```
Signup → Step 1 → Step 2 → Activation → Retention
100%      80%       60%       40%         25%
```
Identify biggest drops, focus there.

## Key metrics

| Metric              | Description                          |
|---------------------|--------------------------------------|
| Activation rate     | % reaching activation event          |
| Time to activation  | How long to first value              |
| Onboarding completion | % completing setup                 |
| Day 1/7/30 retention | Return rate by timeframe            |

## Common patterns by product type

| Product type     | Key steps                                                    |
|------------------|--------------------------------------------------------------|
| B2B SaaS         | Setup wizard → first value action → team invite → deep setup |
| Marketplace      | Complete profile → browse → first transaction → repeat       |
| Mobile app       | Permissions → quick win → push setup → habit loop            |
| Content platform | Follow/customize → consume → create → engage                 |

---

## Why these specific gates

These six gates exist because activation flows fail in predictable
ways: skipping PM context produces flows that don't match the ICP
(gate 1); vague or compound "aha" definitions make activation rate
unmeasurable (gate 2); wrong first-30s approach overwhelms or under-
delivers (gate 3); 10-item checklists are abandoned (gate 4); broken
empty states are silent killers (gate 5); duplicated email/in-app
messaging trains users to ignore both (gate 6).

See `references/experiments.md` for activation-flow experiment ideas
(simplification, progress mechanics, personalization, support). Load
only when generating tests on top of an audit — hand off to
`ab-testing` skill for the actual test plan.
