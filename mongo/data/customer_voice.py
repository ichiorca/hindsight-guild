"""Customer voice quotes — 30 across 3 ICP segments, thematically distributed.

RevOps gets the heaviest coverage because it's the primary demo ICP. Themes
are chosen so the reject-then-redraft demo moment is reproducible: when the
Content Agent vector-searches for an ICP, the returned quotes should map to
topics agents would naturally draft about (integrations, time-to-value,
handoffs, pricing, self-serve, list quality, process).

Voyage AI embeddings on the `text` field are generated automatically by the
MongoDB MCP server's auto-embed feature on insert.
"""
from __future__ import annotations

REVOPS_DIRECTOR: list[tuple[str, str]] = [
    # integration_critical (3)
    ("We tried five tools — the Salesforce integration is what made us stay.",
     "integration_critical"),
    ("Three days of HubSpot integration on the old vendor. Three minutes here.",
     "integration_critical"),
    ("Pipedrive sync was the dealbreaker, not the price.",
     "integration_critical"),
    # time_to_value (3)
    ("Setup took 20 minutes, not 20 days. That's the first time I've said that.",
     "time_to_value"),
    ("We were running our first campaign on day two. Day two.",
     "time_to_value"),
    ("I budgeted a quarter for onboarding. We finished in a week.",
     "time_to_value"),
    # handoff_friction (3)
    ("The handoff from sales to CS used to take 3 days. Now it's 3 minutes.",
     "handoff_friction"),
    ("Deal handoffs were where leads went to die. The new flow keeps them warm.",
     "handoff_friction"),
    ("AE-to-AM transitions are the worst part of B2B. This makes them invisible.",
     "handoff_friction"),
    # pricing_clarity (3)
    ("I knew what it would cost before I got on a call. That's rare.",
     "pricing_clarity"),
    ("No 'contact sales for enterprise' tier — pricing is on the page.",
     "pricing_clarity"),
    ("Annual pricing with monthly billing. Why isn't this standard?",
     "pricing_clarity"),
]

SAAS_FOUNDER: list[tuple[str, str]] = [
    # self_serve (4)
    ("I needed something I could plug in this afternoon, not a 3-month migration.",
     "self_serve"),
    ("Signed up at 2pm, sending first email by 4pm. That was the test.",
     "self_serve"),
    ("If I have to talk to sales before I can try it, I move on.",
     "self_serve"),
    ("Self-serve onboarding means I can decide if it works on a weekend.",
     "self_serve"),
    # pricing_transparency (3)
    ("Pricing was clear. No 'contact sales for enterprise' nonsense.",
     "pricing_transparency"),
    ("I budgeted from the pricing page. No surprise quote.",
     "pricing_transparency"),
    ("Annual commitment is the right ask once I've used it for a month.",
     "pricing_transparency"),
    # speed_to_iterate (3)
    ("We test five variants in the time we used to test one.",
     "speed_to_iterate"),
    ("Iteration speed is the only moat at our size.",
     "speed_to_iterate"),
    ("If I have to wait a sprint for a config change, I'm using the wrong tool.",
     "speed_to_iterate"),
]

AE_GROWTH: list[tuple[str, str]] = [
    # list_quality (4)
    ("The list-build feature alone justifies the price.",
     "list_quality"),
    ("We've stopped buying third-party data. The native enrichment is enough.",
     "list_quality"),
    ("Lookalike lists from our actual customers — that's what was missing.",
     "list_quality"),
    ("We tripled list quality and halved list size. Replies went up.",
     "list_quality"),
    # process_over_intuition (4)
    ("Outbound used to be a guessing game. Now it's a process.",
     "process_over_intuition"),
    ("The team's bad days look like other teams' good days because the process holds.",
     "process_over_intuition"),
    ("We can onboard a new SDR in a week. The system carries the institutional memory.",
     "process_over_intuition"),
    ("Repeatability beats heroics. This makes us repeatable.",
     "process_over_intuition"),
]


def build_voice_docs(rng) -> list[dict]:
    """Return Mongo-ready customer_voice documents.

    rng: a random.Random instance — passed in for deterministic source IDs
    when called from seed_demo.
    """
    quotes: list[dict] = []
    for text, theme in REVOPS_DIRECTOR:
        quotes.append({
            "text": text,
            "icp_segment": "seg_revops_director",
            "persona": "rev_ops_director",
            "source": f"sales_call_{rng.randint(1000, 9999)}",
            "theme": theme,
            "sentiment": "positive",
        })
    for text, theme in SAAS_FOUNDER:
        quotes.append({
            "text": text,
            "icp_segment": "seg_saas_founder",
            "persona": "founder",
            "source": f"nps_{rng.randint(1000, 9999)}",
            "theme": theme,
            "sentiment": "positive",
        })
    for text, theme in AE_GROWTH:
        quotes.append({
            "text": text,
            "icp_segment": "seg_ae_growth",
            "persona": "ae",
            "source": f"support_{rng.randint(1000, 9999)}",
            "theme": theme,
            "sentiment": "positive",
        })
    return quotes
