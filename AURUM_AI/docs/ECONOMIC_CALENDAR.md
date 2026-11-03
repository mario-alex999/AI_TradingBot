# Economic calendar and event risk

## Providers
The calendar is provider-independent: every provider is normalised into one `EconomicEvent` model. AURUM never invents events. With no provider configured, the calendar shows only what the official schedule supplies, and connectivity is reported as unavailable or partial.

| Provider | Type | Access requirement (as researched 2026-11-03) | Env |
|---|---|---|---|
| `official_schedule` | central-bank decisions from `config/central_banks.yaml` (Fed, ECB, BoE, BoJ, RBA, BoC, SNB; `verified_on` per bank, plus the `meetings-update` overlay) | none: dates from the banks' official calendars | always on |
| `bls_ics` | US Bureau of Labor Statistics release calendar, iCalendar `https://www.bls.gov/schedule/news_release/bls.ics`: Employment Situation, CPI, PPI, JOLTS, ECI, Productivity, Import/Export prices (exact ET times) | none (published for subscription) | `OFFICIAL_DATA_ENABLED` |
| `bea_ics` | US Bureau of Economic Analysis, iCalendar `https://www.bea.gov/news/schedule/ics/online-calendar-subscription.ics`: GDP, Personal Income and Outlays (PCE), Trade (exact UTC times) | none | `OFFICIAL_DATA_ENABLED` |
| `census_calendar` | US Census Bureau economic-indicator calendar, `https://www.census.gov/economic-indicators/calendar-listview[-YYYY].html`: Retail Sales, Durable Goods, housing, construction, advance indicators (exact ET times) | none | `OFFICIAL_DATA_ENABLED` |
| `ons_releases` | UK ONS release calendar API, `https://api.beta.ons.gov.uk/v1/search/releases`: CPI, Labour Market, GDP, Retail Sales, Public Finances (exact UTC times; cancelled/postponed honoured) | none | `OFFICIAL_DATA_ENABLED` |
| `eurostat_ical` | Eurostat release calendar, iCalendar `https://ec.europa.eu/eurostat/o/calendars/eventsIcal`: euro-indicator releases only (flash HICP, HICP, GDP, unemployment, retail, industry, PPI, trade). Dates only; time = Eurostat's standard 11:00 Luxembourg release time (`typical`) | none | `OFFICIAL_DATA_ENABLED` |
| `fmp` | full calendar, `GET {base}/stable/economic-calendar?from&to&apikey` | API key. The FMP plan must include the economic calendar; the plan pages could not be read automatically, so check your plan | `ECONOMIC_CALENDAR_PROVIDER=fmp` |
| `finnhub` | full calendar, `GET {base}/calendar/economic?from&to&token` | **premium** endpoint | `ECONOMIC_CALENDAR_PROVIDER=finnhub` |
| `tradingeconomics` | full calendar, `GET {base}/calendar/country/All/{d1}/{d2}?c=KEY&f=json` | **paid subscription** | `ECONOMIC_CALENDAR_PROVIDER=tradingeconomics` |
| `fred_release_dates` | US release dates (CPI, Employment Situation, GDP, PIO, PPI, Retail Sales, Claims) | free FRED API key | `FRED_API_KEY` |

### Environment variables
- `ECONOMIC_CALENDAR_ENABLED` (default `true`)
- `ECONOMIC_CALENDAR_PROVIDER`
- `ECONOMIC_CALENDAR_API_KEY`
- `ECONOMIC_CALENDAR_BASE_URL` (optional override)
- `FRED_API_KEY`

Keys are read from the environment (`.env`, git-ignored) as `SecretStr`. They never appear in logs, errors, API responses or the database:
- the HTTP client logs URLs without query strings;
- error text is scrubbed of every configured secret.

**Official agency calendars** keep only releases matched by an explicit rule (`official_calendars.providers.<name>.releases` in `market_intelligence.yaml`: pattern, category, subcategory, importance). The importance is AURUM's documented rating; the agencies publish none. Unmatched releases are ignored, never guessed. These calendars carry **schedules only**: `consensus`, `actual` and `previous` stay empty (never zero, never invented). Validated against real responses on 2026-11-03: BLS 13, BEA 6, Census 12, ONS 10, Eurostat 17 events for 27 Oct – 18 Dec 2026, re-ingested idempotently (0 new on the second run).

**No full provider has been validated with a real response on this installation.** The adapters are tested against recorded response *shapes* using mocked HTTP. "Coverage: full" appears only after a configured full provider has actually returned data.

### Time precision
| Precision | Meaning |
|---|---|
| `exact` | the provider gave a time |
| `typical` | the usual announcement time (most central banks, Eurostat 11:00, FRED dates) |
| `date_only` | no time known; the event counts as "approaching" for the whole day |

## Event model
Each event carries:
- `provider`, `provider_event_id`, `name`, `country`, `currency`, `scheduled_at` (UTC), `source_timezone`, `time_precision`;
- `importance` and `importance_source` (`provider` or `rule:<subcategory>`), `category`, `subcategory`;
- `actual`, `consensus`, `previous`, `revised_previous`, `unit`, `status`;
- `source_url`, `provider_updated_at`, `retrieved_at`, `affected_instruments`, `raw`.

