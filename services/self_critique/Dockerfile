FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./pyproject.toml
# worker-bq carries pymongo + secret-manager (shared.mongo_tools). The miners
# are deterministic Mongo-only Python — no genai/MCP — so we don't need the
# heavy agent extra here.
RUN pip install --no-cache-dir '.[worker-bq]'

# PRD-03 self-critique miners run as a Cloud Run job: self_critique_runner
# orchestrates agents/_miners/* over Mongo telemetry and writes
# self_critique_runs + proposals. Bundle exactly those modules (+ shared) —
# agents/__init__.py and _miners/__init__.py are intentionally light so this
# stays lean and doesn't pull the full agent graph (Vertex/MCP) at import.
COPY shared/ ./shared/
COPY agents/__init__.py ./agents/__init__.py
COPY agents/_schema_constants.py ./agents/_schema_constants.py
COPY agents/self_critique_runner.py ./agents/self_critique_runner.py
COPY agents/_miners/ ./agents/_miners/
COPY services/self_critique/ ./services/self_critique/

ENV PYTHONPATH=/app
CMD ["python", "-m", "services.self_critique.main"]
