# Module 05 API

All paths are under `/api/v1`. Admin routes need the `X-AURUM-Admin-Key` header. There are no trading endpoints.
Responses that describe candidates, analysis or evaluation carry a `disclaimer`.

## Signals and workflow

| Method | Path | Notes |
|---|---|---|
| GET | `/analysis/signals` | `TradeSignal[]`; `?status=&instrument=&limit=` |
| GET | `/analysis/signals/{id}` | Dossier: evidence, gates, AI review, history, outcome |
| POST | `/analysis/signals/{id}/approve` | Admin. `{reviewer, note}`. Records a decision only |
| POST | `/analysis/signals/{id}/reject` | Admin |
| POST | `/analysis/signals/{id}/reopen` | Admin. Overrides an AI rejection, with a note |
| GET | `/decision/context/{symbol}` | Assembled inputs; `?asOf=` |
| POST | `/decision/run` | Admin. Background job |
| GET | `/decision/status` | Runs, reviewer state, counts, calibration |
| GET | `/decision/jobs/{id}` | Background job state |
| GET | `/backtests/latest` | `PerformanceReport`; 404 until one has run |
| POST | `/backtests/run` | Admin. `{from, to, symbols?, template?}`. Background job |

## AI surface

| Method | Path | Notes |
|---|---|---|
| POST | `/ai/analyze` | Admin. `{symbol, asOf?, aiReview?, timeframes?}`. Can spend on the AI provider |
| POST | `/ai/analyze/batch` | Admin. Background job |
| GET | `/ai/analysis`, `/ai/analysis/{id}` | Stored analyses |
| GET | `/ai/analysis/{id}/evidence`, `/decision` | Parts of one analysis |
| GET | `/ai/bias/{symbol}`, `/ai/regime/{symbol}` | Calculated now |
| GET | `/ai/setups/{symbol}` | Strategy scan; saves nothing |
| GET | `/ai/trade-ideas` | Ideas, sizes marked as estimates |
| GET | `/ai/strategies`, `/ai/providers`, `/ai/health` | Status |
| GET / PUT | `/ai/config` | PUT is admin |
| POST | `/ai/evaluation/run` | Admin. Background job |
| GET | `/ai/evaluation/runs`, `/ai/evaluation/{id}` | Stored evaluations |

Errors: 404 unknown, 409 blocked or illegal transition, 422 invalid, 503 tables missing (run `init-db`).

## CLI

```
python -m aurum.cli decide run|list|show|approve|reject|reopen|expire-stale|outcomes-update|backtest|status|check
python -m aurum.cli ai status|analyze|bias|regime|setups|providers|evaluate|check
python -m aurum.cli ai strategies list
python -m aurum.cli ai config show
```
