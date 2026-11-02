# AURUM AI - Module 02: COT Institutional Intelligence Engine

> **Read-only research module.** It never places, modifies or closes trades, never calls MT5 trading
> functions and does not bypass human approval. COT analysis is medium- to long-term positioning context,
> not an entry signal.

## What it does
1. Retrieves CFTC Commitments of Traders data (Legacy, Traders in Financial Futures, Disaggregated - futures
   only) from the official CFTC API, or - automatically, when the API is unreachable - from the official
   CFTC weekly-updated history files.
2. Stores every report with its original CFTC record (`source_payload`), report date, release date/time,
   retrieval and processing timestamps; revisions keep the previous payload.
3. Validates the data (missing fields/values, invalid numbers, negative/zero open interest, accounting
   identities, duplicates, contract-name changes, schema changes, gaps, staleness, unusual changes).
4. Calculates net positioning, weekly changes, % of open interest, 12/24/36/48-month percentiles, COT index,
   z-scores, classifications, change types, participant comparisons and price-position divergence (with
   Module 01 prices) - using only data public at each moment (no look-ahead).
5. Produces instrument intelligence reports that separate CFTC facts, calculated metrics and
   interpretation, with confidence and data-quality limitations.
6. Exposes everything through the CLI (`python -m aurum.cli cot ...`), a FastAPI backend
   (`python -m aurum.api`) and the COT dashboard in `aurum-frontend`.

How COT data works, every formula and every limitation: **[COT_METHODOLOGY.md](COT_METHODOLOGY.md)**.
Exact operating commands: **[COT_OPERATIONS.md](COT_OPERATIONS.md)**.

## Supported report families
| Key | CFTC report | Socrata ID (status) | History from | Official file |
|---|---|---|---|---|
| `legacy` | Legacy - Futures Only | `6dca-aqww` (unverified*) | 1986 | `deacot{year}.zip`, `deacot1986_2016.zip` |
| `tff` | Traders in Financial Futures - Futures Only | `gpe5-46if` (unverified*) | 2006-06 | `fut_fin_txt_{year}.zip`, `fin_fut_txt_2006_2016.zip` |
| `disaggregated` | Disaggregated - Futures Only | `72hh-3qpy` (unverified* candidate) | 2006-06 | `fut_disagg_txt_{year}.zip`, `fut_disagg_txt_hist_2006_2016.zip` |

\* `publicreporting.cftc.gov` returned HTTP 403 to the development PC (2026-11-02), so the API IDs could not
be confirmed live. The history-file names were verified (HTTP 200) and their contents parsed. The IDs are
configuration (`config/cot.yaml`), not code.

## Instruments and futures mappings (verified against the 2025 CFTC files, 2026-11-02)
| AURUM | CFTC contract (code) | Exchange | Families | Headline group |
|---|---|---|---|---|
| EURUSD | EURO FX - CHICAGO MERCANTILE EXCHANGE (`099741`) | CME | Legacy, TFF | Non-commercial / Leveraged Funds |
| GBPUSD | BRITISH POUND - CHICAGO MERCANTILE EXCHANGE (`096742`) | CME | Legacy, TFF | Non-commercial / Leveraged Funds |
| XAUUSD | GOLD - COMMODITY EXCHANGE INC. (`088691`) | COMEX | Legacy, Disaggregated | Non-commercial / Managed Money |
| US30 | DJIA x $5 - CHICAGO BOARD OF TRADE (`124603`) - the E-mini Dow ($5) | CBOT (CME Group) | Legacy, TFF | Non-commercial / Leveraged Funds |

One contract per instrument and family (enforced). Not mapped on purpose: *DJIA Consolidated* (`12460+`,
an aggregate that would double-count `124603`), *Micro E-mini DJIA* (`124608`), *Micro Gold* (`088695`) and
cross rates - different contracts are never combined.

## Database (migration `sql/migrations/0002_cot_engine.sql`, additive)
| Table | Content |
|---|---|
| `cot_datasets` | report families, dataset IDs, endpoint, coverage, verification status |
| `cot_contract_mappings` | AURUM symbol ↔ CFTC contract per family, status, verification source/date |
| `cot_reports` | one row per (family, contract, report date): release date/timestamp/method, retrieval/processing time, source, source file/record id, open interest, verbatim `source_payload`, value hash. Unique `(dataset_id, contract_code, report_date)` |
| `cot_report_revisions` | previous payload whenever the CFTC republishes different numbers |
| `cot_positions` | long / short / spreading / net per trader category (NULL where not reported) |
| `cot_metrics` | all calculated metrics per report and category, with `calculation_version` |
| `cot_ingestion_logs` | every backfill/update/calculate chunk: counts, status, duration, errors |
| `cot_data_quality_issues` | issues with severity info/warning/error/critical, explanation, occurrences, resolution |

