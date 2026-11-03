# Module 04 – Trading Sessions and Market Intelligence Engine

**Version:** module 4.0.0, scoring methodology 4.0.0.
**Status:** implemented and validated against the real Module 01 database (Exness demo candles), 2026-11-03.

## Purpose

Module 04 gives the future AI Decision Engine (and the human operator) the market context needed to tell apart:
- a technically attractive setup during a quiet market;
- the same setup occurring minutes before a major economic announcement.

It answers:
- **Time:** which sessions are open, and which overlap or kill zone applies.
- **Levels:** where the session, day, week and month highs and lows are, and how price has interacted with them.
- **Scheduled risk:** what is scheduled, and how close it is.
- **Policy:** what the central banks have done and when they meet next.
- **Volatility:** how volatile the instrument is compared with its own history.
- **Conditions:** which market-condition tags apply, and the evidence for each.

> **What it is not.** Module 04 is read-only and analytical. It places no orders, changes no account settings, and gives no trade signals or direction predictions. Scores are explainable indices, not probabilities. Every API response and CLI report carries this disclaimer.

## Architecture

```
aurum/market_intelligence/
  config.py              YAML config -> MIConfig (sessions, kill zones, overlaps, windows, scoring); IANA zones validated
  sessions.py            session / kill-zone / overlap engine (DST, midnight crossing, weekends, holidays)
  candles.py             Module 01 candle access, closed candles only (close_t <= as_of)
  levels.py              session, day (17:00 New York roll), week and month levels; D1 fallback
  liquidity.py           wick sweep / confirmed breakout / false-breakout candidate / unconfirmed / range expansion
  statistics.py          rolling session statistics (5/10/20/30/60), breakout frequency, daily/weekly ranges
  volatility.py          ATR (Module 03's Wilder ATR), realised volatility, causal percentiles, regimes
  economic_calendar.py   provider-independent event model, classifier, impact mapper, event-risk states
  providers.py           official_schedule, FMP, Finnhub, Trading Economics, FRED release dates
  official_calendars.py  keyless agency release calendars: BLS, BEA, Census, ONS, Eurostat
  meetings.py            central-bank meeting schedules read from the banks' own calendars (check / update)
  sentiment.py           rule-based headline tone (lexicon_v1), terms listed
  macro.py               official central-bank rate sources, decisions, meetings, indicator vintages
  news.py                official central-bank RSS feeds (+ Finnhub news when keyed)
  conditions.py          market-condition tags with evidence
  scoring.py             versioned, explainable scores
  module_adapters.py     optional Module 02 / 03 integration (never required)
  http.py                provider HTTP client: retries, backoff, Retry-After, secret scrubbing
  db_models.py           18 new tables (no change to Module 01-03 tables)
  repository.py          persistence: idempotent upserts, calendar revisions, point-in-time reads
  service.py             MarketIntelligenceService: the single entry point for the API, CLI and n8n
  cli.py                 `python -m aurum.cli intelligence ...`
aurum/api/routes/market_intelligence.py   REST API under /api/v1/intelligence
config/market_intelligence.yaml           sessions, windows, thresholds, classification, impact map
config/central_banks.yaml                 central-bank sources, meeting calendars (with verified_on dates)
sql/migrations/0004_market_intelligence.sql
aurum-frontend/src/pages/MarketIntelligencePage.tsx, components/intelligence/*
```

### Data flow
1. Module 01 candles from PostgreSQL/TimescaleDB feed `RepositoryCandleSource`, filtered to closed candles at `as_of`.
2. From those candles, Module 04 computes:
   - sessions and levels;
   - liquidity;
   - statistics;
   - volatility.
3. The calendar comes from configured providers. It is normalised, classified, mapped to instruments and upserted with revision history.
4. Macro data and news come from official sources, stored with provenance and with `known_from` (point-in-time) timestamps.
5. `analyze(symbol, as_of)` combines everything with the optional Module 03 structure and Module 02 COT context into conditions and scores. Results can be persisted.

## Principles
- **No look-ahead.** Everything is computed "as of" a timestamp:
  - candles count only once closed;
  - calendar events are shown as AURUM had observed them, and actual values stay hidden until release;
  - rates and indicators are shown only once `known_from` has passed.
  - Tests check this (see [MODULE_04_TESTING.md](MODULE_04_TESTING.md)).
- **No fabrication.**
  - A missing provider is reported as unavailable.
  - Missing consensus or actual values stay `null`, never zero.
  - A central bank without a configured source is reported as "not sourced" (all eight are sourced now).
  - When calendar coverage is incomplete for an instrument's currencies, event risk is `UNKNOWN`, never `NORMAL`. Official-schedule coverage allows `NORMAL` and lists its gaps.
