# Module 06 final delivery report: Advanced Risk Management Engine

Date: 2026-11-09. Branch: `claude/module-06-risk-engine` (not committed or pushed; that is for you to decide).
This report follows the 21 items of section 22 of the Module 06 specification. Detail lives in the ten Module 06
documents linked from [MODULE_06_README.md](MODULE_06_README.md).

---

## 1. Executive summary
Module 06 is built, tested and running. It is a deterministic risk engine:
- It sizes trades from the broker's own contract specification using Decimal maths. Volume is floored to the lot step and
  never rounded up.
- It checks every proposal against **40 rules** and returns **PASS, REVIEW, BLOCK or ERROR**, with a reason for each
  rule.
- It keeps persistent locks, a versioned configuration and a hash-chained audit log.
- It publishes events to Modules 07/08 and n8n.

**It authorises; it does not execute.** There is no order placement, no MT5 trading call and no execution route (item 21).

| Measure | Result |
|---|---|
| Module 06 backend tests | **153: 152 passed, 0 failed, 1 skipped** (Postgres-only, opt-in) |
| Existing backend suites | 715: 712 passed, 0 failed, 3 skipped (opt-in) |
| Frontend | 105 vitest tests passed (15 files); typecheck, lint and production build clean |
| Live system | Migration 0008 applied to PostgreSQL 16 + TimescaleDB. The API serves `/api/v1/risk/*`. The DEMO account (500.00 USD) is synced and its live status is **WITHIN_LIMITS**. |

Since the test report was first written, one defect was found on live data and fixed. A deposit made before the first
snapshot was counted as a loss: the month showed 100% and BLOCKED. It now shows 0% and two regression tests cover it.
Details are in item 13.

## 2. Repository assessment
What existed before Module 06, and how the module fits in:
- **`AURUM_AI/`:** Python 3.11, FastAPI, SQLAlchemy and Typer.
  - Module 01: market data from MT5, read-only.
  - Module 02: COT.
  - Module 03: institutional intelligence.
  - Module 04: sessions and market intelligence.
  - Module 05: decision engine and trade candidates.
  - An account mirror (`sync-account`).
  - Migrations 0001–0007, the shared `aurum.ui` table helper, and about 715 tests.
- **`aurum-frontend/`:** React, Vite, TypeScript, React Query and Tailwind.
  - It had a Risk page with an informational position-size calculator and a `getRiskOverview` stub with no backend
    behind it.
- **Gaps that Module 06 closes:**
  - There was no risk authority, no persistent limits or locks, and no audit of decisions.
  - The legacy `RISK_MAX_*` environment variables were not enforced anywhere.
- **Constraints respected:** Module 01 stays read-only; DEMO accounts only; nothing can trade; existing UI style kept.

## 3. Architecture implemented
```
API /api/v1/risk   CLI `aurum.cli risk`   inbox events (n8n, Module 05)
            \            |                 /
             +------ RiskService ---------+    validation, config changes, locks, events
                         |
                     RiskEngine                assess(), position_size(), account/portfolio views
           +-------------+--------------+
     RiskData (read)   40 pure rules    RiskRepository (only writer, one transaction per assessment)
     DatabaseRiskData  + decide()       config versions, assessments, locks, audit chain, outbox/inbox
     MemoryRiskData (simulation and tests)
```
- **Serialised decision step.**
  - PostgreSQL advisory lock plus a process lock.
  - Reservations: unexpired PASS/REVIEW approvals count as open risk for 15 minutes.
  - Idempotency keys.
- **Fail-safe.**
  - Any missing or invalid data gives ERROR or BLOCK, never PASS.
  - A rule that raises an exception is recorded as an error.
- **Determinism.** Every assessment stores its input snapshot, input hash, configuration version and engine version
  (6.0.0).

Full description: [MODULE_06_ARCHITECTURE.md](MODULE_06_ARCHITECTURE.md).

## 4. Files created

