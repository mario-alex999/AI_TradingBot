# AI Agent Storage — Data Dictionary (SQLite: `data/smartflow.db`)

| Table | What is stored | Updated | Why the agent needs it |
|---|---|---|---|
| `instruments` | symbol, COT market name, asset class, pip size, contract size | once | Maps price ↔ COT contract; sizing |
| `candles` | OHLCV per symbol & timeframe (D1, H1, M15, M5) | each bar | Structure, zones, backtests |
| `cot_reports` | Raw CFTC: OI, long/short for commercials, large specs, small specs | weekly (Fri) | Source of truth for positioning |
| `cot_analytics` | Net positions; COT index 52/156/208w per group; extreme counts; 52w P&L per group; commercial↔large-spec correlation; informed alignment; 6w commercial momentum; **bias** + reasons | weekly | Weekly directional bias & "who is smart money now" |
| `session_ranges` | Asian / London / NY high & low per trading day | daily | Liquidity targets & sweep detection |
| `zones` | Order blocks, FVGs, S&R: top/bottom, created, mitigated, touches | each bar | POIs for entries and targets |
| `signals` | Every setup: entry, SL, TP, RR, score, **score breakdown**, reasons, session, AI decision/confidence/rationale, mode | per setup | Audit trail; similar-setup lookup |
| `trades` | Fills: size, exit, outcome, R multiple, P&L, MAE/MFE (R), mode | per trade | Performance & learning |
| `journal` | Trader/AI reviews: emotion, followed plan, notes, lesson | per trade | Behavioural edge, discipline |
| `component_stats` | Per confluence component: trades, win rate, avg R, suggested weight | monthly | Learning loop output |
| `agent_memory` | Durable lessons/rules (e.g. "NY shorts on EURUSD fail on CPI days") | after reviews | Fed back into every AI review |
| `macro_events` | Economic calendar: time, currency, event, impact | weekly | News filter |

### Minimum data to collect before trusting the bot
- ≥ 4 years of weekly COT per instrument (for the 208-week index).
- ≥ 2 years of H1 and ≥ 6 months of M15 candles.
- ≥ 100 backtest trades and ≥ 50 paper trades with full score breakdowns.
