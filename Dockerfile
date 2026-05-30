# Agent runtime image — Python 3.12 + Node 22 LTS + globally-installed
# mongodb-mcp-server.
#
# Used by every a2a-* Cloud Run service. The Node + mongodb-mcp-server combo
# exists for one reason: ADK's MCPToolset launches the MongoDB MCP server
# as a stdio subprocess. By installing mongodb-mcp-server globally at image
# build time (and pinning Node 22 LTS where its transitive deps are all
# supported), runtime is fast (no npx cold-start) and free of the
# "mongodb-redact .esm-wrapper.mjs missing" failure that hits Node 25.
#
# Cron workers (services/*/Dockerfile) keep their per-service slim images
# since they don't use MCPToolset.

FROM python:3.12-slim

# System deps: Node 22 LTS (mongodb-mcp-server's transitive deps require
# ^22.22.2 || ^24.15.0 || >=26.0.0 — Node 22 LTS is the safest bet).
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates gnupg \
 && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
 && apt-get install -y --no-install-recommends nodejs \
 && rm -rf /var/lib/apt/lists/*

# mongodb-mcp-server installed globally at build time so the subprocess
# launches without npx caching. Version pinned to avoid surprise upgrades.
RUN npm install -g mongodb-mcp-server@1.11.0 \
 && mongodb-mcp-server --version

WORKDIR /app

# Install Python deps from pyproject (cached layer; only re-runs when
# pyproject.toml changes).
COPY pyproject.toml ./
RUN pip install --no-cache-dir -e .

# Source — keep this last so code changes don't blow the dep cache.
COPY agents/ ./agents/
COPY mongo/ ./mongo/
COPY shared/ ./shared/
COPY skills/ ./skills/
COPY prompts/ ./prompts/
COPY scripts/aeo/ ./scripts/aeo/

ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1

# A2A apps run via uvicorn; the deploy command supplies the app target
# (agents.a2a_server:research_a2a, etc.) at deploy time, so no CMD here.
