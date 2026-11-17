# Cloud backup prices (Twelve Data)

AURUM's prices come from your broker, through MetaTrader 5 on the Windows PC. When that PC is off, asleep or
disconnected, nothing new reaches the database and the site freezes on its last price. The backup price feed keeps the
site updating from the cloud until the PC is back.

It reads market data only. It cannot place, change or close a trade, and it never connects to MT5.

## How it works

The hosted API runs the feed in its once-a-minute worker pass (`POST /orchestration/worker/tick`, called by Supabase
pg_cron). For each instrument it does nothing unless **all** of these are true:

1. `TWELVE_DATA_API_KEY` is set and `BACKUP_PRICES_ENABLED` is not `false`;
2. the instrument has a `backup_symbol` in `config/instruments.yaml`;
3. its latest **MT5** tick is older than `BACKUP_PRICES_MT5_STALE_MINUTES` (5);
4. its market is open (the same `trading_hours` Module 01 uses);
5. it was not fetched in the last `BACKUP_PRICES_INTERVAL_MINUTES` (10), and the provider has not refused the symbol
   in the last `BACKUP_PRICES_UNAVAILABLE_HOURS` (24).

All instruments that are due go out in one `GET /time_series` request (5-minute candles, UTC). The feed then:

* stores the 5-minute candles, enough to cover the gap since the newest stored candle (at most one day);
* rebuilds M15, H1, H4 and D1 from all stored 5-minute candles in each period, aligned to the broker's server clock
  (`MT5_SERVER_TIMEZONE`), so a period that began with MT5 candles and ends with backup candles is complete;
* stores the latest price as a tick. Its bid is the provider's last price. Its ask is the bid plus the broker's median
  5-minute spread from recent MT5 candles; with no broker spread on record, ask equals bid.

Every candle and tick it writes has `source = "twelvedata"`. MT5 data has `source = "mt5"`.

## Broker data always wins

* A slot that holds a **complete MT5 candle** is never touched. A backup candle, or an MT5 candle that was still forming
  when the PC went offline, may be replaced.
* When the PC is back, the MT5 collector resumes from its **own** last candle (it ignores backup candles), backfills the
  hours it missed and replaces every backup candle it covers, setting `source` back to `mt5`. Replacing a backup candle
  is not logged as a `candle_revision`.
* A minute after MT5 ticks arrive again, the feed stops (`mt5_live`).
* Health checks ignore backup ticks: `/health` still reports `mt5: disconnected` and the dashboard still warns that MT5
  quotes are not updating while the PC is offline.

## What the site and Module 06 do with backup prices

* `GET /market/quotes` adds `source: "mt5" | "backup"` to each quote. A backup quote counts as live for 20 minutes
  (it is refreshed every few minutes, not every second). The dashboard labels it **Backup price** and explains why.
* Module 05 analysis and WF08-01 keep running on the candles, because new candles keep closing.
* Module 06 rule **R051** (quote freshness) treats a backup price as a **warning**: Module 06 can size the trade, but the
  assessment goes to **REVIEW** with `QUOTE_FROM_BACKUP`, so a person checks the prices before approving. A backup
  price older than `backup_quote_max_age_minutes` (20) while the market is open blocks (`QUOTE_STALE`). Set
  `backup_quotes: block` in `config/risk.yaml` to never size on backup prices; changing it back to `review` counts as
  weakening the configuration and needs an acknowledgement and a reason.

## Set-up

1. Create a free account at twelvedata.com and copy the API key from its dashboard.
2. In Vercel, open the **aurum-api** project → Settings → Environment Variables and add `TWELVE_DATA_API_KEY` (mark it
   **Sensitive**, environment **Production**). Do not paste the key into chat, code or a commit.
3. Redeploy aurum-api. The next worker pass after the PC goes offline starts the feed.
4. Check it: `GET https://aurum-api-tan.vercel.app/api/v1/market/backup-prices`, or on any machine with the database
   settings, `python -m aurum.cli backup-prices status`. `python -m aurum.cli backup-prices run --force` runs one pass
   now (MT5 must still be offline and the market open).

The key travels in the `Authorization: apikey …` header, never in the request address, so it does not appear in logs.
`/market/backup-prices` and the CLI never show it.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `TWELVE_DATA_API_KEY` | not set | Turns the feed on. Secret. |
| `BACKUP_PRICES_ENABLED` | `true` | `false` turns the feed off while keeping the key. |
| `TWELVE_DATA_BASE_URL` | `https://api.twelvedata.com` | Provider address. |
| `BACKUP_PRICES_INTERVAL_MINUTES` | `10` | How often each instrument is fetched while MT5 is offline. |
| `BACKUP_PRICES_MT5_STALE_MINUTES` | `5` | MT5 counts as offline after this long without a tick. |
| `BACKUP_PRICES_UNAVAILABLE_HOURS` | `24` | Pause a symbol for this long after the provider refuses it (plan or unknown symbol). |

`config/instruments.yaml`, per instrument: `backup_symbol` (EURUSD → `EUR/USD`, GBPUSD → `GBP/USD`,
XAUUSD → `XAU/USD`, US30 → `DJI`). Remove it to never use backup prices for that instrument.

`config/risk.yaml`: `backup_quotes` (`review` | `block`) and `backup_quote_max_age_minutes` (20).

## Limits of the free plan

Twelve Data's free (Basic) plan allows 8 API credits a minute and 800 a day; each symbol in a time-series request costs
one credit. Four instruments every 10 minutes is at most 576 credits on a full 24-hour day, so the default fits. A
shorter interval may run out of credits; the feed then records `rate_limited` and tries again at the next interval.

The free plan may not include gold (`XAU/USD`) or the Dow Jones index (`DJI`); index data is listed under paid plans.
When the provider refuses a symbol, the feed records `not_on_plan` (or `unknown_symbol`), pauses that symbol for
`BACKUP_PRICES_UNAVAILABLE_HOURS` and keeps updating the others. `/market/backup-prices` shows this per instrument.
Check Twelve Data's terms for your use as well; its free plan is meant for personal use.

Sources: Twelve Data, *API credits and limits* (support.twelvedata.com) and *Pricing* (twelvedata.com/pricing).

## Known differences from broker prices

* Twelve Data's prices are a market feed, not your broker's quotes. Forex and gold are close; the Dow Jones index
  (`DJI`) is the cash index, which can differ from the broker's US30 CFD by a few points.
* The ask is an estimate from the broker's usual spread, not a real quote.
* Candles carry no tick volume (0) and no spread.
* A 5-minute candle that MT5 never had (no broker ticks in it) keeps its backup version after MT5 backfills.

## Status values

`GET /market/backup-prices` → `instruments[]`: `priceSource` (`mt5` / `backup` / `null`), `mt5Live`, `marketOpen`,
`mt5LastPriceAt`, `backupLastPriceAt`, `lastAttemptAt`, `lastSuccessAt`, `lastOutcome`, `lastError`, `pausedUntil`.

`lastOutcome`: `updated`, `no_data`, `not_on_plan`, `unknown_symbol`, `rate_limited`, `failed`. A worker pass reports
`off` (no key / disabled), `idle` (nothing due; `skipped` gives the reason per instrument: `mt5_live`, `market_closed`,
`paused`, `not_due`, `not_in_database`), `updated`, `partial` or `failed`.

Database: `market_ticks.source` and table `backup_price_status` (migration `0015_backup_prices.sql`).

## Tests

`tests/test_backup_prices.py` (feed, storage rules, MT5 resume point, provider errors, quotes, status) and the backup
cases in `tests/risk/test_scenarios.py` (R051 review, stale backup price, `block` policy, weakening check).
