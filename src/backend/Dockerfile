# Prototype single-app deployment per HLD §7.
# TODO(P6 - QA/Integration Lead): review before Day 6 hardening — pin the
# Python patch version, add a non-root user, and a HEALTHCHECK against
# /healthz once main.py's endpoints are implemented.
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
