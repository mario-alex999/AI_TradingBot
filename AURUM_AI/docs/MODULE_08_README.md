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
reports Module 09 as not deployed. WF08-10 (News and COT Update Scheduler) is not built. WF08-01 to WF08-09 are built
(see the table below).

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

## Quick start

```bash
python -m aurum.cli init-db                       # adds the orch_* tables (existing tables untouched)
python -m aurum.cli orchestration status          # seeds config v1, integrations, test + reserved workflows
python -m aurum.cli orchestration health          # real checks of n8n, DB, event store, Modules 01-10
python -m aurum.cli orchestration worker          # or: docker compose -f docker-compose.orchestration.yml up -d --build
```

Then follow [MODULE_08_N8N_INTEGRATION.md](MODULE_08_N8N_INTEGRATION.md) to import and verify the test workflow
end to end. Do that before building WF08-01.

## Documents

1. [Architecture](MODULE_08_ARCHITECTURE.md)
2. [Architecture compatibility report](MODULE_08_COMPATIBILITY_REPORT.md)
3. [Database schema](MODULE_08_DATABASE_SCHEMA.md)
4. [Event catalogue](MODULE_08_EVENT_CATALOGUE.md)
5. [Execution lifecycle](MODULE_08_EXECUTION_LIFECYCLE.md)
6. [n8n integration](MODULE_08_N8N_INTEGRATION.md)
7. [Webhook security](MODULE_08_WEBHOOK_SECURITY.md)
8. [Idempotency, retry and recovery](MODULE_08_RETRY_AND_RECOVERY.md)
9. [Scheduling and locks](MODULE_08_SCHEDULING_AND_LOCKS.md)
10. [Environments](MODULE_08_ENVIRONMENTS.md)
11. [API reference](MODULE_08_API_REFERENCE.md)
12. [RBAC](MODULE_08_RBAC.md)
13. [Configuration and secrets](MODULE_08_CONFIGURATION.md)
14. [Observability](MODULE_08_OBSERVABILITY.md)
15. [Operations runbook](MODULE_08_RUNBOOK.md)
16. [Security and safety](MODULE_08_SECURITY_AND_SAFETY.md)
17. [Test report](MODULE_08_TEST_REPORT.md)
18. [Known limitations](MODULE_08_KNOWN_LIMITATIONS.md)
19. [Delivery report](MODULE_08_DELIVERY_REPORT.md)
