# AURUM AI — session handoff

Context for continuing this work in local Claude Code (`claude --teleport`) on the Windows PC
that runs MetaTrader 5. Written 2026-11-02 at the end of the cloud build session.

## What exists

| Folder | What it is | State |
|---|---|---|
| `AURUM_AI/` | Module 01: read-only MT5 market data engine (Python, CLI) | Built; 51 tests pass with mocked MT5; DB tests also pass on PostgreSQL 16 |
| `aurum-frontend/` | React/Vite dashboard (built in a separate session) | Builds clean; uses the backend API when `VITE_ENABLE_MOCK_DATA=false`, otherwise labelled DEMO DATA |
| `smartflow-bot/` | First prototype: SmartFlow confluence strategy (COT index and extremes, street/informed money, SMC/ICT order blocks, FVG, OTE, sessions, S/R), backtester, AI trade reviewer | Reference for later AURUM analysis modules; runs offline with `python main.py demo`; never validated on real data |

Module 01 commands (run from `AURUM_AI/` with the venv active): `check`, `init-db`, `specs`,
`collect [--symbol X] [--timeframe TF] [--full] [--loop]`, `export` — e.g. `python -m aurum.cli check`.
Full setup and troubleshooting: `AURUM_AI/README.md`.

## Where the Windows setup stands

Done on the PC (`C:\Users\Dell\AI_TradingBot\AURUM_AI`):
- Repo cloned, Python 3.11 venv created, PowerShell execution policy set to RemoteSigned, requirements installed.
- Claude Code installed.

Last `check` run failed on two points:
1. **MT5 `-6 Authorization failed`.** Plan: leave `MT5_LOGIN`, `MT5_PASSWORD`, `MT5_SERVER` empty in `.env`
   so the logged-in terminal account is used; confirm the terminal is logged in to a DEMO account;
   confirm `MT5_TERMINAL_PATH` points to that same terminal's `terminal64.exe`.
2. **Database connection timeout.** Docker Desktop is not installed. Plan: set
   `DATABASE_URL=sqlite:///data/aurum.db` in `.env`, run `init-db`, use PostgreSQL/TimescaleDB later.

Not yet verified against a real terminal: `MT5_SERVER_TIMEZONE` (broker clock), the broker symbol
names in `AURUM_AI/config/instruments.yaml`, and `trading_hours` for gap classification.

## Next steps, in order

1. Get `python -m aurum.cli check` to **ALL CHECKS PASSED** (fixes above).
2. `python -m aurum.cli init-db`, then `python -m aurum.cli collect`, then `python -m aurum.cli export`.
3. Review the first run's `missing_candles` / `abnormal_gap` issues; tune `MT5_SERVER_TIMEZONE` and
   `trading_hours` if weekends or rollover show up as unexpected gaps.
4. **Done (2026-11-03):** the HTTP API now serves Module 01 to the frontend: `/health`, `/market/instruments`,
   `/market/quotes`, `/market/candles`, `/market/specifications`, `/market/data-quality`,
   `/market/collection-logs`, `/market/export` and the live stream `/market/stream/quotes`. Run the live stack:
   `python -m aurum.cli collect --loop`, `python -m aurum.cli stream-ticks` and `python -m aurum.api` (three
   terminals), then in `aurum-frontend/` set `VITE_ENABLE_MOCK_DATA=false` in `.env.local` and `npm run dev`.
   Details: `AURUM_AI/docs/MARKET_API.md`. (`/market/collect` is intentionally not exposed: collection runs
   in the Module 01 service on the MT5 machine.)
5. The same branch now also has Module 02 (COT), Module 03 (technical analysis, `python -m aurum.cli analyze`),
   and the Trade Learning Agent (`python -m aurum.cli learn run --loop`, see
   `AURUM_AI/docs/TRADE_LEARNING_AGENT.md`).
6. **Done (2026-11-04):** account and trade data for the dashboard. `python -m aurum.cli sync-account` (and
   `stream-ticks`, every 10 s / 60 s) mirrors the DEMO account from `account_info` + `history_deals_get`, which
   serves `/account/summary`, `/trades/open`, `/trades/history`, `/risk/overview`, `/performance/summary`,
   `/agents/status` and `/notifications`. Optional `RISK_MAX_*` limits in `.env` are monitored, not enforced.
   Details: `AURUM_AI/docs/ACCOUNT_API.md`. Run `python -m aurum.cli init-db` once to add the new tables.
7. **Done (2026-11-04):** optional AI review, off by default. It needs `AI_REVIEW_ENABLED=true`, `ANTHROPIC_API_KEY` and
   `AI_REVIEW_MONTHLY_BUDGET_USD` in `.env`, then `python -m aurum.cli init-db` once. `python -m aurum.cli ai-review run
   EURUSD` writes an advisory review of stored analysis; the budget is a hard stop checked before each API call.
   Details: `AURUM_AI/docs/AI_REVIEW.md`.
8. Next: a signal engine (`/analysis/signals`) and backtests on stored data (`/backtests/latest`), then chart
   drawing tools.

## Module 05

AI Analysis and Decision Engine, on branch `claude/module-05-decision-engine`. Start with
`AURUM_AI/docs/MODULE_05_AI_ENGINE.md`; results and open items are in `AURUM_AI/docs/IMPLEMENTATION_REPORT.md`.
Still to do on the PC: add `AI_REVIEW_ENABLED`, `ANTHROPIC_API_KEY` and `AI_REVIEW_MONTHLY_BUDGET_USD` to `AURUM_AI/.env`
to use AI review. No real AI provider has been called yet.

## Rules that must not change

- Module 01 is **read-only**: no order placement, modification or closing. `ReadOnlyMT5` in
  `AURUM_AI/aurum/gateways/mt5_gateway.py` allowlists read functions only, and
  `tests/test_mt5_gateway.py` scans the source for trading calls.
- **DEMO accounts only**; real and contest accounts are rejected.
- Never commit `.env` or credentials. Invalid market data is flagged, never deleted.

## GitHub housekeeping still open

- Default branch is `claude/aurum-frontend-foundation`; rename it to `main` (repo Settings → General).
- Fully merged branches that can be deleted: `aurum-module-01`, `claude/merge-module-01`,
  `claude/module-01-windows-docs`.
