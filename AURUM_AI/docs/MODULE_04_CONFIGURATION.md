# Module 04 configuration

There are two YAML files and a set of environment variables. Every YAML key is optional: missing keys use the built-in defaults in `aurum/market_intelligence/config.py`. Invalid IANA time zones or times are rejected at load with a `ConfigError`.

The effective configuration is fingerprinted (a SHA-256 prefix, `configFingerprint`). The fingerprint is stored with every run, so persisted results can be traced to their settings. View the configuration with `GET /intelligence/config`.

## Environment variables (`.env`, git-ignored)
| Variable | Default | Purpose |
|---|---|---|
| `ECONOMIC_CALENDAR_ENABLED` | `true` | `false` turns off third-party calendar providers (official schedules stay on) |
| `ECONOMIC_CALENDAR_PROVIDER` | unset | `fmp` / `finnhub` / `tradingeconomics` |
| `ECONOMIC_CALENDAR_API_KEY` | unset | provider key (`SecretStr`; never logged or returned) |
| `ECONOMIC_CALENDAR_BASE_URL` | provider default | override, e.g. for a proxy or test server |
| `FRED_API_KEY` | unset | free key: US release dates and indicator vintages |
| `OFFICIAL_DATA_ENABLED` | `true` | keyless central-bank, statistics and agency-calendar sources (BLS, BEA, Census, ONS, Eurostat, BIS, Treasury) |
| `NEWS_ENABLED` | `true` | official central-bank RSS feeds |
| `AURUM_ADMIN_API_KEY` | unset | required for the POST (refresh / analyze) endpoints |

## `config/market_intelligence.yaml`
| Section | Keys |
|---|---|
| `sessions`, `kill_zones` | `timezone`, `open`, `close` (local `HH:MM`; close < open crosses midnight), `weekdays`, `holidays` (local dates), `pre_minutes`, `post_minutes` |
| `instrument_kill_zones` | `{SYMBOL: [kill_zone, ...]}` replaces the global list for that symbol |
| `overlaps` | `name`, `sessions: [a, b]`, optional `transition_minutes` (used when the sessions do not intersect) |
| `trading_day` | `timezone`, `roll` (FX day roll, default 17:00 America/New_York) |
| `levels` | `sessions`, `timeframes` (finest first), `daily_fallback_timeframe` |
| `liquidity` | `confirm_bars`, `false_breakout_bars`, `expansion_ratio`, `expansion_lookback`, `module03_timeframe`, `module03_match_minutes`, `pairs` |
| `statistics` | `windows`, `timeframe`, `min_sample` |
| `volatility` | `atr_period`, `returns_window`, `percentile_lookback`, `min_bars`, `regimes`, `expansion`, `abnormal_range_atr`, `abnormal_return_z` |
| `event_risk` | `windows.{high,medium,low}.{approaching,high_risk,release,post}`, `reported_windows`, `post_reported_windows`, `instrument_overrides`, `history_window_minutes`, `stale_after_hours` |
| `conditions` | `timeframe`, `efficiency_window`, `trending_efficiency`, `ranging_efficiency`, `transition_minutes`, `liquidity_lookback_hours` |
| `scoring` | `methodology_version`, `event_risk.{importance,proximity,post_decay,weights}`, `session_activity.weights`, `conditions.min_confidence` |
| `impact` | `currencies.{CCY}: [{instrument, relation, categories?, explanation}]`, `extra: [...]` with `currencies`, `categories`, `min_importance` |
| `classification` | `rules: [{category, subcategory, patterns, default_importance, currencies?}]`, `country_currency` |
| `fred_releases` | `releases: [{name pattern, category, subcategory, importance, time, timezone}]` |
| `macro` | `stale_after_days.{policy_rate,monthly,daily}`, `rate_history_start` |
| `official_calendars` | `stale_after_hours` (168); `coverage.{CCY}: [providers]`; `gaps.{CCY or other}: [text]`; `providers.<bls_ics / bea_ics / census_calendar / ons_releases / eurostat_ical>` with `enabled`, `url`, `releases: [{pattern, category, subcategory, importance}]`, plus `queries` (ONS) and `require_category`, `all_day_time`, `all_day_timezone`, `uid_unstable` (Eurostat) |
| `performance` | `analysis_cache_seconds` (60) |

Change `scoring.methodology_version` whenever weights or formulas change.

## `config/central_banks.yaml`
| Section | Content |
|---|---|
| `banks.{fed,ecb,boe,boj,rba,rbnz,boc,snb}` | `name`, `currency`, `timezone`, `announcement_time` (null = no fixed time), `time_precision` (exact / typical / date_only), `rate_source` (nyfed_effr_target, ecb_data_portal, boe_iadb, rba_a2, boc_valet, snb_data_portal, bis_cbpol; null = not sourced), `official_url`, `meetings` (official dates), `verified_on`, `note` |
| `official_feeds` | RSS feeds: `id`, `url`, `bank`, `category` |
| `official_series` | keyless official indicator series: `source` = eurostat (`dataset`, `params`), bls (`series`), treasury (`column`), ons (`path`) or ecb_data_portal (`flow`, `key`) |
| `fred_series` | FRED series IDs (used only with `FRED_API_KEY`) |

**Maintenance.** Meeting dates come from the banks' published calendars and are marked with `verified_on`. When a bank publishes a new year, run `python -m aurum.cli intelligence meetings-update`: it writes the official dates to `config/central_banks.meetings.yaml`, which is merged in at load (union only). `meetings-check` reports differences without writing anything. Dates are never inferred.

## Instrument settings
Symbols and trading hours come from Module 01's `config/instruments.yaml`. Module 04 reads them and does not duplicate them.
