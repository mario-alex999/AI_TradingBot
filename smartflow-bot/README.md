# SmartFlow Confluence Bot (SFCM)

A rules-engine + AI-review trading bot that combines **COT positioning (commercials vs. speculators)**,
**ICT / Smart Money Concepts** (liquidity sweeps, BOS/CHoCH, order blocks, FVGs, OTE, premium/discount),
**session timing** (Asian / London / New York) and **support & resistance** into a single 0–100
confluence score. Every signal, trade and lesson goes into a SQLite store that the AI agent and
the learning loop read from.

> ⚠️ **Not financial advice. No strategy is guaranteed to be profitable.** The edge here is a
> *hypothesis* until **your** backtest + 3 months of paper trading on **your** instruments prove it.
> Start with paper mode. Never risk money you can't afford to lose.

---

## 1. Architecture

```
            ┌──────────── WEEKLY ────────────┐   ┌────────── DAILY / H1 / M15 ──────────┐
CFTC COT ──►│ cot.py  COT index 52/156/208w   │   │ sessions.py  Asian/London/NY ranges  │◄── Price feed
            │ extremes · street-money P&L     │   │ smc.py  swings·BOS/CHoCH·OB·FVG·     │    (yfinance /
            │ informed-money alignment        │   │         sweeps·OTE·premium/discount  │     broker API)
            └───────────────┬────────────────┘   │ levels.py  S&R clusters              │
                            │ bias [-1..1]        └──────────────┬───────────────────────┘
                            ▼                                    ▼
                    ┌────────────────── strategy.py (7-step SFCM score) ─────────────────┐
                    └──────────────────────────────┬────────────────────────────────────┘
                                                   ▼ setup (entry/SL/TP/score/reasons)
          storage.py (SQLite) ◄──── agent.py (Claude review: TAKE / REDUCE / SKIP) ────► risk.py sizing
                 ▲                                                                         │
                 └──────── backtest.py / broker execution ◄──────────── learn.py ◄─────────┘
```

| File | Role |
|---|---|
| `bot/cot.py` | Fetch CFTC Legacy COT, COT index, extremes, P&L per trader group, commercial↔large-spec relationship, bias |
| `bot/sessions.py` | Session tagging (NY time), Asian/London/NY highs & lows |
| `bot/smc.py` | Swings, market structure, order blocks, FVGs, liquidity sweeps, OTE, premium/discount |
| `bot/levels.py` | Support/resistance via clustered pivots |
| `bot/strategy.py` | The SmartFlow Confluence Model (7 steps → score) + rejection funnel |
| `bot/risk.py` | Position sizing, daily loss limit, max open trades |
| `bot/agent.py` | Claude reviewer with structured JSON output; can only veto/reduce risk |
| `bot/storage.py` | SQLite schema = the AI agent's memory |
| `bot/backtest.py` | Conservative event-driven backtest (SL checked before TP, costs in R) |
| `bot/learn.py` | Measures each confluence component's edge, suggests bounded weight changes |
| `main.py` | CLI: `demo`, `update-cot`, `backtest`, `scan`, `learn` |

---

## 2. Step-by-step: building & running the bot

### Phase 0 — Environment (day 1)
```bash
python -m venv .venv && source .venv/bin/activate
```
```bash
pip install -r requirements.txt
```
```bash
python -m pytest -q tests          # 4 tests: causality, OTE math, FVG, COT index
```
```bash
python main.py demo                # offline synthetic run - proves the pipeline end to end
```

### Phase 1 — Data (week 1)
1. Pick 1–3 instruments that have a CFTC futures contract (EURUSD↔`EURO FX`, GBPUSD↔`BRITISH POUND`,
   Gold↔`GOLD`, USDJPY↔`JAPANESE YEN` (inverted!), S&P↔`E-MINI S&P 500`, Oil↔`CRUDE OIL, LIGHT SWEET`).
   Set `symbol` and `cot_market` in `config.yaml`.
2. `python main.py update-cot` — pulls COT history from 2016 and stores raw + analytics. Run it every
   **Friday after 15:30 ET** (report release; data is as of Tuesday).
3. Price data: yfinance is fine for research (H1 ≈ 2 years). For production, use your broker's feed
   (OANDA v20, MetaTrader 5 `MetaTrader5` package, Interactive Brokers `ib_insync`) and write candles to
   the `candles` table.
4. Load the economic calendar (NFP, CPI, FOMC, ECB, BoE) into `macro_events` weekly — the agent skips
   trades near red-folder news.

### Phase 2 — Validate the edge (weeks 2–4)
1. `python main.py backtest` → stats + `data/backtest_trades.csv` + rejection funnel.
2. Accept the strategy only if, **after costs**, on ≥ 100 trades: profit factor ≥ 1.3, avg R ≥ 0.15,
   max drawdown ≤ 15 %, and it holds in both halves of the data (walk-forward: tune on 2023, test on 2024+).
3. Do **not** optimise weights to the decimal — that's curve-fitting. Change one thing at a time.
4. `python main.py learn` → which components (COT, OB, FVG, OTE…) actually added R. Adopt suggested
   weights only if the sample per component ≥ 30 trades.

### Phase 3 — AI agent (week 4)
1. `export ANTHROPIC_API_KEY=...` (or `ant auth login`). Without credentials the agent returns
   "offline" and the rules engine decides.
2. `python main.py scan` — analyses latest bars; if an A-grade setup exists, Claude reviews it against
   COT context, similar historical setups from the DB, stored lessons, and upcoming news, then returns
   `TAKE / REDUCE / SKIP` + size multiplier ≤ 1. Lessons are written back to `agent_memory`.
3. Schedule it: cron every 15 min during 02:00–11:00 NY, plus Friday COT update:
   ```
   */15 2-11 * * 1-5  cd /path/smartflow-bot && .venv/bin/python main.py scan >> logs/scan.log 2>&1
   45 15 * * 5        cd /path/smartflow-bot && .venv/bin/python main.py update-cot >> logs/cot.log 2>&1
   ```

### Phase 4 — Paper trading (months 2–4)
Route `scan` output to a **demo** broker account (OANDA practice / MT5 demo). Log every fill in `trades`
with `mode='paper'`, and journal each trade (`journal` table: followed plan? emotion? lesson?).
Compare paper results with the backtest; a big gap means slippage, timing, or look-ahead bugs.

### Phase 5 — Live (only after Phase 4 passes)
Start at 0.25 % risk/trade, keep the 2 % daily loss cap, add a kill switch (stop after 3 consecutive
losses or 6 % weekly drawdown), and run the `learn` report monthly.

---

## 3. The method in one page
See **[docs/STRATEGY.md](docs/STRATEGY.md)** for the full playbook (COT rules, ICT model, entry checklist).

## 4. What the AI agent stores
See **[docs/DATA_DICTIONARY.md](docs/DATA_DICTIONARY.md)** for every table and field.

## 5. Known limitations (be honest with yourself)
- COT is weekly and lagging — it gives **bias**, never timing. Timing comes from sessions + SMC.
- yfinance FX data has no real volume; H1 backtests approximate M5/M15 entries.
- In the backtest, when SL and TP are both inside one bar, SL is assumed first (pessimistic).
- Commercial "P&L" is a mark-to-market *estimate* from net position × weekly price change — hedgers
  also hold physical positions we cannot see.
