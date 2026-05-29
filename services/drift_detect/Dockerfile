FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./pyproject.toml
RUN pip install --no-cache-dir '.[worker-bq]'

COPY shared/ ./shared/
COPY services/drift_detect/ ./services/drift_detect/

ENV PYTHONPATH=/app
CMD ["python", "-m", "services.drift_detect.main"]
