# Module 05 configuration

Secrets live in `AURUM_AI/.env` and nowhere else. Rules live in `config/ai_engine.yaml`; defaults are in
`aurum/decision/config.py`, and unknown keys are rejected.

## .env

| Name | Default | Meaning |
|---|---|---|
| `AI_REVIEW_ENABLED` | false | Master switch |
| `AI_PROVIDER` | anthropic | anthropic, openai or local |
| `ANTHROPIC_API_KEY` | none | Claude key |
| `AI_REVIEW_MODEL` | see `.env.example` | Claude model; must be priced in the budget table |
| `AI_REVIEW_MONTHLY_BUDGET_USD` | 0 | Hard monthly limit for Claude |
| `AI_REVIEW_MAX_CALLS_PER_HOUR` | 20 | Applies to every provider |
| `AI_PROVIDER_MODEL`, `AI_PROVIDER_BASE_URL` | none | For OpenAI-compatible or local servers |
| `OPENAI_API_KEY` | none | OpenAI key. Install `openai` yourself; it is not a requirement |
| `DECISION_LOOP_INTERVAL_SECONDS` | 300 | Agent loop interval |

Only Claude has a price table, so **the monthly budget covers Claude only**. OpenAI and local servers are limited by the
hourly cap alone, and `/ai/providers` says so.

## config/ai_engine.yaml

- `entry_timeframes`, `templates`: which strategies run, and where.
- `bias`: timeframes, weights, `min_agreement`.
- `regime`: which timeframes and volatility states define a regime.
- `decision`: `min_grades`, `strong_grade`, `insufficient_after_missing`.
- `gates`: `min_rr`, correlation groups, session filters, `quality_lookback_candles`.
- `score`: weights (must add up to 100) and grade thresholds.
- `sizing.default_risk_pct`: drives the size estimate.
- `ai_review`: `max_retries` (default 0), `unavailable_confidence_cap`, `min_grade`.
- `backtest`: `step_timeframe`, `slippage_r`, `commission_r`, `split_date`, `small_sample`.

`PUT /api/v1/ai/config` stores validated overrides in `data/decision/config_overrides.json`. Each run records the
fingerprint it used. The browser cannot call it, because the API's CORS rule allows GET and POST only.
