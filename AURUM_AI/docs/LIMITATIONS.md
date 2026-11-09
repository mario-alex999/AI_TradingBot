# Module 05 limitations

- No real AI provider has been called. The Anthropic and OpenAI paths are tested with fakes only.
- OpenAI and local servers are not covered by the monthly budget.
- Strategies, regime rules and session rules are unproven on large samples.
- Calendar coverage is `official` only with no consensus figures, because `ECONOMIC_CALENDAR_API_KEY` is unset.
- No live MT5 test was run for this module.
- Approval records a decision, and nothing consumes it yet.
- Candles are bar data, so a same-candle stop and target is resolved pessimistically.
- Stored spreads are bar-level, so backtest costs are approximate.
- `PUT /ai/config` from the browser needed CORS to allow PUT. Module 06 added PUT to the allowed methods (2026-11-08).
- Backtests are slow: 4.0 s per H1 step on one day, and 9.1 s per step over one week (121 steps in 1,101 s, with only
  1.6 GB of RAM free). A year of H1 steps for one instrument would take roughly 7 to 16 hours on this PC.
