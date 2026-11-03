# Module 04 API reference

**Conventions**
- **Base path:** `/api/v1/intelligence`.
- **Format:** JSON keys are camelCase; times are ISO-8601 UTC.
- **`asOf`:** every read accepts this parameter and returns the point-in-time view at that instant. The default is now.
- **Admin:** endpoints marked "admin" need the `X-AURUM-Admin-Key` header, which must match `AURUM_ADMIN_API_KEY`.
  - Without a configured key, they return 503.
  - With a wrong or missing header, they return 401.
- **No trading:** there are no endpoints that place, modify or approve trades.

**Errors**

| Status | Meaning |
|---|---|
| 404 | unknown instrument or event |
| 422 | invalid parameter (e.g. `importance=extreme`) |
| 503 | Module 04 tables not initialised (`init-db`), or database unavailable |
| 500 | anything else, with a generic message; details stay in the server log |

## Sessions
| Method & path | Parameters | Returns |
|---|---|---|
| GET `/sessions/current` | `symbol?`, `asOf?` | active sessions, overlap flag, kill zones, every window's status, local times, DST, countdowns, next/previous occurrences, `configFingerprint` |
| GET `/sessions/upcoming` | `asOf?`, `symbol?` | next occurrence of every session and kill zone |
| GET `/sessions/history` | `start?` (default: end − 7 d), `end?` (default: now), `names?` (csv), `includeKillZones?`, `page`, `pageSize` (≤ 1000) | session occurrences in UTC (max 120 days) |
| GET `/sessions/overlaps` | `asOf?`, `symbol?` | overlap / transition windows, plus measured historical activity when `symbol` is given |
| GET `/sessions/kill-zones` | `symbol?`, `asOf?` | kill zones (per-instrument definitions), active and next |
| GET `/sessions/levels/{symbol}` | `asOf?` | previous/current session, day, week and month levels, with source candle, timeframe, period, state, swept/broken times and issues |
| GET `/sessions/statistics/{symbol}` | `asOf?` | rolling session statistics (5/10/20/30/60), breakout frequency, daily/weekly ranges |
| GET `/sessions/liquidity/{symbol}` | `asOf?` | sweep / breakout / false-breakout / unconfirmed / range-expansion events, with the Module 03 link |

## Economic calendar
| Method & path | Parameters | Returns |
|---|---|---|
| GET `/calendar` | `range?` (today, tomorrow, this_week, next_week) or `start`/`end`; `currency?`, `importance?`, `category?` (csv); `instrument?`; `asOf?`; `page`, `pageSize` (≤ 500) | `items`, `total`, `coverage {complete, level (full / official / partial / none), providers, reason, currencies {CCY: {covered, providers, missing}}, gaps}`, `pointInTime` |
| GET `/calendar/upcoming` | `hours?` (default 48, ≤ 720), `currency?`, `importance?`, `instrument?`, `page`, `pageSize` | upcoming events |
| GET `/calendar/status` | — | providers (configured, access requirement, last success/attempt/error), coverage, last ingestions |
| GET `/calendar/impact/{symbol}` | `asOf?` | event-risk state, reason, next event, active and upcoming events, coverage, historical context |
| GET `/calendar/{event_id}` | — | one event with its full revision history and original provider payload |
| POST `/calendar/refresh` (admin) | body `{daysBack?: 7 (0–90), daysAhead?: 21 (1–120)}` | 202 `{jobId}`; poll `/jobs/{jobId}` |

## Macro and news
| Method & path | Parameters | Returns |
|---|---|---|
| GET `/macro/overview` | `asOf?` | central banks (rate, range, in effect since, previous, latest decision, next meeting, source, retrieved, stale) and indicators |
| GET `/macro/central-banks` | `asOf?` | the central-bank part only |
| GET `/macro/indicators` | `asOf?` | indicators: latest/previous point-in-time values, source, status |
| GET `/macro/rates` | `bank` (fed, ecb, boe, boj, rba, rbnz, boc, snb), `asOf?`, `limit` (≤ 2000) | rate changes and decisions |
| POST `/macro/refresh` (admin) | — | 202 job |
| GET `/news` | `symbol?`, `asOf?`, `page`, `pageSize` (≤ 200) | headlines as published, with `sourceType` (`official_announcement` / `reported_news`), `tone {kind, label, terms, method}` (AURUM's rule-based reading), and `sentimentSummary` |
| POST `/news/refresh` (admin) | — | 202 job |

## Volatility and market
| Method & path | Parameters | Returns |
|---|---|---|
| GET `/volatility/{symbol}` | `asOf?` | H1/D1 ATR, percentiles, realised vol, expansion, abnormal flag, regime + reason, daily/weekly ratios, per-session ratios |
| GET `/volatility/{symbol}/sessions` | `asOf?` | per-session volatility vs history |
| GET `/volatility/{symbol}/regime` | `asOf?` | regime, timeframe, reason, per-timeframe regimes |
| GET `/market/{symbol}` | `asOf?`, `detail` (summary / full), `includeModule03?` | full analysis bundle (sessions, levels, liquidity, statistics, volatility, event risk with per-instrument coverage (`symbolCurrencies`, `uncoveredCurrencies`, `gaps`), conditions, scores, `news` (72 h headlines with tone), integrations, data quality, disclaimer, `cache {hit, computedAt, ttlSeconds}`); not persisted. Repeated requests within the same as_of minute are served from the cache (60 s) |
| GET `/market/{symbol}/conditions` | `asOf?` | condition tags with evidence |
| GET `/market/{symbol}/risk` | `asOf?` | event-risk state plus event-risk score |
| POST `/analyze/{symbol}` (admin) | body `{asOf?, persist?: true}` | 201 run summary (`runUid`, tags, event risk, regime, scores); persisted to the Module 04 tables |

## System
| Method & path | Returns |
|---|---|
| GET `/status` | module/methodology version, config fingerprint, schema readiness, providers (including the official agency calendars), coverage, row counts, `meetingSchedules` (staleness per bank), `tradeExecution: false` |
| GET `/config` | the effective configuration (no secrets) |
| GET `/jobs/{job_id}` | status, progress messages and result of a background job |

`GET /api/v1/health` also reports `modules.intelligence` (whether the Module 04 tables are ready).

## Example
```bash
curl "http://localhost:8000/api/v1/intelligence/market/EURUSD/risk"
```
Response excerpt from the real database, 2026-11-03:
```json
{"symbol": "EURUSD", "state": "UNKNOWN",
 "reason": "No full economic-calendar provider is configured (ECONOMIC_CALENDAR_PROVIDER / _API_KEY); only official_schedule data is available.",
 "nextEvent": null, "activeEvents": [], "coverage": {"complete": false, "level": "partial", "providers": ["official_schedule"]},
 "score": {"score": null, "state": "UNKNOWN"}}
```
