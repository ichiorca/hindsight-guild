"""Approved messaging claims — Content Agent uses these, Review Agent validates against them.

Every claim has an evidence_url (internal pointer or external link) so the
Review Agent can defend any claim that survives into a draft.

The applies_to_icp field is the join key — Content + Review pull claims
matching the target ICP via mongodb.find(filter={"applies_to_icp": <seg>,
"status": "approved"}).
"""
from __future__ import annotations

MESSAGING_CLAIMS: list[dict] = [
    {
        "_id": "claim_integration_value",
        "claim_text": "Connects to Salesforce, HubSpot, and Pipedrive in under 10 minutes.",
        "claim_type": "feature_proof",
        "evidence_url": "internal://product/integrations",
        "applies_to_icp": ["seg_revops_director", "seg_ae_growth"],
        "status": "approved",
    },
    {
        "_id": "claim_handoff_speed",
        "claim_text": "Average sales-to-CS handoff time: 3 minutes (median across 200 customers).",
        "claim_type": "performance",
        "evidence_url": "internal://analytics/handoff_speed",
        "applies_to_icp": ["seg_revops_director"],
        "status": "approved",
    },
    {
        "_id": "claim_pricing_transparency",
        "claim_text": "Public pricing on the page, including the enterprise tier.",
        "claim_type": "positioning",
        "evidence_url": "https://example.com/pricing",
        "applies_to_icp": ["seg_revops_director", "seg_saas_founder"],
        "status": "approved",
    },
    {
        "_id": "claim_self_serve_setup",
        "claim_text": "Self-serve setup; first campaign live in under an hour.",
        "claim_type": "feature_proof",
        "evidence_url": "internal://product/onboarding",
        "applies_to_icp": ["seg_saas_founder"],
        "status": "approved",
    },
    {
        "_id": "claim_list_quality",
        "claim_text": "Native enrichment replaces 3rd-party data for ~80% of customers.",
        "claim_type": "performance",
        "evidence_url": "internal://analytics/enrichment_replacement",
        "applies_to_icp": ["seg_ae_growth"],
        "status": "approved",
    },
    {
        "_id": "claim_iteration_speed",
        "claim_text": "Configuration changes ship same-day; no engineering tickets.",
        "claim_type": "feature_proof",
        "evidence_url": "internal://product/config",
        "applies_to_icp": ["seg_saas_founder"],
        "status": "approved",
    },
    {
        "_id": "claim_sdr_onboard",
        "claim_text": "New SDRs onboard in a week using the same playbook the team uses.",
        "claim_type": "process",
        "evidence_url": "internal://customer/sdr_onboarding",
        "applies_to_icp": ["seg_ae_growth"],
        "status": "approved",
    },
    {
        "_id": "claim_close_rate_lift",
        "claim_text": "Customers report 15-25% close-rate lift within 90 days (case studies pending).",
        "claim_type": "performance",
        "evidence_url": "internal://customer/case_studies",
        "applies_to_icp": ["seg_revops_director", "seg_ae_growth"],
        "status": "approved",
    },
    {
        "_id": "claim_annual_monthly",
        "claim_text": "Annual pricing with monthly billing.",
        "claim_type": "pricing",
        "evidence_url": "https://example.com/pricing",
        "applies_to_icp": ["seg_revops_director", "seg_saas_founder"],
        "status": "approved",
    },
    {
        "_id": "claim_integration_count",
        "claim_text": "30+ native integrations across CRM, marketing automation, and data.",
        "claim_type": "feature_proof",
        "evidence_url": "internal://product/integrations",
        "applies_to_icp": ["seg_revops_director"],
        "status": "approved",
    },
]
