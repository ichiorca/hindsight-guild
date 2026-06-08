"""Negative examples — rejected drafts the rubric harness uses for grounding.

Agentic-commerce domain. Distribution: 20 across 5 categories (claim_risk: 6,
tone: 4, originality: 4, icp_relevance: 3, conversion_intent: 3). At least 2
LinkedIn-style overclaims in claim_risk so the rubric harness can pull them for
the reject-then-redraft demo moment.

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
    _neg(1, 18, "Guaranteed 100% agent-checkout success across every platform.",
         "Absolute guarantee, unsupportable.", "claim_risk",
         "linkedin", "seg_merchant_dtc", ["overclaim", "guarantee"]),
    _neg(2, 15, "We make your store fully ChatGPT-ready overnight, guaranteed.",
         "Unsupportable guarantee + 'fully'.", "claim_risk",
         "linkedin", "seg_merchant_dtc", ["guarantee", "absolute"]),
    _neg(3, 14, "Eliminates all protocol conformance failures, permanently.",
         "'Eliminates all' is an absolute we can't back.", "claim_risk",
         "linkedin", "seg_merchant_dtc", ["overclaim", "absolute_language"]),
    _neg(4, 12, "The only agentic-commerce testing tool you'll ever need.",
         "'Only' and 'ever' are unsupportable.", "claim_risk",
         "linkedin", "seg_agent_platform", ["absolute"]),
    _neg(5, 10, "Guaranteed 3x revenue from AI shoppers within 30 days.",
         "Revenue-lift guarantee is legally fraught.", "claim_risk",
         "email", "seg_ecom_leader", ["guarantee"]),
    _neg(6, 9, "The industry's #1 ranked agent-readiness platform.",
         "Unsupportable ranking claim.", "claim_risk",
         "linkedin", "seg_merchant_dtc", ["overclaim", "ranking"]),
    # tone (4)
    _neg(7, 17, "Hey merchants! Let's crush those AI bots together!",
         "Tone too casual for the persona.", "tone",
         "linkedin", "seg_merchant_dtc", ["off_brand", "casual"]),
    _neg(8, 11, "Sup founders, your store is SO not agent-ready rn.",
         "Tone wrong for the buyer.", "tone",
         "linkedin", "seg_merchant_dtc", ["off_brand"]),
    _neg(9, 8, "URGENT: agents are stealing your sales — act now!",
         "Spammy urgency in the body.", "tone",
         "email", "seg_ecom_leader", ["spam"]),
    _neg(10, 6, "Discover our revolutionary synergistic AI-native protocol intelligence.",
         "Jargon stack, no specificity.", "tone",
         "linkedin", "seg_agent_platform", ["jargon"]),
    # originality (4)
    _neg(11, 16, "We're the Datadog for agentic commerce.",
         "Direct competitor framing not allowed.", "originality",
         "linkedin", "seg_ecom_leader", ["competitor_echo"]),
    _neg(12, 13, "Imagine if Stripe and ChatGPT had a baby.",
         "Competitor mashup framing.", "originality",
         "linkedin", "seg_payments_network", ["competitor_echo"]),
    _neg(13, 7, "10 ways to make your store agent-ready.",
         "Listicle, generic — competitor template.", "originality",
         "blog", "seg_merchant_dtc", ["listicle", "generic"]),
    _neg(14, 5, "Stop wasting money on outdated checkout flows.",
         "Echoes a known competitor headline.", "originality",
         "linkedin", "seg_merchant_dtc", ["competitor_echo"]),
    # icp_relevance (3)
    _neg(15, 14, "Perfect for RevOps directors managing renewals.",
         "ICP mismatch — wrote RevOps to the merchant list.",
         "icp_relevance", "email", "seg_merchant_dtc", ["wrong_icp"]),
    _neg(16, 9, "Great for solo SaaS founders just getting started.",
         "Wrong ICP — sent to ecom_leader list.", "icp_relevance",
         "linkedin", "seg_ecom_leader", ["wrong_icp"]),
    _neg(17, 4, "Ideal for SDR teams running cold outbound.",
         "Wrong ICP for the payments/network list.", "icp_relevance",
         "email", "seg_payments_network", ["wrong_icp"]),
    # conversion_intent (3)
    _neg(18, 12, "Reply immediately for a free agent-readiness audit call.",
         "Hard sell CTA mid-funnel.", "conversion_intent",
         "email", "seg_merchant_dtc", ["hard_sell"]),
    _neg(19, 3, "Book a demo, then call us, then email us.",
         "Multi-CTA forest.", "conversion_intent",
         "email", "seg_merchant_dtc", ["multi_cta"]),
    _neg(20, 2, "Click here, click here, then click here to get agent-ready.",
         "Too many CTAs.", "conversion_intent",
         "email", "seg_ecom_leader", ["multi_cta"]),
]
