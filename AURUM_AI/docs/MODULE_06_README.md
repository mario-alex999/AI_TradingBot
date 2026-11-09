# Module 06: Advanced Risk Management Engine

Module 06 decides whether a proposed trade fits the account's risk limits, and how large it may be. It answers with one
of four decisions and explains each one rule by rule.

| Decision | Meaning |
|---|---|
| **PASS** | Every rule passed. The trade may go to human review (Module 07). It is not an order. |
| **REVIEW** | No hard rule failed, but warnings must be read and accepted by a person. |
| **BLOCK** | A hard rule failed. Nothing downstream can override it: not Module 05 confidence, Module 07 or n8n. |
| **ERROR** | Required data was missing, stale or invalid, so no decision was possible. It is treated as not approved. |

**Module 06 never places, modifies or closes an order.** It has no MT5 execution connection and imports nothing
that can trade. A safety test scans the code for this (see [MODULE_06_SECURITY_AND_SAFETY.md](MODULE_06_SECURITY_AND_SAFETY.md)).

## What it covers
- **Position sizing.** Uses Decimal maths from the broker's own contract specification and includes spread, slippage
  and commission. The size is floored to the lot step and never rounded up. A trade is rejected when even the minimum lot
  would exceed the risk budget.
- **Trade checks.** Stop-loss required and on the right side, take-profit, tick alignment, broker stop level, ATR sanity
  check, and minimum reward-to-risk after costs.
- **Account limits.** Daily, weekly and monthly loss (trading day rolls at 17:00 New York); drawdown from peak;
  consecutive losses; trades per day; margin.
- **Portfolio limits.** Open positions, total open risk, per instrument, per asset class and correlated exposure. Risk
  already reserved by unexpired PASS/REVIEW approvals is counted.
- **Market filters** from Module 04. Market open, quote freshness, spread, economic events, volatility regime, sessions,
  and Module 01 data quality.
- **Locks and kill switch.** Automatic locks on loss, drawdown and streak limits, plus manual locks. Locks survive
  restarts and configuration changes.
- **Versioned configuration** with three profiles. Every change needs a reason, and loosening a limit needs an explicit
  acknowledgement.
- **Audit trail.** A hash-chained, append-only audit log. Every assessment stores its inputs, rule results and config
  version.
- **Events.** An outbox (`/risk/events`, webhook dispatch with retries) and an inbox (deduplicated) for Modules 05, 07
  and 08 and for n8n.
- **Dashboard.** The Risk Management page in `aurum-frontend` (account, locks, calculators, exposure, settings, history).

## Quick start
Run these from `AURUM_AI/` with the venv active.

```powershell
python -m aurum.cli init-db                 # adds the Module 06 tables (migration 0008)
python -m aurum.cli sync-account            # account data the engine needs (MT5 open, DEMO account)
python -m aurum.cli risk health             # what is ready and what is missing
python -m aurum.cli risk status             # account limits and overall status
python -m aurum.cli risk size -s EURUSD -d BUY --entry 1.1001 --sl 1.0981 --tp 1.1051
python -m aurum.cli risk assess -s EURUSD -d BUY --entry 1.1001 --sl 1.0981 --tp 1.1051
python -m aurum.cli risk simulate --report data/exports/risk_simulation.md   # simulated scenarios, no broker
python -m aurum.api                         # serves /api/v1/risk/* to the dashboard
```

Without a recent account snapshot, assessments return **ERROR** (rule R001). This is deliberate: the engine never
assumes an account balance.

## Documents
| Document | Contents |
|---|---|
| [MODULE_06_ARCHITECTURE.md](MODULE_06_ARCHITECTURE.md) | Components, assessment flow, concurrency, idempotency |
| [MODULE_06_RISK_FORMULAS.md](MODULE_06_RISK_FORMULAS.md) | Every formula, with a worked example |
| [MODULE_06_CONFIGURATION_GUIDE.md](MODULE_06_CONFIGURATION_GUIDE.md) | `config/risk.yaml`, profiles, versioning, rule overrides |
| [MODULE_06_API_DOCUMENTATION.md](MODULE_06_API_DOCUMENTATION.md) | Endpoints, sample payloads, errors |
| [MODULE_06_DATABASE_SCHEMA.md](MODULE_06_DATABASE_SCHEMA.md) | The 13 tables and their guarantees |
| [MODULE_06_INTEGRATION_GUIDE.md](MODULE_06_INTEGRATION_GUIDE.md) | Modules 01–05, the Module 07 contract, Module 08/n8n, Module 09 |
| [MODULE_06_SECURITY_AND_SAFETY.md](MODULE_06_SECURITY_AND_SAFETY.md) | Non-negotiable restrictions and how they are enforced |
| [MODULE_06_TEST_REPORT.md](MODULE_06_TEST_REPORT.md) | What was tested and the real results |
| [MODULE_06_KNOWN_LIMITATIONS.md](MODULE_06_KNOWN_LIMITATIONS.md) | What it does not do, and approximations |
| [N8N_SETUP.md](N8N_SETUP.md) | n8n in Docker, wired to the risk events |
| [MODULE_06_DELIVERY_REPORT.md](MODULE_06_DELIVERY_REPORT.md) | Final delivery report (the 21 items of section 22) |

## Code map
```
aurum/risk/
  __init__.py      ENGINE_VERSION 6.0.0; no-trading statement
  money.py         Decimal helpers: exact parsing, floor to step, money rounding
  config.py        RiskConfig (pydantic), profiles, overrides, weakening detection
  calendar.py      trading day/week/month (America/New_York 17:00, DST-aware)
  calculator.py    instrument value per price unit, position size, costs, margin estimate
  account.py       account evaluation: period losses, drawdown, streak, margin
  portfolio.py     exposure, reservations, correlation (Pearson on log returns)
  models.py        decisions, severities, proposal, market state, locks
  rules.py         the 40 rules, evaluation, decision
  providers.py     DatabaseRiskData: reads Modules 01–05 from the database
  repository.py    persistence: config versions, assessments, locks, audit chain, outbox/inbox
  engine.py        RiskEngine: assess, position_size, account/portfolio views
  service.py       RiskService: API/CLI facade, validation, config changes, events
  simulation.py    MemoryRiskData and the scenario runner (simulated data only)
  scenarios/       5 built-in simulation scenarios (YAML)
  cli.py           `python -m aurum.cli risk ...`
aurum/api/routes/risk.py   /api/v1/risk router
config/risk.yaml           seed configuration (version 1)
sql/migrations/0008_risk_engine.sql
tests/risk/                unit, scenario, API and safety tests
```
