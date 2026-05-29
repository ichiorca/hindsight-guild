---
name: thinking-framework
description: The 10-Principle Thinking Framework — a meta-cognitive procedure for every audit, plan, decision, and creative output. Use when about to make a recommendation, before finalizing a weekly memo, when stress-testing your own conclusions, when conducting a self-critique pass, or any time you ask "am I thinking about this right?" Pairs OBSERVE x 2, LISTEN, THINK, CONNECT x 2, FEEL, ACCEPT, CREATE, and GROW. Separates a number-crunching report from a strategic deliverable. The body specifies a 4-step procedure (stage-identify, engaged-principle-check, skipped-principle-name, meta-analysis emit) keyed off a stage -> dominant-principle map and a required THINKING_FRAMEWORK_PASS output object the calling agent uses. You cannot apply the framework from this description alone — the principle definitions, the stage map, and the output schema are only in the body.
metadata:
  version: 1.8.0
  user-invokable: false
---

<!--
v1.8.0 — Restructured from principle reference to procedural meta-gate.
The 10 principles are preserved as the source-of-truth definitions; the
gates force you to USE them on the work in front of you rather than just
recognize them. Imported from https://github.com/AgriciDaniel/claude-ads
(MIT License). Original lives at ads/references/thinking-framework.md
upstream; promoted to standalone here because its value extends beyond
ad-account audits.
-->

# Thinking Framework — meta-cognitive procedure

The shared cognitive discipline that runs underneath every strategic
deliverable. Run all 4 gates IN ORDER on the work in front of you.
This is not a checklist or a phase model — it is a mindset gate.
Attach a `THINKING_FRAMEWORK_PASS` object so the calling agent
(typically **Self-Critique** or **CMO Planner**) can verify the
discipline was applied.

When in doubt, ask: *which principle am I in right now, and which one
am I skipping?* The skipped principle is usually where the work is
weakest.

## Required output schema

Your final response MUST end with a JSON block in this exact shape:

```
THINKING_FRAMEWORK_PASS = {
  "stage":                "<stage name from the map below>",
  "principles_engaged":   [<principle name>, ...],
  "principles_skipped":   [<principle name>, ...],
  "dominant_engaged":     "yes" | "no:<which dominant principle was missing>",
  "concrete_gap":         "<one-line description of what skipping cost the work>",
  "recommendation":       "<one-line concrete fix, not generic advice>"
}
```

`dominant_engaged = "no:..."` is allowed and expected when you catch
the gap — that's the entire purpose of the framework. Missing or
malformed `THINKING_FRAMEWORK_PASS` is treated as the entire skill not
having been applied.

---

## Gate 1 — Identify the current stage

Locate the work in the stage map. If it doesn't fit cleanly, pick the
closest stage and note the deviation.

| Stage                                  | Dominant principles                | Why                                            |
|----------------------------------------|------------------------------------|------------------------------------------------|
| Drafting pipeline start (Research)     | OBSERVE, LISTEN                    | Read the data + the brief                      |
| Content drafting                       | LISTEN, FEEL                       | Hear the customer; feel the audience           |
| Image brief                            | FEEL, CREATE                       | Visual produced with emotional intent          |
| Review                                 | OBSERVE-Internal, ACCEPT           | Check your biases; accept dead drafts          |
| Weekly memo (CMO Planner)              | THINK, CONNECT-Lateral             | Math + cross-channel insight                   |
| Promotion gate decision                | THINK, ACCEPT                      | Stats rigor + willingness to retire incumbents |
| Drift investigation                    | OBSERVE, CONNECT-System            | Pull data + see the system-level cause         |
| Self-Critique pass                     | OBSERVE-Internal, ACCEPT, GROW     | Audit your own conclusions                     |
| Founder weekly review                  | CREATE, GROW                       | Ship decisions; close the loop                 |

Record `stage`.

## Gate 2 — Check that the dominant principles are engaged

For the stage you identified, name which of its dominant principles
ARE engaged in the current work (with evidence — a line in the memo,
a section of the audit, a specific data pull). Generic claims of
"yes I observed" don't count; cite the artifact.

Record `principles_engaged` as the list of principles you can defend
with evidence from the work.

## Gate 3 — Name the skipped principle

This is where the value is. For the stage's dominant principles, name
any that are NOT engaged AND explain what the work would look like if
they were.

Set `dominant_engaged = "yes"` only if every dominant principle for
the stage has evidence in Gate 2. Otherwise
`"no:<missing principle>"`.

Record `principles_skipped` and write a one-line `concrete_gap`
describing what the skip cost the work.

## Gate 4 — Emit meta-analysis

Produce a one-line `recommendation`. It must be a CONCRETE fix, not
"engage more OBSERVE." Examples of concrete:

- "Pull the actual MMP data instead of trusting Meta's reported ROAS
  before issuing the budget reallocation."
- "Run the second-order check on the promo — what happens to LTV in
  Q2 if Q1 buyers were discount-trained?"
- "Cite the customer_voice quote behind the claim; right now the memo
  asserts it without source."

---

## The 10 principles

The five-pair source of truth. Gate 2 and Gate 3 read against these.

### 1. OBSERVE — External Input

Thinking begins with data collection. Look at the environment without
rushing to solve. Read raw inputs.

**In our work.** Pull the actual telemetry. Read the customer voice
quotes verbatim. Look at the live landing page on mobile in a fresh
session. Don't audit from memory.

