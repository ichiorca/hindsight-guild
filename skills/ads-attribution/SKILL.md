---
name: ads-attribution
description: "Attribution health audit procedure. Invoke when the user says attribution audit, attribution model, AdAttributionKit, AAK, view-through attribution, GA4 attribution, Consent Mode V2, conversion window, MMP audit, or cross-device attribution. The body specifies a 5-gate procedure (iOS / web / Consent Mode V2 / server-side / cross-device), a required ADS_ATTRIBUTION_PASS output object the Review Agent uses, and the conversion-window matrix + Consent Mode signal list that you cannot reproduce from this description alone."
metadata:
  version: 1.6.0
user-invokable: false
---

<!--
v1.6.0 — Restructured from narrative guide to procedural attribution gate.
Each gate produces a tracked surface verdict. Domain content (windows,
Consent Mode signals, sGTM hygiene rules) preserved verbatim from the
upstream skill. Imported from https://github.com/AgriciDaniel/claude-ads
(MIT License).
-->

# Cross-Platform Attribution Health Audit — procedure

Attribution decay is the silent revenue killer of 2026: Consent Mode V2 EEA
enforcement (Jul 21, 2025), iOS ATT, SKAdNetwork → AdAttributionKit
migration, and cookie deprecation have mis-attributed **15-40% of
conversions** on misconfigured stacks. Run all 5 gates IN ORDER. For each
gate: collect the named signals, score the surface PASS / WARNING / FAIL,
emit findings, record the verdict. Attach `ADS_ATTRIBUTION_PASS` to your
final output. The Review Agent reads it — audits without it are
auto-rejected.

## Required output schema

Your final response MUST end with a JSON block in this exact shape:

```
ADS_ATTRIBUTION_PASS = {
  "ios_attribution":   "PASS" | "WARNING" | "FAIL",
  "web_attribution":   "PASS" | "WARNING" | "FAIL",
  "consent_mode_v2":   "PASS" | "WARNING" | "FAIL",
  "server_side":       "PASS" | "WARNING" | "FAIL",
  "cross_device":      "PASS" | "WARNING" | "FAIL",
  "findings_count":    <int>,
  "critical_findings": <int>,
  "findings": [
    { "severity": "critical|high|medium|low",
      "platform": "...",
      "issue": "...",
      "fix": "...",
      "eta_days": <int> }
  ]
}
```

Missing or malformed `ADS_ATTRIBUTION_PASS` is treated as the entire skill
not having been applied.

Before running gates, **collect** the stack: GA4 property ID, Google Ads
conversion actions, Meta CAPI config, Apple Ads / AdAttributionKit
registration, MMP dashboard (AppsFlyer / Adjust / Branch / Singular), any
sGTM container.

---

## Gate 1 — iOS attribution (AdAttributionKit + ATT)

Check each signal:

- **AdAttributionKit registered** with Apple Ads (post-Apr 10, 2025 cutover)
- **View-through attribution** active — 24h post-impression view window
- **Configurable attribution windows** (WWDC 2025) per-campaign
- **Country code in postbacks** (WWDC 2025) enabled if geo attribution needed
- **ATT opt-in rate** monitored; <30% opt-in means heavy SKAN/AAK reliance
- **Privacy threshold awareness** — consolidate campaigns below 1k installs/week

Verdict: **PASS** = all signals green. **WARNING** = ATT opt-in <30% but
SKAN/AAK consolidated correctly. **FAIL** = AAK not registered OR low-volume
campaigns receiving null postbacks unaddressed. Set
`ios_attribution` accordingly.

## Gate 2 — Web attribution (GA4 + Google Ads + Meta CAPI)

Check each signal:

- **GA4 attribution model**: confirm Data-Driven (default) vs Last-Click is intentional, not pre-2026 residue
- **Google Ads attribution model**: Data-Driven default; audit Last-Click overrides for justification
- **Cross-channel attribution**: Google, Meta, LinkedIn, TikTok, Microsoft integrated with consent + auto-tagging
- **Conversion window matches sales cycle**:

| Business type        | Click window | View window |
|----------------------|--------------|-------------|
| E-commerce           | 7-day        | 1-day       |
| B2B / lead gen       | 30-90 day    | none        |
| App install          | 7-day        | 1-day       |
| Subscription renewal | 30-day       | 7-day       |

Verdict: **FAIL** if windows mismatch sales cycle by >2x OR Last-Click
overrides on Data-Driven-eligible campaigns are undocumented. Set
`web_attribution`.

## Gate 3 — Consent Mode V2 (EEA enforcement Jul 21, 2025)

Non-compliance = no remarketing data. Confirm all four signals are tracked
and modeled:

- `ad_storage` granted/denied tracked
- `ad_user_data` granted/denied tracked (V2-specific)
- `ad_personalization` granted/denied tracked (V2-specific)
- `analytics_storage` granted/denied tracked
- Server-side modeling enabled for `denied` users

Verdict: **FAIL** if any V2-specific signal missing. **WARNING** if all
signals present but modeling for denied users not enabled. **PASS** = all
five. Set `consent_mode_v2`.

## Gate 4 — Server-side attribution (sGTM + CAPI)

Check each signal:

- sGTM container deployed to **first-party domain** (NOT gtm-server.com)
- CAPI Gateway for Meta enabled with deduplication
- Event hashing per platform (SHA-256 for email/phone/name)
- Match quality score **>7/10** per platform

Verdict: **FAIL** if sGTM on gtm-server.com (cookie misclassification) OR
match quality <5. **WARNING** if 5-7. **PASS** if all green. Set
`server_side`.

## Gate 5 — Cross-device / cross-platform stitching

Confirm a user-ID or signed-in stitching mechanism is in place across the
identified MMP / GA4 / CAPI surfaces. Score:

- **PASS** = user-ID stitching active AND MMP integration health green
- **WARNING** = device-graph-only stitching OR MMP integration partial
- **FAIL** = no cross-device stitching OR MMP integration broken

Set `cross_device`.

---

## Findings emission

For every WARNING/FAIL surface, emit one finding entry into `findings[]`
with: `severity`, `platform`, `issue` (1 sentence), `fix` (1-2 sentences),
`eta_days`. Update `findings_count` and `critical_findings` totals.

## Why these specific gates

These five gates correspond to the five attribution surfaces where 2025-2026
regulatory + platform changes have created silent data loss. Specifically:

- Consent Mode V2 enforcement (Jul 21, 2025) — gate 3
- iOS AAK migration cutover (Apr 10, 2025) — gate 1
- GA4 attribution model defaults shifting Data-Driven — gate 2
- sGTM first-party-domain requirement post-cookie-deprecation — gate 4
- Death of third-party cookies forcing user-ID stitching — gate 5

Each gate is a known-recurring source of 15-40% mis-attribution. The
Analytics Agent and Ops/QA Agent depend on this verdict to open
`ops_incidents` for misconfigured surfaces.

See `references/attribution-deep-dive.md` for vendor-specific MMP setup
matrices and full GA4 attribution-model migration playbook. Load only if a
gate verdict is contested or a new vendor surface needs scoring.
