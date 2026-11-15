# WF08-03 — Trade Opportunity Processor

Takes each trade idea Module 05 has open and sends it to Module 06 for a risk assessment. Built from *AURUM AI —
Module 08: Complete n8n Automation Architecture* (section 3, WF08-03).

> **Module 06 makes the risk decision.** Neither the n8n workflow nor this backend half calculates whether a trade is
> safe. WF08-03 approves, places and executes nothing, and has no MT5 access.

## How it runs

```
ai.analysis.completed (from WF08-02)
  └─▶ AURUM subscription ─▶ one execution per WF08-02 run ─▶ n8n webhook  aurum-wf08-03-trade-opportunity
        1  Check the webhook token, accept, callback "started"
        2  Load automation configuration ...... GET  /orchestration/trade-opportunities/config
        3  Check system health ................ GET  /orchestration/trade-opportunities/preflight
        4  Validate trade idea ids ............ GET  /orchestration/trade-opportunities/pending
        5  Loop over ideas
        6    Retrieve idea, check status/expiry GET  /orchestration/trade-opportunities/idea-status
        7    Module 06 risk assessment ........ POST /orchestration/trade-opportunities/assess
        8    Validate the risk response
        9    Decision?  PASS / REVIEW → record "risk passed"     BLOCK → record the reason
       10  Record the run ..................... callback "completed" with every idea's outcome
```

| Document step | Where it happens |
|---|---|
| Validate Trade Idea ID, Check Duplicate Event | `pending`: open Module 05 ideas with no Module 06 assessment for their current levels |
| Retrieve Full Trade Idea, Check Idea Status, Check Setup Expiry | `idea-status`: exists, still open (`new` or `under_review`), not expired, not already assessed |
| HTTP Request: Module 06 Risk Assessment | `assess` calls Module 06 (`RiskService.assess` with the idea id) |
| Validate Risk Response | the workflow accepts only a completed assessment with PASS, REVIEW or BLOCK |
| REJECTED → Record reason, Notify dashboard | recorded in the execution output with Module 06's reasons; Module 06 stores the assessment the dashboard shows |
| APPROVED → Store risk result, Send to WF08-04 | Module 06 stores it and publishes its own `risk.assessment.*` event, which starts WF08-04 ([MODULE_08_WF08_04.md](MODULE_08_WF08_04.md)) |

* **Decisions are reported unchanged.** PASS → `approved`, REVIEW → `review` (passed, with points for the human
  reviewer), BLOCK → `rejected`, ERROR → `error`.
* **Assessed once.** The assessment's idempotency key is `wf08-03:<idea id>:<hash of entry, stop, target, direction>`.
  Module 06 returns the stored assessment for a repeated key and `pending` skips ideas that have one. A re-priced idea
  gets a new key and a new assessment.
* **One run per WF08-02 run.** With `trade_opportunities.coalesce` (default on) the `ai.analysis.completed` events of
  one WF08-02 run share one WF08-03 execution.
* **Off switch.** `trade_opportunities.enabled: false` stops the run at step 3 and makes `assess` refuse.
* **Manual runs.** Trigger WF08-03 with `{"dryRun": true}`, or `{"ideaIds": ["<id>"]}` to see why specific ideas are or
  are not assessed.

## Configuration (`config/orchestration.yaml` → `trade_opportunities`)

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `true` | Backend off switch for WF08-03 |
| `coalesce` | `true` | One WF08-03 run per WF08-02 run instead of one per analysis event |
| `max_ideas_per_run` | 20 | The rest are deferred to the next run |

## Install

1. Import `n8n/workflows/aurum_wf08_03_trade_opportunity.json` into n8n (it arrives inactive). In its *AURUM settings*
   node set `aurumApi` to your API address and replace the two `REPLACE_WITH_...` token placeholders, then activate it.
2. `python -m aurum.cli orchestration setup-wf08-03 --n8n-workflow-id <id from the n8n workflow URL>` (safe to re-run).
3. Dry run once: trigger WF08-03 with input `{"dryRun": true}`.

The Module 08 registration id is `wf08-03-risk-approval` (reserved by the Module 08 core). The service token needs
`orchestration:view` and `orchestration:callback`.

## Tests

`tests/orchestration/test_wf08_03.py`: pending lists only open, unexpired, unassessed ideas; an idea reaches Module 06
once; all four Module 06 decisions are reported unchanged; closed and expired ideas, dry run, the off switch and a
Module 06 failure; the assessment key changes when an idea is re-priced; installation with its subscription and one
execution per WF08-02 run; the HTTP routes and who may request an assessment; and one test against the real Module 06
service.

The workflow file `n8n/workflows/aurum_wf08_03_trade_opportunity.json` is checked by
`tests/orchestration/test_workflow_files.py`.
