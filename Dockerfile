# syntax=docker/dockerfile:1
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl build-essential \
  && rm -rf /var/lib/apt/lists/*

# Copy dependency definitions
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy app
COPY . .

# Healthcheck: simple TCP to Discord (optional best-effort)
HEALTHCHECK --interval=30s --timeout=10s --retries=5 CMD python - <<'PY' || exit 1
import socket
try:
    with socket.create_connection(("discord.com", 443), timeout=5):
        pass
except Exception as e:
    raise SystemExit(1)
PY

# Default command
CMD ["python", "Reminderbot.py"] 