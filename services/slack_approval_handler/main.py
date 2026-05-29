"""C11 — Slack approval handler (one-way notification for hackathon).

Receives 'slack_approval' tool calls from agents, posts to a Slack incoming
webhook, returns an approval_id. The Sheet is the actual approval surface;
Slack just nudges the founder.

Phase-2 upgrade: replace with bidirectional Slack app (interactive components,
buttons, action handlers).
"""
from __future__ import annotations

import os
import uuid

import httpx
from flask import Flask, jsonify, request

from shared.clients import secret_value

app = Flask(__name__)
PROJECT_ID = os.environ["PROJECT_ID"]


def _webhook_url() -> str:
    return secret_value("slack_webhook_url")


@app.post("/approval_request")
def approval_request():
    p = request.get_json(force=True)
    approval_id = f"appr_{uuid.uuid4().hex[:10]}"
    msg = {
        "text": (
            f":memo: *Approval requested* (`{approval_id}`)\n"
            f"Action: {p.get('action_type')}\n"
            f"Draft preview: {p.get('draft_summary', '')[:300]}\n"
            f"To approve, set decision=approve on the corresponding row "
            f"in the approval Sheet."
        )
    }
    url = _webhook_url()
    if url and url != "PENDING":
        r = httpx.post(url, json=msg, timeout=5)
        r.raise_for_status()
    return jsonify({"approval_id": approval_id}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