**Anti-pattern.** Diagnosing from a generic checklist before opening
the data. Recommending a fix without seeing the inputs that produced
the problem.

### 2. OBSERVE — Internal Metacognition

Observe yourself. Are you operating on assumptions? Do you have a
bias?

**In our work.** Notice when you're applying B2B-SaaS heuristics to a
B2C brand. Notice when you're penalizing a setup because it doesn't
match your preferred structure. Notice when you anchored on the first
KPI and ignored the funnel beneath.

**Anti-pattern.** Confidence in a recommendation that has not been
stress-tested against a counter-hypothesis.

### 3. LISTEN — Active Receptivity

Shut down ego and absorb feedback. Pay attention to user intent and
subtle signals in the noise.

**In our work.** Read the founder's actual words in the brief — don't
translate "more leads" into "lower CPL" without checking. Listen to
the customer_voice corpus. Cross-check vendor claims against
independent operator reports.

**Anti-pattern.** Telling a brand-awareness advertiser to optimize
for ROAS because that's the default recommendation.

### 4. THINK — Critical Processing

Once you have inputs, break the problem down to first principles.
Structure the logic. Compute unit economics by hand.

**In our work.** Don't trust platform-attributed ROAS — derive CAC,
LTV:CAC, payback, MER from raw data. Build the funnel: impression ->
click -> landing -> micro-conversion -> conversion -> revenue ->
repeat. Where does the leak live?

**Anti-pattern.** Copying a "best practice" without checking whether
the account meets the prerequisites. Trusting platform attribution as
ground truth when MMP, server-side, and platform numbers disagree by
>30%.

### 5. CONNECT — Associative / Lateral Thinking

Great insight lives at intersections. Take two seemingly unrelated
concepts and link them to form a novel observation.

**In our work.** Andromeda creative similarity + Entity-ID retrieval
+ GEM embeddings = "creative is the new targeting" is mechanical, not
slogan. AI Max keywordless + Demand Gen + PMax = the post-keyword
era. iOS AdAttributionKit + Consent Mode V2 + sGTM/CAPI = the
privacy stack that must be coherent across all three.

**Anti-pattern.** Siloed audits that miss cross-platform leverage.

### 6. CONNECT — System Orchestration

Move from isolated idea to integrated system. How do tools, agents,
and skills plug into one another?

**In our work.** The drafting pipeline IS a connected system:
Research -> Content -> ImageBrief -> Review. Each output feeds the
next. The attribution stack is a system: Pixel/CAPI + Consent Mode
V2 + sGTM + MMP + AdAttributionKit. Recommendations in one must be
coherent with the others.

**Anti-pattern.** Recommending fixes that conflict with each other
in the same memo. "Increase budget 30%" and "pause this campaign"
without acknowledging the trade-off.

### 7. FEEL — Emotional Intelligence & Intuition

Pure logic is brittle without empathy. Factor in user experience,
emotional resonance, and hard-earned intuition when data is
ambiguous.

**In our work.** Read the ad copy emotionally. Does the headline make
a user feel something *they want to feel*? Look at the landing page
as a first-time visitor. Trust intuition when the data is ambiguous —
scoring an ad 100% compliant while it has zero emotional pull is a
fail.

**Anti-pattern.** A rubric that rewards "spec compliance" and
penalizes nothing about emotional flatness.

### 8. ACCEPT — Intellectual Humility

No plan survives first contact with reality. Embrace constraints,
acknowledge when a hypothesis failed, let go of sunk costs.

**In our work.** The 3x Kill Rule: if CPA > 3x target with 3+
optimization attempts, accept that the campaign is dead. Don't keep
tweaking. If a recommendation was implemented and didn't move the
needle, accept it and move on. If a client's stated goal doesn't
match their data signal, name the gap rather than rationalizing it.

**Anti-pattern.** Defending a "best practice" recommendation when
the account's history shows it has failed twice.

### 9. CREATE — Generative Output

Analysis paralysis is the enemy of progress. At some point you stop
strategizing and start producing.

**In our work.** Ship the weekly memo. Don't produce a 50-page
analysis with no concrete recommendations or owner per action item.
Write the actual ad copy, not a brief about a brief. Render the
deliverable; quality-gate before shipping but ship.

**Anti-pattern.** Endless "more analysis needed" loops. A brief that
hedges every concept and forces the next collaborator to make every
hard call.

### 10. GROW — The Iterative Loop

Thinking is not a straight line; it's a feedback loop. Take what you
built, see how it performs, use lessons to upgrade for the next
cycle.

**In our work.** Every recommendation has a measurement plan
attached. A/B test design: hypothesis -> significance -> duration ->
result -> next hypothesis. Re-audit at 30 or 90 days; compare against
baseline. Carry lessons into the next cycle — the
`experiments.lesson` field exists for this reason. The Self-Critique
Agent IS step 10.

**Anti-pattern.** One-shot audits with no follow-up. Recommendations
without measurement criteria.

---

## Why these gates

Strategic deliverables fail in three patterns the gates catch:

- **Mechanical work** (Gate 2) — number-crunching with no engaged
  principle reads as a report, not a recommendation.
- **Unnamed skip** (Gate 3) — the agent feels something is off but
  doesn't surface what. The framework forces the agent to name it.
- **Generic recommendations** (Gate 4) — "think harder" instead of a
  concrete fix tied to the missing principle.

This is the skill that makes the rest of the skills strategic instead
of mechanical.