**Backend engine (`AURUM_AI/aurum/risk/`)**
| File | Purpose |
|---|---|
| `__init__.py` | Engine version; no-trading statement |
| `money.py` | Exact Decimal parsing; floor to step; money rounding |
| `config.py` | Configuration model and profiles; weakening detection |
| `calendar.py` | Trading day, week and month (17:00 New York, DST-aware) |
| `calculator.py` | Value per price unit, position size, costs, margin estimate |
| `account.py` | Period losses, drawdown, loss streak, margin |
| `portfolio.py` | Exposure, reservations, correlation |
| `models.py` | Decisions, proposals, market state, locks |
| `rules.py` | The 40 rules and the decision |
| `providers.py` | `DatabaseRiskData`: reads Modules 01–05 |
| `repository.py` | All persistence; audit chain; outbox and inbox |
| `db_models.py` | The 13 Module 06 tables |
| `engine.py` | `RiskEngine` |
| `service.py` | `RiskService` facade |
| `simulation.py` | Simulated data and the scenario runner |
| `cli.py` | The `risk` commands |
| `scenarios/01_normal_day.yaml` … `05_exposure_and_positions.yaml` | 5 simulation scenarios |

**Backend, other files**
- `aurum/api/routes/risk.py`: the `/api/v1/risk` router.
- `config/risk.yaml`: seed configuration (version 1, three profiles).
- `sql/migrations/0008_risk_engine.sql`.
- n8n:
  - `docker-compose.n8n.yml`.
  - `n8n/workflows/aurum_risk_events_receiver.json`.
  - `n8n/workflows/aurum_risk_status_check.json`.
- Tests (`tests/risk/`):
  - `conftest.py`.
  - `test_calculations.py`, `test_account_portfolio.py`, `test_scenarios.py`.
  - `test_api.py`, `test_safety.py`, `test_cli.py`, `test_database_provider.py`.
- Documentation (`docs/`):
  - `MODULE_06_README.md`, `MODULE_06_ARCHITECTURE.md`, `MODULE_06_RISK_FORMULAS.md`.
  - `MODULE_06_CONFIGURATION_GUIDE.md`, `MODULE_06_API_DOCUMENTATION.md`, `MODULE_06_DATABASE_SCHEMA.md`.
  - `MODULE_06_INTEGRATION_GUIDE.md`, `MODULE_06_SECURITY_AND_SAFETY.md`, `MODULE_06_KNOWN_LIMITATIONS.md`.
  - `MODULE_06_TEST_REPORT.md`, `N8N_SETUP.md`, and this report.

**Frontend (`aurum-frontend/src/`)**
- `types/risk.ts`, `services/riskService.ts`, `hooks/useRisk.ts`, `utils/riskLabels.ts`.
- `components/risk/` (8 files):
  - `AccountOverview.tsx`, `LocksPanel.tsx`, `RuleList.tsx`, `UsageBar.tsx`.
  - `RiskCalculator.tsx`, `ExposurePanel.tsx`, `RiskSettings.tsx`, `AssessmentHistory.tsx`.
- `test/risk.test.tsx`.
- Dark theme (requested during delivery): `hooks/useTheme.ts` and `test/theme.test.tsx`.

## 5. Existing files modified

**Backend**
| File | Change |
|---|---|
| `aurum/api/app.py` | Mounts the risk router; `risk` readiness in `/health`; CORS allows PUT |
| `aurum/cli.py` | Registers `risk` commands |
| `aurum/database/connection.py` | Registers the Module 06 tables on the shared metadata |
| `aurum/ui.py` | Fixes a crash for tables built with positional headers (one line) |
| `.env.example` | Module 06 block; `RISK_EVENTS_WEBHOOK_URL`; note that legacy `RISK_MAX_*` are superseded |
| `README.md` | Module 06 section |
| `docs/IMPLEMENTATION_REPORT.md`, `docs/LIMITATIONS.md` | Updated for Module 06 |
| `../HANDOFF.md` | Updated for Module 06 |

**Frontend: Module 06**
| File | Change |
|---|---|
| `pages/RiskManagementPage.tsx` | Rebuilt around the engine: account, locks, tabs |
| `services/api.ts` | `apiPut`; error codes |
| `types/auth.ts`, `config/permissions.ts`, `pages/SettingsPage.tsx` | `risk:manage` permission |
| `hooks/queries.ts`, `services/tradingService.ts`, `types/trading.ts` | Removed the unbacked risk stub |

The original `PositionSizeCalculator`, its tests and its style are kept unchanged. Its only edit is a colour token for
dark mode.

**Frontend: dark theme (requested during delivery)**
- Tokens and theming:
  - `tailwind.config.ts` and `styles/globals.css`: colour tokens became CSS variables with identical light values and
    a dark set.
  - `stores/settingsStore.ts`: a `theme` setting.
  - `App.tsx` and `index.html`: the theme is applied before first paint.
