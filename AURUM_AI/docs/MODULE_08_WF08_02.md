# WF08-02 — Market Analysis Orchestrator

Answers the analysis requests WF08-01 publishes. For every symbol with unanswered `ai.analysis.requested` events it
checks the data and asks the AURUM backend to run **Module 05 once**. Built from *AURUM AI — Module 08: Complete n8n
Automation Architecture* (section 3, WF08-02), following its recommendation that the backend owns the
module-to-module work and n8n triggers one backend operation and routes the result.

> WF08-02 produces analysis for human review only. It places, approves and executes nothing, and has no MT5 access.

## How it runs

```
ai.analysis.requested (from WF08-01)
  └─▶ AURUM subscription ─▶ one execution per WF08-01 run ─▶ n8n webhook  aurum-wf08-02-market-analysis
        1  Check the webhook token, accept, callback "started"
        2  Load automation configuration ...... GET  /orchestration/market-analysis/config
        3  Check system health ................ GET  /orchestration/market-analysis/preflight
        4  Validate analysis requests ......... GET  /orchestration/market-analysis/pending
        5  Loop over symbols
        6    Check data quality ............... GET  /orchestration/market-analysis/data-quality
        7    Run Module 05 analysis ........... POST /orchestration/market-analysis/run
        8    Validate the analysis response, record the outcome
        9  Record the run ..................... callback "completed" with every symbol's outcome
```

| Document step | Where it happens |
|---|---|
| Validate Analysis Request | `pending`: only catalogued request events, grouped by symbol, not yet answered |
| Retrieve Market Data, Check Data Quality | `data-quality`: Module 01 must hold the required closed candles; stale data is flagged |
| Retrieve COT Context, Technical Analysis, Market Intelligence, Build Analysis Context | inside `run`: Module 05 builds its context from Modules 01–04 |
| Run Module 05 AI Analysis | `run` (`DecisionService.run`, with the AI reviewer when one is configured) |
| Validate Analysis Response | the workflow accepts only `completed`, `dry_run` or `skipped`; anything else is recorded as failed |
| Store Analysis Result | Module 05 stores the run, its candidates and its decision |
| Send Candidate to WF08-03 | `ai.analysis.completed` (or `ai.analysis.failed`) is published; WF08-03 will subscribe to it |

* **One run per WF08-01 run.** WF08-01 publishes one request per closed candle, all with its execution id as correlation
  id. With `market_analysis.coalesce` (default on) those events share one WF08-02 execution, which answers every pending
  request. Requests that arrive after that run has read the list wait for the next run.
* **Answered requests.** `run` stamps its result event with the newest request it served. A request is pending while no
  result event for its symbol is at least that new. A failed analysis also answers its requests, so a broken symbol is
  retried on the next candle, not in a loop.
* **Off switch.** `market_analysis.enabled: false` stops the run at step 3 and makes `run` refuse.
* **Manual runs.** Trigger WF08-02 with `{"dryRun": true}` to see what would be analysed, or
  `{"symbols": ["EURUSD"], "force": true}` to analyse a symbol that has no pending request.

## Configuration (`config/orchestration.yaml` → `market_analysis`, then versioned in the database)

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `true` | Backend off switch for WF08-02 |
| `ai_review` | `true` | Module 05 may use its AI reviewer when one is configured |
| `coalesce` | `true` | One WF08-02 run per WF08-01 run instead of one per request |
| `lookback_hours` | 72 | Unanswered requests older than this are ignored |
| `max_symbols_per_run` | 8 | The rest are deferred to the next run |
| `required_timeframes` | H1 | Closed candles that must exist before a symbol is analysed |
| `max_data_age_hours` | 120 | Newest required candle older than this is reported stale |
| `block_when_stale` | `false` | `true` = skip symbols whose data is stale |

## Install

1. Import `n8n/workflows/aurum_wf08_02_market_analysis.json` into n8n (it arrives inactive). In its *AURUM settings*
   node set `aurumApi` to your API address and replace the two `REPLACE_WITH_...` token placeholders, then activate it.
2. Link, version, validate, activate and subscribe it in AURUM (safe to re-run):
   ```powershell
   python -m aurum.cli orchestration setup-wf08-02 --n8n-workflow-id <id from the n8n workflow URL>
   ```
3. Dry run once: trigger WF08-02 with input `{"dryRun": true}`.

The service token the workflow uses needs `orchestration:view` and `orchestration:callback` (the same token as WF08-01).

## Tests

`tests/orchestration/test_wf08_02.py` (no n8n, MT5 or Module 05 needed; Module 05 is replaced by a recorder): pending
requests grouped by symbol, a run answers its requests once and publishes `ai.analysis.completed` as Module 05, dry run,
forced run, off switch, a failing analysis publishes `ai.analysis.failed`, the AI-review setting, data quality (missing,
stale, blocked), installation with its subscription, one execution per WF08-01 run (and one per event with coalescing
off), and the HTTP routes including who may start an analysis.

The workflow file `n8n/workflows/aurum_wf08_02_market_analysis.json` is checked by `tests/orchestration/test_workflow_files.py`.
