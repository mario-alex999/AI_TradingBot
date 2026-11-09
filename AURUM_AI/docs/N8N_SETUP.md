# n8n with Docker (local automation for AURUM AI)

n8n runs in its own Docker Compose project (`docker-compose.n8n.yml`, project `aurum-n8n`). It is separate from the database
container, so starting or stopping n8n never touches TimescaleDB.

## Safety boundaries
- n8n is an orchestrator only. It calls the AURUM HTTP API. It has **no MT5 credentials, no broker access and no provider API keys**.
- n8n cannot override a Module 06 risk decision. A `BLOCK` stays a block; there are no order or execution endpoints to call.
- The UI is bound to `127.0.0.1:5678` (this machine only). Workflows cannot read container environment variables
  (`N8N_BLOCK_ENV_ACCESS_IN_NODE=true`).
- If a workflow ever needs an admin endpoint, store the key in the n8n UI as a **Header Auth credential** named
  `X-AURUM-Admin-Key`. Never type it into node parameters or commit it.

## Start, stop, update
```powershell
docker compose -f docker-compose.n8n.yml --env-file .env up -d     # start -> http://localhost:5678
docker compose -f docker-compose.n8n.yml --env-file .env logs -f n8n
docker compose -f docker-compose.n8n.yml --env-file .env down      # stop (data stays in the n8n_data volume)
```
To update n8n, change `N8N_VERSION` in `.env` (the default pin is `2.46.6`) and run `up -d` again.

On the first visit, n8n asks you to create the local owner account. That account lives only in the `n8n_data` volume.

### `.env` keys
| Key | Purpose |
|---|---|
| `N8N_ENCRYPTION_KEY` | Encrypts the credentials n8n stores. It is generated once. **Back it up**: if you lose it, saved credentials become unreadable. |
| `N8N_PORT` | Host port, 5678 by default. |
| `N8N_TIMEZONE` | Time zone for schedule triggers, UTC by default. |
| `N8N_VERSION` | Optional image tag override. |

## Reaching AURUM from n8n
Inside the container, the host is `host.docker.internal`. Use:
- the API at `http://host.docker.internal:8000/api/v1/...` (start it with `python -m aurum.api`);
- the n8n webhooks from the host at `http://localhost:5678/webhook/<path>`.

## Starter workflows (imported, inactive)
| Workflow | What it does |
|---|---|
| **AURUM - Risk events receiver** | A webhook at `POST /webhook/aurum-risk-events`. Events `risk.assessment.blocked`, `risk.assessment.requires_review` and `risk.lock.activated` go to a "Notify" placeholder. Connect Telegram, Slack or email there. |
| **AURUM - Risk status check (read-only)** | Every 15 minutes it calls `GET /risk/account`. Anything other than `WITHIN_LIMITS` goes to an "Alert" placeholder. |

To use one, open it, replace the placeholder node with your notifier, then toggle the workflow **Active**.

To feed risk events into the receiver from AURUM:
```powershell
python -m aurum.cli risk events dispatch --url http://localhost:5678/webhook/aurum-risk-events
```
Delivery is retried with backoff. After 8 failed attempts an event is dead-lettered and stays visible in `GET /risk/events`.
Alternatively, n8n can pull events itself with `GET /risk/events?after=<sequence>`.

### Re-importing the workflow files
Run this after editing `n8n/workflows/*.json`. Re-importing with the same id overwrites that workflow.
```powershell
docker exec aurum-n8n n8n import:workflow --separate --input=/workflows
```
From Git Bash, prefix the command with `MSYS_NO_PATHCONV=1`. Otherwise `/workflows` gets rewritten into a Windows path.
