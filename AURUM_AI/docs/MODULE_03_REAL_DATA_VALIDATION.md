# Module 03 – Real-data validation report

**Date:** 2026-11-03.
**Analysis time (as_of):** 2026-11-03 14:00 UTC.
**Look-ahead replay:** run as of 2026-11-02 14:00 UTC and compared with the as_of run.

**Verdict.** Actual broker-data validation **passed**. Module 03 analysed real Exness demo candles that Module 01 collected through MT5, for all 4 instruments × 5 timeframes. Every reported finding was re-derived independently from the stored candles, and 0 discrepancies were found.

Validation found and fixed three defects:
- historical recalculation rolled back persisted zone states (§9);
- a broker-clock false alarm stopped Module 01 collection after a PC restart (§4, §11);
- two tests depended on the time of day (§10).

No trade-execution capability exists or was added (§13).

> **How to read the evidence.** Three kinds of evidence are kept apart:
> - **Automated tests:** synthetic data, no MT5.
> - **Mocked integration tests:** a fake MT5 module, or SQLite.
> - **Actual broker-data validation:** real candles from the Exness demo terminal, stored in the real PostgreSQL/TimescaleDB database.
>
> Sections 4–9 are broker-data validation unless they say otherwise.

## 1. Environment

| Item | Value |
|---|---|
| OS | Windows 11 Pro 10.0.22621 |
| Python | 3.11.9, 64-bit (`AURUM_AI\.venv`) |
| Packages | MetaTrader5 5.0.6231, pandas 3.1.0, SQLAlchemy 2.1.6, psycopg 3.3.6 |
| MT5 terminal | MetaTrader 5 build 6296, `C:\Program Files\MetaTrader 5\terminal64.exe` |
| Account | Exness demo 486061866, server Exness-MT5Trial10, **DEMO** (credentials are not stored in `.env`; the terminal's own login is used) |
| Broker clock | `MT5_SERVER_TIMEZONE=Etc/UTC`, verified live (server clock UTC+0.0h) |
| Database | PostgreSQL 16.15 + TimescaleDB 2.31.0 in Docker (`timescale/timescaledb:latest-pg16`, container `aurum-timescaledb`, healthy), Docker 29.9.2, `localhost:5432/aurum` |
| Code | branch `claude/module-03-real-data-validation`, from main `edef2cf` (Modules 01–03 merged); engine version 3.0.0, config fingerprint `d3f6259fdb4bba02` |

## 2. MT5 connection – diagnosis of error −6

**Reported problem:** `-6 Authorization failed`, from `HANDOFF.md`, which was written before the PC was set up.

**Root causes found.** The error had two different causes, which a one-line message could not tell apart:

1. **Initial setup (2026-11-02).** The terminal was installed but not logged in, and `MT5_TERMINAL_PATH` pointed at the installation folder instead of `terminal64.exe`.
   - The user logged in to the demo account in the terminal.
   - The path was corrected.
   - `MT5_LOGIN`, `MT5_PASSWORD` and `MT5_SERVER` stay empty, so AURUM attaches to the account the terminal is already logged in to and never logs in or switches accounts itself.
2. **Intermittent (still occurring).** The collector log shows `-6` at 00:48–00:50 and at 09:48 on 2026-11-03:
   - 00:48–00:50: the terminal's session dropped.
   - 09:48: Windows had just started and the terminal had not finished logging in.

   In both cases the supervisor (`scripts/run_collector.py`) retried with backoff, and collection resumed by itself (09:52, 13:03). No user action was needed.

**Current status (verified 2026-11-03 ~14:30 UTC with `check` and the new `diagnose`).** Every step passes:

| Check | Result |
|---|---|
| Python | 64-bit |
| MetaTrader5 package | installed |
| Terminal path | correct |
| Terminal process | running from the configured path |
| Broker connection | connected |
| Account | authorised, DEMO |
| Symbol mapping | present for all 4 instruments |
| Market Watch | all 4 shown |
| History | bars returned for all 5 timeframes per symbol |

**What was added.**
- `python -m aurum.cli diagnose` (`aurum/diagnostics.py`): read-only, step by step. It reports:
  - interpreter bitness and `MetaTrader5` package version;
  - terminal path (file vs folder);
  - running `terminal64.exe` process(es) and whether they match the configured path;
  - login mode;
  - `initialize()` result, with an explanation of the error code;
  - broker connection, account authorisation and DEMO mode;
  - per-symbol mapping and Market Watch visibility;
  - history bars per timeframe.
- Missing symbols are listed with server candidates (e.g. `EURUSD.a, EURUSDm`) but **never substituted**.
- Nothing is changed: no login, no account switch, no `symbol_select`.
- `MT5_ERROR_HINTS` / `explain_mt5_error()` in the gateway: the `initialize()` error used by `check` and `collect` now explains the code. For `-6`, it gives the manual steps:
  - *File > Login to Trade Account*, DEMO account, tick *Save password*;
  - this is expected for a minute or two after Windows starts;
  - the collector retries by itself.

**Manual steps if −6 persists** (AURUM will not do these for you):
1. Open the MT5 terminal.
2. Choose *File > Login to Trade Account*.
3. Sign in to the **demo** account and tick *Save password*.
4. Wait until the status bar shows a ping.
5. Run `python -m aurum.cli diagnose`.

## 3. Database connection – diagnosis of the timeout

**Reported problem:** database connection timeout. At the time of `HANDOFF.md` no database service existed (Docker was not installed). Docker Desktop and the TimescaleDB container were set up on 2026-11-02. A second failure, *password authentication failed*, came from a `#` in the password that Docker Compose treated as a comment; it was fixed with an alphanumeric password and a fresh, empty volume. Neither problem occurs now.

**Current status (`diagnose`, broker-data environment).** Every step passes:

| Check | Result |
|---|---|
| Connection string | `postgresql+psycopg host=localhost port=5432 database=aurum user=aurum`, built from `DB_*`, connect timeout 10 s |
| Driver | psycopg 3.3.6 |
| Network | TCP `localhost:5432` accepts connections |
| Login | connected as `aurum` |
| Migrations | `0001_initial`, `0002_cot_engine`, `0003_technical_analysis` applied |
| Permissions | SELECT/INSERT/UPDATE on Module 01–03 tables, CREATE in `public` |

**What was added.** `diagnose` tells these failures apart:
- **network:** connection refused (service not running / wrong port), timeout (Docker not running / firewall), host name not resolvable;
- **authentication:** wrong password or unknown role, with a note that the Docker volume keeps its original password;
- **missing database:** a missing database is never created automatically;
- **migration status:** pending files;
- **permissions.**

Passwords are scrubbed from every message, both plain and URL-encoded.

`0003_technical_analysis.sql` was still pending on this PC. Before applying it, I confirmed it is additive only (it creates `ta_*` tables; no DROP/ALTER/DELETE), then applied it with `init-db`.

## 4. Module 01 collection results (actual broker data)

| Check | Result |
|---|---|
| Connect to MT5 / DEMO only | ✔ account 486061866, DEMO; non-demo accounts are rejected (mocked tests) |
| Symbol information | ✔ all 4 instruments (digits, contract size, tick value, spread, trade mode) |
| Symbol mapping (no silent substitution) | ✔ explicit in `config/instruments.yaml`: EURUSD→**EURUSDm**, GBPUSD→**GBPUSDm**, XAUUSD→**XAUUSDm**, US30→**US30m** |
| Historical OHLC retrieval | ✔ D1/H4/H1/M15/M5 for every symbol |
| Timestamps normalised to UTC | ✔ `timestamptz`, session time zone UTC, broker clock verified |
| OHLC validation | ✔ 0 rows violate high ≥ max(o,c,l) / low ≤ min(o,c,h); 0 `invalid` rows |
| No duplicate candles | ✔ 0 duplicate (instrument, timeframe, timestamp) keys; re-collection upserts |
| Forming candle excluded | ✔ `forming_candle_excluded` logged on every run; no incomplete row stored |
| Collection errors reported | ✔ `data_collection_logs`: all 76 runs on the validation day `success`; failures are logged with messages (e.g. the −6 periods above) |
| Gaps reported, not hidden | ✔ 478 market-closure gaps (info), 6 `missing_candles` warnings (see §12) |

**Candles stored** (11,819 in total after the last collection run):

| | D1 | H4 | H1 | M15 | M5 |
|---|---|---|---|---|---|
| EURUSD | 501 | 506 | 526 | 603 | 810 |
| GBPUSD | 501 | 506 | 526 | 603 | 810 |
| XAUUSD | 501 | 506 | 525 | 599 | 798 |
| US30 | 501 | 506 | 522 | 599 | 798 |

Counts were taken at 14:2x UTC. Totals per timeframe after the last run: D1 2,004 · H4 2,024 · H1 2,103 · M15 2,420 · M5 3,268.

**Defect found and fixed: broker-clock false alarm.** At 13:01 local time the collector stopped with *"Broker clock is UTC-2.9h … collection stopped"*:
- the PC had just started, and the terminal still showed its last tick from before shutdown, about 2.9 hours old;
- with the market open, that old tick looked like a whole-hour time-zone error.

`verify_clock` now re-samples the tick (15 s) before declaring a "behind" mismatch:
- a live feed on a wrong zone keeps ticking, so it is still reported as a mismatch;
- a stale feed doesn't move, so it is reported as *not verified* and collection continues;
- a healthy clock costs no wait.

Tests cover all three cases. The collector was restarted twice on the current code. Both runs succeeded for 4 symbols × 5 timeframes.

## 5. Instruments and timeframes tested

- **Required starting set:** EURUSD H1, GBPUSD M15, XAUUSD M15, US30 H1.
- **Multi-timeframe:** D1/H4/H1/M15/M5 for all four symbols.
- **Extended:** the independent re-check and the look-ahead replay were also run on **all 20 symbol × timeframe combinations**.

## 6. Analysis results (actual broker data, as_of 2026-11-03 14:00 UTC)

All runs returned `status: ok` and used closed candles only (`provisional: false`). Times are UTC candle open times; "conf" is the close of the confirming candle.

| | EURUSD H1 | GBPUSD M15 | XAUUSD M15 | US30 H1 |
|---|---|---|---|---|
| Candles used (all closed) | 526 (10-02 15:00 → 11-03 13:00) | 602 | 598 | 522 |
| Last close | 1.11918 | 1.34210 | 3960.977 | 51887.4 |
| External structure | bullish, transitioning, 60% | bullish, transitioning, 60% | bullish, transitioning, 60% | bearish, transitioning, 60% |
| Internal structure | bullish | bullish | bullish | bullish |
| Latest structure event | internal **CHoCH** ↑: broke 1.11974 (swing 11-03 00:00); breakout 07:00; conf 08:00 | external **BOS** ↑: broke 1.34261 (swing 08:00); breakout 13:15; conf 13:30 | internal **CHoCH** ↑: broke 3910.193 (swing 11:00); breakout 12:15; conf 12:30 | internal **CHoCH** ↑: broke 51821.8 (swing 10:00); breakout 13:00; conf 14:00 |
| Premium/discount | equilibrium (0.51 of 1.11768–1.12062) | premium (0.78 of 1.34025–1.34261) | **above range** (range 3898.9–3910.6) | discount (0.00 of 51885.4–52479.5) |
| OTE | bullish 1.1183–1.1188, price outside | bearish 1.34171–1.34211, **price inside** | bearish 3906.17–3908.16, outside | bullish 52010.2–52111.2, outside |
| Active order blocks | 5 (latest: bearish OB src 11-02 13:00, detected 22:00, 1.12100–1.12291, fresh) | 6 (bullish OB src 11:15, detected 13:15, 1.34025–1.34106) | 9 (bullish OB src 12:00, detected 12:30, 3898.911–3906.806) | 2 (bearish OB src 11-02 16:00, detected 11-03 02:00, 52295.5–52412.5) |
| Open FVGs | 1 (bullish, candles 11-02 03:00–05:00, detected 06:00, 1.11704–1.11747) | 11 (latest bullish 12:45–13:15, detected 13:30, partially filled) | 9 (latest bullish 13:15–13:45, detected 14:00, 3956.704–3960.355) | 5 (latest bearish 00:00–02:00, detected 03:00, partially filled) |
| Latest liquidity sweep | sell-side swing low 1.11768 at 11:00 (conf 12:00) | buy-side swing high 1.34308 at 13:30 (conf 13:45) | buy-side **previous-day high** 3970.344 at 13:45 (conf 14:00) | sell-side **previous-day low** 51579.5 at 12:00 (conf 13:00) |
| Evidence score | bullish 37 / bearish 5 → bias bullish, grade none | 28 / 37 → neutral | 28 / 19 → neutral | 38 / 12 → bullish, grade none (−4 penalty: 2 unexpected gaps) |

**Multi-timeframe confluence (D1/H4/H1/M15/M5).**
- Every timeframe used only candles closed by 14:00:
  - the D1 analysis stopped at the 11-02 daily candle (effective as_of 11-03 00:00);
  - H4 at the 08:00 candle (closes 12:00);
  - H1 at 13:00, M15 at 13:45, M5 at 13:55.
- Each timeframe's bias equals its stand-alone analysis.
- COT context came from Module 02 (read-only).

| Symbol | Consensus | Aligned / conflicting | Combined |
|---|---|---|---|
| EURUSD | bearish (73% of weight) | D1, H4, M15 / H1, M5 | neutral 27 vs 22; COT bearish |
| GBPUSD | bullish (100%) | all five / none | bullish 38; COT bearish |
| XAUUSD | bearish (80%) | D1, H4, H1 / M15, M5 | bearish 43; COT neutral |
| US30 | mixed (53%) | – | neutral 27; warning "Timeframes disagree on external structure" |

No result reached a grade (A ≥ 75, B ≥ 60, C ≥ 45). Scores measure how much evidence lines up; they are not signals.

**Source-candle verification of every finding.** `scripts/validate_module03.py` re-derives each finding from the raw `market_candles` rows. It does not use Module 03's detection code. For all **20** combinations it checked:

| Finding | Checked | Discrepancies |
|---|---|---|
| Swings (pivot rule, price = candle high/low, confirmation = close of candle *i*+right, HH/HL/LH/LL label vs previous swing) | 4,481 | 0 |
| BOS / CHoCH (close beyond broken level, broken swing confirmed before the breakout closed, conf = breakout close, bias change rules, confirmed flag) | 1,628 | 0 |
| Order blocks (source candle OHLC, opposite colour, wick zone = candle range, detected ≥ break confirmation, state times ≥ detection) | 647 | 0 |
| Fair value gaps (3 consecutive candles, gap = c1/c3 boundaries, detected = close of c3, fill/invalidation candles after detection and touching the gap) | 2,086 | 0 |
| Balanced price ranges (overlap inside both gaps, detected after both) | 833 | 0 |
| Liquidity pools (level = member candle / that UTC day's extreme, created after the day closed) | 2,819 | 0 |
| Sweeps / breaks (wick beyond + close back inside / close beyond, candle after pool creation) | 1,230 sweeps + breaks | 0 |
| Premium/discount + OTE (range = latest external swing high/low, equilibrium, zone, 62/70.5/79% levels, price-inside flag) | 20 | 0 |
| Support/resistance (bounds, touches, role) | 237 | 0 |
| Displacement (direction = candle colour, body/range, documented thresholds) | single-candle displacements | 0 |
| Metadata (candles used = closed valid candles in DB, last candle closed by as_of, price = last close) | 20 | 0 |

**The checker itself was tested.** It caught all five deliberately planted errors:
- a swing confirmed one candle early;
- an altered FVG boundary;
- a BOS level the close never broke;
- an order block detected before its break;
- a finding dated after as_of.

## 7. Historical correctness

| Requirement | Evidence (actual broker data unless noted) | Result |
|---|---|---|
| No look-ahead bias / future leakage | Replay over 24 h for all 20 combinations: every swing, BOS/CHoCH, OB, FVG, pool and sweep known at 11-02 14:00 appears **identically** in the 11-03 14:00 result (e.g. EURUSD H1: 190 swings, 70 events, 26 OBs, 83 FVGs, 120 pools, 103 sweeps). None appears retroactively. Also `tests/analysis/test_no_lookahead.py` (automated). | ✔ |
| No repainting | Same replay: 0 findings changed or vanished | ✔ |
| Nothing confirmed after as_of | 0 items in any run with a confirmation/detection time after its as_of; 0 persisted events confirmed after their run's as_of | ✔ |
| No duplicate analysis events | 0 duplicate events in results and in `ta_structure_events` | ✔ |
| No invalid zone boundaries | 0 zones with top < bottom (results and `ta_zones`) | ✔ |
| No unconfirmed BOS/CHoCH presented as confirmed | All 1,628 events `confirmed: true`, and every one re-verified against its closed breakout candle; 0 unconfirmed rows in `ta_structure_events` | ✔ |
| No unfinished candle presented as confirmed | Last candle of every run closed by as_of; `provisional: false`; Module 01 never stores forming candles | ✔ |
| Valid historical timestamps | Every finding time maps to a stored closed candle (index ↔ timestamp checked) | ✔ |
| Deterministic | Two runs at the same as_of are byte-identical apart from `generated_at` | ✔ |

## 8. Errors encountered during validation

| # | Error | Classification | Resolution |
|---|---|---|---|
| 1 | Historical recalculation overwrote newer zone states: re-saving 11-02 after 11-03 set 31 of 1,122 zones back (e.g. FVG `fully_filled` → `unfilled`, BPR `invalidated` → `active`) | **Defect (Module 03 persistence)** | Fixed (§9), regression test added, live states repaired |
| 2 | First fix rejected by PostgreSQL: `CardinalityViolation – more than one row returned by a subquery`; SQLAlchemy does not correlate a subquery inside `INSERT … ON CONFLICT`. SQLite had silently accepted it | Defect in my fix; the transaction rolled back, so no partial writes | Guard rewritten as explicit SQL referencing the conflicting row; verified on PostgreSQL |
| 3 | Broker-clock false mismatch stopped collection after PC start (stale ~2.9 h tick) | **Defect (Module 01)** | Fixed (§4) |
| 4 | `tests/test_market_api.py`: 2 tests failed in the full suite but passed alone; fixture timestamps were fixed at import, so the "5-second-old" tick was minutes old by the time the tests ran | Test defect | Clock re-anchored per test |
| 5 | Supervisor log 15:53 "Docker engine not running" | False negative of the supervisor's Docker probe under heavy load (container up 27 h, healthy) | No action; harmless (starting Docker Desktop while it runs is a no-op) |
| 6 | MT5 −6 periods (00:48–00:50, 09:48) | Terminal session not yet or no longer authorised | Recovered automatically; message and `diagnose` now explain it (§2) |

## 9. Database persistence (actual broker data, PostgreSQL)

Sequence: save 11-03 analyses → save again → recalculate 11-02 → save 11-03 again.

| Check | Result |
|---|---|
| Analysis runs persisted | ✔ one `ta_analysis_runs` row per analysis (an audit log; 24 rows after the sequence) |
| Structure events persisted | ✔ 313 rows = the 73+78+81+81 events of the four analyses |
| Zones persisted | ✔ 1,210 rows (OB, FVG, BPR, liquidity, S/R) |
| Repeated analysis creates no duplicates | ✔ events 313 → 313, zones unchanged; 0 duplicate keys |
| Historical recalculation | ✔ after the fix: re-analysing 11-02 leaves 0 of 1,122 zones differing from the latest (11-03) state; the historical run is still recorded in `ta_analysis_runs` with its full summary |
| Module 01 tables unchanged by Module 03 | ✔ schemas identical. Row growth (`market_candles` +16, `market_ticks` +12, `instrument_specs` +12, `data_collection_logs` +63) is entirely from 3 normal collector runs (source `mt5`, run IDs present in `data_collection_logs`) |
| Module 02 tables unchanged | ✔ all 8 `cot_*` tables: identical row counts and schemas |
| No tables dropped | ✔ only additive migration 0003 applied |

**Fix:** `aurum/analysis/services/analysis_repository.py`. A stored event or zone is only overwritten by a run whose as_of is not older than the as_of of the run that last wrote it. Test: `tests/analysis/test_multi_timeframe.py::test_historical_recalculation_does_not_roll_back_zone_states`. It fails without the fix and passes with it.

**Known behaviour (not changed).** S/R zone IDs include their ATR-padded boundaries, so an analysis at a different as_of creates new S/R rows instead of updating old ones. The historical recalculation added 46. Every row records the run that produced it (`first_run_uid` / `last_run_uid`).

## 10. Test results

| Suite | Kind | Result |
|---|---|---|
| Backend `pytest` (full) | automated + mocked integration (fake MT5, SQLite) | **266 passed, 1 skipped** (opt-in live CFTC test). Before this work: 243 passed, 2 failed, 1 skipped |
| – of which Module 03 `tests/analysis` | automated (synthetic candles, SQLite) | 53 passed (including the new regression test) |
| – new `tests/test_diagnostics.py` | mocked (fake MT5, SQLite, closed local port) | 14 passed |
| – `tests/test_mt5_gateway.py` | mocked | 25 passed (including 2 new clock tests; read-only source scan passes) |
| Frontend `vitest` + `tsc` + `eslint` | automated | 74/74 tests, typecheck and lint clean (frontend not modified) |
| `scripts/validate_module03.py --check-persistence` | **actual broker data** | ALL CHECKS PASSED (4 required pairs) |
| `scripts/validate_module03.py` on 20 combinations | **actual broker data** | ALL CHECKS PASSED |
| `python -m aurum.cli check` / `diagnose` | **actual terminal + database** | All checks passed / all diagnostics passed |

Repeat the real-data validation at any time (read-only unless `--check-persistence`):

```powershell
python scripts\validate_module03.py --as-of 2026-11-03T14:00 --check-persistence
python scripts\validate_module03.py --pairs EURUSD:D1,EURUSD:M5 --out data\validation
```

## 11. Files changed

| File | Change |
|---|---|
| `aurum/diagnostics.py` (new) | read-only MT5 + database diagnosis |
| `aurum/cli.py` | `diagnose` command |
| `aurum/gateways/mt5_gateway.py` | MT5 error-code explanations; `verify_clock` re-sample before a "behind" mismatch |
| `aurum/analysis/services/analysis_repository.py` | no-rollback guard on event/zone upserts |
| `scripts/validate_module03.py` (new) | repeatable real-data validation |
| `tests/test_diagnostics.py` (new), `tests/test_mt5_gateway.py`, `tests/analysis/test_multi_timeframe.py`, `tests/test_market_api.py` | tests (new, updated, clock fix) |
| `docs/MODULE_03_REAL_DATA_VALIDATION.md` (new) | this report |

## 12. Outstanding limitations

- **US30 history gap.** There are no US30m candles on any timeframe between 2026-07-18 and 2026-10-10 (EURUSD has 72 D1 candles there); the gap is in the history the broker served. Module 01 flags it (`missing_candles`, warning). Module 03 counts it (US30 H1 `unexpected_gaps: 2`, −4 score penalty). US30 D1/H4/H1 structure spanning that period should be read with this in mind.
- **History depth.** Module 01 keeps about 500 candles per timeframe (D1 ≈ 20 months, H1 ≈ 1 month, M5 ≈ 3 days). Long-window context (S/R recency, percentile-style judgements) is limited accordingly.
- **Provisional mode not exercised on broker data.** Module 01 never stores forming candles, so `--provisional` has nothing to include. It is covered by automated tests only.
- **Replay window.** Look-ahead was replayed over 24 hours (1 as_of pair). The automated test replays every bar on synthetic data.
- **Stale dealing ranges.** Premium/discount uses the latest *external* swings. After a strong move it can be far from price (XAUUSD M15 reported `above_range`). This is reported honestly, not forced.
- **Checker scope.** The independent checker verifies the default `break_mode: close` and `zone_mode: wick`. Other settings are flagged by the script as not covered. Equal-highs/lows and inducement candidates are heuristics; their geometry is checked, their usefulness is not.
- **MT5 −6.** It will still appear briefly after Windows starts or when the broker session drops. It recovers automatically while the terminal stays logged in with a saved password.
- **Docker probe.** The supervisor's Docker check can false-alarm under heavy CPU load (harmless).

## 13. Safety confirmation

- **No trade execution functionality was added.**
  - Module 03 never imports or calls MetaTrader 5.
  - `diagnose` reaches MT5 only through `ReadOnlyMT5`, which allows read functions only. Order functions raise `ReadOnlyViolation`, and `test_no_trading_calls_in_source` scans the package for `order_send`/`order_check`/`positions_get`/`orders_get`/`TRADE_ACTION_*` calls and still passes.
  - `diagnose` does not even call `symbol_select`.
- **No account changes.** AURUM never logs in or switches accounts; a non-demo account is reported and refused.
- **No secrets exposed.** Passwords, the admin key and the full database URL are never printed; diagnostic messages are scrubbed (tested).
- **No destructive database operations.** No DROP/TRUNCATE/DELETE was run; the only migration applied (0003) is additive. No database was created or recreated.
- **No fabricated data.** Every number in this report comes from the stored broker candles, the analyses run on them, or the test output.
- **No bypass of human approval.** Scores and findings are decision support only.
