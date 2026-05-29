FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./pyproject.toml
RUN pip install --no-cache-dir '.[worker-eval]'

COPY shared/ ./shared/
COPY services/eval_harness/ ./services/eval_harness/

ENV PYTHONPATH=/app
CMD ["python", "-m", "services.eval_harness.main"]
