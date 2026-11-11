# WF08-01 — Market Monitoring Scheduler

Starts an analysis cycle for every configured instrument and timeframe **once its latest candle has closed**, and never
twice for the same candle. Built from *AURUM AI — Module 08: Complete n8n Automation Architecture* (section 3, WF08-01) on
top of the Module 08 core.

> WF08-01 only **requests** analysis. It reads no broker data, has no MT5 access and cannot place, approve or execute a
> trade. Every rule it branches on is decided by the AURUM backend; n8n only sequences the calls.

## How it runs

```
AURUM schedule (Module 08, cron in UTC, editable from the backend)
  └─▶ n8n webhook  aurum-wf08-01-market-monitoring   (executionId, attempt, environment, input)
        1  Accept + callback "started"
        2  Load automation configuration ........ GET  /orchestration/market-monitoring/config
        3  Check system health .................. GET  /orchestration/market-monitoring/preflight
        4  Essential services available? ........ no → callback "completed" (status: skipped, reasons)
        5  Get eligible symbols and timeframes .. GET  /orchestration/market-monitoring/eligible
        6  Split out / loop over instruments
        7  Check candle completion .............. GET  /orchestration/market-monitoring/candle-status
        8  Candle closed and settled? ........... no → record "not_ready"
        9  Check duplicate analysis ............. GET  /orchestration/market-monitoring/dispatch-status
       10  Already requested? ................... yes → record "duplicate"
       11  Request analysis ..................... POST /orchestration/webhooks/n8n/events  (ai.analysis.requested)
       12  Record the result .................... callback "completed" with every pair's outcome
           (continue to next instrument: every branch returns to the loop)
```

* **Trigger.** The document's "Schedule Trigger" is the Module 08 schedule, so the timing is stored, versioned and editable
  in AURUM (Orchestration page → Schedules, or `PUT /orchestration/schedules/{id}`), and every run is a tracked AURUM
  execution with retries and dead letters. Default: `1-59/5 * * * *` in UTC — one minute after every M5 close, which also
  covers every M15, H1, H4 and D1 close.
* **Candle-close rule.** A pair is ready only when Module 01 has stored a *closed* candle and `settle_seconds` (default
  60) have passed since it closed. Forming candles are never used. A ready candle older than `stale_factor` × its period
  is flagged `stale` (collection behind or market closed); because it was already requested, it is skipped as a duplicate.
* **Duplicate prevention, two layers.** Step 9 skips candles already requested; the request event also carries the
  idempotency key `wf08-01:<SYMBOL>:<TF>:<candle open UTC>`, which the event store accepts only once, so overlapping or
  retried runs still cannot request the same candle twice (a repeat returns `duplicate: true`).
* **What gets requested.** One `ai.analysis.requested` event per new candle (payload: symbol, timeframe, timeframes,
  candle_open, reason), published as `sourceModule: n8n`. It is consumed by WF08-02 once that is built (subscribe it to
  `ai.analysis.requested`). Set `request_event: technical.analysis.requested` to target Module 03 instead.
* **Failures.** A failed configuration, health or eligibility call ends the run with callback `failed`
  (`DEPENDENCY_UNAVAILABLE`, retryable) and AURUM retries with backoff. A failure on one pair is recorded and the loop
  continues; the run then completes with status `partial`.
* **Off switch.** `market_monitoring.enabled: false` (versioned config, `PUT /orchestration/config`) makes the next run
  stop at step 4 before checking or requesting anything. Pausing the workflow in the registry also stops new runs.

## Configuration (`config/orchestration.yaml` → `market_monitoring`, then versioned in the database)

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `true` | Backend off switch for WF08-01 |
| `symbols` | EURUSD, GBPUSD, XAUUSD, US30 | Instruments monitored |
| `timeframes` | D1, H4, H1, M15, M5 | Timeframes monitored |
| `settle_seconds` | 60 | Wait after a candle closes before using it |
| `stale_factor` | 3.0 | Flag a closed candle older than factor × period |
| `request_event` | `ai.analysis.requested` | Or `technical.analysis.requested` |
| `require_module_01_data` | `true` | Stop when Module 01 has stored no candles at all |

A run can be narrowed with input `{"symbols": [...], "timeframes": [...]}`; `{"dryRun": true}` checks everything and
reports what it *would* request without publishing.

## Install

1. Prerequisites: API running (`python -m aurum.api`), n8n running (`docker-compose.n8n.yml`), the Module 08 core test
   workflow verified end to end, and the two n8n credentials the core uses:
   *AURUM Module 08 - webhook token* (Header Auth `X-AURUM-Webhook-Token`) and *AURUM Module 08 - service token*
   (Header Auth `Authorization: Bearer <service token>`).
2. Import the workflow (it arrives inactive):
   ```powershell
   docker exec aurum-n8n n8n import:workflow --input=/workflows/aurum_wf08_01_market_monitoring.json
   ```
   Open it in n8n, check that both credentials are selected on the webhook and HTTP nodes, and toggle it **Active**.
3. Link, version, validate, activate and schedule it in AURUM (safe to re-run):
   ```powershell
   python -m aurum.cli orchestration setup-wf08-01 --n8n-workflow-id <id from the n8n workflow URL>
   ```
4. Dry run once from the Orchestration page (trigger WF08-01 with input `{"dryRun": true}`) and check the execution's
   output lists every pair with `ready` or a reason.

### n8n Cloud

The JSON file above targets self-hosted n8n in Docker (`host.docker.internal`). n8n Cloud cannot reach your machine, so
the Cloud build of WF08-01 differs in three ways:

* **API address.** The address lives in one node, *AURUM settings* (`aurumApi`). Set it to a public HTTPS URL of the
  AURUM API ending in `/api/v1/orchestration`, for example a reverse proxy or tunnel. Every request in the workflow goes
  only to this address.
* **Service token credential.** Its type is *Custom Auth* (`httpTemplatedCustomAuth`), with headers
  `{"Authorization": "Bearer <service token>"}`. The webhook token stays *Header Auth* with `X-AURUM-Webhook-Token`.
  Select the credentials on the webhook node and on all 13 HTTP nodes.
* **AURUM calls the Cloud webhook.** Set `n8n_webhook_base_url` (or `N8N_WEBHOOK_BASE_URL`) to
  `https://<your-instance>.app.n8n.cloud/webhook`. Then run step 3 with the Cloud workflow id, which is the last part of
  the workflow URL.

## Tests

`tests/orchestration/test_wf08_01.py` (no n8n or MT5 needed): configuration defaults, preflight and the off switch,
eligible pairs and filters, closed-and-settled candle rule (forming candle ignored), stale and missing data, both
duplicate-prevention layers, n8n cannot publish decision events, installation from placeholder to ACTIVE with a schedule
one minute after M5 closes (idempotent), input contract enforcement, the HTTP routes with a service token, and static
checks of the workflow file (only AURUM URLs, only the callback and events endpoints are written to, every document step
present, every branch returns to the loop).

Not tested here: a live n8n instance (the import, credentials and execution are verified with step 4 above).
