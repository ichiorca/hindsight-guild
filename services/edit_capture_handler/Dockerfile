FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./pyproject.toml
RUN pip install --no-cache-dir '.[http-handler-edit]'

COPY shared/ ./shared/
COPY services/edit_capture_handler/ ./services/edit_capture_handler/

ENV PYTHONPATH=/app
CMD ["gunicorn", "-b", "0.0.0.0:8080", "services.edit_capture_handler.main:app"]
