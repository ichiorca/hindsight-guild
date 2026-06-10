FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./pyproject.toml
RUN pip install --no-cache-dir '.[http-publisher]'

COPY shared/ ./shared/
COPY mongo/ ./mongo/
COPY agents/_common.py ./agents/_common.py
COPY agents/__init__.py ./agents/__init__.py
COPY services/substack_publisher/ ./services/substack_publisher/

ENV PYTHONPATH=/app
CMD ["gunicorn", "-b", "0.0.0.0:8080", "--timeout", "60", "services.substack_publisher.main:app"]
