# Module 06 test report

Run on 2026-11-08: Windows 11, Python 3.11, Node 22. No broker, MT5 terminal or live account was used.

- **Mocked tests** (unit, scenario, API, safety, CLI) feed the real engine, rules and repository through
  `MemoryRiskData`, with illustrative specifications and a temporary SQLite database built from the real models.
- **Integration tests** (`test_database_provider.py`) seed the **real** Module 01, account-mirror and Module 05 tables
  and read them through `DatabaseRiskData`, the code path used in production. Only Module 04 is a stand-in there,
  because its analysis needs long candle and calendar history; that is stated in the test.

## Results

| Suite | Tests | Result |
|---|---|---|
| `tests/risk/test_calculations.py` | 33 | Passed |
| `tests/risk/test_account_portfolio.py` | 28 | Passed |
| `tests/risk/test_scenarios.py` | 45 | Passed |
| `tests/risk/test_api.py` | 20 | Passed |
| `tests/risk/test_safety.py` | 17 | 16 passed, 1 skipped (Postgres-only, opt-in) |
| `tests/risk/test_cli.py` | 2 | Passed |
| `tests/risk/test_database_provider.py` (integration, real tables) | 8 | Passed |
| **Module 06 total** | **153** | **152 passed, 0 failed, 1 skipped** |
| Existing backend suites (`tests/`, excluding `tests/risk`) | 715 | **712 passed, 0 failed, 3 skipped** (opt-in: Postgres, live AI, live CFTC), in 21 min |
| The existing CLI/table suites, rerun after the `aurum/ui.py` fix | 117 | 115 passed, 2 skipped |
| Frontend `vitest` (all 15 files, including `risk.test.tsx` and the dark-theme `theme.test.tsx`) | 105 | Passed |
| Frontend typecheck, lint (`--max-warnings=0`), production build | — | Passed |

**Timing.** A clean run of `tests/risk` took 61 s, with a schema template database copied per test instead of rebuilt.
One later run took 357 s because it overlapped the full backend suite while the PC had about 1 GB of free memory.

## What is covered

**Calculations (33).**
- Exact Decimal parsing; floor to step.
- Hand-checked sizes for EURUSD, GBPUSD, XAUUSD (contract 100, tick 0.001) and US30 (calc mode 4, minimum lot 0.05).
- Spread, slippage and commission included; minimum lot over budget rejected; maximum volume cap; risk % capped.
- GBP-account conversion; stale conversion falls back to a fresh broker tick value; unavailable conversion is an error.
- Inconsistent tick value, calc mode 3 and incomplete specs are rejected.
- **A 2,000-case property test shows the volume is never above the raw size and the loss never above the budget.**

**Account and portfolio (28).**
- New York 17:00 trading day across DST, including the 25-hour day.
- Week and month boundaries; naive times rejected; missing, stale and non-DEMO accounts.
- Drawdown and daily loss by hand; balance against equity basis.
- Deposits and withdrawals are not profit or loss; a profitable closed trade does not hide a floating loss.
- Uncertain period start; streaks with breakeven and duplicate broker records.
- Position risk and unbounded positions; groups; currency exposure.
- Pearson against numpy; insufficient, stale and missing data; common timestamps only; direction logic.

**Scenarios (45).** These are the 30 scenarios required by the specification, plus 15 more:
- normal approval; missing stop; stop on the wrong side; take-profit on the wrong side; off-tick prices;
- stop inside the broker level; poor R:R after costs; risk above maximum; minimum lot over budget;
- daily, weekly and monthly loss limits; drawdown; consecutive losses (locks and acknowledgement);
- trades per day; margin; open positions; total, per-symbol, asset-class and correlated risk;
- unreliable correlation; same direction; duplicate idea; market closed; stale quote; wide spread;
- high-impact event; extreme volatility; session restriction;
- stale and missing account; Module 04 unavailable; stale spec; manual and scoped locks;
- idempotent replay and conflict; reservations and expiry; consume and cancel; config-change invalidation;
- restart persistence; outbox dead-letter; **concurrency (at most 3 of many concurrent requests PASS when 3 fit)**;
- determinism.

**API (20).**
- 401 without a key; 503 when no key is configured.
- camelCase responses; correlation id; actor recorded.
- `Idempotency-Key` replay and 409 conflict; five invalid payloads give 422 with a structured error.
- Position size stores nothing; it explains a missing account currency.
- History pagination and filters; rule counts.
- Config versioning, stale version, weakening (the message names the loosened field), unknown key, per-symbol tables;
  profile switch.
- Locks: auth, validation, create, release, double release returns 409.
- validate, consume and cancel; read endpoints; outbox fields and `after`; inbound deduplication; rate limit 429; CORS
  allows PUT.

