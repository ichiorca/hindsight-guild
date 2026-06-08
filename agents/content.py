"""Content Agent — pipeline node #2.

Reads {research_findings} from session state (set by ResearchAgent). Writes
the draft to state['draft'] via output_key. Eval scoring runs in the
after_agent_callback (Vertex AI Gen AI Evaluation Service, all 6 rubrics).
"""
from __future__ import annotations

from agents._factory import make_llm_agent
from agents._models import HEAVY
from agents._prompts import CONTENT_INSTRUCTIONS

# Read-only intent is enforced per-call inside shared/rubrics.py and
# shared/allocator.py via secret_name="mongo_uri_readonly". Setting a process
# default here would be silently overwritten when the multi-agent a2a_server
# imports the writer agents.

content_agent = make_llm_agent(
    name="content_agent",
    instructions=CONTENT_INSTRUCTIONS,
    model=HEAVY,
    mode="read",
    output_key="draft",
    skill_id="linkedin_post",
    action_type="draft_linkedin",
    channel="linkedin",
)