- The toggle: `layout/Topbar.tsx` (sun/moon button next to Search) and the Settings theme row.
- Charts: `charts/chartTheme.ts`, `CandlestickChart.tsx`, `IndicatorPane.tsx`, `ChartWorkspace.tsx`,
  `indicatorSeries.ts`, `analytics/chartStyles.ts`, `cot/cotFormat.ts`, `CotCharts.tsx`, `PerformanceChart.tsx` and
  `ExposurePanel.tsx` follow the theme.
- Text on the accent: `Button`, `InfoTip`, `NavList`, `CommandPalette`, `TopMovers`, `LearningAgentPanel`,
  `MarketStructurePage`, `InstrumentConfigTable` and both calculators.
- `test/lightweightChartsMock.ts`: the stub gained `applyOptions`, as the real library has.

## 6. Database models and migrations
- **Migration** `sql/migrations/0008_risk_engine.sql`, applied by `init-db`.
  - It is idempotent and matches the SQLAlchemy models (both are tested).
  - Append-only triggers protect the audit log and the configuration history on PostgreSQL.

**The 13 tables**
| Table | Holds |
|---|---|
| `risk_config_versions` | Versioned configuration |
| `risk_assessments` | Each assessment |
| `risk_rule_evaluations` | One row per rule per assessment |
| `risk_account_snapshots` | Account state used by an assessment |
| `risk_portfolio_snapshots` | Portfolio state used by an assessment |
| `risk_correlation_snapshots` | Correlations used by an assessment |
| `risk_period_records` | Persisted day/week/month start values |
| `risk_locks` | Active and past locks |
| `risk_lock_history` | Lock changes |
| `risk_loss_streaks` | Acknowledged loss streaks |
| `risk_audit_log` | Hash-chained audit |
| `risk_events` | Outbox |
| `risk_inbox` | Inbox |

Money columns use NUMERIC (Decimal round-trip tested). See [MODULE_06_DATABASE_SCHEMA.md](MODULE_06_DATABASE_SCHEMA.md).

## 7. API endpoints (`/api/v1/risk`)
| Method | Path | Purpose |
|---|---|---|
| POST | `/assess` | Full assessment. Admin key; `Idempotency-Key` supported |
| POST | `/position-size` | Size only. Stores nothing |
| GET | `/assessments/{id}` | One assessment |
| POST | `/assessments/{id}/validate` | Is it still usable (not expired, consumed or locked)? |
| POST | `/assessments/{id}/consume` | Mark used (admin) |
| POST | `/assessments/{id}/cancel` | Cancel (admin) |
| GET | `/account` | Account limits and overall status |
| GET | `/portfolio` | Exposure, reservations, correlation |
| GET | `/config` | Active configuration |
| PUT | `/config` | New version (admin, reason, weakening acknowledgement) |
| GET | `/rules` | Rule catalogue |
| GET | `/locks` | List locks |
| POST | `/locks` | Create a lock / kill switch (admin, reason) |
| POST | `/locks/{id}/release` | Release a lock (admin, reason; 409 if not active) |
| GET | `/history` | Paginated assessment history |
| GET | `/history/rules` | Rule-failure counts |
| GET | `/audit` | Audit log and chain check |
| GET | `/events` | Outbox (`after=<sequence>`) |
| POST | `/events/inbound` | Inbox (admin; deduplicated) |
| GET | `/health` | Readiness |

- Responses are camelCase.
- Errors are structured (`code`, `message`, `details`).
- Rate limiting returns 429.
- See [MODULE_06_API_DOCUMENTATION.md](MODULE_06_API_DOCUMENTATION.md).

## 8. Risk calculations
- **Value per price unit per lot,** from calc mode, contract size and tick size/value, converted to the account currency.
  - A stale conversion falls back to the broker tick value; an unavailable conversion is an error.
- **Costs per lot:** spread, slippage and commission.
- **Volume:** `floor_to_step(risk_amount / total_loss_per_lot)`, then capped at the maximum volume.
  - It is rejected if even the minimum lot exceeds the budget.
- **Reward-to-risk after costs.** Margin is an estimate.
- **Period loss:** `start_value − current + deposits after the start snapshot`.
  - Trading day 17:00 New York; ISO weeks; calendar months.
  - Start values are persisted.
- **Drawdown** from peak equity.
- **Loss streak:** breakeven band; duplicates removed.
- **Portfolio:** open risk at stop, plus reservations; per symbol, asset class and correlated group.
  - Correlation is Pearson on H1 log returns, common timestamps only.

