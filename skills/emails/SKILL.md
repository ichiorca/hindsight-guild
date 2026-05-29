---
name: emails
description: When the user wants to create or optimize an email sequence, drip campaign, automated email flow, or lifecycle email program. Also use when the user mentions "email sequence," "drip campaign," "nurture sequence," "onboarding emails," "welcome sequence," "re-engagement emails," "email automation," "lifecycle emails," "trigger-based emails," "email funnel," "email workflow," "what emails should I send," "welcome series," or "email cadence." The body specifies a 6-gate sequence design procedure (context check, sequence-type selection, one-job-per-email, value-before-ask, subject-line gate, CTA exclusivity) and a required EMAILS_PASS output object the Review Agent uses to verify the sequence is well-formed. You cannot produce a passing sequence from the description alone — the per-sequence-type templates, subject-line patterns, cadence tables, and output schema are only in the body. For cold outreach emails, see cold-email. For in-app onboarding, see onboarding.
metadata:
  version: 2.1.0
---

<!--
v2.1.0 — Restructured from reference guide to procedural sequence-design
gate. Each gate produces a tracked output field. Domain content (sequence
templates, subject-line patterns, copy structure) preserved under the gate
it informs. Imported from https://github.com/iannuttall/marketingskills
(MIT License).
-->

# Emails — sequence design procedure

Run all 6 gates IN ORDER when designing or auditing an email sequence.
For each gate: apply the criteria, REWRITE the sequence/email if it
fails, record the result. Attach an `EMAILS_PASS` object to your final
output. The Review Agent reads it to verify the sequence is well-formed
— drafts without it are auto-rejected.

## Required output schema

Your final response MUST end with a JSON block in this exact shape:

```
EMAILS_PASS = {
  "context_loaded":       "pass" | "fail:no_pm_context",
  "sequence_type":        "welcome" | "nurture" | "reengagement" | "onboarding" | "winback" | "campaign" | "post_purchase" | "event" | "sales",
  "one_job_per_email":    "pass" | "fail:<email_N>_has_<M>_jobs",
  "value_before_ask":     "pass" | "fail:<reason>",
  "subject_line_score":   <int 1-5>,
  "cta_exclusivity":      "pass" | "fail:<email_N>_has_<M>_primary_ctas",
  "emails_count":         <int>,
  "cadence_days":         "<comma-separated delays>"
}
```

---

## Gate 1 — Load product marketing context

Before designing, check for `.agents/product-marketing.md` (or legacy
`.claude/product-marketing.md`, or `product-marketing-context.md`). Read
it for ICP, positioning, customer language, proof points.

- Found → `context_loaded = "pass"`. Use the context; do not re-ask the user.
- Not found → `context_loaded = "fail:no_pm_context"`. Ask the user the
  minimum needed (audience, trigger, goal) and proceed.

## Gate 2 — Sequence-type selection

Identify ONE primary sequence type from the table. The type determines
length, cadence, and emails.

| Type            | Length     | Cadence                        | Goal                          |
|-----------------|------------|--------------------------------|-------------------------------|
| welcome         | 5-7 emails | Immediate, then 1-2d apart     | Activate, build trust, convert |
| nurture         | 6-8 emails | 2-4d apart, 2-3 weeks total    | Trust, expertise, convert     |
| reengagement    | 3-4 emails | Over 2 weeks                   | Win back or clean list        |
| onboarding      | 5-7 emails | Over 14 days                   | Activate, drive to aha, upgrade |
| winback         | 3-5 emails | Over 2-3 weeks                 | Recover expired trials/cancels |
| campaign        | 1-3 emails | Single event                   | Announce / promote            |
| post_purchase   | 3-5 emails | Day 1, 7, 30                   | Onboard + cross-sell          |
| event           | 4-6 emails | Pre + post event               | Drive attendance + follow-up  |
| sales           | 5-7 emails | 2-3d apart                     | Move SQL to close             |

Record in `sequence_type`.

**Reference sequences** (use as starting templates):

*Welcome (5-7 emails over 12-14 days):*
1. Welcome + deliver promised value (immediate)
2. Quick win (day 1-2)
3. Story/why (day 3-4)
4. Social proof (day 5-6)
5. Overcome objection (day 7-8)
6. Core feature highlight (day 9-11)
7. Conversion (day 12-14)

