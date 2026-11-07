# Module 05 limitations

- No real AI provider has been called. The Anthropic and OpenAI paths are tested with fakes only.
- OpenAI and local servers are not covered by the monthly budget.
- Strategies, regime rules and session rules are unproven on large samples.
- Calendar coverage is `official` only with no consensus figures, because `ECONOMIC_CALENDAR_API_KEY` is unset.
- No live MT5 test was run for this module.
- Approval records a decision, and nothing consumes it yet.
- Candles are bar data, so a same-candle stop and target is resolved pessimistically.
- Stored spreads are bar-level, so backtest costs are approximate.
- `PUT /ai/config` works from the API and not from the browser, because CORS allows GET and POST only.
- Backtests are slow: about 4 seconds per H1 step for one instrument, measured on one day, so about 7 hours per year of
  H1 steps if that rate holds. It was much slower when the PC was short of memory or busy.