Worked example, tested: EURUSD, 1% of 10,000 USD, entry 1.10010, stop 1.09810.
- Cost per lot: 200 + 12 + 5 = 217.
- Volume: 100 / 217 → **0.46 lots**.
- Estimated loss: **99.82**.
- Adjusted R:R: **2.25**.

See [MODULE_06_RISK_FORMULAS.md](MODULE_06_RISK_FORMULAS.md).

## 9. Risk rules (40)
| Group | Rules |
|---|---|
| Data (3) | R001 account data, R002 instrument spec, R003 risk calculation |
| Trade (11) | R010 stop required, R011 stop side, R012 take-profit, R013 price precision, R014 minimum stop distance, R015 stop vs ATR (warning), R016 minimum R:R, R017 risk per trade, R018 minimum lot within budget, R019 requested volume, R020 volume cap (warning) |
| Account (8) | R030/R031/R032 daily/weekly/monthly loss, R033 drawdown, R034 period-start certainty (warning), R035 consecutive losses, R036 trades per day, R037 margin |
| Portfolio (8) | R040 open positions, R041 total open risk, R042 per symbol, R043 per asset class, R044 correlated exposure, R045 correlation reliability (warning), R046 same direction (warning), R047 duplicate idea |
| Market (7) | R050 market open, R051 quote freshness, R052 spread, R053 economic events, R054 volatility, R055 session/weekday, R056 data quality (warning) |
| Protection (1) | R060 risk locks |
| Context (2) | R070 COT, R071 AI confidence (informational; they never change a decision) |

- 32 are hard blocks, 6 are warnings and 2 are informational.
- Safety rules cannot be disabled.
- Overrides can only tighten a rule.

## 10. Frontend features
The Risk Management page (`/risk`), in the existing style:
- **Account overview:** equity, drawdown, and day/week/month loss meters from the backend. When data is missing it shows
  "Data unavailable" with the reason.
- **Locks panel:** active locks and a kill switch. Creating or releasing a lock needs the admin key and a reason.
- **Calculators tab:** the original informational calculator, unchanged, beside the engine calculator. The engine
  calculator uses the same slider-and-chips style, gives the backend's size and can run a full assessment with rule
  results.
- **Exposure tab:** risk per symbol, asset class and correlated group against their limits.
- **Settings tab:** versioned configuration.
  - It is read-only without the `risk:manage` permission (administrator).
  - Loosening a limit asks for a second confirmation that lists the limits being loosened.
- **History tab:** filtered, paginated assessments.
- **Dark theme (requested during delivery):** a sun/moon button at the top right next to Search, also in Settings. The
  choice is saved and applied before first paint, and charts recolour live. The light theme is unchanged.

## 11. Module integrations completed
- **Module 01 (read-only):**
  - Instrument specs and ticks, with an M5 fallback.
  - Spread median; H1 closes.
  - Data-quality issues.
  - Integration-tested against the real tables.
- **Account mirror:** snapshots (DEMO only), deals (deposits and withdrawals), open and closed trades. Integration-tested,
  and live: 500 USD DEMO synced.
- **Module 02 (via Module 04):** COT context as R070.
- **Module 04:** market open, event risk, volatility regime and ATR via `analyze()`. Unknown values go to REVIEW, never
  PASS.
- **Module 05:** `trade_idea_id` expands a candidate into a proposal. Its confidence is informational only (R071); a
  test shows confidence 1.0 cannot override a BLOCK.
- **n8n (Module 08 stand-in):**
  - Docker environment.
  - Two starter workflows, imported and inactive.
  - Outbox dispatch with retries and dead-lettering.
  - Deduplicated inbox.

## 12. Integrations still awaiting later modules
- **Module 07 (human review):** not built yet. The `module07` contract block is in every assessment and is specified in
  the integration guide: review only on PASS/REVIEW; BLOCK and ERROR are final; call `validate` before approval.
- **Module 08 (orchestration):** n8n stands in for it now; the event contract is in place.
- **Module 09 (execution, future):** must call `validate` just before an order, never exceed `finalVolume`, and call
  `consume` or `cancel` afterwards. Module 06 itself will never execute.
- **Module 04 in integration tests:** a stand-in is used there, because its real analysis needs long candle and
  calendar history. Module 04 is used for real at runtime.

