"""Negative examples — rejected drafts the rubric harness uses for grounding.

Distribution: 20 across 5 categories (claim_risk: 6, tone: 4, originality: 4,
icp_relevance: 3, conversion_intent: 3). At least 2 LinkedIn-style overclaims
in claim_risk so the rubric harness can pull them for the reject-then-redraft
demo moment.

The Review Agent's find_sorted(sort=[("ts", -1)]) means freshly inserted
negatives surface immediately; rebuilds of the seed re-anchor timestamps to
the seed date.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

NOW = datetime(2026, 5, 26, 9, 0, tzinfo=UTC)


def _neg(idx: int, days_ago: int, text: str, reason: str, category: str,
         channel: str, icp: str, tags: list[str]) -> dict:
    return {
        "_id": f"neg_{idx:03d}",
        "ts": NOW - timedelta(days=days_ago),
        "draft_text": text,
        "rejection_reason": reason,
        "rejection_category": category,
        "channel": channel,
        "icp_segment": icp,
        "rejected_by": "rohit",
        "tags": tags,
    }


NEGATIVE_EXAMPLES: list[dict] = [
    # claim_risk (6)
    _neg(1, 18, "Our platform eliminates churn for B2B SaaS companies.",
         "Absolute claim, no proof.", "claim_risk",
         "linkedin", "seg_saas_founder", ["overclaim", "absolute_language"]),
    _neg(2, 15, "100% of customers see ROI in week one. Guaranteed.",
         "Unsupportable guarantee.", "claim_risk",
         "linkedin", "seg_revops_director", ["guarantee", "absolute"]),
    _neg(3, 14, "We completely transformed their entire sales process.",
         "'Completely transformed' is an absolute we can't back.", "claim_risk",
         "linkedin", "seg_revops_director", ["overclaim"]),
    _neg(4, 12, "The only RevOps platform you'll ever need.",
         "'Only' and 'ever' are unsupportable.", "claim_risk",
         "linkedin", "seg_revops_director", ["absolute"]),
    _neg(5, 10, "Guaranteed 3x pipeline within 30 days.",
         "Pipeline lift guarantee is legally fraught.", "claim_risk",
         "email", "seg_revops_director", ["guarantee"]),
    _neg(6, 9, "Our solution is the industry's #1 ranked platform.",
         "Unsupportable ranking claim.", "claim_risk",
         "linkedin", "seg_saas_founder", ["overclaim", "ranking"]),
    # tone (4)
    _neg(7, 17, "Hey RevOps fam! Let's crush these quotas together!",
         "Tone too casual for the persona.", "tone",
         "linkedin", "seg_revops_director", ["off_brand", "casual"]),
    _neg(8, 11, "Sup founders, you NEED to see this.",
         "Tone wrong for B2B SaaS founders.", "tone",
         "linkedin", "seg_saas_founder", ["off_brand"]),
    _neg(9, 8, "URGENT: limited-time offer, act now!",
         "Spammy urgency in the body.", "tone",
         "email", "seg_revops_director", ["spam"]),
    _neg(10, 6, "Discover the synergistic AI-powered revolutionary solution.",
         "Jargon stack, no specificity.", "tone",
         "linkedin", "seg_saas_founder", ["jargon"]),
    # originality (4)
    _neg(11, 16, "We're the Salesforce for revenue operations.",
         "Direct competitor framing not allowed.", "originality",
         "linkedin", "seg_revops_director", ["competitor_echo"]),
    _neg(12, 13, "Imagine if HubSpot and Salesforce had a baby.",
         "Competitor mashup framing.", "originality",
         "linkedin", "seg_revops_director", ["competitor_echo"]),
    _neg(13, 7, "10 ways to improve your sales process.",
         "Listicle, generic — competitor template.", "originality",
         "blog", "seg_saas_founder", ["listicle", "generic"]),
    _neg(14, 5, "Stop wasting time on outdated CRM workflows.",
         "Echoes a known competitor headline.", "originality",
         "linkedin", "seg_revops_director", ["competitor_echo"]),
    # icp_relevance (3)
    _neg(15, 14, "If you're a marketer, you'll love this.",
         "ICP mismatch — wrote 'marketer' to revops_director list.",
         "icp_relevance", "email", "seg_revops_director", ["wrong_icp"]),
    _neg(16, 9, "Perfect for solo founders just getting started.",
         "Wrong ICP — sent to ae_growth list.", "icp_relevance",
         "linkedin", "seg_ae_growth", ["wrong_icp"]),
    _neg(17, 4, "Enterprise teams will appreciate the scale.",
         "Wrong ICP for SMB founder list.", "icp_relevance",
         "email", "seg_saas_founder", ["wrong_icp"]),
    # conversion_intent (3)
    _neg(18, 12, "Reply to this email immediately for a free 30-min call.",
         "Hard sell CTA mid-funnel.", "conversion_intent",
         "email", "seg_revops_director", ["hard_sell"]),
    _neg(19, 3, "Click here, click here, then click here.",
         "Multi-CTA forest.", "conversion_intent",
         "email", "seg_revops_director", ["multi_cta"]),
    _neg(20, 2, "Want to chat? Reply, book, call, or message us.",
         "Too many CTAs.", "conversion_intent",
         "email", "seg_saas_founder", ["multi_cta"]),
]
