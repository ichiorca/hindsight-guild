FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./pyproject.toml
RUN pip install --no-cache-dir '.[worker-bq]'

COPY shared/ ./shared/
COPY mongo/ ./mongo/
COPY services/outcome_attach/ ./services/outcome_attach/

ENV PYTHONPATH=/app
CMD ["python", "-m", "services.outcome_attach.main"]
