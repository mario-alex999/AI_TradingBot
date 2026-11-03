# Module 04 integration (Modules 01–03, the frontend, n8n)

## Module integration
| Module | How Module 04 uses it | If unavailable |
|---|---|---|
| 01 Market data | `Repository.load_candles` via `RepositoryCandleSource` (cached, closed candles only); instrument trading hours via `load_instruments`; `is_market_open` | levels, statistics and volatility report `insufficient_history` / UNKNOWN |
| 02 COT | `Module02Adapter` → Module 03's existing `Module02CotAdapter` (stance, lean) | `integrations.module02.available = false` |
| 03 Technical analysis | `Module03Adapter.context` (structure events, external/internal bias, liquidity, score on M15, H1 fallback), `.confluence` | liquidity events have no `related_structure_event`; conditions use Module 04 evidence only |

Module 04 never changes Module 01–03 code or tables. Each integration is optional and reported in `integrations` in every analysis.

**Future AI Decision Engine.** Consume `GET /intelligence/market/{symbol}?asOf=...` (or `MarketIntelligenceService.analyze`). Use:
- `eventRisk.state`, not the score, as the hard context;
- `UNKNOWN` as "calendar coverage missing";
- the `conditions`, with their evidence, as the explanation.

## Frontend
| Page | Panels |
|---|---|
| **Intelligence → Market Intelligence** (`/intelligence`) | Impact & conditions (C, G) · Economic calendar with filters (B) · Volatility (E) · Session liquidity (D) · Central banks & macro (F) · Announcements |
| **Intelligence → Trading Sessions** (`/sessions`) | existing panels, plus Session engine (A, backend), Session levels, Session liquidity (D) |

Every panel shows:
- its data source, via the "Backend" or "Demo data" badge;
- its freshness;
- provenance lines;
- tooltips that explain each measure.

In **demo mode, no Module 04 data is generated**: panels say "Unavailable in demo mode". A disconnected backend is reported as an error, never filled with demo data.

## n8n (API usage only; no workflows are shipped)
n8n should call the read endpoints with plain HTTP requests. Guidelines:
- **Credentials.**
  - n8n needs **no MT5 credentials and no provider API keys**: those stay in the AURUM server's `.env`.
  - The admin key is needed only for refresh jobs. Store it as an n8n credential (Header Auth: `X-AURUM-Admin-Key`), never in node parameters.
- **No trade execution.** Module 04 has no trading endpoints, and n8n must not be used to place orders based on these outputs.
- **Polling.** Respect rate limits: poll `/market/{symbol}` at most once a minute. The calendar refresh job should run no more than hourly; the providers have their own limits.

Example n8n HTTP Request nodes:
```text
1. Daily calendar refresh (Schedule Trigger 05:30 UTC)
   POST http://<aurum-host>:8000/api/v1/intelligence/calendar/refresh
   Header Auth credential: X-AURUM-Admin-Key
   Body (JSON): {"daysBack": 2, "daysAhead": 21}
   -> returns {"jobId": "..."}; then GET /api/v1/intelligence/jobs/{{$json.jobId}} until status = succeeded

2. Pre-event notification (Schedule Trigger every 15 min)
   GET http://<aurum-host>:8000/api/v1/intelligence/calendar/upcoming?hours=1&importance=high
   IF {{$json.items.length > 0}} -> send a message (Slack/Telegram node) listing name, currency, scheduledAt

3. Context snapshot for a reviewer
   GET http://<aurum-host>:8000/api/v1/intelligence/market/EURUSD/risk
   IF {{$json.state === "UNKNOWN"}} -> message "calendar coverage incomplete" (do not treat as quiet)
```

Treat `coverage.complete = false` as "unknown", never as "no events". With `coverage.level = "official"`, `NORMAL` means "no official release or central-bank decision in the windows". Private surveys and speeches (`coverage.gaps`) are not covered, so messages should say so. Run `intelligence meetings-check` monthly (for example from an n8n schedule calling the CLI host), or rely on the `meetingSchedules` warnings in `/status`. Messages should quote the event `provider` and `sourceUrl`.
