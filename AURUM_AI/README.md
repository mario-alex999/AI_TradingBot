# AURUM AI — Module 01: Market Data Collection Engine

> **READ-ONLY.** This module never places, modifies or closes trades. The MetaTrader 5 package is
> wrapped in an allowlist proxy (`ReadOnlyMT5`) that blocks every trading function, and only
> **DEMO** accounts are accepted — real and contest accounts are rejected and disconnected.

## 1. What it does
Connects Python to a MetaTrader 5 terminal and, for each configured instrument and timeframe:

1. Verifies the terminal is running, connected, and logged in to a **demo** account.
2. Checks each symbol exists (selecting it in Market Watch if needed) and snapshots its specification
   (digits, point, contract size, tick size/value, volume min/max/step, spread, bid/ask, trade mode).
3. Stores the current tick (bid, ask, last, spread, UTC tick time).
4. Downloads up to 500 historical candles with `copy_rates_from_pos` (D1, H4, H1, M15, M5), converts broker
   server time to **UTC**, and excludes the still-forming candle (no look-ahead bias).
5. Saves an immutable **raw** snapshot (`data/raw/…`), validates the data, saves a **processed** snapshot
   (`data/processed/…`), and upserts it into PostgreSQL/TimescaleDB with its quality status.
6. Logs every run and every data-quality issue to the database; exports to CSV/Parquet on request.

Instruments: EURUSD, GBPUSD, XAUUSD, US30 (broker names configurable). If a symbol is missing at your
broker, it is reported and the rest are still collected.

### Data quality rules
| Check | Severity | What happens |
|---|---|---|
| High < max(O,C,L) or Low > min(O,C,H) | error | stored, `quality_status=invalid`, issue logged with the prices |
| Price ≤ 0, missing O/H/L/C | error | stored as `invalid`, issue logged |
| Missing timestamp | error | cannot be stored (no key); issue logged with raw MT5 values |
| Duplicate timestamp (identical / conflicting) | warning / error | first copy kept and flagged `duplicate`; all copies recorded in the issue |
| Timestamps out of order | warning | sorted; inversion count logged |
| Gap during weekend / daily break / holiday | info | `market_closure_gap` |
| Gap while the market should be open | warning | `missing_candles` with first/last missing time |
| Open–previous-close jump > `GAP_RANGE_MULTIPLE` × median range | warning | `suspect` + `abnormal_gap` |
| Forming candle | info | excluded (default) or stored with `is_complete=false` |
| Stored complete candle changed by the broker | warning | `candle_revision` with old/new values, then updated |
| Symbol / data unavailable | error | `instrument_unavailable` / `no_data` |

Nothing is deleted silently. Exports and the `validated_candles` view exclude `invalid` and incomplete candles by default.

### Traceability
Every candle row keeps `broker_symbol`, the original `mt5_time` (server epoch seconds), `collection_run_id`,
`source`, `ingested_at` and `updated_at`. Each run's raw MT5 response is kept in
`data/raw/<YYYYMMDD>/<run_id>/<SYMBOL>_<TF>.parquet`.

## 2. Required software
* Windows 10/11 (the `MetaTrader5` Python package is Windows-only)
* MetaTrader 5 terminal from your broker, logged in to a **demo** account
* Python 3.11 or 3.12, 64-bit (must match the 64-bit terminal)
* PostgreSQL 14+ with TimescaleDB 2.x — easiest via Docker Desktop (`docker-compose.yml` included);
  plain PostgreSQL also works (see §8)
* Git (optional)

## 3–5. Install on Windows (PowerShell, from the `AURUM_AI` folder)
```powershell
py -3.11 -m venv .venv
```
```powershell
.\.venv\Scripts\Activate.ps1
```
```powershell
python -m pip install --upgrade pip
```
```powershell
pip install -r requirements.txt
```
If PowerShell blocks activation: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.
Optional: `pip install -e .` adds an `aurum` command (`aurum check` = `python -m aurum.cli check`).

## 6. Configure MetaTrader 5
1. Install MT5 from your broker and log in to a **demo** account (File → Open an Account → Demo).
2. **Allow algorithmic trading** (Tools → Options → Expert Advisors) is *not* needed for reading
   data and can stay off (this module never trades).
