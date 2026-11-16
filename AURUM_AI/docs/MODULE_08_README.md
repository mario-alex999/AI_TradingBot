# Module 08 — Workflow Orchestration, Integration and Automation Control Plane (Core)

Module 08 is the control plane between AURUM and n8n. It registers and versions workflows, triggers them, follows each
execution through to a callback, routes events, retries safely, parks permanent failures in dead letters, runs schedules,
hands out coordination leases, checks the health of every integration and audits every change.

**It does not trade.** Module 06 owns risk decisions, Module 07 owns human approval, Module 09 owns execution. n8n is a
workflow runner: it can *request* work (e.g. `ai.analysis.requested`) but can never publish a risk, approval or
execution decision, and nothing in Module 08 turns AI output into a trade.

## What is in the core

| Area | Where |
| --- | --- |
| State machines (execution, workflow, version) | `aurum/orchestration/states.py` |
| Event catalogue and envelope validation | `aurum/orchestration/catalogue.py` |
| Database (22 `orch_*` tables), migration `0010` | `aurum/orchestration/db_models.py`, `sql/migrations/0010_orchestration.sql` |
| n8n adapter + controlled test adapter | `aurum/orchestration/engines.py` |
| Registry, executions, events, operations | `registry.py`, `executions.py`, `events.py`, `operations.py` |
| Module gateway (health of Modules 01–10) | `aurum/orchestration/gateway.py` |
| HMAC signing, secret status | `aurum/orchestration/security.py` |
| Worker loop | `aurum/orchestration/worker.py` — `python -m aurum.cli orchestration worker` |
| REST API | `/api/v1/orchestration` — `aurum/api/routes/orchestration.py` |
| Dashboard | aurum-frontend → *Others → Workflow Orchestration* (8 tabs) |
| Harmless test workflow | `n8n/workflows/aurum_core_test_echo.json` |
| Docker | `docker-compose.orchestration.yml` (worker) |

## What is deliberately NOT built

**Module 09 (trade execution) is not built**, and nothing in AURUM sends an order. WF08-06 and WF08-07 are built up to
that boundary: WF08-06 verifies and records the execution request Module 07 creates and reports that it is waiting for
Module 09 (see [MODULE_08_WF08_06.md](MODULE_08_WF08_06.md), *What is left for Module 09*); WF08-07 watches the
positions Module 07 holds, which come from the read-only account mirror until Module 09 reports them; WF08-09
reports Module 09 as not deployed. All ten Module 08 workflows, WF08-01 to WF08-10, are built (see the table below).

## Built workflows

