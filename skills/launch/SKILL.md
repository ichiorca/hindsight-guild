---
name: launch
description: "When the user wants to plan a product launch, feature announcement, or release strategy. Also use when the user mentions 'launch,' 'Product Hunt,' 'feature release,' 'announcement,' 'go-to-market,' 'beta launch,' 'early access,' 'waitlist,' 'product update,' 'how do I launch this,' 'launch checklist,' 'GTM plan,' or 'we're about to ship.' The body specifies a 6-gate launch-planning procedure (context check, ORB channel mix, phase selection, announcement-tier sizing, Product Hunt prep, post-launch lock) and a required LAUNCH_PASS output object the Review Agent uses to verify the plan is complete. You cannot produce a passing plan from the description alone — the five-phase definitions, ORB framework, announcement-tier table, and PH pre/day/post checklists are only in the body. For ongoing marketing after launch, see marketing-ideas."
metadata:
  version: 2.1.0
---

<!--
v2.1.0 — Restructured from playbook into procedural launch-plan gate.
Each gate produces a tracked output field. Domain content (ORB framework,
five phases, Product Hunt, case studies) preserved under the gate it
informs. Imported from https://github.com/iannuttall/marketingskills
(MIT License).
-->

# Launch — go-to-market planning procedure

Run all 6 gates IN ORDER when producing a launch plan. For each gate:
apply the criteria, fill the spec, record the result. Attach a
`LAUNCH_PASS` object to your final output. The Review Agent reads it
to verify the plan is complete — plans without it are auto-rejected.

## Required output schema

Your final response MUST end with a JSON block in this exact shape:

```
LAUNCH_PASS = {
  "context_loaded":       "pass" | "fail:no_pm_context",
  "orb_mix":              { "owned": [<channel>...], "rented": [<channel>...], "borrowed": [<channel>...] },
  "phase":                "internal" | "alpha" | "beta" | "early_access" | "full",
  "announcement_tier":    "major" | "medium" | "minor",
  "product_hunt":         "yes" | "no" | "later",
  "ph_readiness":         "pass" | "fail:<missing>" | "n/a",
  "post_launch_locked":   "pass" | "fail:<missing>"
}
```

---

## Gate 1 — Load product marketing context

Check for `.agents/product-marketing.md` (or legacy `.claude/...` or
`product-marketing-context.md`). Read it for positioning, ICP, proof.

