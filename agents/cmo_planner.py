"""CMO Planner — top-level orchestrator. Calls other agents as tools.

This is the ADK-coded version. The Agent Designer YAML at
agents/cmo_planner_visual/playbook.yaml is the visual demo variant; this
file is the real execution path. See agents/cmo_planner_visual/README.md
for when to use which. (The visual variant lives in a separate directory
to avoid Python's package-vs-module name collision with this file.)

Tool stack:
  - AgentTool(research_agent)   — ICP-scoped customer voice + competitor scans
  - AgentTool(analytics_agent)  — weekly performance snapshot from BQ
  - mongodb_toolset (writer)    — experiments, skills, self-critique proposals
  - slack_approval (FunctionTool) — post memo for founder approval
"""
from __future__ import annotations

import os
import uuid

import httpx
from google.adk.tools import FunctionTool
from google.adk.tools.agent_tool import AgentTool
from google.cloud import secretmanager

from agents._evidence_tool import evidence_validator_tool
from agents._factory import make_llm_agent
from agents._models import HEAVY
from agents._prompts import CMO_PLANNER_INSTRUCTIONS
from agents.analytics import analytics_agent
from agents.research import research_agent
from agents.web_search import web_search_tool

# mongo_tools defaults to mongo_uri_writer; explicit setter removed because the
# multi-agent a2a_server would otherwise overwrite reader/writer intent based
# on import order (see C5 in the codebase review).

PROJECT_ID = os.environ.get("PROJECT_ID", "hindsight-guild-mvp")
_sm: secretmanager.SecretManagerServiceClient | None = None


def _slack_webhook_url() -> str:
    # Lazy-init the Secret Manager client. Constructing it at import time
    # fails in LOCAL_DEV (no ADC) — and we don't need it unless the agent
    # actually calls slack_approval. Keep import side-effect free.
    global _sm
    try:
        if _sm is None:
            _sm = secretmanager.SecretManagerServiceClient()
        name = f"projects/{PROJECT_ID}/secrets/slack_webhook_url/versions/latest"
        return _sm.access_secret_version(name=name).payload.data.decode()
    except Exception:
        return ""


def slack_approval(memo_markdown: str, action_type: str = "weekly_memo") -> dict:
    """Post a memo to Slack for founder approval. Returns {approval_id}."""
    import logging
    log = logging.getLogger(__name__)

    approval_id = f"appr_{uuid.uuid4().hex[:10]}"
    url = _slack_webhook_url()
    if not url or url == "PENDING":
        log.info(
            "slack_approval skipped — slack_webhook_url secret is %r. Run "
            "`gcloud secrets versions add slack_webhook_url --data-file=- ...` "
            "with the real webhook to enable founder notifications. "
            "(approval_id=%s, action_type=%s)",
            url or "missing", approval_id, action_type,
        )
        return {"approval_id": approval_id, "skipped": "webhook_unconfigured"}
    try:
        httpx.post(url, json={
            "text": (f":memo: *Approval requested* (`{approval_id}`)\n"
                     f"Action: {action_type}\n"
                     f"--- MEMO ---\n{memo_markdown[:3000]}"),
        }, timeout=5)
    except Exception as e:
        return {"approval_id": approval_id, "warning": f"slack post failed: {e}"}
    return {"approval_id": approval_id}


slack_approval_tool = FunctionTool(func=slack_approval)

cmo_planner = make_llm_agent(
    name="cmo_planner",
    instructions=CMO_PLANNER_INSTRUCTIONS,
    model=HEAVY,
    mode="write",
    output_key="weekly_plan",
    skill_id="weekly_memo",
    action_type="cmo_plan_op",
    extra_tools=[
        AgentTool(agent=research_agent),
        AgentTool(agent=analytics_agent),
        evidence_validator_tool,   # self-verify claims in the memo
        web_search_tool,           # check competitive/industry references
        slack_approval_tool,
    ],
)
