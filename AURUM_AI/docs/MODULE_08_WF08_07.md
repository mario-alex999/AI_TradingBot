# WF08-07 — Trade Monitoring Orchestrator

Watches the positions AURUM knows about, records each change once and notifies the dashboard when the change matters.
Built from *AURUM AI — Module 08: Complete n8n Automation Architecture* (section 3, WF08-07).

> **WF08-07 takes no trade action.** It moves no stop, closes or reduces nothing, requests no trade-management action
> and sends no order. It has no MT5 or broker node and asks nothing of a broker. **Module 09 (trade execution) is not
> built**; until it is, the positions come from the read-only account mirror.

## Where the positions come from

The document's "Request Module 09 Trade Status" reads **Module 07's position records**, which Module 07 fills from its
position source:

* **Module 09's reports**, once Module 09 exists and reports positions to Module 07.
* **The account mirror** until then: the open positions of the demo account as the read-only `sync-account` of Module
  01 last stored them. These are whatever is open on the account, including trades opened by hand in the terminal, and
  they are only as fresh as the collector's last run.

## How it runs

```
AURUM schedule (cron in UTC) · execution.completed · position.closed
  └─▶ n8n webhook  aurum-wf08-07-trade-monitoring
        1  Check the webhook token, accept, callback "started"
        2  Load automation configuration ...... GET  /orchestration/trade-monitoring/config
        3  Check system health ................ GET  /orchestration/trade-monitoring/preflight
        4  Retrieve open trade records ........ GET  /orchestration/trade-monitoring/open-trades
        5  Split out / loop over trades
        6    Request trade status, check for
             risk and lifecycle events ........ GET  /orchestration/trade-monitoring/status
        7    Anything changed? ................ no → record "unchanged"
        8    Record status update ............. POST /orchestration/trade-monitoring/record
        9    Needs attention? ................. yes → "dashboard alerted"     no → "status update"
       10  Record the run ..................... callback "completed" (recorded, unchanged, skipped)
```

| Document step | Where it happens |
|---|---|
| Schedule Trigger / Trade Event | the Module 08 schedule, plus `execution.completed` and `position.closed` |
| Retrieve Open Trade Records | `open-trades`: Module 07 refreshes its records from its source, then open positions are listed with positions that closed since they were last recorded |
| Split Out Trades | the loop |
| Request Module 09 Trade Status, Check Position Status | `status`: the position as Module 07 holds it and how old that record is |
| Retrieve Monitoring Data | `status`: prices, levels, volume, result, linked approval, whether Module 09 is deployed |
| Check Risk / Lifecycle Events | `status`: Module 06's active risk locks for the instrument, and the changes since the last recorded status |
| Record Status Update | `record`: one entry in the Module 08 audit log (`wf08-07.status_recorded`) per change |
| Notify Dashboard if Necessary | `record`: Module 07 stores a dashboard notification for the changes that matter |

## What is reported

Each event is a fact read from the records, compared with the last status this workflow recorded for the position.

| Event | When | Dashboard |
|---|---|---|
| `position_opened` | first time the position is seen; says when it is not linked to an AURUM approval | normal |
| `position_closed` | the position is no longer open | normal |
| `levels_changed` | stop-loss or take-profit differs from the last record | normal |
| `volume_changed` | volume differs from the last record (for example a partial close) | normal |
| `status_changed` | Module 07's status for the position changed | normal |
| `stop_loss_missing` | an open position has no stop-loss | high |
| `unexpected_state` | the source reports a state AURUM does not know | high |
| `data_stale` | the record is older than `stale_after_seconds` (collector or broker connection down) | high |
| `risk_lock_active` | Module 06 has a risk lock active for the instrument | high |
| `data_fresh`, `risk_lock_released` | the two conditions above ended | recorded only |

* **Price and profit movements are not events.** A position whose price moved but whose status, volume and levels are
  the same is `unchanged`: nothing is recorded and nobody is notified.
* **Said once.** A lasting condition (no stop-loss, stale data, a risk lock) is reported when it starts, not on every
  run.
* **Why a position closed is not guessed.** "Stop-loss reached" and "take-profit reached" are the broker's facts. The
  account mirror does not supply the reason for a close, so the close is reported without one. Module 09 will report
  it.
* **No trade-state calculation and no action.** The document leaves those to Module 09 and the monitoring module;
  every trade-management action still needs a person's decision in Module 07.
* **Off switch.** `trade_monitoring.enabled: false` stops the run at step 3 and makes `record` refuse.
* **Manual runs.** Trigger WF08-07 with `{"dryRun": true}` (records and notifies nothing), or
  `{"positionIds": ["<id>"]}` for specific positions.
* **Failures.** A failed configuration, health or list call ends the run with callback `failed`
  (`DEPENDENCY_UNAVAILABLE`, retryable). A failure on one position is recorded and the loop continues; the run then
  completes with status `partial`. If Module 07 cannot refresh from its source, the run still checks the stored
  records and reports the error in `feed`.

## Configuration (`config/orchestration.yaml` → `trade_monitoring`)

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `true` | Backend off switch for WF08-07 |
| `max_trades_per_run` | 50 | The rest are deferred to the next run |
| `stale_after_seconds` | 300 | A position record older than this is reported as stale |
| `closed_lookback_hours` | 72 | How long a close may wait to be recorded |
| `notify_dashboard` | `true` | `false` = record only |

## Install

1. Import `n8n/workflows/aurum_wf08_07_trade_monitoring.json` into n8n (it arrives inactive). In its *AURUM settings*
   node set `aurumApi` to your API address and replace the two `REPLACE_WITH_...` token placeholders, then activate it.
2. `python -m aurum.cli orchestration setup-wf08-07 --n8n-workflow-id <id from the n8n workflow URL>` (safe to re-run).
   It registers the workflow, links it, adds and validates version 1.0.0, activates it, subscribes it to
   `execution.completed` and `position.closed`, and creates the schedule. Options: `--schedule "<cron>"`,
   `--timezone`, `--no-schedule`, `--no-subscribe`.
3. Dry run once: trigger WF08-07 with input `{"dryRun": true}`.

The Module 08 registration id is `wf08-07-trade-monitoring`, which this install creates. The service token needs
`orchestration:view` and `orchestration:callback`.

**Schedule.** The default is `3-59/5 * * * *` (every 5 minutes). Every scheduled run is one n8n execution, whether or
not a position is open. The hosted demo runs `41 */4 * * 1-5` (every 4 hours on weekdays, about 130 runs a month) to
stay inside a small n8n Cloud execution quota; change it on the Orchestration page or with
`PUT /orchestration/schedules/{id}`.

## Tests

`tests/orchestration/test_wf08_07.py`, against the real Module 07 service: a position is recorded when it opens and
then only when something changes (levels, volume, close), with one dashboard notification per change; risk and data
events are raised once and cleared; no trade action is ever taken and the module calls no broker; dry run, an unknown
position, a named run, record-only mode and the off switch; installation with its schedule and events; the HTTP
routes, who may record, and that no trade endpoint exists; and the wiring on an empty database.

The workflow file `n8n/workflows/aurum_wf08_07_trade_monitoring.json` is checked by
`tests/orchestration/test_workflow_files.py`.

Verified on the hosted demo: installation with its schedule, a normal run ("No open position and no unrecorded
close."), a named run through the loop for a position that does not exist, and a 401 for a wrong or missing webhook
token. Not exercised there: a real position change, because the account mirror held no open position and its last
update was three days old (the MT5 collector is not running).
