FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./pyproject.toml
RUN pip install --no-cache-dir '.[worker-publish-sweep]'

COPY shared/ ./shared/
COPY services/substack_publish_sweep/ ./services/substack_publish_sweep/

ENV PYTHONPATH=/app
CMD ["python", "-m", "services.substack_publish_sweep.main"]
