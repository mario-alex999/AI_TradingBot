# Module 05 implementation report

**Status: built and tested.** The older backend suites ran after this work, with one out-of-date expectation corrected.
See "Test results".

## What was built

An analysis and decision engine that reads Modules 01-04 and publishes explained candidates for human review. It has no
way to place a trade. See [MODULE_05_AI_ENGINE.md](MODULE_05_AI_ENGINE.md).

## Files

- New backend: `aurum/decision/` (config, adapters, context, regime, gates, scoring, recommendation, lifecycle, outcomes,
  repository, service, frontend_views, cli, `candidates/`, `ai_reviewer/`, `backtest/`), `aurum/api/routes/decision.py`,
  `aurum/api/routes/ai_engine.py`, `config/ai_engine.yaml`, `sql/migrations/0007_decision_engine.sql`, `tests/decision/`.
- Changed backend, registration only: `aurum/config.py`, `aurum/cli.py`, `aurum/api/app.py`, `aurum/api/system_queries.py`,
  `aurum/account/queries.py`, `aurum/database/connection.py`, `.env.example`.
- New frontend: `pages/AIEnginePage.tsx`, `components/signals/SignalDrawer.tsx`, `components/ai/EvaluationDetails.tsx`,
  `services/decisionService.ts`, `hooks/useDecision.ts`, `stores/adminKeyStore.ts`, `types/decision.ts`, `utils/labels.ts`,
  `test/decision.test.tsx`.
- Changed frontend: `AISignalsPage`, `SignalCard`, `BacktestingPage`, `SettingsPage`, `navigation.ts`, `App.tsx`.
- Docs: the 15 module docs, `README.md`, `HANDOFF.md`.

Reference tables for endpoints, CLI, config, strategies and schema are in [API_REFERENCE.md](API_REFERENCE.md),
[CONFIGURATION.md](CONFIGURATION.md), [STRATEGIES.md](STRATEGIES.md) and [DATABASE_SCHEMA.md](DATABASE_SCHEMA.md).

## Test results

| Check | Result |
|---|---|
| `tests/decision` | 265 passed, 2 skipped. Mocked model clients and SQLite |
| Skipped | Postgres migration test and live AI test, both opt-in |
| Postgres migration test, run separately | Passed on PostgreSQL 16.15: applied twice in a throwaway database, since dropped |
| Backtest speed | 25 EURUSD H1 steps in 101 s, or 4.0 s per step, on 2026-10-27. Slower runs (a two-day replay unfinished at about 10 minutes, a four-day run at 3,272 s) were competing with other work or short of memory |
| Existing backend tests, after this work | 446 passed, 1 failed, 1 skipped. See below |
| `tests/test_account_api.py`, after correcting that test | Passed on its own. The whole suite was not rerun |
| Baseline before Module 05 | 447 passed, 1 skipped |
| Frontend `vitest` | 90 passed, 1 failed in the full run. The failure, a timing wait in `layout.test.tsx`, passed 7 of 7 when rerun alone |
| Frontend typecheck and lint | Passed |
| Frontend `npm run build` | Passed |

### Failures found and fixed

| Problem | Kind | Fix |
|---|---|---|
| `KeyError: 'frozen'` after an AI review | Product bug, caught by tests | The flag is read before the review |
| COT stance ignored when `lean` is empty, so extreme positioning scored as "not directional" | Product bug, caught on real data | `cot_lean()` is used everywhere |
| Regime read POTENTIAL_REVERSAL on all four instruments | Product bug, caught on real data | Needs a recent confirmed CHoCH |
| Data quality gate failing on two old D1 gaps | Product bug, caught on real data | `quality_lookback_candles` |
| `test_agents_status_and_notifications` expected the decision agent to be `disabled` | Out-of-date test: the agent was a placeholder until this module | Expects `idle` |
| About 20 test expectations | Fixture now matches more templates, or wrong arithmetic | Corrected |

## Security scan

The source scan over `aurum/decision/` passes, and no order or execution route exists in the real app. Admin routes
returned 401 without a key. `ReadOnlyMT5` is unchanged. Staged files were scanned for secrets before the commit.

## Look-ahead

Passing: the module's test that appending later data leaves an earlier decision unchanged. Modules 02-04 cover the rest
in their own tests, listed in [TESTING.md](TESTING.md), and all of those passed in the regression run.

## Integration with Modules 01-04 on real data

Run on the real PostgreSQL database with the AI reviewer off, on 2026-11-04 at about 22:00 UTC.

| | EURUSD | GBPUSD | XAUUSD | US30 |
|---|---|---|---|---|
| Module 03 timeframes ok | 5 of 5 | 5 of 5 | 5 of 5 | 5 of 5 |
| Candles used D1 / H4 / H1 / M15 / M5 | 2000 / 514 / 2000 / 2000 / 2000 | same | same | 745 / 514 / 2000 / 2000 / 2000 |
| Module 02 COT | bearish | bearish | neutral, leaning bearish | neutral, leaning bullish |
| Module 04 volatility | VERY_HIGH | HIGH | NORMAL | VERY_HIGH |
| Module 04 event risk | NORMAL | EVENT_APPROACHING | EVENT_APPROACHING | NORMAL |
| Market open | yes | yes | no | no |
| Regime | POTENTIAL_REVERSAL | BULLISH_TREND | BULLISH_TREND | BULLISH_TREND |
| Bias (agreement) | bearish (0.73) | bullish (0.80) | bearish (0.60) | bullish (1.00) |
| Decision | NO_TRADE | WAIT | NO_TRADE | NO_TRADE |
| Why | Volatility gate | Best candidate is grade C | Market closed | Market closed |

Missing on all four: the account snapshot and finished Learning Agent trials. Both are reported, not defaulted.

XAUUSD shows a bullish H1 regime with a bearish weighted bias. That is a real disagreement between timeframes, and it is
listed under conflicts.

The master prompt expected US30 to have no COT mapping. Module 02 maps it to "DJIA x $5", so US30 uses that real output.

## Limitations and outstanding tasks

See [LIMITATIONS.md](LIMITATIONS.md). Outstanding:

1. Make one real AI call once a key and budget are set.
2. Time a longer replay. Backtest speed below comes from one day of EURUSD.

## Commands (PowerShell, from `AURUM_AI`, venv active)

```powershell
pip install -r requirements.txt
python -m aurum.cli init-db
python -m aurum.cli ai check
# configure the AI provider: add AI_REVIEW_ENABLED, ANTHROPIC_API_KEY, AI_REVIEW_MONTHLY_BUDGET_USD to .env, then
python -m aurum.cli ai providers
foreach ($s in 'EURUSD','GBPUSD','XAUUSD','US30') { python -m aurum.cli ai analyze --symbol $s --no-ai }
python -m aurum.cli ai analyze --symbol EURUSD --timeframes H1,M15 --no-ai
python -m pytest tests/decision -q
python -m pytest tests -q
python -m aurum.api
cd ..\aurum-frontend; npm run dev
```
