# WF08-06 — Approved Trade Handoff

Follows the execution request Module 07 creates for an approved trade: verifies it, records the hand-off once and
reports whether Module 09 has acknowledged it. Built from *AURUM AI — Module 08: Complete n8n Automation Architecture*
(section 3, WF08-06).

> **WF08-06 sends no order.** It has no MT5 or broker node, calls no execution endpoint and creates no execution
> request. **Module 09 (trade execution) is not built**, so today every valid hand-off ends as `awaiting_module_09` and
> nothing is executed. The document says to build WF08-06 only after Module 09 has its secure execution API; this is
> the part that can be built before that, and the part after it is listed under *What is left for Module 09*.

## How the hand-off works in AURUM

The document draws WF08-06 as "Create Execution Request → HTTP Request: Module 09". AURUM already assigns those two
steps to the modules that own them, and WF08-06 does not take them over:

* **Module 07 creates the execution request.** After a person approves, Module 07 runs its final validation, writes
  one execution request per approval into its queue (unique per approval, with a stored hash) and publishes
  `trade.approved_for_execution`.
* **Module 09 fetches it.** Module 09's contract is to read Module 07's queue (`GET /approvals/execution/queue`,
  scope `execution:read`), verify the approval and the risk assessment itself, and report the outcome
  (`POST /approvals/execution/results`, scope `execution:report`). Nothing pushes a trade to it, so a lost
  acknowledgement can never cause a second order.

## How it runs

```
trade.approved_for_execution (Module 07)
  └─▶ AURUM subscription ─▶ approval guard (Module 07 must show the approval) ─▶ n8n webhook  aurum-wf08-06-trade-handoff
        1  Check the webhook token (Authenticate Event), accept, callback "started"
        2  Load automation configuration ...... GET  /orchestration/trade-handoff/config
        3  Check system health ................ GET  /orchestration/trade-handoff/preflight
        4  Validate approval id ............... GET  /orchestration/trade-handoff/pending
        5  Loop over handoffs
        6    Retrieve approved trade, verify
             approval, risk and expiry ........ GET  /orchestration/trade-handoff/status
        7    Record execution status .......... POST /orchestration/trade-handoff/record
        8    Status?  valid, waiting for Module 09 → record "queued"     acknowledged → record acknowledgement
                      execution failed → record failure                  anything else → record "not valid"
        9  Record the run ..................... callback "completed" (recorded, skipped)
```

| Document step | Where it happens |
|---|---|
| Webhook: Approved Trade | `trade.approved_for_execution` starts the run. The Module 08 core refuses every trigger of this workflow unless Module 07 shows the approval as APPROVED or EXECUTION_PENDING |
| Authenticate Event | the workflow answers 401 without the right `X-AURUM-Webhook-Token`; every backend call needs the service token |
| Retrieve Approved Trade | `status`: Module 07's execution request for the approval |
| Verify Approval | Module 07 (`verify_decision`): the required decisions exist, each made in a role with approval permission by an existing account |
| Verify Risk Approval | Module 07 (`verify_handoff`): Module 06's decision on the request is PASS or REVIEW, the request carries that assessment, and Module 06 still validates it |
| Verify Trade Expiry | Module 07: the idea is at the approved version, still active and unexpired |
| Check Duplicate Execution Request | Module 07 allows one execution request per approval (database constraint); WF08-06 records each hand-off state once and creates no request |
| Create Execution Request | done by Module 07 before the event. WF08-06 checks the request is intact: it matches the hash Module 07 stored and carries the approved symbol, direction, entry, stop-loss, take-profit, volume, idea version and risk assessment |
| HTTP Request: Module 09 | not made: Module 09 reads the queue. WF08-06 reads whether Module 09 is deployed and healthy from the Module 08 integration registry |
| Validate Execution Acknowledgement | `status`: Module 09 has picked the request up or reported on it |
| Record Execution Status | `record`: one entry in the Module 08 audit log (`wf08-06.handoff_recorded`) per execution request and state |
| Notify AURUM AI Dashboard | Module 07 notifies the dashboard when it hands the trade on and when Module 09 reports; `record` reports those notifications |

