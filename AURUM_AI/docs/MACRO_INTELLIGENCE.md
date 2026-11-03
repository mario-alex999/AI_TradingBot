# Macro intelligence: central banks, indicators, news

## Central-bank policy rates (official sources only)
All sources below are keyless official data services, verified reachable from this installation on 2026-11-03.

| Bank | Source | Series | Notes |
|---|---|---|---|
| Fed | NY Fed Markets API, `markets.newyorkfed.org/api/rates/unsecured/effr/search.json` | `targetRateFrom` / `targetRateTo` | target range (lower–upper); EFFR kept in `detail` |
| ECB | ECB Data Portal | FM `B.U2.EUR.4F.KR.DFR.LEV` (+ MRR_FR, MLFR) | deposit facility rate as the policy rate |
| BoE | Bank of England IADB | `IUDBEDR` | Bank Rate |
| RBA | RBA statistical table A2 (csv) | cash rate target | dates are **effective** dates (the day after the decision) |
| BoC | Bank of Canada Valet | `V39079` | target for the overnight rate |
| SNB | SNB data portal, cube `snbgwdzid`, `D0 = LZ` | SNB policy rate | |
| BoJ | BIS central-bank policy-rate dataset, `stats.bis.org/api/v1/data/WS_CBPOL/D.JP` (csv) | official BoJ policy rate as compiled by the BIS | BIS publishes with a lag of about 1–2 weeks; the BIS compilation note is kept in `detail` |
| RBNZ | BIS `WS_CBPOL/D.NZ` | Official Cash Rate | rbnz.govt.nz refuses automated clients (HTTP 403), and AURUM does not circumvent that; the BIS republishes the OCR |

**AURUM does not infer policy rates from news headlines.**

### Storage and decisions
- The first stored observation for a date wins. A later conflicting value is recorded as a data-quality issue, not overwritten.
- **Decisions** are derived from the official series:
  - `changed`: the rate differs from the previous observation;
  - `held`: no change around a configured scheduled meeting.
- `matched_meeting` says whether a change aligns with a configured meeting date. A change on the meeting day, or up to 3 days after it, counts as aligned.

### Point in time (`known_from`)
- **Change matched to a meeting:** the bank's announcement time on the meeting day (`timezone` + `announcement_time` in `central_banks.yaml`). Example: Fed, 14:00 New York.
- **Meeting without a fixed announcement time** (`time_precision: date_only`, BoJ): 23:59 local time on the meeting day, so a decision is never visible before it could have been announced.
- **No matching meeting:** local midnight of the effective date.
- `rates(..., as_of)` and `decisions(..., as_of)` return only rows with `known_from <= as_of`.

### Meetings
- Official 2026–2027 meeting dates are configured for Fed, ECB, BoE, BoJ, RBA, BoC and SNB (`verified_on: 2026-11-03`; 2027 FOMC dates are tentative per the Fed). Time precision per bank: SNB `exact` (09:30, from its official iCal), BoJ `date_only` (no fixed time), the others `typical`.
- `next_meeting` returns the next announcement after `as_of` with its `time_precision`.
- **No more manual yearly updates:** `python -m aurum.cli intelligence meetings-check` reads the banks' own calendars (Fed, ECB, BoE, RBA, BoC and BoJ pages, SNB iCal) and reports dates missing from the configuration and configured dates no longer on the page. `meetings-update` writes the official dates to `config/central_banks.meetings.yaml`, which the loader merges in as a union (it never removes a configured date). Run on 2026-11-03, it confirmed every configured date and added Fed meetings back to 2021 plus the ECB's 2028 dates.
- `intelligence check` and `/status` (`meetingSchedules`) warn when a bank's configured meetings run out within 120 days.
- RBNZ meeting dates are not configured, because the RBNZ site blocks automated access. Add them by hand if needed.

## Macro indicators
| Series | Source | Point-in-time rule |
|---|---|---|
| `EA_HICP_YOY` | Eurostat `prc_hicp_minr` (ECOICOP v2, `geo=EA, coicop18=TOTAL, unit=RCH_A`). It replaces the ECB ICP series, which stopped at 2025-12 with the ECOICOP change | no vintages, so `known_from` = first retrieval by AURUM |
| `US_CPI_SA`, `US_CORE_CPI_SA`, `US_NFP`, `US_UNRATE` | BLS Public Data API v1 (keyless; BLS allows 25 requests a day) | first retrieval |
| `US_2Y`, `US_10Y` | US Treasury daily par yield curve CSV | first retrieval |
| `UK_CPI_YOY` | ONS time series D7G7 / MM23 | first retrieval |
| CPIAUCSL, CPILFESL, PCEPI, PAYEMS, UNRATE, GDPC1, RSAFS, UMCSENT, DGS10, DGS2 | FRED / ALFRED (needs `FRED_API_KEY`) | every vintage stored; `known_from` = end of the `realtime_start` day in New York |

- Indicators are flagged `stale` when the latest period is older than `macro.stale_after_days` (policy_rate 7, monthly 75, daily 7).
- The keyless BLS, Treasury, ONS and Eurostat series cover the core US, UK and euro-area indicators without any key. The FRED series stay optional: with `FRED_API_KEY` they add full revision vintages. Without it they show `not_configured`.
- Revised values never overwrite earlier vintages, so a historical `as_of` sees the value as it was known then.

## News
| Source | Type | Notes |
|---|---|---|
| Fed, ECB, BoE RSS feeds (`official_feeds`) | `official_announcement` | headline, link and publication time exactly as published |
| Finnhub general news (only with a Finnhub key) | `reported_news` | |

- AURUM does not write, summarise or rewrite headlines, and generates none.
- Feed failures are reported per feed and never filled with substitute content.
- Items are deduplicated by `(source, guid)`.

### Headline tone (news intelligence)
Every headline carries `tone`, computed by `sentiment.py` (method `lexicon_v1`):
- central-bank items get `policy_tone` (hawkish / dovish / neutral / unclear);
- other headlines get `risk_tone` (positive / negative / neutral / unclear).

The tone is AURUM's rule-based reading of the wording. It is not part of the news and not a forecast:
- each label lists the exact matched terms;
- simple negation ("will not raise rates") is not counted;
- headlines with no policy wording stay `unclear`.

`GET /news` adds a `sentimentSummary` per currency. Each analysis carries `news`: the instrument's headlines from the previous 72 h, with tone, as of the analysis time.

```powershell
python -m aurum.cli intelligence macro-update
python -m aurum.cli intelligence macro
python -m aurum.cli intelligence news-update
```
