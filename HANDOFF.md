# AURUM AI — session handoff

Context for continuing this work in local Claude Code (`claude --teleport`) on the Windows PC
that runs MetaTrader 5. Written 2026-11-02 at the end of the cloud build session.

## What exists

| Folder | What it is | State |
|---|---|---|
| `AURUM_AI/` | Module 01: read-only MT5 market data engine (Python, CLI) | Built; 51 tests pass with mocked MT5; DB tests also pass on PostgreSQL 16 |
| `aurum-frontend/` | React/Vite dashboard (built in a separate session) | Builds clean; runs on labelled DEMO DATA because no backend API exists yet |

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
4. Build the HTTP API the frontend expects (FastAPI at `http://localhost:8000/api/v1`) on top of
   Module 01's database: `/health`, `/market/instruments`, `/market/specifications`,
   `/market/candles`, `/market/quotes`, `/market/data-quality`, `/market/collection-logs`,
   `/market/collect`, `/market/export`. The frontend's endpoint calls live in
   `aurum-frontend/src/services/`.

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
