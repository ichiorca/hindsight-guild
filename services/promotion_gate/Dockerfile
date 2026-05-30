FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./pyproject.toml
RUN pip install --no-cache-dir '.[worker-bq]'

COPY shared/ ./shared/
COPY agents/ ./agents/
COPY services/promotion_gate/ ./services/promotion_gate/

ENV PYTHONPATH=/app
CMD ["python", "-m", "services.promotion_gate.main"]