- **Provenance everywhere.** Each record carries:
  - the provider and source URL;
  - the retrieval time;
  - the calculation method;
  - the config fingerprint and methodology version.
- **Additive.** Module 01–03 code and tables are untouched. Integrations go through adapters and are optional.

## Quick start
```powershell
python -m aurum.cli init-db                      # applies migration 0004 (additive)
python -m aurum.cli intelligence check           # configuration, tables, candles, providers
python -m aurum.cli intelligence calendar-update # central-bank + agency calendars (+ provider if configured)
python -m aurum.cli intelligence meetings-check  # configured vs official meeting dates
python -m aurum.cli intelligence macro-update    # official central-bank rates + indicators
python -m aurum.cli intelligence news-update     # official central-bank RSS feeds
python -m aurum.cli intelligence analyze --symbol EURUSD
```
In the frontend, use **Intelligence → Market Intelligence** and **Intelligence → Trading Sessions**.

## Related documents
- [TRADING_SESSIONS.md](TRADING_SESSIONS.md)
- [ECONOMIC_CALENDAR.md](ECONOMIC_CALENDAR.md)
- [MACRO_INTELLIGENCE.md](MACRO_INTELLIGENCE.md)
- [VOLATILITY_ENGINE.md](VOLATILITY_ENGINE.md)
- [MARKET_CONDITIONS.md](MARKET_CONDITIONS.md)
- [MODULE_04_API_REFERENCE.md](MODULE_04_API_REFERENCE.md)
- [MODULE_04_CONFIGURATION.md](MODULE_04_CONFIGURATION.md)
- [MODULE_04_DATABASE.md](MODULE_04_DATABASE.md)
- [MODULE_04_TESTING.md](MODULE_04_TESTING.md)
- [MODULE_04_INTEGRATION.md](MODULE_04_INTEGRATION.md)

## Limitations addressed (2026-11-03) and what remains
| Former limitation | What was done | What remains |
|---|---|---|
| No economic calendar without paid keys, so event risk was mostly UNKNOWN | Five keyless official agency calendars (BLS, BEA, Census, ONS, Eurostat), plus seven central banks, and a new `official` coverage level. EURUSD, GBPUSD, XAUUSD and US30 now get real `NORMAL` / approaching / release states, with gaps listed | No consensus or actual values (those need a full provider: FMP plan, Finnhub premium, Trading Economics). Private surveys (ISM, S&P Global PMI, UMich, Conference Board, ZEW, Ifo) and speeches are not in official calendars |
| Meeting times typical, not exact | SNB exact (official iCal); BoJ marked `date_only`, with a conservative end-of-day `known_from`; agency releases exact (Eurostat typical 11:00) | Fed, ECB, BoE, RBA and BoC pages give dates only, so their times stay `typical` (the usual announcement time) |
| Meeting dates added by hand each year | `meetings-check` / `meetings-update` read the banks' own pages and iCal; status warns 120 days before a schedule runs out | RBNZ (its site blocks automated clients) |
| Statistics windows insufficient (about 1 month of H1) | Module 01 history backfilled read-only from the demo account: D1 since 2018 (US30 since 2023), H1 about 15 months, M15 about 6 months, M5 about 2 months. The London 60-session window is now `sufficient` for EURUSD, GBPUSD and XAUUSD | US30's London window has 17 sessions (the index trades few London hours) |
| Analysis took 4–14 s, with no caching | Cached results per as_of minute (exact, since candles close on whole minutes), cached Module 03 calls, incremental candle cache with a fast read path, binary-search slicing. A repeat takes about 0.01 s; Module 04's own work takes about 2.8 s | A cold run with Module 03 still takes 9–14 s on the deeper history, because Module 03's own analysis takes about 7 s; Module 03 is not modified. `includeModule03=false` gives about 2.8 s |
| BoJ and RBNZ rates not sourced | BIS `WS_CBPOL` (official rates as compiled by the BIS) | BIS publishes about 1–2 weeks after a change |
| US indicators needed FRED | BLS API v1 (CPI, core CPI, NFP, unemployment), Treasury yields, ONS UK CPI, all keyless | Revision vintages still need FRED |
| EA HICP stale (ECB ICP stopped at 2025-12) | Eurostat `prc_hicp_minr` (ECOICOP v2), latest 2026-09 = 3.8 | — |
| News had no sentiment | Rule-based headline tone with matched terms, summarised per currency and attached to every analysis | Lexicon method: short headlines, simple negation only; it is context, not a signal |

Other notes:
- **Session definitions are configurable reference periods,** not universal market rules.
