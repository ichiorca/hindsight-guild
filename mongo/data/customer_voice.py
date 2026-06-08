"""Customer voice quotes — agentic commerce, across 4 ICP segments.

Merchants (DTC) get the heaviest coverage because they're the primary ICP
(signalCommerce sells the agent-readiness / protocol-conformance testing &
trust layer). Themes map to topics agents would naturally draft about so the
Content Agent's vector-search returns on-topic quotes: agent-readiness,
checkout reliability, discoverability, protocol conformance, observability,
payment assurance, integration conformance.

Voyage AI embeddings on the `text` field are generated automatically by the
MongoDB MCP server's auto-embed feature on insert.
"""
from __future__ import annotations

# PRIMARY ICP — DTC / e-commerce merchants going "agent-ready".
MERCHANT_DTC: list[tuple[str, str]] = [
    # agent_readiness (3)
    ("We thought we were ChatGPT-ready until a test agent failed at checkout.",
     "agent_readiness"),
    ("Going agent-ready took one sprint once we could see what the agent saw.",
     "agent_readiness"),
    ("Our catalog looked fine to humans and broke for every shopping agent.",
     "agent_readiness"),
    # checkout_reliability (3)
    ("An agent abandoned a $400 cart because our coupon field wasn't machine-readable.",
     "checkout_reliability"),
    ("A protocol update broke our agent checkout twice last quarter — silently.",
     "checkout_reliability"),
    ("We only caught the conformance gap because revenue from AI traffic flatlined.",
     "checkout_reliability"),
    # discoverability (3)
    ("Perplexity recommended a competitor because our feed wasn't structured for agents.",
     "discoverability"),
    ("If ChatGPT can't parse your product, you don't exist to the buyer.",
     "discoverability"),
    ("Agent traffic is now one in six sessions and we were invisible to all of it.",
     "discoverability"),
    # protocol_conformance (3)
    ("The UCP vs ACP differences are exactly where our flow silently failed.",
     "protocol_conformance"),
    ("We needed a readiness score we could show the board, not a vibe check.",
     "protocol_conformance"),
    ("Fault injection caught the edge case that would have cost us Black Friday.",
     "protocol_conformance"),
]

# E-commerce / digital leaders at brands + retailers.
ECOM_LEADER: list[tuple[str, str]] = [
    # agent_observability (3)
    ("AI-driven sessions convert differently and we had no instrumentation for them.",
     "agent_observability"),
    ("Observability for agent checkout is the gap our APM never filled.",
     "agent_observability"),
    ("We run human A/B tests but had zero coverage on agent journeys.",
     "agent_observability"),
    # readiness_as_kpi (3)
    ("Leadership asked 'are we agent-ready?' and no one could answer with data.",
     "readiness_as_kpi"),
    ("Half our roadmap is agentic commerce; none of our QA covered it.",
     "readiness_as_kpi"),
    ("We needed one number — a readiness score — to prioritize the work.",
     "readiness_as_kpi"),
]

# Payment providers, PSPs, card networks / issuers.
PAYMENTS_NETWORK: list[tuple[str, str]] = [
    # payment_assurance (3)
    ("Agent payment mandates are easy to issue and hard to test end to end.",
     "payment_assurance"),
    ("We need to prove an agent transaction was authorized before it settles.",
     "payment_assurance"),
    ("Issuers want assurance the agent had delegated authority — we had logs, not proof.",
     "payment_assurance"),
    # agent_fraud (3)
    ("Fraud patterns from autonomous agents don't look like human fraud.",
     "agent_fraud"),
    ("AP2 and ACP both touch our rails; conformance testing was manual and brittle.",
     "agent_fraud"),
    ("Tokenized agent credentials fail in ways our old test suite never imagined.",
     "agent_fraud"),
]

# Agentic buyer platforms + teams building shopping/commerce agents.
AGENT_PLATFORM: list[tuple[str, str]] = [
    # integration_conformance (3)
    ("Our shopping agent passed demos and failed real merchant checkout flows.",
     "integration_conformance"),
    ("Every merchant implements MCP slightly differently — we needed conformance signals.",
     "integration_conformance"),
    ("We needed to certify a merchant integration before shipping it to users.",
     "integration_conformance"),
    # agent_reliability (3)
    ("Tool calls that work on one store 500 on another; we were flying blind.",
     "agent_reliability"),
    ("WebMCP and A2A interop bugs only show up under multi-session load.",
     "agent_reliability"),
    ("Deterministic and agentic test modes let us reproduce the flaky checkout.",
     "agent_reliability"),
]


def build_voice_docs(rng) -> list[dict]:
    """Return Mongo-ready customer_voice documents.

    rng: a random.Random instance — passed in for deterministic source IDs
    when called from seed_demo.
    """
    quotes: list[dict] = []
    for text, theme in MERCHANT_DTC:
        quotes.append({
            "text": text,
            "icp_segment": "seg_merchant_dtc",
            "persona": "merchant",
            "source": f"sales_call_{rng.randint(1000, 9999)}",
            "theme": theme,
            "sentiment": "positive",
        })
    for text, theme in ECOM_LEADER:
        quotes.append({
            "text": text,
            "icp_segment": "seg_ecom_leader",
            "persona": "ecom_leader",
            "source": f"nps_{rng.randint(1000, 9999)}",
            "theme": theme,
            "sentiment": "positive",
        })
    for text, theme in PAYMENTS_NETWORK:
        quotes.append({
            "text": text,
            "icp_segment": "seg_payments_network",
            "persona": "payments_lead",
            "source": f"sales_call_{rng.randint(1000, 9999)}",
            "theme": theme,
            "sentiment": "positive",
        })
    for text, theme in AGENT_PLATFORM:
        quotes.append({
            "text": text,
            "icp_segment": "seg_agent_platform",
            "persona": "agent_builder",
            "source": f"support_{rng.randint(1000, 9999)}",
            "theme": theme,
            "sentiment": "positive",
        })
    return quotes
