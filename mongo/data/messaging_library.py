"""Approved messaging claims — Content Agent uses these, Review Agent validates against them.

Claims describe signalCommerce's offering: the testing & trust layer for
agentic commerce (agent-readiness scoring, protocol conformance, fault
injection, native observability). Every claim has an evidence_url (internal
pointer or external link) so the Review Agent can defend any claim that
survives into a draft.

The applies_to_icp field is the join key — Content + Review pull claims
matching the target ICP via mongodb.find(filter={"applies_to_icp": <seg>,
"status": "approved"}).
"""
from __future__ import annotations

MESSAGING_CLAIMS: list[dict] = [
    {
        "_id": "claim_readiness_score",
        "claim_text": ("Agent-readiness score across ChatGPT Shop, Copilot "
                       "Checkout, Google AI Mode, and Gemini Shopping."),
        "claim_type": "feature_proof",
        "evidence_url": "internal://product/readiness-score",
        "applies_to_icp": ["seg_merchant_dtc", "seg_ecom_leader"],
        "status": "approved",
    },
    {
        "_id": "claim_protocol_coverage",
        "claim_text": ("Conformance testing for UCP, ACP, MCP, A2A, and WebMCP "
                       "in one suite."),
        "claim_type": "feature_proof",
        "evidence_url": "internal://product/protocol-diagnostics",
        "applies_to_icp": ["seg_merchant_dtc", "seg_agent_platform",
                           "seg_payments_network"],
        "status": "approved",
    },
    {
        "_id": "claim_fault_injection",
        "claim_text": ("Stress-test agent flows with 21 fault injections "
                       "before they reach production."),
        "claim_type": "feature_proof",
        "evidence_url": "internal://product/fault-injection",
        "applies_to_icp": ["seg_merchant_dtc", "seg_ecom_leader"],
        "status": "approved",
    },
    {
        "_id": "claim_pre_prod_catch",
        "claim_text": ("Catch protocol conformance gaps before they reach a "
                       "live buyer."),
        "claim_type": "positioning",
        "evidence_url": "internal://product/aegis",
        "applies_to_icp": ["seg_merchant_dtc"],
        "status": "approved",
    },
    {
        "_id": "claim_native_observability",
        "claim_text": ("Native observability for agent checkout, with Datadog, "
                       "Splunk, and Elastic dashboards."),
        "claim_type": "feature_proof",
        "evidence_url": "internal://product/observability",
        "applies_to_icp": ["seg_ecom_leader", "seg_agent_platform"],
        "status": "approved",
    },
    {
        "_id": "claim_persona_system",
        "claim_text": ("Simulate buyers across 8 proprietary persona dimensions "
                       "in deterministic and agentic modes."),
        "claim_type": "feature_proof",
        "evidence_url": "internal://product/persona-system",
        "applies_to_icp": ["seg_merchant_dtc", "seg_ecom_leader"],
        "status": "approved",
    },
    {
        "_id": "claim_revenue_protection",
        "claim_text": ("Prevent silent revenue loss when an agent protocol "
                       "changes underneath you."),
        "claim_type": "positioning",
        "evidence_url": "internal://product/agent-activity-pulse",
        "applies_to_icp": ["seg_merchant_dtc", "seg_ecom_leader"],
        "status": "approved",
    },
    {
        "_id": "claim_payment_assurance",
        "claim_text": ("Verify agent payment mandates and delegated authority "
                       "before settlement."),
        "claim_type": "feature_proof",
        "evidence_url": "internal://product/payment-assurance",
        "applies_to_icp": ["seg_payments_network"],
        "status": "approved",
    },
    {
        "_id": "claim_agent_decoder",
        "claim_text": "See exactly what the agent saw with the Native Agent Decoder.",
        "claim_type": "feature_proof",
        "evidence_url": "internal://product/agent-decoder",
        "applies_to_icp": ["seg_merchant_dtc", "seg_agent_platform"],
        "status": "approved",
    },
    {
        "_id": "claim_magento_connector",
        "claim_text": ("Drop-in Magento connector — agent-readiness checks with "
                       "no re-platforming."),
        "claim_type": "feature_proof",
        "evidence_url": "internal://product/magento-connector",
        "applies_to_icp": ["seg_merchant_dtc"],
        "status": "approved",
    },
]