*Nurture (6-8 emails over 2-3 weeks):*
Deliver lead magnet → expand on topic → problem deep-dive → solution
framework → case study → differentiation → objection handler → direct offer.

*Onboarding (5-7 emails over 14 days):*
Welcome+first step → getting started → feature highlight → success story
→ check-in → advanced tip → upgrade/expand. Must NOT duplicate in-app
onboarding — supports it.

For detailed templates per type, see `references/sequence-templates.md`.
For the full taxonomy of email categories (retention, billing, usage,
win-back, campaign), see `references/email-types.md`.

## Gate 3 — One email, one job

For each email in the sequence, identify the single primary job. If any
email has ≥2 jobs (e.g., "welcome AND announce feature AND ask for
review"), `fail:<email_N>_has_<M>_jobs` and split it.

Set `one_job_per_email`.

## Gate 4 — Value before ask

Across the sequence, the first ask (direct sell, upgrade prompt, paid
CTA) MUST NOT appear before the user has received at least one piece of
value (useful content, quick win, story, social proof).

- Welcome/nurture: no direct sell before email 4
- Onboarding: no upgrade prompt before activation event
- Reengagement: value reminder before incentive

Fail = first email is a hard sell. `fail:<reason>`.

## Gate 5 — Subject line gate

For EVERY subject line, score 1-5:

| Score | Criteria                                                              |
|-------|-----------------------------------------------------------------------|
| 5     | Specific, 40-60 chars, benefit/curiosity-driven, matches one pattern  |
| 4     | Specific but slightly long or generic phrasing                        |
| 3     | Clear but bland ("Welcome to ProductName")                            |
| 2     | Vague ("Important update")                                            |
| 1     | Clickbait, spammy caps, irrelevant emoji, or no relation to body      |

Patterns that score 4+:
- Question: "Still struggling with X?"
- How-to: "How to [outcome] in [timeframe]"
- Number: "3 ways to [benefit]"
- Direct: "[First name], your [thing] is ready"
- Story tease: "The mistake I made with [topic]"

Preview text rules: ~90-140 chars, extends (does not repeat) the subject.

Average the scores across the sequence into `subject_line_score`. If
average <4, REWRITE the lowest-scoring lines.

## Gate 6 — CTA exclusivity

Every email MUST have exactly ONE primary CTA (button). Secondary
actions are links in the body, NOT buttons. If any email has ≥2
primary CTAs, `fail:<email_N>_has_<M>_primary_ctas` and consolidate.

Button copy: action + outcome (e.g., "Get my report," not "Submit").

---

## Copy guidelines (apply across all gates)

- Structure: Hook → Context → Value → CTA → Human sign-off
- Short paragraphs (1-3 sentences), white space, bullets for scanning
- Mobile-first (most opens are on phone)
- Conversational, first/second person, active voice — read aloud
- Length: 50-125 words transactional / 150-300 educational / 300-500 story

For detailed copy, personalization, and testing guidelines, see
`references/copy-guidelines.md`.

## Tool integrations

For implementation, see the tools registry. Key email tools:

| Tool        | Best for                              | MCP | Guide                                  |
|-------------|---------------------------------------|:---:|----------------------------------------|
| Customer.io | Behavior-based automation             |  -  | tools/integrations/customer-io.md      |
| Mailchimp   | SMB email marketing                   |  ✓  | tools/integrations/mailchimp.md        |
| Nitrosend   | AI-native email (sequences via prompts)| ✓  | tools/integrations/nitrosend.md        |
| Resend      | Developer-friendly transactional      |  ✓  | tools/integrations/resend.md           |
| SendGrid    | Transactional email at scale          |  -  | tools/integrations/sendgrid.md         |
| Kit         | Creator/newsletter focused            |  -  | tools/integrations/kit.md              |

---

## Why these specific gates

These six gates target the failure modes most often caught in lifecycle
email reviews: missing PM context leading to off-tone copy (gate 1),
wrong sequence type for the trigger (gate 2), kitchen-sink emails with
no focus (gate 3), selling before earning trust (gate 4), generic
subject lines tanking open rates (gate 5), and competing CTAs killing
click-through (gate 6).

See `references/sequence-templates.md` for per-type templates and
`references/email-types.md` / `references/copy-guidelines.md` for
deeper references. Load only when designing a sequence type not covered
in Gate 2's table.