**Safety and database (17).** See [MODULE_06_SECURITY_AND_SAFETY.md](MODULE_06_SECURITY_AND_SAFETY.md):
- no trading calls or MT5 imports (source and AST scan); no trading routes;
- no PASS without a stop under any option; AI confidence cannot override; stale account never passes;
- disabling rules cannot turn ERROR into PASS; safety rules cannot be disabled; overrides only tighten;
- reservations expire; volume never rounded up;
- Decimal round-trip from the database; audit tampering detected; atomic writes;
- config history is append-only; every table exists; the migration matches the models;
- the opt-in Postgres migration test (idempotent apply and the append-only trigger).

**CLI (2).** Tables render with positional headers; `risk simulate` runs and writes its report.

**Integration with the real tables (8).**
- Module 01: specification parsing (calc mode, contract, step) and a missing spec; tick quote, falling back to an M5
  candle when there is no tick; median spread; H1 closes that skip incomplete or invalid bars.
- Account mirror: the DEMO snapshot; an open position (side and stop); a closed loss; cash flows; trades opened.
- Data quality: candle revisions are excluded.
- Module 05: a candidate read from `trade_candidates`.
- End to end through the engine: the EURUSD example sizes to **0.46 lots** from the tables. It returns REVIEW because of
  the seeded data-quality warning. The open XAUUSD position counts as 100 USD of open risk. A Module 05 idea (confidence
  0.9) is assessed, and its confidence is recorded only as informational R071.

**Frontend (`src/test/risk.test.tsx`, 10).**
- Account and limit meters come from the backend; the "data unavailable" reason is shown.
- The engine calculator posts the camelCase body and shows the backend size; no request is sent without a stop.
- A full assessment needs the admin key and shows the block reason.
- Kill switch and lock release each need a reason.
- Loosening a limit asks for a second confirmation and resends with `acknowledgeWeakening`.
- Settings are read-only without the key; history filters are sent to the backend.
- The original calculator's tests are unchanged and pass.

## Simulation run (`python -m aurum.cli risk simulate`)
This is simulated data: assumed inputs in a throw-away database. It is not account performance.

| Scenario | Assessments | PASS | REVIEW | BLOCK | ERROR | Max drawdown % | Locks |
|---|---|---|---|---|---|---|---|
| normal_day | 3 | 3 | 0 | 0 | 0 | 0.99 | 0 |
| losing_streak | 5 | 3 | 0 | 2 | 0 | 2.95 | 1 |
| daily_loss_breach | 3 | 1 | 1 | 1 | 0 | 3.20 | 1 |
| correlated_exposure | 3 | 2 | 0 | 1 | 0 | 0.00 | 0 |
| exposure_and_positions | 5 | 3 | 0 | 2 | 0 | 0.00 | 0 |

In each scenario the engine behaved as designed:
- **losing_streak:** the third loss triggered R035 and a manual-release lock.
- **daily_loss_breach:** R030 blocked and locked until the next trading day. The next day returned REVIEW because of the
  uncertain period-start warning (R034).
- **correlated_exposure:** the correlated BUY was blocked (R044); the SELL was allowed.
- **exposure_and_positions:** total open risk was blocked (R041).

## Defects found and fixed while testing
- Releasing an already-released lock returned 200 instead of 409. The check compared timestamps, which fail when two
  releases share a clock tick. It now checks the lock status.
- `/position-size` with an equity but no known account currency returned `valid: false` with no reason. It now reports
  `ACCOUNT_CURRENCY_UNKNOWN`, and accepts `accountCurrency`/`currency`.
- History rows returned money as strings. They now return numbers, like every other endpoint.
- The weakening error did not name the loosened limits in its message. It now does, so the dashboard dialog shows them.
- On the live DEMO account, the month showed a 100% loss and BLOCKED. A 500 USD deposit on 2 November was made before
  the first snapshot (8 November), so it was already inside the start value but was also added as a deposit in the
  period. Only cash moved after the start snapshot now counts. Two regression tests were added.
- `risk simulate` (and any table built with positional headers) crashed in the shared `aurum/ui.py` table helper. The
  helper is fixed, and a test was added.

## Not run
- The Postgres-only test needs `AURUM_TEST_DATABASE_URL`, so it was skipped. Migration 0008 was applied to the live
  PostgreSQL 16 + TimescaleDB database by `init-db`, and `/risk/health` reports its tables and the audit chain as healthy.
- No live end-to-end assessment was run, because the account mirror is empty. A live `/position-size` with an entered
  equity returned 0.39 lots for the EURUSD example.