* **Results.** `awaiting_module_09` (valid, Module 09 not deployed), `awaiting_acknowledgement` (valid, Module 09
  deployed but has not picked it up), `acknowledged`, `executed`, `execution_failed`, `not_valid`.
* **A request that does not verify** is recorded as `not_valid` with the reasons (audit outcome `refused`). WF08-06
  cannot withdraw it from Module 07's queue. Module 09 must refuse it on its own verification, which is why Module 09
  may never treat n8n or this workflow as its security boundary. If Module 06 cannot be reached, the request counts as
  not confirmed.
* **No blind retry.** A missing acknowledgement is recorded, not retried: there is nothing to send again.
* **Recorded once.** The audit entry is keyed by execution request and state. A repeated event is skipped.
* **Off switch.** `trade_handoff.enabled: false` stops the run at step 3 and makes `record` refuse. It does not stop
  Module 07 or Module 09; each enforces its own switch.
* **Manual runs.** Only with an approval: trigger it as an administrator with `{"approvalId": "<id>"}` (add
  `"dryRun": true` to record nothing). A trigger without an approval Module 07 confirms is refused
  (`APPROVAL_REQUIRED`, `APPROVAL_NOT_APPROVED`), so there is no sweep run.
* **Failures.** A failed configuration, health or list call ends the run with callback `failed`
  (`DEPENDENCY_UNAVAILABLE`, retryable); a retry re-checks the approval with Module 07. A failure on one request is
  recorded and the run completes with status `partial`.

## What is left for Module 09

* Module 09 itself: reading the queue, verifying the approval and the risk assessment
  (`POST /risk/assessments/{id}/validate`) immediately before any order, idempotent order handling, status
  reconciliation and reporting results.
* Following the outcome after the hand-off (`execution.completed`, `execution.failed`, positions) belongs to WF08-07.
  The approval guard refuses WF08-06 for approvals that are already executed or failed.
* Module 07's retry of a deferred hand-off (`handoff_due`) runs in the Module 07 worker, which the hosted demo does not
  run.

## Configuration (`config/orchestration.yaml` → `trade_handoff`)

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `true` | Backend off switch for WF08-06 |
| `max_handoffs_per_run` | 20 | The rest are deferred to the next run |
| `lookback_hours` | 72 | How far back the backend's list of unrecorded requests looks |

## Install

1. Import `n8n/workflows/aurum_wf08_06_trade_handoff.json` into n8n (it arrives inactive). In its *AURUM settings* node
   set `aurumApi` to your API address and replace the two `REPLACE_WITH_...` token placeholders, then activate it.
2. `python -m aurum.cli orchestration setup-wf08-06 --n8n-workflow-id <id from the n8n workflow URL>` (safe to re-run).
   It names the reserved registration, links it, adds and validates version 1.0.0, activates it and subscribes it to
   `trade.approved_for_execution`.

The Module 08 registration id is `wf08-04-execution-handoff`. The core reserved that id for the execution hand-off
before the workflows were numbered; it is the document's WF08-06, and its category (`execution_handoff`) is what
switches the approval guard on. The service token needs `orchestration:view` and `orchestration:callback`, and must
not have `execution:read` or `execution:report`.

## Tests

`tests/orchestration/test_wf08_06.py`, against the real Module 07 service: without Module 09 a valid hand-off is
recorded once as waiting and Module 07's request is untouched; with a stand-in Module 09 the acknowledgement and the
outcome are followed; a hand-off whose idea version, risk assessment or request contents no longer hold is recorded as
not valid and left untouched; an approval without an execution request, an unknown approval, dry run and the off
switch; installation on the reserved registration with the approval guard still refusing unapproved triggers; the HTTP
routes, who may record, no order endpoint, and the n8n token refused on Module 09's queue; the module calls no broker
and creates no request; and the wiring on an empty database.

The workflow file `n8n/workflows/aurum_wf08_06_trade_handoff.json` is checked by
`tests/orchestration/test_workflow_files.py`.

Verified on the hosted demo: installation, the approval guard refusing triggers without an approval, a 401 for a
wrong or missing webhook token, the backend routes, and Module 09 reported as not deployed. Not run there end to end:
the guard only lets the workflow start for a trade Module 07 has approved, and the hosted database holds none.
