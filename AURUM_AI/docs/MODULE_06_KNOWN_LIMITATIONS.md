# Module 06 known limitations

## Data and setup
- **Account data must be fresh.** The DEMO account (500.00 USD) was synced on 2026-11-08. When the latest snapshot is
  older than `account_max_age_minutes`, assessments return ERROR (R001) and the dashboard shows "Data unavailable".
  Keep `python -m aurum.cli sync-account --loop` running. This is deliberate: the engine never assumes a balance.
- **November's start value is approximate.** The first snapshot is from 8 November, so the month's start uses it and is
  flagged uncertain (R034 warning, so REVIEW). Deposits made before that snapshot are already inside it and are not
  counted again.
- **Weekend quotes are stale.** Outside market hours, R050 reports the market as closed. If Module 04 does not know the
  market is closed, R051 blocks on quote age.
- **Live sizing differs from the simulation.** For example, 0.39 lots live against 0.46 simulated for the same EURUSD
  trade, because the live spread from the last stored quote was wider. The engine uses whatever spread is stored.
- **The `SAMPLE_SPECS` in the simulation are illustrative.** They follow the demo server's stored specifications, but are
  not broker facts. Live assessments always read `instrument_specs`.

## Calculation approximations
- **Margin is an estimate:** notional ÷ leverage for forex and CFD-leverage modes, full notional for futures and CFD
  index modes. Broker margin tiers, hedged margin and margin currency are not modelled. When no estimate is possible,
  R037 returns a warning, not a pass.
- **The spread and slippage assumptions are static.** The cost uses the current (or median) spread and a fixed slippage
  per symbol. Gaps and news spikes can exceed both. The disclaimer says so.
- **The drawdown peak is not adjusted for deposits.** A large deposit raises the peak, and a later withdrawal can then
  look like drawdown. Period losses *are* adjusted for deposits and withdrawals.
- **Correlation is linear (Pearson) on H1 returns** over at most 500 bars. Regime changes and non-linear dependence are
  not captured. Unreliable estimates are treated as correlated, which is cautious and can block more often.
- **The currency exposure view is notional** and informational. No limit is enforced on it.

## Scope
- **No users or roles on the backend.** Admin actions use one shared key, and the actor name is self-declared. The
  frontend `risk:manage` permission only hides controls. Real per-user authorisation would be a later module.
- **Rules are a code catalogue plus configuration overrides,** not a database table that can be edited at runtime. New
  rules need a code change and tests.
- **No automatic position management.** Module 06 never closes or reduces positions when a limit is reached. It blocks
  new trades only.
- **Locks have four scopes:** global, account, instrument and strategy. There is no asset-class lock; use instrument
  locks.
- **Webhook delivery is pull-triggered.** Events are delivered when `risk events dispatch` runs (manually, on a schedule,
  or from n8n). No background dispatcher runs inside the API.
- **The Module 04 analysis is slow** (about 3 s per assessment), so assessments take seconds, not milliseconds. It runs
  outside the serialised section, so it does not block other assessments.

## Testing gaps
- The Postgres-specific migration test (advisory lock, append-only triggers) is opt-in through `AURUM_TEST_DATABASE_URL`
  and was skipped in the recorded run. Migration 0008 was applied to the live PostgreSQL 16 database by `init-db` on
  2026-11-08.
- Scenario tests use simulated data. The integration tests read the real Module 01, account and Module 05 tables, but
  with seeded rows; Module 04 is a stand-in there. No assessment has yet been checked against a live account with real
  positions.
