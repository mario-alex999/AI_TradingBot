# Module 08 — Architecture

```
            people (web app, session + CSRF)          services / n8n (Bearer service token)
                          │                                        │
                          ▼                                        ▼
                ┌───────────────────── /api/v1/orchestration ───────────────────┐
                │ registry · executions · events · subscriptions · schedules     │
                │ dead letters · locks · integrations · config · audit · metrics │
                └──────────────┬──────────────────────────────┬─────────────────┘
                               │ OrchestrationService         │
       ┌───────────────────────┼──────────────┬───────────────┼────────────────────┐
       ▼                       ▼              ▼               ▼                    ▼
  PostgreSQL orch_*      WorkflowEngine   ModuleGateway   Bridge (read-only)    Worker loop
  (state, events,        (N8nEngine →     (health of      risk_events (M06),    bridge → deliver →
   outbox/inbox,          webhook +        Modules 01-10)  outbox_events (M07)   schedules → dispatch
   audit hash chain)      public API)                                            → time-outs → health
```

## Principles

* **One source of truth: PostgreSQL.** No new broker. Events, deliveries, executions, attempts, callbacks, locks and
  schedules are rows. Workers coordinate through conditional updates under a transaction-scoped advisory lock
  (`pg_advisory_xact_lock(80800808)`), unique constraints and fencing tokens.
* **n8n runs workflows; AURUM keeps the bookkeeping.** Workflows are built in the n8n editor and imported; Module 08
  registers them, triggers them through their webhook and learns the outcome through a callback (or, when the n8n API key
  is configured, by asking n8n). It does not re-implement n8n nodes, credentials, editor or execution engine.
* **Retries happen in AURUM, not in n8n.** The n8n "retry execution" API is not used, so idempotency stays in one place:
  every dispatch carries the AURUM `executionId` (also the `Idempotency-Key` header) and an attempt number.
* **Network calls never happen inside a database transaction.** Claim (transaction) → call n8n (no transaction) →
  record (transaction). A crash between the two leaves the execution `DISPATCHED`; it is never re-sent blindly.
* **Ownership is enforced.** Each catalogued event names its owning module(s). Risk, approval and execution events can
  only come from their owners and are never replayable from Module 08.

## Components

| Component | Responsibility |
| --- | --- |
| `OrchRepository` | Write/read sessions, advisory lock, environment pin, hash-chained audit, idempotency records, config versions |
| `RegistryMixin` | Workflow registration, semantic versions, validation against n8n, activation, rollback, state changes |
| `ExecutionMixin` | Trigger, dispatch with back-pressure, callback processing, time-out sweep and reconciliation, retry, cancel |
| `EventMixin` | Publish (API, n8n), routing to subscriptions, delivery (workflow trigger or signed webhook), bridge, replay |
| `OpsMixin` | Dead letters, leases, schedules, integrations and health, configuration, audit queries, overview, metrics, purge |
| `N8nEngine` | HTTP adapter for n8n 2.x: health, webhook dispatch, public API (workflow, activate, executions, stop) |
| `ControlledTestEngine` | Scripted stand-in for tests and local development; refused in `demo` and `production` |
| `ModuleGateway` | Health checks of the other AURUM modules with a per-module circuit breaker (5 failures → open 30 s) |
| `Worker` | One loop: bridge, deliver, schedules, dispatch, time-outs; health every `health.interval_seconds` |

## Data flow — an event-driven run

1. An event arrives (`POST /events/publish`, the n8n events webhook, or the bridge from Module 06/07 outboxes).
2. It is validated against the catalogue (type, version, owner, environment, payload, no credentials) and stored once
   (unique `event_id` and `idempotency_key`; inbox deduplicates by message id).
3. Each matching active subscription gets one delivery row (`unique(event, subscription, replay)`).
4. The worker delivers: a workflow subscription becomes a trigger with idempotency key `evt:{event}:{sub}:{replay}`;
   a webhook subscription becomes a signed POST.
5. The execution is queued, claimed, sent to n8n, and finished by the callback. Each step is a legal state transition,
   recorded in the execution timeline and emitted as an `orchestration.workflow.*` event.
