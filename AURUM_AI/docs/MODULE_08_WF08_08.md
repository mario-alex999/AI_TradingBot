# WF08-08 — Error Handling and Recovery

Makes the automation reliable when something fails: every failed run of an AURUM workflow is identified, classified
and recorded once, retried only when that is safe and within limits, and escalated to the dashboard when it is not.
Built from *AURUM AI — Module 08: Complete n8n Automation Architecture* (section 3, WF08-08).

> **WF08-08 starts no retry itself and never retries anything about a trade.** Retries belong to the Module 08 core,
> inside each workflow's retry policy. A time-out is never resent automatically, because the work may have been done
> and only the answer lost. A failure of the execution hand-off is never classified as retryable.

## What the core already does, and what WF08-08 adds

The Module 08 core tracks every run. A failed run is classified (`TRANSIENT`, `PERMANENT`, `UNKNOWN`), retried with
back-off up to the workflow's attempt limit, and moved to the dead-letter list when it cannot be retried. WF08-08
does not repeat any of that. It adds:

* **Crashes the workflow could not report.** When an n8n workflow stops with an error before its own "failed"
  callback, AURUM would only notice at the run's deadline. n8n's *Error Trigger* reports the crash to AURUM at once;
  AURUM finds the run by its n8n execution id, classifies the error and applies its usual retry rules.
* **One record per failure, in plain words.** Which workflow, which error, how it is classified, whether a retry is
  safe, and what happened next.
* **Escalation.** A failure that is not being retried is announced on the dashboard through Module 07's notifications.
* **Recovery.** A run that succeeds on a later attempt is recorded as recovered.

## How it runs

Two ways in, sharing one *AURUM settings* node:

```
(1) n8n Error Trigger  (another AURUM workflow names WF08-08 as its error workflow and crashed)
      Identify failed workflow and capture error details  (n8n workflow id, n8n execution id, last node, message)
      Classify error and record it in the backend ......... POST /orchestration/error-recovery/report
      Is retry safe?   yes → retry scheduled by AURUM (within limits)     no → escalated and alerted

(2) AURUM webhook  aurum-wf08-08-error-recovery
      (orchestration.workflow.failed / timed_out / dead_lettered, and .completed on attempts 2-5)
      Check the webhook token, accept, callback "started"
      Load configuration / check health ........ GET  /orchestration/error-recovery/config | preflight
      Identify failed workflow runs ............ GET  /orchestration/error-recovery/pending
      Loop: capture error details and classify . GET  /orchestration/error-recovery/status
            record error in the backend ........ POST /orchestration/error-recovery/record
            Is retry safe? → retry with limits | recovery verified | escalated and alerted
      Record the run ........................... callback "completed"
```

| Document step | Where it happens |
|---|---|
| Error Trigger | n8n's Error Trigger (crashes), and AURUM's own failure events (failures the workflow reported) |
| Identify Failed Workflow | the n8n workflow and execution ids of a crash; the AURUM execution id of a reported failure |
| Capture Error Details | the error message, the node it stopped at, the error category and the attempt |
| Classify Error | `report`: the message is classified (below); `status`: the category the core stored |
| Record Error in Backend | one audit entry per failure state (`wf08-08.error_recorded`, `wf08-08.engine_error`) |
| Is Retry Safe? | yes only for a transient error within the attempt limit and not on the execution hand-off |
| Retry with limits | the core's retry: back-off and the workflow's `max_attempts`; WF08-08 sends nothing again |
| Escalate and alert | Module 07 dashboard notification (`orchestration.error_escalated`, high; critical for the hand-off) |
| Verify Recovery | a run that completed on attempt 2 or later is recorded as `recovered` |
| Notify Dashboard | the escalation above; retries and recoveries are recorded without a notification |

## Classification

| Error text from n8n | Category | Retried? |
|---|---|---|
| time-out, connection refused or reset, DNS failure, 502/503/504 | `DEPENDENCY_UNAVAILABLE` | yes, within the limit |
| 429, rate limit | `RATE_LIMITED` | yes, within the limit |
| 401, 403, unauthorised, forbidden | `AUTH_FAILED` | no: dead-lettered |
| 400, 404, 422, validation | `INVALID_PAYLOAD` | no: dead-lettered |
| anything else | `WORKFLOW_ERROR` | only if the workflow's policy retries workflow errors |
| any error in the execution hand-off | `OUTCOME_UNKNOWN` | never: dead-lettered and escalated as critical |

A reported failure keeps the category the workflow or the core gave it. A time-out (`TIMED_OUT`) is escalated with
the instruction to check the run in n8n first. Business outcomes (`RISK_BLOCKED`, `APPROVAL_EXPIRED`, ...) are
recorded as `closed`: they are not faults.

* **Recorded once.** Each failure state is keyed by run, state and attempt. A crash reported twice (n8n retrying its
  error workflow) changes nothing the second time: only a run the engine is still running can be failed by a report,
  so a repeat cannot use up a retry.
* **Never its own trigger.** A workflow is not triggered by events about its own runs, so a failing WF08-08 run cannot
  start another one. WF08-08 is also not the error workflow of itself.
* **Off switch.** `error_recovery.enabled: false` stops both paths. The core's retries continue regardless.
* **Manual runs.** Trigger WF08-08 with `{"dryRun": true}` to list what it would record, or with
  `{"executionIds": ["<id>"]}` for specific runs.

## Configuration (`config/orchestration.yaml` → `error_recovery`)

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `true` | Backend off switch for WF08-08 |
| `max_errors_per_run` | 20 | The rest are deferred to the next run |
| `lookback_hours` | 72 | How far back a sweep without an event looks |
| `notify_dashboard` | `true` | `false` = record only |

## Install

1. Import `n8n/workflows/aurum_wf08_08_error_recovery.json` into n8n (it arrives inactive). In its *AURUM settings*
   node set `aurumApi` to your API address and replace the two `REPLACE_WITH_...` token placeholders, then activate it.
2. In n8n, open each other AURUM workflow (WF08-01 to WF08-07), *Settings* → *Error workflow* → WF08-08, save and
   publish. The Error Trigger only fires for the workflows that name it.
3. `python -m aurum.cli orchestration setup-wf08-08 --n8n-workflow-id <id from the n8n workflow URL>` (safe to re-run).
   It registers the workflow, links it, adds and validates version 1.0.0, activates it and subscribes it to the
   failure events of the other workflows and to runs that completed after a retry.

The Module 08 registration id is `wf08-08-error-recovery`. The service token needs `orchestration:view` and
`orchestration:callback`.

## Tests

`tests/orchestration/test_wf08_08.py`: a transient failure is retried by the core and recorded as recovered; a
permanent error, a time-out and the dead-letter list are escalated and nothing is retried here; a business outcome is
closed without an alert; unknown and healthy runs, dry run, record-only mode and the off switch; the classification of
engine error texts; a crash reported by n8n is applied to its run at once, a repeat changes nothing, and an unknown
crash is escalated; the execution hand-off is never retryable and is escalated as critical; installation, and no run
started by WF08-08's own failures; the HTTP routes and who may record or report.

The workflow file `n8n/workflows/aurum_wf08_08_error_recovery.json` is checked by
`tests/orchestration/test_workflow_files.py`.

Verified on the hosted demo: installation, WF08-08 set as the error workflow of WF08-01 to WF08-07 in n8n, two manual
runs through n8n, a 401 for a wrong or missing webhook token, and a 401 on `report` without a service token. Not
exercised there: a real crash through the Error Trigger, because none of the workflows failed.
