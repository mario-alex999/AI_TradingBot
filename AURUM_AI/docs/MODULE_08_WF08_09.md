# WF08-09 — System Health Watchdog

Checks that the whole platform works, service by service, records the result and alerts the dashboard when a service
becomes unhealthy. Built from *AURUM AI — Module 08: Complete n8n Automation Architecture* (section 3, WF08-09).

> **WF08-09 only reads.** It restarts nothing, changes no configuration and touches no trade. There is no MT5 or broker
> node in the workflow: the MT5 data service is judged from the data AURUM has stored.

## What "healthy" means here

An API that answers HTTP 200 is not taken as proof that it works. Each service gets one of these statuses:

| Status | Meaning | Counts as working? |
|---|---|---|
| `ONLINE` | Responding and current | yes |
| `DEGRADED` | Responding, but part of it does not work (for example a module whose dependency is down) | no |
| `STALE` | Responding but stale: the data has not been updated within the allowed age | no |
| `OFFLINE` | Not responding | no |
| `NO_CREDENTIALS` | Cannot be checked or used because a key or token is not configured; the result names it | no |
| `UNKNOWN` | The check itself failed; the result says why | no |
| `MARKET_CLOSED` | The FX market is closed (Friday 22:00 to Sunday 22:00 UTC), so old data is expected | yes |
| `NOT_DEPLOYED` | Module 09 is not built yet | yes |
| `NOT_USED` | The optional AI provider is not configured | yes |

Every result carries the time of the service's last successful update or run where one exists (`lastSuccessAt`).

## Services checked

| Service | How it is checked |
|---|---|
| n8n | Module 08's n8n health check, plus the time of the last workflow run that succeeded |
| AURUM API (FastAPI) | The API answering the check request itself (the request goes through the public address) |
| Database | `SELECT 1`, timed; slower than `database_slow_ms` is `DEGRADED`, no answer is `OFFLINE` |
| MT5 data service | The time of the last stored complete candle and account snapshot (`STALE` past `market_data_stale_minutes` / `account_stale_minutes` while the market is open; `OFFLINE` if no market data was ever stored) |
| Modules 01–07 | Each module's own health endpoint, through Module 08's module gateway at `AURUM_INTERNAL_BASE_URL` |
| Module 09 | `NOT_DEPLOYED` until it exists (optional) |
| AI provider | Whether Module 05's optional AI review is switched on and its API key is set; no paid call is made (optional) |

The overall status is `HEALTHY` when every required service works, `DEGRADED` when the worst problem is a degraded,
stale or unknown service, and `UNHEALTHY` when a required service is offline or lacks credentials. Optional services
are shown but do not change the overall status.

## How it runs

```
AURUM schedule (every 2 hours at :07 on the hosted demo; default hourly)
  → webhook aurum-wf08-09-health-watchdog
      Check the webhook token, accept, callback "started"
      Load configuration / check it is switched on .. GET  /orchestration/health-watchdog/config | preflight
      List the services to check .................... GET  /orchestration/health-watchdog/services
      Loop: check service ........................... POST /orchestration/health-watchdog/check
            service working? → record working | record problem | record check request failed
      Evaluate status and alert on new problems ..... POST /orchestration/health-watchdog/evaluate
      Record the run ................................ callback "completed"
```

| Document step | Where it happens |
|---|---|
| Schedule Trigger | the Module 08 schedule `WF08-09 health watchdog` |
| Check n8n / FastAPI / Database / MT5 Data Service / Modules 01–07 / Module 09 | `check`, one service per loop item, recorded as `wf08-09.service_checked` with the run's id |
| Evaluate Service Status | `evaluate` uses this run's checks (a service the run did not check is checked then) |
| Record Health Result | one audit entry per run, `wf08-09.health_recorded`, with every service's status |
| Alert if Unhealthy | a Module 07 dashboard notification (`orchestration.health_alert`) |
| Record Last Successful Check Time | `lastSuccessAt` of each service, stored with the result |

* **Alerted once.** A service is announced when it becomes unhealthy or changes to another unhealthy status. While it
  stays the same, later runs record it without a new alert. A service that works again is listed under `recovered`.
* **Off switch.** `health_watchdog.enabled: false` stops the checks; the run records that it was skipped.
* **Manual runs.** Trigger WF08-09 with `{"dryRun": true}` to see the result without recording or alerting, or with
  `{"services": ["database", "mt5_data"]}` to check only some services.
* **If the scheduler stops.** The watchdog runs from the Module 08 schedule; if that stops, WF08-09 stops too. n8n's
  execution list then shows that it has not run.

## The background health check and the hosted demo

The Module 08 worker also runs a short integration health check of its own (`health.interval_seconds`). It now reads
the time of the last check from the database, so on a serverless host where every tick is a fresh process the
interval still holds. The hosted demo runs it every 900 seconds; WF08-09 is the full check.

On a serverless host the module checks must use the API's public address: set
`AURUM_INTERNAL_BASE_URL=https://<api host>/api/v1`. With the default `http://127.0.0.1:8000/api/v1` the modules
cannot be reached there, and the result says so. Module 07's health endpoint needs a token
(`ORCHESTRATION_SERVICE_TOKEN`); without it Module 07 is reported as `NO_CREDENTIALS`, not as down.

## Configuration (`config/orchestration.yaml` → `health_watchdog`)

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `true` | Backend off switch for WF08-09 |
| `market_data_stale_minutes` | 30 | Market data older than this, while the market is open, is `STALE` |
| `account_stale_minutes` | 60 | An account snapshot older than this, while the market is open, is `STALE` |
| `database_slow_ms` | 2000 | A slower database query is `DEGRADED` |
| `notify_dashboard` | `true` | `false` = record only |

## Install

1. Import `n8n/workflows/aurum_wf08_09_health_watchdog.json` into n8n (it arrives inactive). In its *AURUM settings*
   node set `aurumApi` to your API address and replace the two `REPLACE_WITH_...` token placeholders. Under
   *Settings* → *Error workflow* choose WF08-08, then activate it.
2. `python -m aurum.cli orchestration setup-wf08-09 --n8n-workflow-id <id from the n8n workflow URL>` (safe to re-run).
   It registers the workflow, links it, adds and validates version 1.0.0, activates it and creates the schedule
   (`--schedule "7 * * * *"` by default, `--timezone`, `--no-schedule`). The hosted demo uses `--schedule "7 */2 * * *"`.

The Module 08 registration id is `wf08-09-health-watchdog`. The service token needs `orchestration:view` and
`orchestration:callback`.

## Tests

`tests/orchestration/test_wf08_09.py`: market hours; a healthy platform is recorded without an alert; stale data,
offline and degraded services are told apart and alerted once, then recovery is listed; a closed market is not stale;
missing credentials are their own status; an unreachable internal address on a serverless host is explained; each
service is checked once per run and the run's own checks are evaluated; dry run and the off switch; the worker's health
interval holds when every tick is a fresh process; installation with its schedule; the HTTP routes and who may check.

The workflow file `n8n/workflows/aurum_wf08_09_health_watchdog.json` is checked by
`tests/orchestration/test_workflow_files.py`.

Verified on the hosted demo: installation and schedule, a dry run and two live runs through n8n (13 services checked;
the first live run alerted once, the second recorded the same state without an alert), and a 401 for a wrong webhook
token. The first runs reported the platform `UNHEALTHY` for real reasons: the MT5 collector has not delivered since
12 November, n8n's API key is not configured, Module 07's health needs a service token, and Modules 01–03, 05 and 06
are degraded because of the missing data.