## 13. Test results
| Suite | Tests | Result |
|---|---|---|
| `tests/risk/test_calculations.py` | 33 | Passed |
| `tests/risk/test_account_portfolio.py` | 28 | Passed |
| `tests/risk/test_scenarios.py` | 45 | Passed |
| `tests/risk/test_api.py` | 20 | Passed |
| `tests/risk/test_safety.py` | 17 | 16 passed, 1 skipped (Postgres-only, needs `AURUM_TEST_DATABASE_URL`) |
| `tests/risk/test_cli.py` | 2 | Passed |
| `tests/risk/test_database_provider.py` (real tables) | 8 | Passed |
| **Module 06 total** | **153** | **152 passed, 0 failed, 1 skipped** (67 s) |
| Existing backend suites | 715 | 712 passed, 0 failed, 3 skipped (opt-in: Postgres, live AI, live CFTC) |
| Frontend vitest (15 files, including `risk.test.tsx` and `theme.test.tsx`) | 105 | **105 passed, 0 failed** |
| Frontend typecheck, lint (`--max-warnings=0`), production build | — | Passed |

**Defects found and fixed during delivery:**
- **Double lock release returned 200.** It now returns 409.
- **Unknown account currency in `/position-size` gave no reason.** It now returns `ACCOUNT_CURRENCY_UNKNOWN`.
- **History money fields were returned as strings.** They are now numbers.
- **The weakening message did not name the fields.** It now lists them.
- **`risk simulate` crashed in `aurum/ui.py`.** Fixed.
- **Deposit before the first snapshot counted as a loss (found on the live DEMO account).**
  - The 500 USD deposit on 2 November preceded the first snapshot (8 November), so it was already inside the start
    value, but it was also added as an in-period deposit.
  - The month showed **100% loss and BLOCKED**.
  - Now only cash moved after the start snapshot counts. Two regression tests were added and the live status is
    WITHIN_LIMITS.

Full detail: [MODULE_06_TEST_REPORT.md](MODULE_06_TEST_REPORT.md).

## 14. Safety test results (`test_safety.py`: 16 passed, 1 skipped)
These all passed:
- **No trading code:** a source and AST scan finds no MT5 imports and no `order_send`, `order_check` or `positions_*`
  trading calls. No trading route exists.
- **No PASS without a stop,** under any option.
- **AI confidence cannot override** a decision.
- **A stale account never passes.**
- **Disabling rules cannot turn ERROR into PASS.** Safety rules cannot be disabled, and overrides only tighten.
- **Reservations expire.**
- **Volume is never rounded up.** A 2,000-case property test in `test_calculations.py` also covers this.
- **Database guarantees:** Decimal round-trip; audit tampering detected; atomic writes; configuration history
  append-only.
- **Schema:** every table exists and the migration matches the models.

Skipped: the opt-in PostgreSQL migration and trigger test (needs `AURUM_TEST_DATABASE_URL`). The migration itself was
applied to the live database, and `/risk/health` reports the audit chain as healthy.

## 15. Simulation results (`python -m aurum.cli risk simulate`; simulated data, not account performance)
| Scenario | Assessments | PASS | REVIEW | BLOCK | ERROR | Max DD % | Locks |
|---|---|---|---|---|---|---|---|
| normal_day | 3 | 3 | 0 | 0 | 0 | 0.99 | 0 |
| losing_streak | 5 | 3 | 0 | 2 | 0 | 2.95 | 1 |
| daily_loss_breach | 3 | 1 | 1 | 1 | 0 | 3.20 | 1 |
| correlated_exposure | 3 | 2 | 0 | 1 | 0 | 0.00 | 0 |
| exposure_and_positions | 5 | 3 | 0 | 2 | 0 | 0.00 | 0 |

Each scenario behaved as designed:
- **losing_streak:** the third loss locks (R035).
- **daily_loss_breach:** R030 blocks and locks until the next trading day.
- **correlated_exposure:** R044 blocks the correlated BUY and allows the SELL.
- **exposure_and_positions:** R041 blocks.

## 16. Known limitations
- **Margin is an estimate.** Broker margin tiers and hedged margin are not modelled.
- **Spread and slippage assumptions are static.** Gaps and news spikes can exceed them.
- **The drawdown peak is not adjusted for deposits** (period losses are).
- **Correlation is linear** (Pearson on H1). Unreliable estimates are treated as correlated, which is cautious.
- **One shared admin key, no per-user backend roles.** The frontend permission only hides controls.
- **Rules are a code catalogue plus configuration overrides,** not editable at runtime.
- **A month's start is "approximate" until a snapshot exists near the boundary.** This is the case for November on this
  account, because the first snapshot is from 8 November. R034 adds a REVIEW warning.
