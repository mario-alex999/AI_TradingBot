# Module 05 historical evaluation

`decide backtest`, `ai evaluate` and `POST /backtests/run` replay stored candles through the same templates and gates.
Results are simulations and do not predict future results.

- Each step uses only candles closed by then, and the trade is then followed on later M5 candles.
- Costs: spread from the stored candles, `backtest.slippage_r` (default 0.05 R), and `backtest.commission_r` (default 0).
- When one candle touches both stop and target, the order is unknown, so it is labelled `ambiguous` and counts as a loss.
- The AI reviewer is not used, so a past review can never leak into a result.
- `backtest.split_date` reports later trades as out-of-sample.
- Runs under `small_sample` trades (default 30) carry a warning.

Reports give trades, win rate, profit factor, expectancy, drawdown, equity, and breakdowns by strategy, instrument,
session, timeframe, confidence and market regime. Results are in R, then converted at the stated risk.

Speed: a replay rebuilds the whole analysis at every step, so it is slow. See
[LIMITATIONS.md](LIMITATIONS.md). Use a short period or set `step_timeframe: H4` for long ones.

## Benchmark: every change must beat the original rules

`python -m aurum.cli decide benchmark --from 2026-11-10 --to 2026-11-21 --profiles current,no_choch,choch_only --out bench.json`
replays each named rule set on the same history and compares it with a **frozen baseline** (`aurum/decision/backtest/benchmark.py`).

| Profile | Rules |
|---|---|
| `baseline` | **Frozen**: the engine before the 22 Nov 2026 changes (no minimum stop, no one-signal-per-idea, counter-trend reversals at any grade). Always runs. Never edit it. |
| `current` | `config/ai_engine.yaml` as it is now |
| `selection` | Baseline + one signal per idea + counter-trend reversals need A/B |
| `min_stop_1atr` | Baseline + stops at least 1 ATR from the entry |
| `no_choch` | Baseline without the `choch_reversal` template |
| `no_counter_trend` | Baseline, but reversal templates never trade against both D1 and H4, at any grade |
| `dedupe_only` | Baseline + one signal per idea (after the gates; in the replay this equals the baseline, because the duplication gate already does it) |
| `no_counter_trend_dedupe` | Both of the above |
| `choch_only` | Only `choch_reversal`, with the baseline rules (to judge it on its own) |

**Costs.** Each simulated trade keeps its gross result and its spread in R, so one replay is priced four ways:

| Scenario | Spread | Slippage | Commission |
|---|---|---|---|
| low | × 1.0 | 0.02 R | 0 |
| base | × 1.0 | 0.05 R | 0 (the `backtest` config) |
| high | × 1.5 | 0.10 R | 0.05 R |
| stress | × 2.0 | 0.20 R | 0.10 R |

**Verdict.** A profile beats the baseline only if, under base **and** high costs, its net R and profit factor are at least
the baseline's and its worst drawdown (in R) is at most 25% deeper. A higher profit factor with less total profit does
not pass.

Results of the first run (10–20 Nov 2026) are in [STRATEGIES.md](STRATEGIES.md).

**Splits.** Results are broken down by grade band (A/B recommended vs C/D research), template, symbol, higher-timeframe
alignment (`with`: D1 and H4 agree with the trade, `against`: both oppose, `mixed`), regime and session, at base costs.
Each trade also records its stop distance in ATR and its risk-reward.

**Caution.** A good backtest is not a forecast: fills are labelled from candles, partial fills, requotes and news gaps
are not modelled, and a high profit factor on a few dozen trades is not evidence of an edge.

## Live results by grade

`GET /analysis/performance` keeps the hypothetical live outcomes of **recommended setups** (grade A/B, not rejected by a
gate or the AI reviewer) apart from **research candidates** (C/D, or rejected), each split by template. The AI Signals
page shows both next to the win rate.

## More history

The MT5 collector stores 500 candles per series by default, which is about 10 days of M5. For longer backtests, run once
on the MT5 PC (in the `AURUM_AI` folder; MT5 open and logged in):

```powershell
$env:HISTORICAL_CANDLE_COUNT="25000"; python -m aurum.cli collect --full
```

25,000 M5 candles are about 3.5 months (M15 about a year). MT5's *Tools → Options → Charts → Max bars in chart* must be at
least that many. The regular collector keeps running.
