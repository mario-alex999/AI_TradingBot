# Module 06 database schema

- Migration: `sql/migrations/0008_risk_engine.sql`, applied by `python -m aurum.cli init-db` and recorded in
  `schema_migrations`. It is idempotent: `CREATE … IF NOT EXISTS`, with no DROP or DELETE.
- SQLite (tests, quick start) builds the same tables from the ORM models in `aurum/risk/db_models.py`. A test checks
  that every model column appears in the migration.
- Money and volumes are `NUMERIC(24, 8)`. They are read back as Decimal, never float.
- Times are `TIMESTAMPTZ`, in UTC.

## Tables

| Table | Holds | Keys and guarantees |
|---|---|---|
| `risk_config_versions` | Every configuration version: full config JSON, fingerprint, author, reason, loosened fields | `version` unique; **no UPDATE or DELETE** (trigger) |
| `risk_assessments` | One row per assessment: proposal, decision, state, volumes, risk, R:R, reserved risk, the four section statuses, config and engine version, `input_snapshot`, `input_hash`, full `result` JSON, expiry | `assessment_id` unique; `(idempotency_key, attempt)` unique; state is `active`, `consumed` or `cancelled` (CHECK); indexes on time, symbol, live reservations and trade idea |
| `risk_rule_evaluations` | Every rule result for every assessment: outcome, severity, reason code, message, evidence, thresholds | Index on `(rule_id, outcome)`; **no UPDATE** (trigger) |
| `risk_account_snapshots` | The account view each assessment used: balance, equity, drawdown, period losses, margin, streak | Index on time |
| `risk_portfolio_snapshots` | The exposure each assessment or portfolio refresh saw, including reservations | Index on time |
| `risk_correlation_snapshots` | Pair coefficients with samples, window and reliability; reused while fresh | Index on pair and time |
| `risk_period_records` | Day, week and month anchors: start value and its source, uncertainty, last loss %, when the limit was reached | `(kind, label, basis)` unique, so an anchor never moves |
| `risk_locks` | Active and past locks: scope, reason, trigger, rule, expiry, release | `lock_id` unique; `dedupe_key` unique while active (set to NULL on release), so the same automatic lock is never duplicated |
| `risk_lock_history` | activated, expired and released actions with actor and reason | Index on lock |
| `risk_loss_streaks` | Streaks that triggered a lock, by trade-id fingerprint | `fingerprint` unique, so a released streak does not re-lock |
| `risk_audit_log` | Every state change: actor, action, entity, correlation id, details | Hash chain (`prev_hash`, `hash` = SHA-256 of the previous hash plus the canonical row); **append-only** (trigger) |
| `risk_events` | Outbox: event type, schema version, idempotency key, entity, payload, delivery status, attempts, next attempt, last error | `event_id` and `idempotency_key` unique; status is `pending`, `delivered` or `dead` |
| `risk_inbox` | Received events with processing status and result | `event_id` unique; status is `processed`, `ignored`, `failed` or `dead` |

## Integrity guarantees
- **Atomic assessment.** The assessment, its rule rows, snapshots, period records, outbox events and audit row are
  written in one transaction. Either all exist or none do (tested).
- **Append-only history.** In PostgreSQL the trigger function `risk_reject_change()` raises on:
  - UPDATE or DELETE of `risk_audit_log`;
  - UPDATE of `risk_rule_evaluations`;
  - UPDATE or DELETE of `risk_config_versions`.
- **Tamper evidence.** `python -m aurum.cli risk audit-verify` (and `/risk/health`) recomputes the hash chain and names
  the first row that does not match. A test edits a row directly and confirms the change is detected.
- **Locks persist.** Restarting the API or changing the configuration leaves `risk_locks` untouched (tested).

## Reading the data
```sql
-- Why was a trade blocked?
SELECT rule_id, severity, reason_code, message
FROM risk_rule_evaluations WHERE assessment_id = :id AND outcome IN ('fail','error');

-- Decisions per day
SELECT date_trunc('day', created_at) AS day, decision, count(*) FROM risk_assessments GROUP BY 1, 2 ORDER BY 1;

-- Active reservations
SELECT assessment_id, symbol, reserved_risk, expires_at FROM risk_assessments
WHERE state = 'active' AND decision IN ('PASS','REVIEW') AND expires_at > now();
```
