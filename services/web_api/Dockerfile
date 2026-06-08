FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./pyproject.toml
RUN pip install --no-cache-dir '.[web-api]'

COPY shared/ ./shared/
COPY agents/ ./agents/
COPY skills/ ./skills/
COPY prompts/ ./prompts/
COPY mongo/ ./mongo/
# Signal adapters (scripts.signals.*) are imported in-process by the signal
# watcher (agents/signal_watcher.py) when web-api serves /api/signals/poll-now,
# and agents pull scripts.aeo. Without this the watcher fails every source with
# "No module named 'scripts'" and ingests 0 signals.
COPY scripts/ ./scripts/
COPY services/web_api/ ./services/web_api/
COPY services/promotion_gate/ ./services/promotion_gate/
COPY services/edit_capture_handler/ ./services/edit_capture_handler/

ENV PYTHONPATH=/app
CMD ["uvicorn", "services.web_api.main:app", "--host", "0.0.0.0", "--port", "8080"]
