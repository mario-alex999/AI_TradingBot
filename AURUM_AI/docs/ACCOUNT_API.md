# Account, trades, risk, performance, agents and notifications API

Read-only FastAPI endpoints under `/api/v1` that complete the dashboard. They read a mirror of the MT5
**DEMO** account kept in the database. The API process never talks to MT5. AURUM places, changes and closes
no orders.

## Filling the mirror

```bash
python -m aurum.cli sync-account --days 90   # one pass: account snapshot + positions from the last 90 days of deals
python -m aurum.cli sync-account --loop --interval 60   # keep the mirror fresh; MT5 errors are logged and retried
python -m aurum.cli stream-ticks             # live quotes, plus an account snapshot every 10 s and a deal sync every 60 s
```

`stream-ticks` options:

| Option | Default | What it sets |
| --- | --- | --- |
| `--account-every` | 10 | Seconds between account snapshots |
| `--deals-every` | 60 | Seconds between deal syncs |
| `--lookback-days` | 30 | Days of deal history read on each sync |

Pass `0` to `--account-every` or `--deals-every` to turn that sync off.

Only two MT5 functions are used: `account_info` and `history_deals_get`.
- **Raw deals** are stored once each, keyed by deal ticket. Every position a sync touches is rebuilt from
  *all* of its stored deals, so overlapping windows and partial closes are never counted twice.
- **Open positions** are rebuilt from the deal history: entry deals whose volume is not yet covered by
  exit deals. This means no position or order query is needed, and `ReadOnlyMT5` still blocks those.
- **Netting reversals** (an in/out deal) close the current position and open the opposite side. The new
  side gets the ticket `<position id>-2`, `-3` and so on.
- **The sync window** reaches back to the newest stored deal (minus a day) whenever that is older than
  `--lookback-days`, so a long pause never leaves a close unseen.
- **Balance operations** are every non-trade deal type: deposits, withdrawals, credits, commissions,
  interest, dividends and tax. They are what lets past balances be reconstructed.
- **Snapshots** are kept in full for 7 days. Older ones are thinned to one per UTC hour, hourly while
  `stream-ticks` runs and on each `sync-account`.
- A snapshot from a non-DEMO account is refused.
- Account-sync errors (MT5 or database) are logged and skipped; they never stop the quote stream.

Tables: `account_snapshots`, `account_deals`, `trade_records`. PostgreSQL gets them from
`sql/migrations/0005_account_trading.sql`; SQLite gets them from `init-db`.

## Endpoints

| Endpoint | Returns |
| --- | --- |
| `GET /account/summary` | Latest snapshot: balance, equity, free margin, margin level, open positions. `pendingApprovals` is 0 because AURUM proposes no trades yet. Returns `null` before the first sync. |
| `GET /trades/open` | Open positions. `currentPrice` and `floatingPnl` come from the latest stored tick, only while it is fresh (≤ 2 min old); otherwise they are `null`. Floating P/L = price move × tick value ÷ tick size × volume, plus commission and swap charged so far. |
| `GET /trades/history?limit=` | Closed positions, newest first, with net realised P/L (profit + swap + commission + fee). |
| `GET /risk/overview` | Daily, weekly and max drawdown from equity snapshots. Open risk to each position's stop loss. Exposure as notional % of equity. Configured limits. |
| `GET /performance/summary?from=&to=&strategy=&instrument=&session=` | Closed-trade metrics, equity curve, monthly returns, and P/L by instrument, session and strategy. Returns `null` when no closed trade matches. |
| `GET /agents/status` | One entry per agent, with status taken from that module's latest stored run. |
| `GET /notifications?limit=` | Alerts derived from stored state. Ids are stable between calls. |

### How the numbers are made

- **Stop loss / take profit** are the values on the entry deal (what was set when the position opened).
  Later modifications are not visible without order queries.
- **Open risk** is reported only when every open position has a stop loss and a stored specification.
  Otherwise it is `null`, because a position without a stop has unbounded risk.
- **Exposure**: volume × contract size × price when the profit currency is the account currency, or
  volume × contract size when the base currency is. Other crosses are left out rather than converted at a
  guessed rate.
- **Drawdowns**:
  - Daily and weekly drawdown compare current equity with the last snapshot before 00:00 UTC (daily) or
    before Monday 00:00 UTC (weekly).
  - Max drawdown is peak-to-trough over all snapshots.
- **Performance**:
  - The equity curve starts at the reconstructed balance just before the first matching trade and adds
    each trade's net P/L. Deposits do not inflate it.
  - Monthly return = month P/L ÷ balance at the start of the month.
  - `profitFactor` is `null` when there are no losing trades.
  - `sharpe` (daily, annualised, risk-free 0) is `null` with fewer than 20 days of history.
  - Win rate is a percentage.
  - Sessions are assigned by the trade's open time on each market's local clock, so daylight saving is
    handled. This is the same classifier the Trade Learning Agent uses:
    | Session | Local hours |
    | --- | --- |
    | Asian | Tokyo 09:00–18:00 |
    | London | 08:00–17:00 |
    | New York | 08:00–17:00 |
  - When both London and New York are open, the trade counts as "London-New York overlap". The `london`
    and `newyork` filters both include the overlap.
  - `timeframe` is accepted but does not filter, because trades carry no timeframe.

### Agents

| Agent id | Status source |
| --- | --- |
| `market-data` | Collection runs |
| `cot` | COT ingestion log |
| `structure`, `smc` | Technical-analysis runs |
| `sessions` | Market-intelligence runs |
| `risk` | Live check against the limits below. `disabled` when no limit is set. |
| `learning` | The Trade Learning Agent lineage |
| `strategy`, `decision` | Always `disabled`: they are not implemented yet |

If a module's tables are missing, that agent reports `unknown`.

### Notifications

Sources:
- A stale quote feed.
- Failed collections in the last 7 days.
- Unresolved error-level data-quality issues.
- COT updates and failures in the last 14 days.
- Breached risk limits.

`read` is always `false`; the frontend keeps read state locally.

## Risk limits (`.env`)

All limits are optional. AURUM sends no orders, so the limits are **monitored, not enforced at the
broker**: a breach turns the risk agent to `warning` and raises a `risk_limit` notification.

```
RISK_MAX_PER_TRADE_PCT=1
RISK_MAX_DAILY_LOSS_PCT=3
RISK_MAX_OPEN_POSITIONS=5
RISK_MAX_TOTAL_EXPOSURE_PCT=300
RISK_MAX_CORRELATED_EXPOSURE_PCT=200
```

## Still not served (frontend shows "not available")

- `/analysis/signals`: no signal engine exists yet. AURUM does not invent signals.
- `/backtests/latest`: no backtest engine is wired to stored data yet.
