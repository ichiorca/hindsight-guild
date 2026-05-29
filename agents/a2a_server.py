"""A2A server entry points for every agent in the team.

Each agent gets its own port and Cloud Run service via deploy.sh. Each
auto-generates an AgentCard at /.well-known/agent-card.json.

Usage (local dev):
  uvicorn agents.a2a_server:research_a2a   --host 0.0.0.0 --port 8001
  uvicorn agents.a2a_server:content_a2a    --host 0.0.0.0 --port 8002
  uvicorn agents.a2a_server:review_a2a     --host 0.0.0.0 --port 8003
  uvicorn agents.a2a_server:analytics_a2a  --host 0.0.0.0 --port 8004
  uvicorn agents.a2a_server:pipeline_a2a   --host 0.0.0.0 --port 8005
  uvicorn agents.a2a_server:cmo_a2a        --host 0.0.0.0 --port 8006
  uvicorn agents.a2a_server:positioning_a2a       --host 0.0.0.0 --port 8007
  uvicorn agents.a2a_server:customer_voice_a2a    --host 0.0.0.0 --port 8008
  uvicorn agents.a2a_server:lifecycle_email_a2a   --host 0.0.0.0 --port 8009
  uvicorn agents.a2a_server:paid_media_a2a        --host 0.0.0.0 --port 8010
  uvicorn agents.a2a_server:ops_qa_a2a            --host 0.0.0.0 --port 8011
  uvicorn agents.a2a_server:self_critique_a2a     --host 0.0.0.0 --port 8012
  uvicorn agents.a2a_server:image_brief_a2a       --host 0.0.0.0 --port 8013
"""
from __future__ import annotations

from google.adk.a2a.utils.agent_to_a2a import to_a2a

from agents.analytics import analytics_agent
from agents.cmo_planner import cmo_planner
from agents.content import content_agent
from agents.customer_voice import customer_voice_agent
from agents.image_brief import image_brief_agent
from agents.lifecycle_email import lifecycle_email_agent
from agents.ops_qa import ops_qa_agent
from agents.paid_media import paid_media_agent
from agents.pipeline import drafting_pipeline
from agents.positioning import positioning_agent
from agents.research import research_agent
from agents.review import review_agent
from agents.self_critique import self_critique_agent

# Drafting team
research_a2a   = to_a2a(research_agent, port=8001)
content_a2a    = to_a2a(content_agent, port=8002)
review_a2a     = to_a2a(review_agent, port=8003)
analytics_a2a  = to_a2a(analytics_agent, port=8004)
pipeline_a2a   = to_a2a(drafting_pipeline, port=8005)
cmo_a2a        = to_a2a(cmo_planner, port=8006)

# Extended team
positioning_a2a       = to_a2a(positioning_agent, port=8007)
customer_voice_a2a    = to_a2a(customer_voice_agent, port=8008)
lifecycle_email_a2a   = to_a2a(lifecycle_email_agent, port=8009)
paid_media_a2a        = to_a2a(paid_media_agent, port=8010)
ops_qa_a2a            = to_a2a(ops_qa_agent, port=8011)
self_critique_a2a     = to_a2a(self_critique_agent, port=8012)
image_brief_a2a       = to_a2a(image_brief_agent, port=8013)
