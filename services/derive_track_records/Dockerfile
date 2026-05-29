FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./pyproject.toml
RUN pip install --no-cache-dir '.[worker-bq]'

COPY shared/ ./shared/
COPY services/derive_track_records/ ./services/derive_track_records/

ENV PYTHONPATH=/app
CMD ["python", "-m", "services.derive_track_records.main"]
