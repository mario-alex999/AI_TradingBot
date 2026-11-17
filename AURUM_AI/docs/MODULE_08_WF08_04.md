# WF08-04 — Human Approval Notification

When Module 06 passes a trade idea, WF08-04 asks a person to decide: Module 07 opens the approval request and notifies
the reviewers. Built from *AURUM AI — Module 08: Complete n8n Automation Architecture* (section 3, WF08-04).

> **A notification is not an approval.** WF08-04 approves, rejects, places and executes nothing, and has no MT5 access.
> The approve and reject controls exist only on the signed-in AURUM dashboard; no email or message link can decide a
> trade. Module 07 records the human decision.

## How it runs

```
risk.assessment.completed / risk.assessment.requires_review (Module 06, for a WF08-03 assessment)
  └─▶ AURUM subscription ─▶ one execution per WF08-03 run ─▶ n8n webhook  aurum-wf08-04-approval-notification
        1  Check the webhook token, accept, callback "started"
        2  Load automation configuration ...... GET  /orchestration/approval-notifications/config
        3  Check system health ................ GET  /orchestration/approval-notifications/preflight
        4  List risk-approved ideas ........... GET  /orchestration/approval-notifications/pending
        5  Loop over ideas
        6    Retrieve idea + risk assessment,
             check still valid ................ GET  /orchestration/approval-notifications/status
        7    Prepare and send notification .... POST /orchestration/approval-notifications/notify
        8    Record the notification status
        9  Record the run ..................... callback "completed" (notified, skipped, waitingFor)
```

| Document step | Where it happens |
|---|---|
| Webhook: Risk Approved | Module 06's `risk.assessment.completed` (PASS) and `risk.assessment.requires_review` (REVIEW) events start the run |
| Retrieve Trade Idea, Retrieve Risk Assessment | `status`: the Module 05 idea and the Module 06 assessment WF08-03 requested for it |
| Check Still Valid | `status`: idea still open and unexpired, assessment passed and still active, Module 06 re-validates it, and Module 07 has no request for the idea yet |
| Prepare Notification | `notify`: Module 07 opens the approval request from the idea; the response carries the notification content |
| Send Notification | `notify`: Module 07 delivers to its reviewers on its configured channels (dashboard always; email and Telegram when set up in Module 07) |
| Record Notification Status | the execution output lists every idea with its approval id, review link and deliveries |
| Wait for Module 07 Approval Event | the run ends with `waitingFor: "Module 07 approval decision"`; the decision arrives later as a Module 07 event, which starts WF08-05 ([MODULE_08_WF08_05.md](MODULE_08_WF08_05.md)) |

* **What the notification contains.** Symbol, direction, strategy, entry zone, stop loss, take profit, risk/reward,
  evidence score, supporting reasons, conflicting evidence, Module 06's risk assessment, expiry time, and a link to the
  review page on the dashboard (`AURUM_DASHBOARD_URL` + `/approvals/<approval id>`). The link only opens the page;
  deciding requires signing in.
* **Decisions are reported unchanged.** WF08-04 reports Module 06's decision (`PASS` or `REVIEW`) and Module 07's
  request status as they are stored. It never changes either.
* **Notified once.** An idea that already has a Module 07 request is skipped, so repeated or overlapping runs cannot
  notify twice. Module 07's own intake check also refuses duplicates.
* **One assessment per request.** Module 07 runs its own Module 06 check when it opens the request. Module 06 allows
  only one live assessment per trade idea (R047), so WF08-04 hands the WF08-03 assessment it started from to Module 07
  (`ingest_candidate(..., supersedes=...)`), which releases it just **before** its own check. Releasing it afterwards,
  as the first version did, made R047 block every request as a duplicate of its own screening (fixed 17 Nov 2026).
  The risk is reserved once. Module 07's check emits another risk event, which starts one more WF08-04 run that finds
  nothing to do.
* **One run per WF08-03 run.** With `approval_notifications.coalesce` (default on) the risk events of one WF08-03 run
  share one WF08-04 execution.
* **Off switch.** `approval_notifications.enabled: false` stops the run at step 3 and makes `notify` refuse.
* **Manual runs.** Trigger WF08-04 with `{"dryRun": true}` (checks everything, opens and sends nothing), or
  `{"ideaIds": ["<id>"]}` to see why specific ideas are or are not notified.
* **Failures.** A failed configuration, health or list call ends the run with callback `failed`
  (`DEPENDENCY_UNAVAILABLE`, retryable). A failure on one idea is recorded and the loop continues; the run then
  completes with status `partial`.

## Configuration

`config/orchestration.yaml` → `approval_notifications`:

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `true` | Backend off switch for WF08-04 |
| `coalesce` | `true` | One WF08-04 run per WF08-03 run instead of one per risk event |
| `max_ideas_per_run` | 20 | The rest are deferred to the next run |

Environment:

| Variable | Meaning |
|---|---|
| `AURUM_DASHBOARD_URL` | Public address of the dashboard, used for the review link. When it is not set the notification has no link (`reviewUrl` is empty) and the health check reports `dashboard_url: false`; the run still proceeds. |

Who is notified and on which channels is Module 07's configuration, not WF08-04's.

## Install

1. Import `n8n/workflows/aurum_wf08_04_approval_notification.json` into n8n (it arrives inactive). In its *AURUM
   settings* node set `aurumApi` to your API address and replace the two `REPLACE_WITH_...` token placeholders, then
   activate it.
2. Set `AURUM_DASHBOARD_URL` on the API so notifications carry a review link.
3. `python -m aurum.cli orchestration setup-wf08-04 --n8n-workflow-id <id from the n8n workflow URL>` (safe to re-run).
   It registers the workflow, links it, adds and validates version 1.0.0, activates it and subscribes it to
   `risk.assessment.completed` and `risk.assessment.requires_review`.
4. Dry run once: trigger WF08-04 with input `{"dryRun": true}`.

The Module 08 registration id is `wf08-04-approval-notification`, which this install creates. The id
`wf08-04-execution-handoff` reserved by the Module 08 core is a different workflow: the document's WF08-06
([MODULE_08_WF08_06.md](MODULE_08_WF08_06.md)). The service token needs `orchestration:view` and `orchestration:callback`.

## Tests

`tests/orchestration/test_wf08_04.py`, against the real Module 07 service: pending lists only ideas Module 06 passed
that Module 07 has not been given; the still-valid check (expired idea, an assessment Module 06 no longer confirms, a cancelled assessment);
notify opens exactly one request, releases WF08-03's assessment before Module 07's own one, and returns the complete
notification; dry run, the off switch and a Module 07
refusal; installation with both subscriptions and one execution per WF08-03 run; the HTTP routes and who may notify;
and the queries on an empty database. `tests/approvals/test_integration_module06.py` checks the release against the
real Module 06: with it the request is PASS/REVIEW; without it R047 blocks the request.

The workflow file `n8n/workflows/aurum_wf08_04_approval_notification.json` is checked by
`tests/orchestration/test_workflow_files.py`.

Verified on the hosted demo: dry run, a named-idea run through the loop, and a 401 for a wrong or missing webhook
token. Not yet exercised there: a real notification, because no risk-passed idea existed at the time.