3. Copy the settings file and edit it:
   ```powershell
   Copy-Item .env.example .env
   ```
   ```powershell
   notepad .env
   ```
   * `MT5_TERMINAL_PATH` – e.g. `C:\Program Files\MetaTrader 5\terminal64.exe` (empty = use the running terminal).
   * `MT5_LOGIN`, `MT5_PASSWORD`, `MT5_SERVER` – optional; leave empty to use the account already logged in.
   * `MT5_SERVER_TIMEZONE` – **important**: the broker server clock's time zone. MT5 returns times in server
     time. Most "GMT+2/GMT+3" brokers = `Europe/Athens`; UTC servers = `Etc/UTC`. Check: compare the time
     in Market Watch with UTC. `auto` estimates a fixed offset (no DST handling) while the market is open.
     **Daylight saving:** use an IANA zone (not a fixed offset) so summer/winter switches are applied per
     candle. As a safeguard, `check` and every `collect` run (including each `--loop` pass) compare the live
     broker clock with the configured zone *for today's date*; on a mismatch `check` fails and `collect`
     stops before storing anything, so shifted candles are never written.

## 7. Configure broker symbols
Edit `config/instruments.yaml`. Set `broker_symbol` to the exact name shown in your Market Watch
(e.g. `EURUSD.m`, `XAUUSD+`, `GOLD`, `US30.cash`, `DJ30`). Adjust `trading_hours` to your broker's
contract specification so market closures are not reported as data gaps. Hours are written in the
`timezone` given in that block (default `UTC`); the shipped file uses `America/New_York`, so the schedule
follows US daylight-saving changes automatically (FX closes 17:00 New York time all year, which is
21:00 UTC in summer and 22:00 UTC in winter). The shipped values match Exness (`EURUSDm`, … and
`MT5_SERVER_TIMEZONE=Etc/UTC`). Choose which instruments and
timeframes are collected with `ENABLED_INSTRUMENTS` and `ENABLED_TIMEFRAMES` in `.env`.

## 8. Configure PostgreSQL / TimescaleDB
**Option A – Docker (recommended).** Set `DB_PASSWORD` in `.env` (letters and digits only: Docker
Compose treats ` #` as a comment and `$` as a variable, so such passwords reach the container altered),
leave `DATABASE_URL` empty, then:
```powershell
docker compose --env-file .env up -d
```
The container restarts automatically with Docker Desktop (`restart: unless-stopped`); data lives in the
`aurum_pgdata` volume. `docker compose down` stops it and keeps the data; `down -v` **deletes** the data.
**Option B – existing PostgreSQL.** Install TimescaleDB for your PostgreSQL version (optional), then in `psql`:
```sql
CREATE ROLE aurum LOGIN PASSWORD 'your_password';
CREATE DATABASE aurum OWNER aurum;
```
**Plain PostgreSQL fallback:** if the TimescaleDB extension is not installed, `init-db` creates the same tables
as ordinary PostgreSQL tables (same keys, constraints, indexes and upserts). Differences: no automatic
time-partitioning (chunks), no TimescaleDB compression/retention policies or time_bucket functions — fine for
Module 01 volumes (≈10k candles/run). Install TimescaleDB later and re-run `init-db`; existing data is migrated
into hypertables (`migrate_data => TRUE`).

**Quick start without PostgreSQL (development only):** `DATABASE_URL=sqlite:///data/aurum.db` in `.env`.

## 9. Initialise the database
```powershell
python -m aurum.cli init-db
```
Idempotent — safe to re-run. Equivalent: `python scripts/setup_database.py`. Migrations: see
`sql/migrations/README.md`.

## 10. Connection test
```powershell
python -m aurum.cli check
```
Shows account, server, broker, account type (must be DEMO), terminal build, broker clock, each symbol's
availability with bid/ask/spread, and database/TimescaleDB status. Exit code 0 = all checks passed.

## 11. Collect historical candles
```powershell
python -m aurum.cli collect
```
```powershell
python -m aurum.cli collect --symbol EURUSD
```
```powershell
python -m aurum.cli collect --symbol EURUSD --timeframe M15
```
* First run downloads `HISTORICAL_CANDLE_COUNT` (500) candles per series; later runs are **incremental**
  (only new candles plus a 5-candle overlap). `--full` re-downloads the full count.
* `--loop` repeats every `COLLECTION_INTERVAL_SECONDS` until Ctrl+C.
* `python -m aurum.cli specs` shows instrument specifications.

### Run continuously, including after a restart
```powershell
powershell -ExecutionPolicy Bypass -File scripts\install_autostart.ps1
```
Registers the **AURUM AI Collector** scheduled task: one minute after you sign in to Windows it starts
`scripts/run_collector.py` (hidden). The supervisor starts Docker Desktop + the database container if needed,
then keeps `collect --loop` running. MT5 is opened from `MT5_TERMINAL_PATH` and logs in with its saved
password; whenever MT5 is closed, logged out or disconnected (or the DB / broker-clock check fails) the
collector exits and is retried (30 s, doubling to 10 min) until things are healthy again.
* Pause: create an empty file `COLLECTOR_DISABLED` in the project folder; delete it to resume.
* Logs: `logs/supervisor.log` (start/stop/retry events), `logs/collector.out.txt` (collection tables).
* Remove autostart: `powershell -ExecutionPolicy Bypass -File scripts\install_autostart.ps1 -Uninstall`.
* Needs a signed-in Windows session (MT5 runs on the desktop) and the PC awake - sleep pauses collection;
  missed candles are back-filled on the next run (incremental download with overlap).

