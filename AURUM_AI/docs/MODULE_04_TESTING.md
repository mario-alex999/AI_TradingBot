# Module 04 testing

## How to run
```powershell
.venv\Scripts\python -m pytest tests/market_intelligence -q      # Module 04 only (~2.5 min)
.venv\Scripts\python -m pytest -q                                # whole backend
cd ..\aurum-frontend; npm run typecheck; npm run lint; npm test; npm run build
```

## Evidence types
| Type | What it is |
|---|---|
| **Automated tests** | synthetic, deterministic candles (`mi_helpers.walk`, a seeded random walk) and hand-built fixtures; labelled as fixtures, never presented as market data |
| **Mocked integration tests** | provider HTTP through `httpx.MockTransport`, so no network is used. Payload shapes follow the providers' documented formats; they are not real responses. SQLite databases live in `tmp_path` |
| **Real-data validation** | Module 01's real Exness demo candles in PostgreSQL/TimescaleDB, plus real responses from the official central-bank sources (2026-11-03) |

## Test suites (`tests/market_intelligence/`, 123 tests)
| File | Tests | Covers |
|---|---|---|
| `test_mi_sessions.py` | 13 | London BST/GMT, US DST three weeks before the UK, Sydney southern-hemisphere DST, midnight-crossing kill zone, weekends and holidays, open/pre/closed/market_closed (Sydney opening before the FX reopen), previous/next/current occurrence, DST-gap overlap (12–17 UTC), Asian–London transition window, per-instrument kill zones, snapshot, history, invalid time zone |
| `test_mi_levels_liquidity.py` | 12 | previous-session levels equal the raw candle extremes; current levels provisional and closed-candle only; **no look-ahead** (future spike, truncated vs full history); 17:00 New York trading-day roll in EDT/EST; Monday → Friday previous day; D1 month fallback; wick sweep, confirmed breakout, false-breakout candidate, unconfirmed, low-side symmetry; New York vs London cut at the NY open; no liquidity event after `as_of` |
| `test_mi_calendar.py` | 33 | value parsing (missing ≠ 0); classifier pattern + currency rule and provider-importance priority; country → currency (no guessing); impact mapping with `direction` always null; FMP parsing; 403 auth error without leaking the key; 429 retries honouring `Retry-After`; timeout; Finnhub non-premium payload is an error, not an empty success; official schedule; **idempotent ingestion and revisions**; point-in-time view (later actuals and later-seen events hidden); rescheduled → `delayed`; failed provider logged without secrets, coverage partial, risk UNKNOWN; full coverage → HIGH_EVENT_RISK, then stale → UNKNOWN; all state windows; cancelled events; partial coverage → UNKNOWN; actual hidden before release |
| `test_mi_analytics.py` | 23 | regime cut-offs; expansion detection; insufficient history → UNKNOWN with a stable shape; **causal volatility percentile**; efficiency ratio; statistics windows and `sufficient` flags; event-risk score null when UNKNOWN, modifier bounds 0.7–1.3; session-activity excludes missing components; macro `known_from` (meeting announcement / effective midnight); changed and held decisions; next meeting; FRED vintage time; BoC Valet parsing; RSS headlines verbatim; feed failure reported, not filled |
| `test_mi_official.py` | 30 | BLS iCalendar (folded lines, escapes, US-Eastern → UTC across DST); BEA UTC times; Eurostat all-day dates → typical 11:00 Luxembourg and the euro-indicator category filter; Eurostat ids stable when the feed regenerates UIDs; Census rows plus the year-page 404 fallback; Census layout change is an error, not an empty success; ONS cancelled/postponed statuses, companion releases excluded, null results; unmatched releases ignored; no consensus or actual values invented; **official coverage → NORMAL with gaps**; agency release detected; failed agency → its currency uncovered → UNKNOWN; coverage point-in-time and stale after 168 h; BIS policy-rate parser; BoJ `date_only` known from 23:59 Tokyo; Fed/BoJ/RBA meeting-page parsers (cross-month, notes ignored); compare, overlay union, staleness, RBNZ reported unavailable; Eurostat JSON-stat (gaps stay gaps, single-series guard); BLS (M13 and missing excluded, daily-limit error); Treasury; ONS series; headline tone (hawkish/dovish/neutral, negation, terms listed); binary-search window equals mask; candle cache cut at as_of; Module 03 cache (failures not cached); analysis cache hit, invalidated by a refresh, bypassed when persisting; long ids bounded deterministically |
| `test_mi_api_cli.py` | 12 | read endpoints (camelCase, provenance, history defaults); calendar without a provider shows nothing and risk is UNKNOWN; 404/422 mapping; admin key required, refresh runs as a job; **no trading routes**; 503 when the schema is missing (sessions still work); persisted analysis is idempotent; **end-to-end no-look-ahead** of the full analysis; all CLI commands, including export, and errors; `calendar-update` exits 1 on failure without printing the key; **migration additive-only** and matching the 18 ORM tables |

