# WF08-05 — Approval Response Handler

Follows the outcome of every approval request: approved, rejected or expired (and invalidated, cancelled or sent back
for modification). For each one it reads Module 07's verification, records the outcome once and reports what happens
next. Built from *AURUM AI — Module 08: Complete n8n Automation Architecture* (section 3, WF08-05).

> **A person decides, Module 07 records it, WF08-05 only follows.** WF08-05 cannot approve, reject, expire,
> invalidate or hand off anything, and has no MT5 access. An approved trade is released to execution only by Module
> 07's own final validation.

## How it runs

```
approval.approved / rejected / expired / invalidated / cancelled / modification_requested (Module 07)
  └─▶ AURUM subscription ─▶ one execution per event ─▶ n8n webhook  aurum-wf08-05-approval-response
        1  Check the webhook token (Authenticate Event), accept, callback "started"
        2  Load automation configuration ...... GET  /orchestration/approval-responses/config
        3  Check system health ................ GET  /orchestration/approval-responses/preflight
        4  Validate approval ids .............. GET  /orchestration/approval-responses/pending
        5  Loop over responses
        6    Retrieve current status and
             verify the decision .............. GET  /orchestration/approval-responses/status
        7    Record the response .............. POST /orchestration/approval-responses/record
        8    Status?  approved and verified → record approval      rejected → record rejection
                      expired → record expiry                      anything else → record "no valid approval"
        9  Record the run ..................... callback "completed" (recorded, skipped)
```

| Document step | Where it happens |
|---|---|
| Webhook: Approval Event | Module 07's outcome events start the run, one execution per event |
| Authenticate Event | the workflow answers 401 without the right `X-AURUM-Webhook-Token`; AURUM signs the trigger; every backend call needs the service token |
| Validate Approval ID | `pending`: the approval named by the event; an unknown id is recorded as skipped |
| Retrieve Current Trade Status | `status`: the request's current Module 07 status, never the status claimed by the event |
| Verify Approval Authority | Module 07 (`verify_decision`): the required number of decisions with a final one, each made in a role with approval permission by an account that exists (and is still active while the request waits for hand-off) |
| Verify Trade Idea Has Not Expired | Module 07: the idea is still active, unexpired and still open in Module 05 |
| Check Risk Assessment Is Still Valid | Module 07 asks Module 06 to validate the assessment the approval was given on |
| IF Status? | the workflow branches on the recorded result |
| Record approval / rejection / expiry | `record`: one entry in the Module 08 audit log (`wf08-05.response_recorded`), written once per request and outcome |
| Send WF08-06 | Module 07's final validation creates the execution request and publishes `trade.approved_for_execution`, which starts WF08-06 ([MODULE_08_WF08_06.md](MODULE_08_WF08_06.md)). WF08-05 publishes nothing itself |
| Notify dashboard | Module 07 notifies the dashboard when it records the outcome; `record` reports those notifications and delivers due email or Telegram ones |

* **The critical rule.** An approval is tied to one trade idea, its version and its risk assessment. While a request
  is APPROVED and not yet handed on, WF08-05 reports it as valid only if the idea is still at the approved version,
  every approving decision was made on the request's current risk assessment and evidence, the idea has not expired,
  and Module 06 still validates the assessment. Otherwise the result is `approved_not_valid`: it is recorded with the
  reasons (audit outcome `refused`) and nothing is passed on. If Module 06 cannot be reached, the approval is treated
  as not confirmed, never as valid.
* **After the hand-off.** Module 07 normally runs its final validation immediately after an approval, so most
  approvals reach WF08-05 as `EXECUTION_PENDING`. That final validation has then already confirmed the same links,
  and WF08-05 reports the execution request id.
* **Results.** `approved`, `approved_not_valid`, `rejected`, `expired`, `invalidated`, `cancelled`,
  `modification_requested`. Module 07's status is reported unchanged.
* **Recorded once.** The audit entry is keyed by request and outcome. A repeated event or an overlapping run is
  skipped. A request that is approved and later invalidated is recorded twice, once for each outcome.
* **Off switch.** `approval_responses.enabled: false` stops the run at step 3 and makes `record` refuse.
* **Manual runs.** Trigger WF08-05 with `{"dryRun": true}` (verifies, records nothing), with
  `{"approvalIds": ["<id>"]}` for specific requests, or with no input to sweep every decided request of the last
  `lookback_hours` whose outcome is not recorded.
* **Failures.** A failed configuration, health or list call ends the run with callback `failed`
  (`DEPENDENCY_UNAVAILABLE`, retryable). A failure on one request is recorded and the loop continues; the run then
  completes with status `partial`.

## Configuration (`config/orchestration.yaml` → `approval_responses`)

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `true` | Backend off switch for WF08-05 |
| `max_responses_per_run` | 20 | The rest are deferred to the next run |
| `lookback_hours` | 72 | How far back a sweep without an event looks. A named request is always answered |
| `expire_overdue_requests` | `false` | Run Module 07's expiry pass on every Module 08 worker tick. Turn it on only where no Module 07 worker runs |

### Expiry on hosts without a Module 07 worker

`approval.expired` is published by Module 07's worker (`python -m aurum.cli approvals worker`). A serverless
deployment runs no such worker, so overdue requests would stay open and WF08-05 would never see an expiry. With
`expire_overdue_requests: true` the Module 08 tick calls Module 07's own `expire_due` first (step `approval_expiry`).
Module 07 still decides what expires; an approved request is never touched. The hosted demo has this switched on.
Module 07's hand-off retry (`handoff_due`) is not run from the tick: an approval whose immediate hand-off was
deferred waits for a Module 07 worker.

## Install

1. Import `n8n/workflows/aurum_wf08_05_approval_response.json` into n8n (it arrives inactive). In its *AURUM settings*
   node set `aurumApi` to your API address and replace the two `REPLACE_WITH_...` token placeholders, then activate it.
2. `python -m aurum.cli orchestration setup-wf08-05 --n8n-workflow-id <id from the n8n workflow URL>` (safe to re-run).
   It registers the workflow, links it, adds and validates version 1.0.0, activates it and subscribes it to the six
   Module 07 outcome events.
3. Dry run once: trigger WF08-05 with input `{"dryRun": true}`.

The Module 08 registration id is `wf08-05-approval-response`, which this install creates. The service token needs
`orchestration:view` and `orchestration:callback`.

## Tests

`tests/orchestration/test_wf08_05.py`, against the real Module 07 service: an approval Module 07 handed on is verified
and recorded once; a rejection and an expiry are recorded and nothing is executed; an approval whose idea version,
risk assessment, decision or idea expiry no longer holds (or whose assessment cannot be confirmed) is recorded as not
valid and the request is left untouched; authority is checked against the recorded decisions; unknown and undecided
requests, dry run, the off switch and the look-back window; Module 07's expiry pass runs on the tick only when
switched on; installation with six subscriptions and one execution per outcome event; the HTTP routes, who may record,
and that no decision endpoint exists; and the wiring on an empty database.

The workflow file `n8n/workflows/aurum_wf08_05_approval_response.json` is checked by
`tests/orchestration/test_workflow_files.py`.

Verified on the hosted demo: installation, a dry-run sweep, an event-shaped run through the loop for an unknown
request, and a 401 for a wrong or missing webhook token. Not yet exercised there: a real approval, rejection or
expiry, because the hosted database held no approval request at the time.
