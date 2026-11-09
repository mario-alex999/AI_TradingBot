# AURUM backend image (Linux). Used by docker-compose.approvals.yml for the Module 07 worker and, optionally, the API.
# MetaTrader5 is Windows-only and is skipped here by its platform marker: nothing in this image can reach MT5 or place
# an order. Account and position data come from the database mirror that `sync-account` fills on the Windows host.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

RUN useradd --create-home --uid 10001 aurum
COPY requirements.txt pyproject.toml ./
RUN pip install -r requirements.txt
COPY aurum ./aurum
COPY config ./config
COPY sql ./sql

USER aurum
# No secrets are baked in: settings come from the environment (.env via compose) at run time.
CMD ["python", "-m", "aurum.cli", "approvals", "worker"]
