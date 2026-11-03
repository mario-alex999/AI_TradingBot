# Module 04 database

**Migration:** `sql/migrations/0004_market_intelligence.sql`.
- It is forward-only and additive: every statement is `CREATE TABLE IF NOT EXISTS` or `CREATE INDEX IF NOT EXISTS`.
- It contains no `DROP`, `TRUNCATE`, `DELETE` or `UPDATE`, and alters no Module 01–03 table. A test enforces this.
- Apply it with `python -m aurum.cli init-db`, which records it in `schema_migrations`.
- On SQLite (tests), the same tables are created from `aurum/market_intelligence/db_models.py`.
- Applied to the real PostgreSQL 16 + TimescaleDB database on 2026-11-03.

## Tables (18)
| Table | Natural key | Content |
|---|---|---|
| `market_intelligence_runs` | `run_uid` | one row per analysis or ingestion run: type, symbol, as_of, config fingerprint, methodology version, status, summary |
| `market_session_definitions` | (name, kind) | sessions and kill zones as configured when stored |
| `market_sessions` | (name, kind, start_utc) | session occurrences |
| `market_session_levels` | (symbol, level_type, period_start) | levels with source candle, timeframe, counts, state, `last_as_of`, first/last run |
| `market_session_statistics` | (symbol, session, window, as_of) | statistics snapshots |
| `market_session_liquidity_events` | (symbol, session, level_type, event_time) | liquidity events with rule, confirmation, Module 03 link, `last_as_of` |
| `economic_calendar_sources` | provider | provider health: status, last success/attempt/error, event count, window |
| `economic_calendar_events` | (provider, provider_event_id) | current version of each event; `values` JSON (actual/consensus/previous/revised_previous), raw payload, `actual_first_seen_at` |
| `economic_calendar_event_revisions` | id, FK → events | every observed version: `observed_at`, change type, changes, full snapshot, run |
| `economic_calendar_ingestion_logs` | id | per provider per run: received / inserted / updated / unchanged / invalid, error (secret-free) |
| `macro_indicators` | (series_id, period, vintage) | indicator values per vintage, with `known_from` and source URL |
| `central_bank_rates` | (bank, effective_date) | official policy-rate observations (rate, lower bound), `known_from`, source |
| `central_bank_decisions` | (bank, decision_date) | changed / held decisions with `known_from` and matched meeting |
| `market_news` | `item_uid` | headlines as published: source type, reliability, currencies |
| `market_volatility_metrics` | (symbol, timeframe, as_of) | volatility snapshots |
| `market_condition_snapshots` | (symbol, as_of) | condition tags and evidence |
| `market_intelligence_scores` | (symbol, as_of, score_type) | scores with components and methodology version |
| `market_intelligence_quality_issues` | fingerprint | deduplicated data-quality issues (component, severity, first/last seen, count) |

Foreign keys link runs to their outputs (`run_uid`) and revisions to events (`event_id`, cascade on event delete; nothing in Module 04 deletes events).

## Idempotency and history rules
- **Levels and liquidity events** are upserted on their natural keys. An upsert is applied only when `stored.last_as_of <= new.as_of`, so a historical re-run never overwrites a newer state with an older one.
- **Calendar ingestion** works as follows:
  - unchanged events update only `retrieved_at`;
  - changes write a revision row;
  - the original `scheduled_at` is kept as `original_scheduled_at`.
- **Rates:** the first stored observation for a (bank, date) pair wins. A conflicting later value becomes a quality issue.
- **Indicator vintages accumulate.** FRED vintages are labelled by `realtime_start`; non-vintage sources are labelled `r:<retrieval UTC>`.
- **Snapshots** (statistics, volatility, conditions, scores) are keyed by `as_of` and replaced on an identical re-run.

## Retention
Nothing is deleted automatically. To prune old snapshots, run a manual, reviewed SQL statement. Module 01–03 data is never touched.
