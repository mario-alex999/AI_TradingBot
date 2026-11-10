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

WF08-01 (market monitoring), WF08-02 (market analysis), WF08-03 (risk/approval) and WF08-04 (execution handoff) exist only
as **reserved registrations** (`placeholder = true`). They cannot be activated or triggered until they are built,
versioned, validated and activated by an administrator.

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