- Found → `context_loaded = "pass"`.
- Not found → `context_loaded = "fail:no_pm_context"`. Ask the user
  minimally (what's launching, audience, timeline) and proceed.

## Gate 2 — ORB channel mix

Every launch plan must allocate channels across all THREE ORB types.
Everything must ultimately funnel back to owned channels.

**Owned** — direct access, no algorithm. Start with 1-2:
- Email list, blog, podcast, branded community (Slack/Discord), website
- *Why:* compound value over time, no pay-to-play
- *Example:* Superhuman built waitlist + 1:1 onboarding for FOMO

**Rented** — visibility you don't control. Pick 1-2 where audience lives:
- Twitter/X, LinkedIn, Instagram, YouTube, Reddit, app stores
- *Rule:* funnel rented attention into owned channels — capture emails
- *Tactics:* X threads → newsletter; LinkedIn posts → gated content;
  marketplace listings → site
- *Example:* Notion hacked virality on Twitter/YouTube/Reddit, funneled
  to signups + email onboarding

**Borrowed** — someone else's audience:
- Guest content (blog, podcast, newsletter), collabs (webinars, social
  takeovers), speaking, influencer partnerships
- *Be proactive:* list industry leaders, pitch win-wins, use SparkToro/
  Listen Notes for audience overlap, set up referrals/affiliates
  (channel partner launches: see tools/integrations/introw.md)
- *Example:* TRMNL sent free unit to Snazzy Labs → 500K views, $500K
  sales, plus affiliate program

Record selections into `orb_mix`. Fail = any category empty.

## Gate 3 — Phase selection

Pick the ONE phase this plan covers. The phase determines tactics and
goal:

| Phase         | Tactics                                                    | Goal                              |
|---------------|------------------------------------------------------------|-----------------------------------|
| internal      | Recruit early users 1:1; collect feedback; prototype demo  | Validate core with friendlies     |
| alpha         | Landing page + early access form; individual invites; MVP  | First external validation, waitlist |
| beta          | Work through list (free + paid); teasers; investor/influencer invites; "Beta" sticker | Build buzz, refine product |
| early_access  | Leak details (screens, GIFs); user research with credits;  product/market fit survey; throttled batches OR all-at-once | Validate at scale, prepare full |
| full          | Self-serve signups; charging on; announcements across all channels; PH/BetaList/HN; in-app, email, banner | Maximum visibility + conversion |

Record in `phase`. Each phase is a distinct plan — do not collapse two
phases into one launch.

## Gate 4 — Announcement-tier sizing

Match marketing effort to update significance:

| Tier   | Examples                              | Channels                                                |
|--------|---------------------------------------|---------------------------------------------------------|
| major  | New product, feature overhaul         | Blog + email + in-app + social + PH/HN — full campaign  |
| medium | New integration, UI enhancement       | Email to relevant segments + in-app banner              |
| minor  | Bug fixes, small tweaks               | Changelog + release notes only                          |

Record in `announcement_tier`. Tier dictates which deliverables Gate 6
requires.

## Gate 5 — Product Hunt readiness (if applicable)

If `product_hunt = "yes"`, the plan MUST include all of:

**Pre-launch:**
- Relationships with influential supporters / content hubs / communities
- Optimized listing: tagline + visuals + short demo video
- Studied past successful launches in your category
- Engaged in relevant communities BEFORE pitching
- Team prepped for all-day engagement

**Launch day:**
- All-day event treatment, real-time comment responses
- Existing audience encouraged to engage
- Traffic routed back to your site to capture signups

**After:**
- Follow-up with everyone who engaged
- PH traffic → email signups (convert borrowed → owned)
- Post-launch content to sustain momentum

If any pre-launch item is missing, `ph_readiness = "fail:<item>"`.
Case studies in references for inspiration (SavvyCal → #2 Product of
Month, Reform → #1 Product of Day).

If `product_hunt = "no"` or `"later"`, set `ph_readiness = "n/a"`.

## Gate 6 — Post-launch lock

Even before launch, the plan MUST commit to post-launch motions. The
required items depend on `announcement_tier`:

**For major:**
- Onboarding email sequence active (coordinate with `emails` skill)
- Roundup email reinforcement (catch people who missed launch)
- Comparison pages vs. competitors
- Updated web pages with feature sections
- Interactive demo (Navattic-style) — optional but recommended

**For medium:**
- Email to relevant segments
- In-app banner during rollout window

**For minor:**
- Changelog entry only

Fail = required items for the tier are missing.
`post_launch_locked = "fail:<missing items>"` or `"pass"`.

---

## Launch checklist reference

**Pre-launch:** landing page w/ value prop; email capture/waitlist;
early access list built; owned channels established; rented channels
optimized; borrowed opportunities identified; PH listing prepared (if
using); assets created (screenshots, demo, GIFs); onboarding ready;
analytics in place.

**Launch day:** announcement email; blog post; social scheduled; PH
listing live (if using); in-app announcement; website banner; team
engaged; monitoring active.

**Post-launch:** onboarding sequence active; follow-up with engaged;
roundup includes announcement; comparison pages published; interactive
demo created; feedback gathered; next launch moment planned.

## Core philosophy (context, not a gate)

The best companies launch again and again. Every feature, improvement,
and update is a launch opportunity. A strong launch isn't a single
moment — it's getting product into users' hands, learning from real
feedback, splashing at every stage, and compounding momentum over time.

---

## Why these specific gates

These six gates exist because launch plans fail in predictable ways:
skipping PM context produces off-positioning campaigns (gate 1),
single-channel reliance leaves momentum stranded (gate 2), confusing
phases means tactics that fit alpha get applied at full launch (gate
3), over-marketing minor updates wastes audience attention (gate 4),
showing up to Product Hunt cold tanks the launch (gate 5), and
launches that "end" on day-1 leave conversion value on the table
(gate 6).

See related skills: `marketing-ideas` (additional tactics, #22 PH,
#23 Early Access Referrals), `emails` (launch + onboarding sequences),
`cro` (landing page optimization), `marketing-psychology` (waitlist/
exclusivity), `programmatic-seo` (comparison pages), `sales-enablement`
(launch collateral). Load only when a gate references one.
