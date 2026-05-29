FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./pyproject.toml
RUN pip install --no-cache-dir '.[worker-a2a-client]'

# agents/a2a_client (URL resolution + JSON-RPC POST) is the only agent
# module needed by the cron — keep the image lean.
COPY agents/__init__.py ./agents/__init__.py
COPY agents/a2a_client.py ./agents/a2a_client.py
COPY services/positioning_review/ ./services/positioning_review/

ENV PYTHONPATH=/app
CMD ["python", "-m", "services.positioning_review.main"]
