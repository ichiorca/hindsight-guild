FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./pyproject.toml
RUN pip install --no-cache-dir '.[http-handler-slack]'

COPY services/slack_approval_handler/ ./services/slack_approval_handler/

ENV PYTHONPATH=/app
CMD ["gunicorn", "-b", "0.0.0.0:8080", "services.slack_approval_handler.main:app"]
