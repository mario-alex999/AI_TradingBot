# Optional AI review

Claude reads one instrument's stored analysis and writes an advisory review: a stance, a confidence level, the evidence
on each side, risks, data gaps and what would change the view.

It is **off by default** and **advisory only**. There are no entries, stops, targets or sizes in the output. The package
has no access to the MT5 gateway, and the model is given no tools. Nothing it writes is executed.

## Turn it on

1. Create an API key in the [Claude Console](https://platform.claude.com/). In the Console, also set a spend limit on the
   key's workspace (see "How far the budget goes").
2. Add these to `AURUM_AI/.env`. All three are required:
   ```
   AI_REVIEW_ENABLED=true
   ANTHROPIC_API_KEY=your_key_here
   AI_REVIEW_MONTHLY_BUDGET_USD=10
   ```
3. Create the tables once: `python -m aurum.cli init-db`
4. Check it: `python -m aurum.cli ai-review status`

## Use it

```
python -m aurum.cli ai-review run EURUSD --dry-run   # free: shows what would be sent and reserved
python -m aurum.cli ai-review run EURUSD             # one review; this spends money
python -m aurum.cli ai-review history
```

Nothing runs on a schedule: each review is started by you.

| Endpoint | Purpose |
|---|---|
| `GET /api/v1/ai-review/status` | Whether it is on, the budget and what's left. Never returns the key. |
| `GET /api/v1/ai-review/reviews?symbol=&limit=` | Stored reviews, newest first. |
| `POST /api/v1/ai-review/reviews` `{"symbol": "EURUSD"}` | Runs a review. Needs `X-AURUM-Admin-Key`, because it spends money. |

`POST` errors: `401` bad admin key, `402` budget reached, `404` unknown instrument, `409` no stored candles, `502` the
call failed (the failed review is stored), `503` the feature is off or incomplete.

## Settings

| Variable | Default | Meaning |
|---|---|---|
| `AI_REVIEW_ENABLED` | `false` | Master switch. |
| `ANTHROPIC_API_KEY` | none | Required. Read from `.env`; never logged or returned. |
| `AI_REVIEW_MONTHLY_BUDGET_USD` | none | Required. Hard limit per calendar month (UTC). |
| `AI_REVIEW_MODEL` | `claude-opus-5-5` | Also `claude-sonnet-5-5` or `claude-haiku-5-5`. Others are refused, because their cost can't be capped. |
| `AI_REVIEW_EFFORT` | `high` | `low`, `medium`, `high`, `xhigh` or `max`. Higher effort uses more tokens. |
| `AI_REVIEW_MAX_TOKENS` | `8000` | Longest answer, including thinking. Raises the worst-case cost. |
| `AI_REVIEW_TIMEOUT_SECONDS` | `300` | Request timeout. There are no automatic retries. |

## How the budget works

1. Before calling the API, the review works out its worst-case cost: the prompt at 2 characters per token, plus all of
   `AI_REVIEW_MAX_TOKENS` as output.
2. It reserves that amount with one conditional database update. If the month's total would pass the budget, the review
   stops with no API call. This holds across the API and the CLI running at the same time.
3. After the call, the reservation is replaced with the real cost from the API's token counts.

So a review can be refused while a small amount of budget is left, because that amount won't cover the worst case.

| Outcome | Charged to the budget |
|---|---|
| Completed | Real cost |
| Refused, cut off, or invalid answer | Real cost, because the tokens were used |
| API rejected the call (bad key, rate limit, server error) | Nothing |
| Timeout, lost connection, unexpected error | The full reservation, because it may have been billed (`costIsEstimate: true`) |
| Process crashed mid-call | The full reservation stays on a `pending` row |

### How far the budget goes

The ledger is AURUM's own estimate from list prices in `aurum/ai_review/budget.py`. It isn't Anthropic's bill, and it only
sees calls made by this feature. If prices change, update that table. Set a spend limit in the Console as well: that is
the limit Anthropic itself enforces.

## What is sent

To Anthropic, for each review: the instrument's conditions, scores, volatility, session state, event risk, recent news
headlines, data quality, multi-timeframe confluence and COT context. Account balances, positions, trades, and
credentials are not sent. Raw levels and per-session statistics are left out to keep the cost down.

News headlines are outside text, so the prompt tells the model to treat them as data, and the output is checked against
a fixed format. An answer with extra fields is rejected.
