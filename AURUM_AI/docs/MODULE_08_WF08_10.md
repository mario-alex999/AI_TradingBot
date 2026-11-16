# WF08-10 — News and COT Update Scheduler

Coordinates the scheduled refreshes of the public information AURUM uses: the economic calendar, official news and
central-bank/macro data (Module 04) and the CFTC Commitments of Traders reports (Module 02). Built from *AURUM AI —
Module 08: Complete n8n Automation Architecture* (section 3, WF08-10).

> **WF08-10 refreshes public information only.** The modules fetch from their official sources, validate and store;
> WF08-10 decides *when* each refresh is due and checks the stored timestamps afterwards. No account, no MT5 and no
> trade is involved, and there is no broker node in the workflow.

## When each source is due

"COT reports should be refreshed according to their actual publication schedule, not every few minutes. Economic
calendar information should be refreshed according to source update frequency and event importance."

| Task | Module | Due |
|---|---|---|
| `cot_legacy`, `cot_tff`, `cot_disaggregated` | 02 | When Module 02's release schedule says a newer report should now be public (normally Friday 15:30 US/Eastern, moved for US holidays; due 6 h after), when the stored report is out of date, or when nothing is stored yet. If the CFTC has not published yet, it is tried again after `cot_retry_hours`. While the data is current nothing is fetched, and the next expected release is shown. |
| `calendar` | 04 | Every `calendar_refresh_hours`; every `calendar_event_refresh_minutes` while a high-importance event is due within `event_window_hours` |
| `news` | 04 | Every `news_refresh_minutes` |
| `macro` | 04 | Every `macro_refresh_hours`, in parts (see below) |

**Macro data in parts.** Refreshing every central bank and indicator series takes 45 s or more, close to the API's
60 s limit on Vercel. So the macro task works in parts, one bank or one series at a time (Module 04's
`refresh_macro(only=[...])`, listed by `macro_parts()`): each run refreshes the parts not refreshed within
`macro_refresh_hours`, the longest-unrefreshed first, starts no new part after `macro_budget_seconds`, and records which
parts it did. The task stays due until every part is current, so the rest follows on the next hourly run. WF08-10
keeps this record itself because Module 04 keeps an unchanged value's original retrieval time.

A refresh that failed is tried again after `retry_after_minutes` (COT: `cot_retry_hours`). A source that is not
configured (for example `NEWS_ENABLED=false`) is listed with the reason and never run. The first time a COT report
family runs, Module 02 fetches `cot_backfill_years` of history; after that only the new reports.

## How it runs

```
AURUM schedule (hourly at :17 UTC)
  → webhook aurum-wf08-10-news-cot-updates
      Check the webhook token, accept, callback "started"
      Load update configuration / check it is on ...... GET  /orchestration/information-updates/config | preflight
      Check source availability and COT schedule ...... GET  /orchestration/information-updates/sources
      Any update due?  no → callback "completed" (what is not due, and when it is next due)
      Loop: trigger refresh ........................... POST /orchestration/information-updates/run
            verify data timestamps, record status ..... POST /orchestration/information-updates/record
            failed or out of date? → record problem (alerted once) | record status
      Record the run .................................. callback "completed"
```

