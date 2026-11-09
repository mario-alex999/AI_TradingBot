# Module 07 Docker setup

Module 07 adds **no new database**. It uses the existing `aurum-timescaledb` container (`docker-compose.yml`). Its own
services live in a separate Compose project, `aurum-approvals` (`docker-compose.approvals.yml`), so starting or
stopping them never restarts the database or n8n.

| Compose file | Project | Services |
|---|---|---|
| `docker-compose.yml` | `aurum_ai` | `timescaledb` (PostgreSQL 16 + TimescaleDB) |
| `docker-compose.n8n.yml` | `aurum-n8n` | `n8n` on `127.0.0.1:5678` |
| `docker-compose.approvals.yml` | `aurum-approvals` | `approvals-worker` (always). `api` (profile `api`, on `127.0.0.1:8001`) |

The image (`Dockerfile`, tag `aurum-backend:local`) is `python:3.11-slim` and runs as a non-root user. **It contains no
MetaTrader5 package** (Windows-only, skipped by its platform marker) and no secrets: settings come from `.env` at run
time, and `.dockerignore` keeps `.env`, data, logs and tests out of the build context.

## Commands (from `AURUM_AI`)
```powershell
# 1. Database (if not running) and migrations
docker compose --env-file .env up -d
python -m aurum.cli init-db

# 2. Module 07 worker in a container
docker compose -f docker-compose.approvals.yml --env-file .env up -d --build
docker compose -f docker-compose.approvals.yml --env-file .env logs -f approvals-worker
docker compose -f docker-compose.approvals.yml --env-file .env run --rm --no-deps approvals-worker python -m aurum.cli approvals status

# Optional: the API in a container too (port 8001, so the host API can keep 8000)
docker compose -f docker-compose.approvals.yml --env-file .env --profile api up -d --build

# Stop (the database and n8n keep running)
docker compose -f docker-compose.approvals.yml --env-file .env --profile api down
```

The containers reach the database through `host.docker.internal:${DB_PORT}`. `DB_HOST` is overridden in the Compose
file, so `.env` can keep `DB_HOST=localhost` for host processes. If you set `DATABASE_URL` in `.env`, it takes priority:
point it at `host.docker.internal` for the containers.

**Run one worker.** Running the worker on the host (`python -m aurum.cli approvals worker`) *and* in a container is
safe, because every step is idempotent and serialised in the database. It is still wasteful, so pick one.

## Verified
On 2026-11-09:
- The image was built.
- `approvals status` ran inside the container against the live `aurum-timescaledb` database and reported status `ok`,
  a valid audit chain, Module 06 `ok`, and Module 09 "Not built".
- `docker compose … config` validates.

## n8n
Import `n8n/workflows/aurum_approval_events_receiver.json` and `aurum_approval_expiry_reminder.json` in the n8n UI
(Workflows → Import). Both are imported **inactive**. See
[MODULE_07_INTEGRATION_GUIDE.md](MODULE_07_INTEGRATION_GUIDE.md#module-08--n8n).

## Production notes
- Put the API behind a TLS reverse proxy and set `AUTH_COOKIE_SECURE=true`. Keep ports bound to `127.0.0.1` unless the
  proxy is on another host.
- Memory limits are 512 MB per Module 07 container. The worker uses far less.
- Back up the `aurum_pgdata` volume. Module 07's audit trail lives there.
