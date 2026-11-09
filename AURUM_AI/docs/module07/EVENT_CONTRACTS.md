# Module 07 event contracts

Contract version: `SCHEMA_VERSION = "1.0"` (`aurum/approvals/__init__.py`). Source module: `module_07`. An inbound event
is accepted only when its major version is the same (`1.x`).

## Outbox envelope
Every outbound event is written to `outbox_events` **in the same transaction** as the state change that caused it, so
an event exists if and only if the change committed. Each event has a unique `idempotency_key`, so a retried request or
a second worker cannot publish the same business event twice.

```json
{
  "sequence": 42,
  "event_id": "uuid",
  "event_type": "trade.approved_for_execution",
  "event_version": "1.0",
  "occurred_at": "2026-11-09T10:15:00+00:00",
  "source_module": "module_07",
  "correlation_id": "trace id shared by every event of one trade idea",
  "causation_id": "approval id (when the event follows from another record)",
  "idempotency_key": "trade.approved_for_execution:<approval id>",
  "payload": { }
}
```

### Delivery
- **Webhook.** When `APPROVALS_EVENTS_WEBHOOK_URL` is set, the worker POSTs each pending event as JSON (the envelope in
  snake_case, without the delivery block). On a 2xx response the event is `delivered`. Otherwise it is retried with
  backoff (30 s, 1, 2, 5, 15 and 30 min) and, after the configured attempts, marked `dead`. Dead events stay visible in
  `/approvals/events` and are counted by `/approvals/health`.
- **Pull.** `GET /api/v1/approvals/events?after=<sequence>` (camelCase) works whether or not a webhook is set. Consumers
  keep their last sequence.
- **Stream.** `GET /api/v1/approvals/events/stream` (SSE) is used by the UI to refresh live.
- **Guarantees.** Delivery is at least once. Consumers must deduplicate by `event_id`. Events are ordered by `sequence`.

## Outbound events
| Event | When | Payload |
|---|---|---|
| `approval.requested` | A request was opened | Approval payload (below) |
| `approval.under_review` | A person opened it | Approval payload + `previous_status`, `reason` |
| `approval.approved` | Final approval | same |
| `approval.rejected` | Rejected | same (the reason is in `reason`) |
| `approval.modification_requested` | Changes requested | same |
| `approval.expired` | Expired | same |
| `approval.cancelled` | Cancelled | same |
| `approval.invalidated` | Superseded, or final validation failed | same, with the failed checks in `reason` |
| `approval.risk_revalidated` | New Module 06 assessment | Approval payload + `previous_risk_decision` |
| `approval.policy.updated` | Policy changed | `{version, previous_version, reason, changed_by}` |
| `trade.approved_for_execution` | Final validation passed. **The execution request for Module 09** | Execution payload (below) |
| `execution.completed` / `execution.failed` | Module 09 reported the outcome | Approval payload + `previous_status`, `reason` |
| `trade_management.requested` | A position change was requested | Management payload |
| `trade_management.approved` | Approved: **the execution request for Module 09** | Management payload + `execution_request_id`, `approved_by`, `approved_at` |
| `trade_management.rejected` / `trade_management.expired` | | Management payload |
| `module05.feedback` | Internal. The worker writes the decision back to Module 05 through `DecisionService` | `{idea_id, decision, approval_id, by, reason_code?, comment?}`. Not sent to the webhook |

**Approval payload.** `approval_id, trade_idea_id, idea_version, status, version, symbol, direction, timeframe, strategy,
entry, stop_loss, take_profit, position_size, risk_pct, risk_decision, risk_assessment_id, evidence_hash, expires_at,
account_reference`. Prices are strings. There is no evidence text and no personal data.

**Management payload.** `management_request_id, position_id, account_reference, symbol, action, params, priority,
status, reason, position_snapshot`.

## Module 09 contract (execution)
Module 09 does not exist yet. The contract is implemented and tested with a labelled test adapter
(`FakeExecution` in `tests/approvals/m07_harness.py`). **No real execution has been integrated.**

### Execution request (`trade.approved_for_execution`, also in `GET /approvals/execution/queue`)
```json
{
  "approval_id": "…", "trade_idea_id": "…", "idea_version": 1, "execution_request_id": "uuid",
  "symbol": "EURUSD", "direction": "BUY",
  "entry": {"type": "price", "price": "1.08500"}, "stop_loss": "1.08300", "take_profit": "1.09000",
  "approved_volume": "0.45",
  "risk_assessment_id": "…", "risk_decision": "PASS",
  "approval_snapshot_hash": "sha256 over approval id, evidence hash, assessment id and decision record hashes",
  "approver_ids": ["user id"], "approved_at": "…", "account_reference": "12345678",
  "correlation_id": "…", "idempotency_key": "exec:<approval id>", "schema_version": "1.0",
  "instructions": "Module 09 must validate this request with Module 06 (POST /risk/assessments/{id}/validate) immediately before any order, never exceed approved_volume, never trade without the stop-loss, and report the outcome to /api/v1/approvals/execution/results."
}
```
The queue response also carries `payload_hash`, so Module 09 can check that the payload was not altered.

### Execution result (`POST /api/v1/approvals/execution/results`, service token with `execution:report`)
```json
{"eventId": "unique per report", "executionRequestId": "uuid", "status": "filled",
 "brokerTicket": "123456", "positionId": "123456", "filledVolume": "0.45", "fillPrice": "1.08502", "message": null}
```
`status` is one of `accepted`, `rejected`, `submitted`, `filled`, `partial`, `failed`. Reports are deduplicated by
`eventId` and only move the request forward: a late `submitted` after `filled` is recorded and ignored. A fill creates
or updates the managed position.

## Inbound events (`POST /api/v1/approvals/events/inbound`, service token with `events:publish`)
Required envelope fields: `eventId, eventType, eventVersion, occurredAt, sourceModule, correlationId, payload`.
Duplicates (the same `eventId`) return the first result with `duplicate: true`.

| Event | Effect |
|---|---|
| `trade_idea.created` | Intake (validated). Payload: `idea_id, symbol, market, direction, entry` (or `entry_low` + `entry_high`), `stop_loss, take_profits[], timeframe, strategy, created_at, expires_at, analysis_version, correlation_id, schema_version` ("1.0"). Optional: `risk_reward, ai_confidence, ai_summary, evidence[], market_conditions, source_refs[], higher_timeframe_context` |
| `trade_idea.updated` | New idea version. The open request is superseded and a new one opened |
| `trade_idea.cancelled` | `{idea_id, reason}`. Closes the open request |
| `execution.completed`, `execution.failed`, `execution.result` | Same as an execution result |
| `position.updated` | `{position_id, symbol, side, volume, entry_price, current_price?, stop_loss?, take_profit?, unrealized_pnl?, realized_pnl?, status, account_login?}` |
| `risk.assessment.completed`, `market.data.updated` | Recorded only. They never change a decision. A person revalidates, or the handoff does |
| anything else | `{ignored: true}` |

**No inbound event can approve, reject or execute anything.** Decisions exist only on the human endpoints.