## 12. Export
```powershell
python -m aurum.cli export
```
```powershell
python -m aurum.cli export --symbol XAUUSD --timeframe M15 --format csv
```
Writes `data/exports/<SYMBOL>/<TF>.csv` and `.parquet` with UTC timestamps (ISO-8601 `+00:00` in CSV).
Existing files are **not overwritten**: `--on-exists version` writes a timestamped copy, `--on-exists overwrite`
replaces explicitly. `--include-invalid` / `--include-incomplete` add flagged rows.

## 13. Automated tests
```powershell
python -m pytest
```
Tests mock MT5 (no terminal needed). Database tests run on SQLite; to also run them on PostgreSQL
(**drops and recreates the `public` schema of that database — use a dedicated test database**):
```powershell
$env:AURUM_TEST_DATABASE_URL="postgresql+psycopg://aurum:your_password@localhost:5432/aurum_test"; python -m pytest tests/test_database.py
```

## 14. Common errors
| Message | Fix |
|---|---|
| `The term 'py' is not recognized` | Python isn't installed (or has no launcher). `winget install -e --id Python.Python.3.11`, or the python.org installer with **Add python.exe to PATH** ticked; then open a new PowerShell window |
| `Activate.ps1 cannot be loaded because running scripts is disabled` | `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned`, then activate again; or skip activation and use `.\.venv\Scripts\python.exe` instead of `python` |
| `The term 'docker' is not recognized` | Docker Desktop isn't installed. Install it (restart, open it once), or use the SQLite quick start: `DATABASE_URL=sqlite:///data/aurum.db` in `.env`, then `init-db` |
| Database `connection timeout expired` on port 5432 | The database isn't running: start Docker Desktop, `docker compose --env-file .env up -d`, wait for `docker ps` to show `healthy` |
| `The 'MetaTrader5' package is not installed` | Run on Windows inside the venv: `pip install MetaTrader5` |
| `initialize() failed (-10003 …)` | Terminal not found: set `MT5_TERMINAL_PATH`, start MT5 once manually, use 64-bit Python |
| `initialize() failed (-6 …) Authorization failed` | Leave `MT5_LOGIN`, `MT5_PASSWORD`, `MT5_SERVER` empty to use the account already logged in; otherwise fix them (trading password, server name exactly as in MT5). Also check the terminal is logged in and `MT5_TERMINAL_PATH` points to that same terminal |
| `terminal is running but not connected` | Check internet / broker server in the terminal's bottom-right status |
| `… is a REAL account … only connects to DEMO` | By design. Log in to a demo account |
| `broker symbol 'XAUUSD' not found` | Fix `broker_symbol` in `config/instruments.yaml` (look in Market Watch → Symbols) |
| Candle times look 2–3 hours off | Set `MT5_SERVER_TIMEZONE` correctly (e.g. `Europe/Athens`) |
| `Unknown MT5_SERVER_TIMEZONE` on Windows | `pip install tzdata` |
| Many `missing_candles` warnings on weekends/rollover | Adjust `trading_hours` in `instruments.yaml` |
| `connection refused` / `password authentication failed` (DB) | Start PostgreSQL/Docker; check `DB_*` in `.env` |
| `Database schema not initialised` | `python -m aurum.cli init-db` |
| `Export exists - not overwritten` | Intended; use `--on-exists version` or `overwrite` |
| `copy_rates_from_pos returned no data` | Open the chart once in MT5 or increase "Max bars in chart" (Tools → Options → Charts) |

## Module 02 - COT Institutional Intelligence Engine
CFTC Commitments of Traders positioning (Legacy, TFF, Disaggregated) for EURUSD, GBPUSD, XAUUSD and US30:
ingestion with full source traceability, look-ahead-safe metrics, institutional intelligence reports, a
FastAPI backend and the COT dashboard in `aurum-frontend`. Read-only; no trading.
```powershell
python -m aurum.cli init-db
python -m aurum.cli cot backfill --years 4
python -m aurum.cli cot report --instrument EURUSD
python -m aurum.api                      # backend API on http://127.0.0.1:8000 (docs at /docs)
```
Documentation: [docs/MODULE_02_COT.md](docs/MODULE_02_COT.md) (overview, database, API, dashboard),
[docs/COT_METHODOLOGY.md](docs/COT_METHODOLOGY.md) (definitions, formulas, timing, limitations),
[docs/COT_OPERATIONS.md](docs/COT_OPERATIONS.md) (exact commands, scheduling, troubleshooting).

