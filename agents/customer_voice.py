"""Customer Voice Agent — raw-text ingestion → structured customer_voice docs.

Takes sales-call transcripts, support tickets, NPS responses, churn
interviews, community posts; writes one or more customer_voice documents
per input. Voyage AI auto-embeds on insert via the MongoDB MCP server.

Distinct from the Research Agent (which has voice ingestion as one of its
three jobs but also does competitor scans and market signal scans).
Customer Voice is the focused ingestion specialist — invoke it when you
have a transcript to digest.
"""
from __future__ import annotations

from agents._factory import make_llm_agent
from agents._models import LIGHT
from agents._prompts import CUSTOMER_VOICE_INSTRUCTIONS

# mongo_tools defaults to mongo_uri_writer; explicit setter removed (see C5).

customer_voice_agent = make_llm_agent(
    name="customer_voice_agent",
    instructions=CUSTOMER_VOICE_INSTRUCTIONS,
    model=LIGHT,
    mode="write",
    output_key="voice_ingest",
    skill_id="voice_ingest",
    action_type="voice_ingest_op",
)