Upserts prevent duplicates; each ingestion chunk is one transaction; positions/metrics/revisions cascade
from their report; all timestamps are UTC.

## Installation
Same environment as Module 01 plus `fastapi`, `uvicorn`, `httpx` (in `requirements.txt`):
```powershell
pip install -r requirements.txt
python -m aurum.cli init-db
python -m aurum.cli cot check
python -m aurum.cli cot backfill --years 4
```

## Configuration
* `config/cot.yaml` - source (`auto|socrata|files`), endpoints, retries/backoff, datasets, mappings, release
  overrides, analytics thresholds.
* `.env` - `CFTC_APP_TOKEN` (optional Socrata token), `AURUM_ADMIN_API_KEY` (enables admin API calls),
  `API_HOST`, `API_PORT`, `API_CORS_ORIGINS` (comma-separated frontend origins).

## API (`python -m aurum.api`, docs at `/docs`)
All under `/api/v1`, JSON in camelCase, ISO-8601 UTC timestamps, pagination `{items,total,page,pageSize}`.

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | database + module status |
| GET | `/cot/status?probe=true` | engine status, CFTC connectivity (probe cached 10 min), latest report/release per series, freshness, counts, last ingestion, pending updates, open issues, running jobs |
| GET | `/cot/datasets` | report families, IDs, coverage, categories |
| GET | `/cot/instruments` | instruments and their mappings |
| GET | `/cot/reports` | filters `instrument, contract, reportType, start, end`, paginated |
| GET | `/cot/reports/{id}` | one report with the original CFTC payload, positions, metrics, revisions |
| GET | `/cot/positions` | `reportId` or `instrument[,reportType,reportDate]` |
| GET | `/cot/metrics` | filters `instrument, reportType, category, start, end`, paginated |
| GET | `/cot/history/{instrument}` | chart series (positions + metrics per report) |
| GET | `/cot/analysis/{instrument}?asOf=` | intelligence report |
| GET | `/cot/divergence/{instrument}?reportType&category&asOf` | price vs positioning |
| GET | `/cot/quality` | issues (`severity, instrument, includeResolved`), paginated |
| GET | `/cot/logs` | ingestion history, paginated |
| GET | `/cot/export/{raw|positions|metrics|analysis|quality}` | CSV download |
| GET | `/cot/jobs`, `/cot/jobs/{id}` | background jobs |
| POST | `/cot/backfill` `{years|start,end, reportTypes, resume}` | **admin**, 202 + job |
| POST | `/cot/update` `{reportTypes}` | **admin**, 202 + job |
| POST | `/cot/calculate` `{instruments}` | **admin**, 202 + job |

Admin calls require `X-AURUM-Admin-Key` = `AURUM_ADMIN_API_KEY` (401 if wrong, 503 if no key configured).
Jobs run one at a time on a background thread. When the CFTC is unreachable, stored data is still served and
freshness is reported. No credentials or secrets appear in responses.

## Dashboard
`aurum-frontend` → **COT Analysis** (`/cot`) and **Institutional Intelligence** (`/institutional`). Start the
API, set `VITE_API_BASE_URL=http://localhost:8000/api/v1` and disable demo mode (Settings → Data, or
`VITE_ENABLE_MOCK_DATA=false`). Sections: overview/status, instrument selector with mappings, positioning
summary, per-family trader-category charts, history charts with period selector, intelligence (facts /
metrics / interpretation separated), price-positioning divergence with the dates used, data quality and
updates, historical report explorer (original CFTC record by date), CSV exports. Demo mode is labelled
**DEMO DATA** and never shown as CFTC data.

## Exports
CLI `cot export` or API `/cot/export/{kind}`: raw CFTC records (with payload JSON), normalised positions,
metrics, analysis summaries (instrument, contract, report/release dates, categories, metrics,
interpretation, limitations) and data-quality reports. Every row carries `data_status`.

## Tests
```powershell
python -m pytest                                   # Module 01 + Module 02 (CFTC mocked; no network)
$env:AURUM_LIVE_CFTC="1"; python -m pytest -m live # optional live check against the CFTC
```
Module 02 test files: `tests/test_cot_*.py` (client, parsers/validation incl. real CFTC sample rows,
calculations/classification, release timing, ingestion/database, look-ahead/divergence/intelligence/exports,
API/CLI) and `tests/fixtures/cftc/`.