## Module 03 - Technical Analysis and SMC Engine
Deterministic, explainable price-action analysis on Module 01's stored candles: swings, internal/external
structure (HH/HL/LH/LL), BOS and CHoCH (close-confirmed), displacement, order blocks with mitigation states,
fair value gaps, balanced price ranges, liquidity pools with sweeps and breaks, inducement candidates,
premium/discount with Fibonacci levels, OTE, support/resistance zones, multi-timeframe confluence and an
explainable score, with optional COT context from Module 02. Non-repainting; analysis only - no MT5 connection,
no trading.
```powershell
python -m aurum.cli init-db
python -m aurum.cli analyze run --symbol EURUSD --timeframe H1
python -m aurum.cli analyze confluence --symbol EURUSD
```
API: `/api/v1/analysis/technical/{symbol}`, `/analysis/confluence/{symbol}`, `/analysis/market-structure`.
Documentation: [docs/MODULE_03_TECHNICAL_ANALYSIS.md](docs/MODULE_03_TECHNICAL_ANALYSIS.md) (definitions,
configuration, API, database, limitations). Settings: `config/analysis.yaml`.

## Module 05 - AI Analysis and Decision Engine

Turns the stored outputs of Modules 01-04 into explained trade candidates for a person to approve or reject. **It cannot
place a trade.** Approval writes a record and nothing else.

```
python -m aurum.cli init-db
python -m aurum.cli decide check
python -m aurum.cli decide run --symbol EURUSD --no-ai
python -m aurum.cli decide list
python -m aurum.cli ai bias --symbol EURUSD
```

Start with [docs/MODULE_05_AI_ENGINE.md](docs/MODULE_05_AI_ENGINE.md).

## Module 06 - Advanced Risk Management Engine

Decides whether a proposed trade fits the account's limits and how large it may be: **PASS, REVIEW, BLOCK or ERROR**,
explained rule by rule. It covers position size from the broker specification (floored, never rounded up), loss limits,
drawdown, losing streaks, portfolio and correlated exposure, market filters, and persistent locks with a kill switch.
**It cannot place, modify or close a trade.**

```
python -m aurum.cli init-db
python -m aurum.cli risk health
python -m aurum.cli risk status
python -m aurum.cli risk size -s EURUSD -d BUY --entry 1.1001 --sl 1.0981 --tp 1.1051
python -m aurum.cli risk assess -s EURUSD -d BUY --entry 1.1001 --sl 1.0981 --tp 1.1051
python -m aurum.cli risk simulate
```

Start with [docs/MODULE_06_README.md](docs/MODULE_06_README.md). n8n in Docker: [docs/N8N_SETUP.md](docs/N8N_SETUP.md).

## Module 07 - Human Approval and Trade Management Engine

Every trade idea needs an explicit, signed-in person to approve it, after Module 06 and 14 server-side checks.
**APPROVE** creates an execution *request* for Module 09, which is not built yet. Module 07 never places, modifies or
closes a trade and never connects to MT5. A Module 06 BLOCK can never be approved.

```
python -m aurum.cli init-db
python -m aurum.cli users create alice --role admin     # password prompted, hidden
python -m aurum.cli approvals status
python -m aurum.cli approvals worker                    # expiry, handoff, outbox, notifications
```

Start with [docs/module07/README.md](docs/module07/README.md).

## Project layout
```
aurum/  config.py · logging_config.py · cli.py
        analysis/                        Module 03: market_structure · smart_money · support_resistance ·
                                         multi_timeframe · scoring · models · services · cli
        cot/                             Module 02: client · parsers · release timing · validation ·
                                         calculations · positioning · divergence · intelligence · exports · cli
        api/                             FastAPI backend (python -m aurum.api) · routes/cot.py ·
                                         routes/technical_analysis.py · jobs
        gateways/mt5_gateway.py          read-only MT5 access, demo enforcement, broker clock
        collectors/                      candles · ticks · instrument specs
        services/                        market_data_service (orchestration) · validation_service
        database/                        connection · models (SQLAlchemy) · repository (upserts)
        risk/                            Module 06: calculator · account · portfolio · rules · engine ·
                                         repository · service · simulation · cli (never places trades)
        exporters/market_data_exporter.py
sql/schema.sql  sql/migrations/   config/instruments.yaml · cot.yaml · analysis.yaml · risk.yaml   scripts/setup_database.py
data/raw · data/processed · data/exports   logs/aurum.log (JSON lines)   tests/
```
Security: secrets live only in `.env` (git-ignored), are `SecretStr` in code, and a log filter masks them.
