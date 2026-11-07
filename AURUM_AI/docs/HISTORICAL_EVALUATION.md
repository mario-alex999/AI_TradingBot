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