### Values
- `parse_value` turns `"0.3%"` into 0.3, `"215K"` into 215000 and `"1,234.5"` into 1234.5.
- An empty string, `"-"`, `"n/a"` or `null` becomes `None`, **never zero**.
- Non-numeric text is kept verbatim.

### Status
| Status | When it is set |
|---|---|
| `scheduled` | default |
| `released` | an actual value was received |
| `revised` | a different actual value was received later |
| `delayed` | the event moved later with no actual |
| `cancelled` | the provider marked the event cancelled |

### Identity
Events are keyed by `(provider, provider_event_id)`. Providers without IDs get a derived ID: SHA-1 of provider, country, name and UTC *date*.
- A time change within the same day keeps the ID and is recorded as `rescheduled`.
- A move to another day produces a new ID. This is a documented limitation.

### Revisions
Every ingestion is idempotent:
- unchanged events count as `unchanged`;
- new events insert a `created` revision;
- changes insert a `values`, `rescheduled`, `status` or `metadata` revision with the old and new values and a full snapshot.

`actual_first_seen_at` records when AURUM first saw the actual value.

## Classification and impact
- **Classification.** Rules in `classification.rules` are regex patterns plus currencies. A rule applies only when a pattern matches **and** the currency is listed (if the rule lists currencies). Rules set `category`, `subcategory` and a default importance. Importance supplied by the provider always takes priority.
- **Currency.** The currency comes from the provider, or from the configured `country_currency` map. Unknown countries get no currency, never a guess.
- **Impact.** `impact` maps currency → instrument:
  - `direct`: the currency is a leg of the instrument;
  - `macro`: an explained, category-limited relevance. For example, USD employment, inflation, Fed, growth or fiscal releases affect US30, and major non-US central-bank decisions affect XAUUSD.

  **No direction is ever assigned:** `direction` is always `null`. AURUM states relevance, not the expected reaction.

## Event-risk states
Windows are in minutes and depend on importance (`event_risk.windows`, with per-instrument overrides):

| Importance | approaching | high_risk | release | post |
|---|---|---|---|---|
| high | 1440 | 60 | 5 | 60 |
| medium | 240 | 30 | 5 | 30 |
| low | 60 | 0 | 5 | 15 |

| State | Condition (m = minutes until release) |
|---|---|
| `HIGH_EVENT_RISK` | 0 < m ≤ high_risk |
| `EVENT_APPROACHING` | high_risk < m ≤ approaching (`date_only`: the whole day) |
| `RELEASE_WINDOW` | −release < m ≤ 0 |
| `POST_EVENT_VOLATILITY` | −(release + post) < m ≤ −release |
| `NORMAL` | outside every window, **and** coverage is `full`, or `official` with every currency of the instrument covered (the response lists the remaining `gaps`) |
| `UNKNOWN` | no event inside any window, **and** coverage is partial, none, stale, or `official` without one of the instrument's currencies |

How the instrument state is determined:
1. The instrument takes the highest-ranked state among the events that affect it. Ties go to the higher importance.
2. Cancelled events carry no risk.
3. Coverage levels (only provider successes at or before the analysis time count):
   - `full`: a full third-party provider succeeded within `event_risk.stale_after_hours` (36);
   - `official`: per currency, every agency calendar listed in `official_calendars.coverage` (USD: BLS + BEA + Census; EUR: Eurostat; GBP: ONS) **and** the central-bank schedule succeeded within `official_calendars.stale_after_hours` (168). An instrument is covered when all currencies that drive it (`impact.currencies`) are covered. `coverage.gaps` lists what official calendars do not contain (private surveys such as ISM, S&P Global PMI, UMich, Conference Board, ZEW, Ifo; weekly jobless claims unless `FRED_API_KEY` is set; speeches; RBNZ dates; consensus values);
   - `partial`: some official data only; `none`: nothing.

The response also includes `historical_context`: the mean post-release range ratio of past releases of the same event type, computed from stored candles.

## Point-in-time reads
`calendar(..., as_of=T)` and `event_risk(symbol, T)` work like this for historical T:
- each event is rebuilt from the last revision AURUM had observed at or before T;
- events first seen after T are excluded;
- `actual` is hidden while `scheduled_at > T`.

## Operations
```powershell
python -m aurum.cli intelligence calendar-update --days-back 7 --days-ahead 21   # exit code 1 if a provider failed
python -m aurum.cli intelligence calendar --days 7 --currency USD --importance high
python -m aurum.cli intelligence calendar-upcoming --hours 48
```
- Ingestions are logged in `economic_calendar_ingestion_logs`.
- Provider health is in `economic_calendar_sources`.
- How provider errors are handled:

| Error | Handling |
|---|---|
| 429 | honours `Retry-After`, with exponential backoff and max 3 retries |
| 5xx, timeouts | backoff, then retry |
| 401, 403 | reported as `auth`, never retried |
| unexpected payload | reported as `parse`, never treated as an empty success |