- **Weekend quotes are stale,** so the market rules block or review outside market hours.

Full list: [MODULE_06_KNOWN_LIMITATIONS.md](MODULE_06_KNOWN_LIMITATIONS.md).

## 17. Setup
From `AURUM_AI/`, in PowerShell:
```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt        # if not already installed
docker compose --env-file .env up -d             # PostgreSQL + TimescaleDB
python -m aurum.cli init-db                      # applies migration 0008 (Module 06 tables, config v1)
```
- Set `AURUM_API_KEY` and `AURUM_ADMIN_API_KEY` in `.env` (never commit `.env`).
- Optional: set `RISK_EVENTS_WEBHOOK_URL` for n8n.
- Profiles and limits are in `config/risk.yaml`. After the first start, change them through `PUT /risk/config` or the
  Settings tab, so every change is versioned and audited.
- **Optional n8n:** `docker compose -f docker-compose.n8n.yml --env-file .env up -d` (http://localhost:5678).
- **Frontend:** `cd ../aurum-frontend; npm install; npm run dev` (http://localhost:5173).
  - Enter the admin key in Settings → Security for admin actions.

## 18. Commands to run the module
```powershell
python -m aurum.cli sync-account                 # DEMO account snapshot (MT5 open); add --loop to keep it fresh
python -m aurum.cli risk health                  # what is ready and what is missing
python -m aurum.cli risk status                  # account limits and overall status
python -m aurum.cli risk size   -s EURUSD -d BUY --entry 1.1001 --sl 1.0981 --tp 1.1051
python -m aurum.cli risk assess -s EURUSD -d BUY --entry 1.1001 --sl 1.0981 --tp 1.1051
python -m aurum.cli risk simulate --report data/exports/risk_simulation.md
python -m aurum.cli risk events dispatch --url http://localhost:5678/webhook/aurum-risk-events
python -m aurum.api                              # API on http://127.0.0.1:8000 (/api/v1/risk/*)
```

## 19. Commands to run all tests
```powershell
# Backend (from AURUM_AI/, venv active)
python -m pytest tests/risk -q                   # Module 06 (~1 min)
python -m pytest -q                              # everything (~20 min)
$env:AURUM_TEST_DATABASE_URL="postgresql+psycopg://aurum:<password>@localhost:5432/aurum_test"; python -m pytest tests/risk/test_safety.py   # opt-in Postgres test

# Frontend (from aurum-frontend/)
npx vitest run
npx tsc --noEmit -p .
npx eslint src --max-warnings=0
npm run build
```

## 20. Validating with demo-account data
1. Open MT5 logged in to the **DEMO** account. Run `python -m aurum.cli sync-account`.
   - Any non-DEMO account is rejected (R001).
   - Keep `sync-account --loop` running so snapshots stay fresh. An old snapshot makes the status "data unavailable".
2. Run `risk status`. Check that the equity matches MT5 and that the day, week and month losses make sense.
   - Today: 500.00 USD, all 0%, **WITHIN_LIMITS**.
3. During market hours, keep Module 01 collection or `stream-ticks` running so quotes are fresh. Then run `risk size` for
   EURUSD and check the volume by hand:
   `risk amount ÷ (stop distance × contract + spread + slippage)`, floored to 0.01.
4. Run `risk assess` for the same trade. Read each rule result, then confirm the assessment appears in the dashboard's
   History tab and in `GET /risk/audit`.
5. Test a block: send a stop on the wrong side, or a stop distance that needs more than the minimum lot. Expect BLOCK
   with R011 or R018.
6. Test the kill switch: create a manual lock in the Locks panel. Every assessment now BLOCKs with R060. Release it with
   a reason.
7. Optional: start n8n, activate the receiver workflow and run `risk events dispatch`. The events should appear in n8n.

## 21. Confirmation: no trade-execution functionality
- **No MT5 order placement was implemented.** Module 06 has no `order_send`, `order_check`, position modification or
  close calls, and imports no MT5 module. `test_safety.py` enforces this by scanning the source and the AST.
- **No live trade has been placed.** The only broker contact is the existing read-only `sync-account`, which reads
  account, deal and position history from a DEMO account.
- **No automatic execution has been enabled.** There are no trading routes or CLI commands. A PASS is an authorisation
  for human review (Module 07), not an order. Module 05, Module 07 and n8n cannot override a BLOCK.
- **No existing module was broken.** The existing backend suites pass (712 passed, 3 opt-in skips), and so do the
  frontend tests.