Frontend: `src/test/intelligence.test.tsx` has 7 tests:
- demo mode fabricates nothing and makes no request;
- calendar rows show blank (not zero) missing values, plus the partial-coverage warning;
- filters are sent as query parameters, and changing a filter refetches;
- a disconnected backend is reported, with no demo fallback;
- an unsourced central bank shows "Not Sourced" with no rate;
- official-schedule coverage is labelled, and its uncovered currencies and gaps are listed.

## Results (executed 2026-11-03)
| Run | Result |
|---|---|
| `pytest tests/market_intelligence` | **123 passed** |
| `pytest` (whole backend) | **389 passed, 1 skipped** (the skip is the opt-in live CFTC test); the previous baseline was 266 passed, 1 skipped, so there are no regressions |
| frontend `tsc -b --noEmit` | no errors |
| frontend `eslint . --max-warnings=0` | clean |
| frontend `vitest run` | **81 passed, 0 failed** (12 files; this run overlapped the backend suite and had no timeouts) |
| frontend `vite build` | success |

## Real-data validation (2026-11-03, real database)
- **CLI and API:** every command and endpoint was run against the real database.
  - DST: on 2026-10-27 New York opened at 12:00 UTC (EDT), and after the change it opens at 13:00 UTC (EST).
  - Levels: EURUSD levels came from M5, the previous week from H1, the previous month from D1.
  - Volatility: XAUUSD H1 HIGH (ATR percentile 89).
- **No look-ahead on real candles:**
  - Method: `analyze(T = 2026-11-02 14:00 UTC)` was run with the full stored history, which includes about 330 M5 and 24–28 H1 candles after T, and again with the same candles cut off at T.
  - Result: the levels, liquidity, volatility, statistics and conditions were **identical for EURUSD, GBPUSD, XAUUSD and US30**.
- **Macro:** official central-bank rates were retrieved from real responses for Fed, ECB, BoE, RBA, BoC and SNB.
- **News:** 80 official headlines were stored.
- **Calendar:** real responses from the official sources on 2026-11-03, for 27 Oct – 18 Dec 2026: central-bank schedule 12 events, BLS 13, BEA 6, Census 12, ONS 10, Eurostat 17.
  - A second run inserted 0 new events (idempotent). The Eurostat feed regenerates its UIDs, so its events are now keyed by title and date.
  - Coverage `official` for EUR, GBP and USD.
  - Real event risk: EURUSD `EVENT_APPROACHING` (euro-area flash HICP, 4 Nov 10:00 UTC); GBPUSD, XAUUSD and US30 `NORMAL`, with gaps listed.
  - **No full calendar provider was tested with a real response**, because no key is configured.
- **Meeting schedules:** `meetings-check` against the official pages and iCal of Fed, ECB, BoE, BoJ, RBA, BoC and SNB matched every configured date. RBNZ is reported unavailable.
- **New macro sources (real responses):**
  - BIS: BoJ 1.25, RBNZ 2.75.
  - Eurostat EA HICP 2026-09: 3.8. BLS CPI-U SA: 336.298. NFP: 159,044k. Unemployment: 4.2.
  - Treasury 2Y / 10Y on 2026-11-02: 4.59 / 4.91. ONS UK CPI 2026-09: 3.5.
- **History backfill (read-only, demo account):**
  - H1: 8,001 candles (about 15 months). D1: 2,500 (since 2018; US30 744). M15 and M5: 12,000 each.
  - The London 60-session statistics window is `sufficient` for EURUSD, GBPUSD and XAUUSD.
- **Performance (real database):**
  - Before the change: 24 s cold for EURUSD on the deeper history.
  - After: 9–14 s cold, of which Module 03 is about 7 s; 2.8 s without Module 03; 0.01 s on a cache hit.
