# COT operations guide (Module 02)

All commands run from the `AURUM_AI` folder in PowerShell. `python` below means the project interpreter -
either activate the venv (`.\.venv\Scripts\Activate.ps1`) or write `.\.venv\Scripts\python.exe`.
Module 02 never trades and does not need MT5 (only the divergence analysis reads Module 01 prices from the
database). It needs internet access to www.cftc.gov and the database (Docker TimescaleDB or SQLite).

## 1. Check CFTC connectivity
```powershell
python -m aurum.cli cot check
```
Shows, per report family, whether the CFTC API (publicreporting.cftc.gov) and the official history files
(www.cftc.gov/files/dea/history) are reachable, and whether the COT tables exist. Exit 0 = every family has
at least one working source and the tables exist. On the development PC the API answers HTTP 403, so
`source: auto` uses the history files (same CFTC publication) - this is expected.

## 2. Verify dataset configuration
```powershell
python -m aurum.cli cot datasets
```
Lists the report families (Socrata IDs, verification status, coverage) and the contract mappings. Edit
`config/cot.yaml` to change them; `python -m aurum.cli cot sync-config` (also run automatically by
backfill/update) copies them into the database. Removed mappings are deactivated, never deleted.
### Verifying the Socrata dataset IDs
```powershell
python -m aurum.cli cot verify-datasets          # --no-write to only display the result
```
Reads `https://publicreporting.cftc.gov/api/views/<id>.json` for each family and marks the ID `verified`
only when the CFTC portal confirms it (title names the family and "Futures Only", required columns
present); a contradiction is written as `mismatch` (exit 2). Results go to `config/cot.yaml` and the
`cot_datasets` table. Exit 1 = portal unreachable; statuses are left unchanged.

Status on 2026-11-02: every Socrata portal (publicreporting.cftc.gov and unrelated ones such as
data.cityofnewyork.us) returns HTTP 403 from nginx to this PC's public IP, regardless of User-Agent - a
network-level block by the platform host, not a client problem. The IDs are therefore `corroborated`
(independent public sources agree on 6dca-aqww = Legacy, gpe5-46if = TFF, 72hh-3qpy = Disaggregated,
all futures-only). Run the command once from a network the portal accepts to promote them to `verified`.
The contract mappings are verified directly against the official 2025 and 2026 CFTC history files.

## 3. Initialise / migrate the database
```powershell
python -m aurum.cli init-db
```
Idempotent. On PostgreSQL it applies `sql/migrations/0002_cot_engine.sql` once (recorded in
`schema_migrations`); Module 01 tables are not modified. On SQLite it creates the tables from the models.

## 4. Historical backfill
```powershell
python -m aurum.cli cot backfill --years 4
```
Options: `--start 2018-01-01 --end 2026-11-02` (dates override `--years`), `-r legacy -r tff` (families),
`--no-resume` (re-download chunks already ingested). Work is split into calendar-year chunks per family;
each chunk is one transaction and is logged. If interrupted, run the same command again: completed years are
skipped, the current year is refreshed. Coverage differs per family (Legacy from 1986, TFF and
Disaggregated from June 2006) and requests outside it are clipped. Downloaded CFTC archives are kept in
`data/raw/cot/<family>/`. Exit code 0 = success, 2 = partial (some chunks failed - see the table).

## 5. Weekly update
```powershell
python -m aurum.cli cot update
```
Fetches from three weeks before the newest stored report to today (picks up new reports and CFTC
revisions), stores changes (revisions keep the previous payload in `cot_report_revisions`), recalculates
metrics. Running it twice changes nothing. With no data stored it performs a 4-year backfill.

**Scheduling (optional):**
```powershell
powershell -ExecutionPolicy Bypass -File scripts\install_cot_schedule.ps1
```
Registers the Windows task "AURUM AI COT Update" (Saturday + Tuesday 08:00; runs at the next opportunity if
the PC was off; output appended to `logs\cot_update.txt`). Remove with `-Uninstall`. Manual run:
`.\.venv\Scripts\python.exe scripts\run_cot_update.py`. On Linux/macOS use cron:
`0 8 * * 2,6 cd /path/AURUM_AI && .venv/bin/python -m aurum.cli cot update >> logs/cot_update.txt 2>&1`.

## 6. Recalculate metrics
```powershell
python -m aurum.cli cot calculate
python -m aurum.cli cot calculate -i EURUSD
```
Needed after changing `analytics` settings in `config/cot.yaml`. Deterministic and idempotent.

## 7. Generate reports
```powershell
python -m aurum.cli cot report --instrument EURUSD
python -m aurum.cli cot report --instrument XAUUSD --json
python -m aurum.cli cot report --instrument US30 --as-of 2026-03-06T12:00:00Z
```
`--as-of` shows only what was public at that UTC instant (backtesting / audit).

## 8. Inspect data quality and status
```powershell
python -m aurum.cli cot status
python -m aurum.cli cot quality
python -m aurum.cli cot quality --severity error -i GBPUSD
```
`quality` exits 2 while error/critical issues are open.

## 9. Export
```powershell
python -m aurum.cli cot export                    # all: raw, positions, metrics, analysis, quality
python -m aurum.cli cot export --kind metrics -i EURUSD --overwrite
```
Files: `data/exports/cot/cot_<kind>[_<SYMBOL>].csv`. Existing files are not overwritten unless `--overwrite`
(a timestamped copy is written instead). Each row has `data_status` = real / incomplete / unavailable.

## 10. Backend API
```powershell
$env:AURUM_ADMIN_API_KEY = "<long random secret>"     # or put AURUM_ADMIN_API_KEY=... in .env
python -m aurum.api
```
Serves `http://127.0.0.1:8000` (interactive docs at `/docs`). Admin POST calls need the header
`X-AURUM-Admin-Key`. Example:
```powershell
Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/v1/cot/update -Headers @{ "X-AURUM-Admin-Key" = $env:AURUM_ADMIN_API_KEY }
Invoke-RestMethod http://127.0.0.1:8000/api/v1/cot/jobs
```

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `Socrata API unavailable (HTTP 403 ...)` note | The CFTC API blocks this network; history files are used automatically (same data). Set `source: files` in `config/cot.yaml` to skip the API attempt. |
| `COT tables missing - run init-db` | `python -m aurum.cli init-db` |
| `schema_change` (critical) issue | The CFTC renamed/removed columns. Compare with the latest file, update `aurum/cot/fields.py`. |
| `unexpected_contract_name` | The CFTC renamed the contract; verify the code in `config/cot.yaml` against the latest file. |
| `stale_data` / freshness `update_due` | Run `cot update`; check `cot check` connectivity. Old data is still served and labelled. |
| `release_date_uncertain` | Report from a publication disruption without an official schedule; availability uses the conservative rule date. |
| Divergence "No Module 01 D1 candles" | Run Module 01 `collect`; divergence needs D1 prices for the instrument. |
| API `503 Admin operations are disabled` | Set `AURUM_ADMIN_API_KEY` for the API process. |