| Document step | Where it happens |
|---|---|
| Schedule Trigger | the Module 08 schedule `WF08-10 news and COT updates` |
| Load Update Configuration | `config` (the `information_updates` section) |
| Check Source Availability | `sources`: whether each source is configured, and whether it is due |
| Trigger Market Intelligence Refresh | `run` for `calendar`, `news`, `macro` (Module 04's own refresh) |
| Verify News Data | `record` for `news`: last successful news refresh and newest headline time |
| Check COT Publication Schedule | `sources` for the COT tasks: Module 02's release schedule and freshness |
| Trigger COT Update When Available | `run` for a due COT family (Module 02 update, or the first backfill) |
| Verify Data Timestamps | `record`: the stored data's own timestamps (below) |
| Record Update Status | `record`: one audit entry per task and run (`wf08-10.update_recorded`) |

## Outcomes

| Outcome | Meaning | Alert |
|---|---|---|
| `updated` | The refresh stored new or revised data and the timestamps are current | — |
| `partial` | The data is current, but some of the task's sources failed (named in the record) | — |
| `current` | Nothing new, and the stored data is current | — |
| `waiting` | COT: the next report is due but the CFTC has not published it yet | — |
| `stale` | The stored data is older than allowed after the refresh | once |
| `failed` | The refresh failed (the source did not answer or returned an error) | once |

Which timestamps are checked: the calendar's last successful download (`calendar_stale_hours`) and how far ahead
events are stored; the last successful news refresh (`news_stale_hours`) and the newest headline; the last macro
refresh (`macro_stale_hours`) and when the policy rates were retrieved; for COT, Module 02's own freshness of every
instrument in the report family.

A source that starts failing, or goes stale, is announced on the dashboard once (Module 07 notification
`orchestration.information_update`); while it stays that way later runs record it without a new alert, and when it
works again the record is marked `recovered`.

* **Off switch.** `information_updates.enabled: false` stops the refreshes; the run records that it was skipped.
* **Manual runs.** Trigger WF08-10 with `{"dryRun": true}` to list what is due without fetching, with
  `{"tasks": ["cot_legacy"]}` for some sources only, or with `{"force": true}` to refresh configured sources now.
* **Time limits.** Each refresh is one API call and must finish within the API's limit (60 s on Vercel); a whole run
  may take up to 600 s (`setup-wf08-10` registers the workflow with that limit). Measured on the hosted demo: news 1 s,
  calendar 6 s, a first 4-year COT backfill of one report family 23 s, macro in parts within its budget.

## Configuration (`config/orchestration.yaml` → `information_updates`)

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `true` | Backend off switch for WF08-10 |
| `calendar_refresh_hours` | 6 | Normal calendar refresh interval |
| `calendar_event_refresh_minutes` | 60 | Calendar interval while a high-importance event is near |
| `event_window_hours` | 12 | How near counts as near |
| `calendar_stale_hours` | 36 | Calendar older than this after a refresh is `stale` |
| `news_refresh_minutes` | 60 | News refresh interval |
| `news_stale_hours` | 24 | No successful news refresh within this is `stale` |
| `macro_refresh_hours` | 24 | Central-bank and macro refresh interval |
| `macro_stale_hours` | 72 | No successful macro refresh within this is `stale` |
| `macro_budget_seconds` | 30 | A macro call starts no new bank or series after this; the rest follows next run |
| `cot_retry_hours` | 3 | COT: try again after this when the CFTC is late or the update failed |
| `cot_backfill_years` | 4 | History fetched the first time a COT report family runs |
| `retry_after_minutes` | 30 | Calendar, news, macro: try again after a failure |
| `notify_dashboard` | `true` | `false` = record only |

The sources themselves are configured in their modules: Module 04's calendar providers, `NEWS_ENABLED`,
`OFFICIAL_DATA_ENABLED`, `FRED_API_KEY` and `ECONOMIC_CALENDAR_PROVIDER` (see the Module 04 documentation); Module 02's
report families and contract mappings in `config/cot.yaml`.

## Install

1. Import `n8n/workflows/aurum_wf08_10_news_cot_updates.json` into n8n (it arrives inactive). In its *AURUM settings*
   node set `aurumApi` to your API address and replace the two `REPLACE_WITH_...` token placeholders. Under
   *Settings* → *Error workflow* choose WF08-08, then activate it.
2. `python -m aurum.cli orchestration setup-wf08-10 --n8n-workflow-id <id from the n8n workflow URL>` (safe to re-run).
   It registers the workflow, links it, adds and validates version 1.0.0, activates it and creates the schedule
   (`--schedule "17 * * * *"` by default, `--timezone`, `--no-schedule`).

The Module 08 registration id is `wf08-10-news-cot-updates`. The service token needs `orchestration:view` and
`orchestration:callback`.

On the Windows MT5 machine, the `AURUM AI COT Update` scheduled task (`scripts/install_cot_schedule.ps1`) can keep
running: COT updates are idempotent, so the two do not conflict. With WF08-10 active it is no longer needed.

## Tests

`tests/orchestration/test_wf08_10.py`: COT follows the CFTC publication schedule (not due while current, due once a
report should be public, retried after `cot_retry_hours` while the CFTC is late, due when nothing is stored); the
calendar is refreshed more often near a high-importance event, news hourly, macro daily; a source that is not
configured is never run; outcomes come from the stored timestamps; a failing source is alerted once, retried later and
its recovery recorded; dry run and the off switch; the real Module 02/04 adapter against the test database;
installation; the HTTP routes and who may run them.

The workflow file `n8n/workflows/aurum_wf08_10_news_cot_updates.json` is checked by
`tests/orchestration/test_workflow_files.py`.

Verified on the hosted demo: installation (schedule `17 * * * *` UTC, run limit 600 s, WF08-08 as the n8n error
workflow), a dry run and a live run through n8n. The live run took 100 s. It backfilled the CFTC TFF (621 reports) and
disaggregated (207 reports) families and recalculated their metrics. It left the legacy family alone because it was
current, and named its next release: Monday 16 November 20:30 UTC, moved by the Veterans Day holiday. It also stored
30 new official headlines, refreshed six calendar sources and 11 of 16 macro parts within the budget. Real source
problems it found and recorded: the US Bureau of Labor Statistics calendar and one news feed failed, and the New York
Fed and Bank of England rate sources answered HTTP 403 to the hosted server. The first scheduled run (11:17 UTC, 17 s)
refreshed only what was still due: the five remaining macro parts, oldest first. This time the New York Fed and Bank of
England answered, so all 16 parts were current. A manual run after that found nothing due and fetched nothing. The
COT families were current, with their next release named; news and calendar were not due until their next interval.
