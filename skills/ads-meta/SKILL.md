---
name: ads-meta
description: "Meta Ads deep analysis procedure for the Andromeda + GEM + Lattice era (Facebook, Instagram, Threads). Invoke when the user mentions Meta Ads, Facebook Ads, Instagram Ads, Advantage+, ASC, AAC, Andromeda, Entity-ID clustering, creative diversity score, Sales/Leads/App optimization, or audits a Meta campaign. The body specifies a 5-gate procedure (5-axis diversity scoring / Entity-ID clustering predictor / ASC defaults / fatigue thresholds / variant drafting), a required ADS_META_PASS output object the Review Agent uses, and the 5-axis scoring rubric + clustering heuristics + ASC audit list that you cannot reproduce from this description alone."
metadata:
  version: 1.6.0
user-invokable: false
---

<!--
v1.6.0 — Restructured from narrative deep-dive to procedural Meta gate.
Each gate updates a tracked verdict; Gate 2 emits the cluster-risk
deliverable. Domain content (5-axis rubric, Entity-ID heuristics, ASC
defaults, fatigue thresholds) preserved verbatim. Imported from
https://github.com/AgriciDaniel/claude-ads (MIT License).
-->

# Meta Ads Deep Analysis — procedure

Meta's delivery stack was rebuilt across three releases:

- **Andromeda** (Oct 2025) — ad-retrieval ranking model with 10,000× more
  capacity. Filters the candidate creative set before the auction layer.
- **GEM** (Generative Embedding Model, late 2025) — replaces the feature
  pipeline. Creative *content* embeds directly into the targeting space —
  "creative is the new targeting" is now mechanical truth.
- **Lattice** (late 2025 / early 2026) — sequence-aware optimizer on top
  of GEM that uses user-action sequences to rank candidate ads.

**Net effect: creative diversity is the #1 performance lever.** Ads with
similarity score >60% get retrieval suppression. **100 minor variations
perform no better than 10 genuinely distinct ones.**

The Paid Media Analyst Agent invokes this skill when a Meta surface needs
analysis. Load the brand's current Meta creative library (from
`attribution_map` filtered by `platform: meta`). Run all 5 gates IN ORDER.
Each gate updates `ADS_META_PASS`. Attach the object to the final response.
Runs without it are auto-rejected.

## Required output schema

```
ADS_META_PASS = {
  "creatives_audited":          <int>,
  "diversity_score":            <int 0-10>,
  "diversity_band":             "LOW" | "MEDIUM" | "HIGH" ,  // clustering risk
  "axis_breakdown": {
    "concept":  <int 0-2>,
    "format":   <int 0-2>,
    "visual":   <int 0-2>,
    "hook":     <int 0-2>,
    "headline": <int 0-2>
  },
  "predicted_clusters":         [ {"cluster_id": "...", "creative_ids": [...], "ship": "...", "cut": [...]} ],
  "asc_audit": {
    "catalog_connected":        true | false,  // Sales objective only
    "existing_customer_cap":    <int 0-100>,
    "advantage_audience_on":    true | false,
    "advantage_creative_on":    true | false
  },
  "fatigue_incidents":          <int>,
  "variants_drafted":           <int>,
  "ops_incidents_opened":       <int>,
  "cluster_risk_md_written":    true | false
}
```

Missing or malformed `ADS_META_PASS` is treated as skill not applied.

---

## Gate 1 — 5-axis creative-as-targeting scoring

Score the current creative set across all 5 axes (each 0-2, total 0-10):

| Axis                       | 0 (Risk)                              | 1 (OK)             | 2 (Strong)                                                        |
|----------------------------|---------------------------------------|--------------------|-------------------------------------------------------------------|
| **Concept diversity**      | Single core message across all assets | 2 distinct messages| 3+ distinct angles (problem-led, social proof, comparison, …)     |
| **Format diversity**       | One format (e.g. all static)          | 2 formats          | 3+ (image, video, carousel, collection)                           |
| **Visual diversity**       | One palette / one model / one comp    | 2 distinct treatments | 3+ visually distinct treatments                                |
| **Hook diversity (video)** | All hooks ≤3s look alike              | 2 hook patterns    | 3+ patterns (UGC POV, question, claim, demo)                      |
| **Headline diversity**     | All headlines paraphrase the same line| 2 structures       | 3+ structures (number-led, question, claim, comparison)           |

Banding:
- Score **8-10** → `diversity_band: "LOW"` (low clustering risk)
- Score **4-7**  → `diversity_band: "MEDIUM"` (some suppression likely)
- Score **0-3**  → `diversity_band: "HIGH"` (significant retrieval loss)

