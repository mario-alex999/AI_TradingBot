# Module 06 architecture

## Components

```
   API (/api/v1/risk)     CLI (aurum.cli risk)     inbox events (n8n, Module 05)
            \                     |                      /
             +------------- RiskService ----------------+     validation, config changes, locks, events
                                  |
                              RiskEngine                      assess(), position_size(), account/portfolio views
                 +----------------+----------------+
                 |                |                |
           RiskData (read)   rules.evaluate     RiskRepository (write)
      DatabaseRiskData or    40 pure rules       config versions, assessments, rule rows,
      MemoryRiskData (sim)   + decide()          locks, periods, audit chain, outbox/inbox
                 |
   Modules 01–05 tables: account_snapshots, account_deals, trade_records, instrument_specs,
   market_ticks/candles, data_quality_issues, Module 04 analyze(), Module 05 trade candidates
```

- **`RiskData`** is a read-only protocol. `DatabaseRiskData` reads the existing tables. `MemoryRiskData` (simulation and
  tests) holds the same data in memory, so the real engine, rules and repository run unchanged against simulated inputs.
- **Rules are pure functions** of an `AssessmentContext`. A rule that raises an exception is recorded as an `error`
  result, never as a pass.
- **The repository is the only writer.** All writes for one assessment happen in one transaction.

## Assessment flow
1. **Normalise** the proposal. `LONG`/`SHORT` become `BUY`/`SELL`; prices are parsed exactly as Decimal; the symbol must
   be enabled. A Module 05 `trade_idea_id` is expanded into a proposal: the entry is the middle of the entry zone,
   snapped to the tick.
2. **Idempotency.** The key is the client's `Idempotency-Key` header, or `auto:` plus a hash of the proposal and config
   version.
   - The same key with the same proposal returns the stored result with `replayed: true`.
   - The same key with a different proposal is a 409 `IDEMPOTENCY_CONFLICT`.
3. **Slow reads outside the lock.** Module 04 market intelligence (about 3 s), instrument specification, quote, spread
   statistics and price series.
4. **Inside the serialised section:**
   - expire due locks, then read active locks, live reservations, positions and the account;
   - compute or reuse correlations, build the context, run all 40 rules;
   - decide, auto-lock if a locking rule failed, then re-decide (the lock adds R060);
   - write the assessment, rule rows, snapshots, period records, outbox events and audit row in **one transaction**.
5. **Return** the full result, including the `module_07` contract block and the disclaimer.

## Decision
```
any HARD_BLOCK rule failed          -> BLOCK
else any rule returned error        -> ERROR
else any WARNING rule failed        -> REVIEW
else                                -> PASS
INFORMATIONAL rules never change the decision (R070 COT context, R071 AI confidence).
```
Unknown or unavailable data follows `data_unavailable_policy` and the per-filter `unknown_policy`. These can be
`review`, `block` or `error`, never `pass`.

## Concurrency
Two assessments must not both use the last unit of a limit. The engine therefore serialises the decision step.
- **PostgreSQL:** `pg_advisory_xact_lock(60600606)` inside the write transaction, so the lock covers every process that
  uses the database.
- **Every backend:** a process-level `RLock`.
- **Reservations:** while a PASS or REVIEW assessment is `active` and unexpired (15 minutes by default), its estimated
  loss counts as open risk. Consuming or cancelling it, or letting it expire, frees the reservation.

A test fires concurrent assessments against a budget that fits three trades; at most three PASS.

## Locks
| Trigger | Lock | Ends |
|---|---|---|
| R030 / R031 / R032 period loss | global | At the start of the next day, week or month (`reset_policy: next_period`), or by manual release |
| R033 drawdown | global | Manual release only |
| R035 consecutive losses | global | Manual release only. The streak is then acknowledged by its trade-id fingerprint, so the same streak does not re-lock |
| Manual | global, account, instrument or strategy | Manual release only, or an optional expiry |

Locks are rows in `risk_locks`, not memory, so they survive restarts. A configuration change never releases a lock.
Releasing a loss or drawdown lock records a person's decision. It does **not** raise the limit: while the limit is still
reached, the rule keeps blocking.

## Trading calendar
Trading days run from 17:00 to 17:00 in America/New_York, following DST, so a day can be 23 or 25 hours long. Weeks
are ISO weeks of trading days, and months are calendar months of trading days.

The equity at the start of each period is the snapshot closest to the boundary, within
`period_start_tolerance_minutes`. If there is none, the earliest snapshot in the period is used and flagged
`start_uncertain` (warning R034). The anchor is stored in `risk_period_records`, so it never changes after the fact.

## Events
- **Outbox (`risk_events`).** Events are written in the same transaction as the change they describe.
  - Consumers pull with `GET /risk/events?after=<sequence>`, or `risk events dispatch` POSTs them to a webhook.
  - Failed deliveries are retried with exponential backoff (15 s doubling, capped at 1 h).
  - An event is dead-lettered after 8 attempts.
- **Inbox (`risk_inbox`).** Received events are deduplicated by `event_id`.
  - `trade_idea.created` triggers an assessment.
  - Account, position and market updates refresh the account status.
  - Unknown types are stored as `ignored`.
  - A failing event is dead-lettered after 5 attempts.

## Determinism
The same inputs, config version and clock produce the same decision and size. A test checks this. Every assessment
stores `input_snapshot`, `input_hash`, `config_version` and `engine_version`, so a decision can be explained and replayed
later.