| Workflow | n8n file | Install | Guide |
| --- | --- | --- | --- |
| WF08-01 Market Monitoring Scheduler | `n8n/workflows/aurum_wf08_01_market_monitoring.json` (Docker), `aurum_wf08_01_market_monitoring.cloud.json` (n8n Cloud) | `python -m aurum.cli orchestration setup-wf08-01 --n8n-workflow-id <id>` | [MODULE_08_WF08_01.md](MODULE_08_WF08_01.md) |
| WF08-02 Market Analysis Orchestrator | `n8n/workflows/aurum_wf08_02_market_analysis.json` | `python -m aurum.cli orchestration setup-wf08-02 --n8n-workflow-id <id>` | [MODULE_08_WF08_02.md](MODULE_08_WF08_02.md) |
| WF08-03 Trade Opportunity Processor | `n8n/workflows/aurum_wf08_03_trade_opportunity.json` | `python -m aurum.cli orchestration setup-wf08-03 --n8n-workflow-id <id>` | [MODULE_08_WF08_03.md](MODULE_08_WF08_03.md) |
| WF08-04 Human Approval Notification | `n8n/workflows/aurum_wf08_04_approval_notification.json` | `python -m aurum.cli orchestration setup-wf08-04 --n8n-workflow-id <id>` | [MODULE_08_WF08_04.md](MODULE_08_WF08_04.md) |
| WF08-05 Approval Response Handler | `n8n/workflows/aurum_wf08_05_approval_response.json` | `python -m aurum.cli orchestration setup-wf08-05 --n8n-workflow-id <id>` | [MODULE_08_WF08_05.md](MODULE_08_WF08_05.md) |
| WF08-06 Approved Trade Handoff | `n8n/workflows/aurum_wf08_06_trade_handoff.json` | `python -m aurum.cli orchestration setup-wf08-06 --n8n-workflow-id <id>` | [MODULE_08_WF08_06.md](MODULE_08_WF08_06.md) |
| WF08-07 Trade Monitoring Orchestrator | `n8n/workflows/aurum_wf08_07_trade_monitoring.json` | `python -m aurum.cli orchestration setup-wf08-07 --n8n-workflow-id <id>` | [MODULE_08_WF08_07.md](MODULE_08_WF08_07.md) |
| WF08-08 Error Handling and Recovery | `n8n/workflows/aurum_wf08_08_error_recovery.json` | `python -m aurum.cli orchestration setup-wf08-08 --n8n-workflow-id <id>` | [MODULE_08_WF08_08.md](MODULE_08_WF08_08.md) |
| WF08-09 System Health Watchdog | `n8n/workflows/aurum_wf08_09_health_watchdog.json` | `python -m aurum.cli orchestration setup-wf08-09 --n8n-workflow-id <id>` | [MODULE_08_WF08_09.md](MODULE_08_WF08_09.md) |
| WF08-10 News and COT Update Scheduler | `n8n/workflows/aurum_wf08_10_news_cot_updates.json` | `python -m aurum.cli orchestration setup-wf08-10 --n8n-workflow-id <id>` | [MODULE_08_WF08_10.md](MODULE_08_WF08_10.md) |

## Quick start

```bash
python -m aurum.cli init-db                       # adds the orch_* tables (existing tables untouched)
python -m aurum.cli orchestration status          # seeds config v1, integrations, test + reserved workflows
python -m aurum.cli orchestration health          # real checks of n8n, DB, event store, Modules 01-10
python -m aurum.cli orchestration worker          # or: docker compose -f docker-compose.orchestration.yml up -d --build
```

Then verify the n8n connection end to end with the harmless test workflow before installing WF08-01: import
`n8n/workflows/aurum_core_test_echo.json` (local n8n in Docker: [N8N_SETUP.md](N8N_SETUP.md)), activate it, and run
`python -m aurum.cli orchestration test-run`. For n8n Cloud and the fully hosted set-up (Vercel + Supabase + n8n Cloud)
see the *Install* section of [MODULE_08_WF08_01.md](MODULE_08_WF08_01.md).

## Documents

* [Architecture](MODULE_08_ARCHITECTURE.md): principles, components and the data flow of an event-driven run.
* One guide per workflow, [WF08-01](MODULE_08_WF08_01.md) to [WF08-10](MODULE_08_WF08_10.md) (table above): how it
  runs, its configuration section, install steps, tests and what was verified on the hosted demo.
* [n8n in Docker](N8N_SETUP.md): running n8n locally and re-importing the workflow files.

Reference material that lives in the code rather than in separate documents:

| Topic | Where |
| --- | --- |
| Configuration (every section, with comments) | `config/orchestration.yaml`; validated by `aurum/orchestration/config_model.py`; versioned in the database and edited from the dashboard |
| Secrets and environment variables | `.env.example` (Module 08 block); values are never shown, only their fingerprints |
| REST API | `/api/v1/orchestration/...` in `aurum/api/routes/orchestration.py`; interactive reference at `/docs` on the API |
| Event catalogue | `aurum/orchestration/catalogue.py` |
| Execution, workflow and version states | `aurum/orchestration/states.py` |
| Database tables | `sql/migrations/0010_orchestration.sql`, `aurum/orchestration/db_models.py` |
| Webhook security (tokens, HMAC signatures) | `aurum/orchestration/security.py` |
| Permissions | `aurum/auth/roles.py` (`orchestration:*`) |
| Tests | `tests/orchestration/` (one file per workflow, plus `test_workflow_files.py` for the n8n files) |
