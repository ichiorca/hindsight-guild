FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./pyproject.toml
RUN pip install --no-cache-dir '.[worker-snapshot]'

COPY shared/ ./shared/
COPY services/snapshot_mongo/ ./services/snapshot_mongo/

ENV PYTHONPATH=/app
CMD ["python", "-m", "services.snapshot_mongo.main"]