Record per-axis + total into `axis_breakdown` and `diversity_score`.

## Gate 2 — Entity-ID Clustering Predictor (pairwise, pre-launch)

For every pair of creatives in the launch set, apply these heuristics:

1. **Visual fingerprint** — same product hero, same model, same backdrop, same lighting → **likely cluster**
2. **Headline fingerprint** — same first 4 tokens → likely cluster (e.g. "Save 30% on" + "Save 30% off" + "Save 30% — limited time")
3. **Body copy fingerprint** — same opening sentence + same CTA verb → likely cluster regardless of middle-body differences
4. **Video hook fingerprint** — same 0-3s shot + same voiceover pattern → likely cluster even if rest of video diverges
5. **Format mismatch wins** — if pair is (static + video) AND visual fingerprint differs, **NOT** clustered. Crossing format AND visual is a strong diversity signal

**Output**: produce a `creative-cluster-risk.md` deliverable that:
- Groups the launch set into predicted clusters
- For each cluster: which creative to ship, which to cut
- Reports the pre-launch diversity score (target ≥8/10)

Record clusters into `predicted_clusters[]` and set
`cluster_risk_md_written: true`.

## Gate 3 — ASC defaults audit (Sales / Leads / App)

When Sales / Leads / App objectives are selected, ASC behaviors are now the
default (MAPI v25). Confirm:

- **Catalog connection** (Sales): product catalog linked, feed health green
- **Existing customer cap** (Sales): set to **10-25%** (default may be too high for high-LTV brands)
- **Advantage+ Audience**: on by default; only override with manual interest stacks for highly restricted categories
- **Advantage+ Creative**: text / brightness / music enhancements on by default; document any per-ad-set exceptions

Record into `asc_audit`. If existing-customer cap >25% on a high-LTV brand,
open `ops_incident` (severity=high).

## Gate 4 — Creative fatigue thresholds (Meta-specific)

| Signal                  | Threshold              | Action                       |
|-------------------------|------------------------|------------------------------|
| CTR declining           | >20% over 14 days      | Refresh creative             |
| Frequency (prospecting) | >5.0                   | New audience or creative     |
| Frequency (retargeting) | >12.0                  | New creative                 |
| Engagement rate drop    | >30% decline           | Full creative overhaul       |

Refresh cadence target: every **14-21 days** on Meta. (Compare: TikTok
7-10d, LinkedIn 4-6w.)

Cross-reference against `telemetry.outcomes`. For every threshold crossed,
open an `ops_incident` (severity=high) and increment `fatigue_incidents` +
`ops_incidents_opened`.

## Gate 5 — Draft replacement variants

For every clustered-cut creative (gate 2) and every fatigued creative
(gate 4), draft a replacement variant and land it in `paid_variants` with
`status="paused"` so the founder activates explicitly. Variants must:

- Increase at least one diversity axis where the score was <2
- Pass the Entity-ID heuristics against the surviving creatives
- Pass `house-style` voice rules
- Cite an approved `messaging_library` claim or `customer_voice` quote

Record `variants_drafted`.

---

## Why these specific gates

These five gates correspond to the failure modes that emerged post-
Andromeda + GEM + Lattice rollout:

- **5-axis scoring** (gate 1) — captures the actual dimensions Andromeda
  measures for clustering. Single-axis diversity (e.g. only headline) is
  not enough.
- **Pairwise Entity-ID prediction** (gate 2) — the only reliable pre-launch
  proxy for retrieval suppression. Saves >50% of variants from being
  silently throttled.
- **ASC defaults audit** (gate 3) — MAPI v25 made ASC the default, and
  high-LTV brands have been silently bleeding budget on too-high existing-
  customer caps.
- **Meta-specific fatigue thresholds** (gate 4) — Meta's frequency caps and
  CTR decline curves differ from every other platform; cross-platform
  fatigue numbers under-fire on Meta.
- **Variant drafting** (gate 5) — closes the loop: every detected
  cluster/fatigue must produce a queued replacement, or the next sweep will
  re-detect the same issue.

> **House style wins.** When generating ad copy variants, the
> `house-style` skill's voice rules and the `messaging_library`
> approved-claims constraint override anything in this skill.

See `references/meta-deep-dive.md` for advanced GEM-embedding patterns,
Lattice sequence diagnostics, and per-objective ASC tuning. Load only if
a gate verdict is contested or a new objective needs an audit rubric.
