# Module 07 API

Base URL: `http://127.0.0.1:8000/api/v1`. Requests and responses use camelCase. Prices and volumes in bodies may be
strings or numbers. The server converts them to `Decimal`, and outbound event payloads carry them as strings.

## Conventions
- **Authentication.**
  - People: the `aurum_session` httpOnly cookie (set by `/auth/login`). Every POST or PUT also needs the
    `X-CSRF-Token` header returned by login or `/auth/me`.
  - Services: `Authorization: Bearer <service token>`, which needs no CSRF.
  - Every route below states the permission it needs. **Decision routes need a signed-in person**: a service token is
    refused even if it was granted the scope.
- **Errors.** Errors look like `{"error": {"code", "message", "details", "correlationId"}, "detail": message}`, with
  HTTP 401 (not signed in), 403 (`FORBIDDEN`, `CSRF_FAILED`), 404, 409 (state conflicts), 422 (validation) or 429
  (`RATE_LIMITED`, with `Retry-After: 60`).
- **Correlation.** Send `X-Correlation-ID` and it is echoed back and stored in the audit trail.
- **Idempotency.**
  - Decisions and management requests carry `clientRequestId`. Retrying with the same id returns the original result.
  - Inbound events and execution results carry `eventId`.
- **Optimistic locking.** Decisions carry `requestVersion`. A stale version gives `409 STALE_VERSION`, and a lost race
  gives `409 CONCURRENT_CHANGE`.
- **Rate limits.** 30 decisions per minute per user. Sign-in attempts are limited per minute, and repeated failures
  lock the account (see [SECURITY_GUIDE.md](SECURITY_GUIDE.md)).

## Authentication: `/auth`
| Method | Path | Auth | Body → Response |
|---|---|---|---|
| GET | `/auth/status` | none | → `{ready, hasUsers, message}`. Tells the UI whether server sign-in is possible |
| POST | `/auth/login` | none | `{username, password}` → `{user, csrfToken, expiresAt, idleTimeoutMinutes}` and sets the cookie. 401 on bad credentials or a locked account |
| POST | `/auth/logout` | session | → revokes the session, clears the cookie |
| GET | `/auth/me` | session | → `{user, csrfToken}` (rotated CSRF token for a reloaded page) |

`user` = `{id, username, displayName, role, kind, permissions[], accountScope}`.

## Approvals: `/approvals`
| Method | Path | Permission | Notes |
|---|---|---|---|
| GET | `/approvals` | `approvals:view` | Filters: `status` (repeat or comma-separated), `symbol`, `direction`, `timeframe`, `risk`, `strategy`, `since`, `until`, `sort` (default `-createdAt`), `page`, `pageSize` (max 200) → `{items, total, page, pageSize}` |
| GET | `/approvals/pending` | view | `PENDING` and `UNDER_REVIEW`, soonest expiry first |
| GET | `/approvals/dashboard` | view | Counts per status, pending list, pending management requests, recent handoffs, statistics |
| GET | `/approvals/statistics` | view | `?since=`. Totals, approval and rejection rates, decision times (for Module 10) |
| GET | `/approvals/health` | view | Schema, audit-chain verification, Module 05, 06 and 09 status, outbox pending and dead, notification failures |
| GET | `/approvals/ideas` | view | Recent trade ideas |
| POST | `/approvals/ideas` | `approvals:create` | Submit an idea. A person's submission is marked `manual`. 201 → the new request |
| GET | `/approvals/ideas/{ideaId}` | view | Idea with all versions |
| GET | `/approvals/ideas/{ideaId}/evidence` | view | Current evidence for the idea |
| POST | `/approvals/sync` | `approvals:create` | Pull Module 05 candidates now → `{ingested, closed, errors}` |
| GET | `/approvals/policy` | view | `{version, policy, meta, history}` |
| PUT | `/approvals/policy` | `approvals:policy` (person) | `{policy, reason, expectedVersion}` → new version. `409 STALE_POLICY` if someone else changed it |
| GET / POST | `/approvals/assignments` | view / `approvals:policy` (person) | Reviewer routing: `{username, symbol?, strategy?, timeframe?}` or `{id, active}` |
| GET | `/approvals/notifications` | view | `?unreadOnly=&limit=` → `{items, unread}` |
| POST | `/approvals/notifications/read` | view (person) | `{id?}`. Without an id, marks all as read |
| GET | `/approvals/audit` | view (`approvals:audit` sees everything) | `?limit=&entityType=&approvalId=&actor=` |
| GET | `/approvals/events` | view | Outbox events. `?after=<sequence>&limit=&type=` → `{items, lastSequence}` |
| GET | `/approvals/events/stream` | view | Server-sent events, one outbox envelope per message, with a keep-alive comment every 15 s. Resume with `?after=` |
| POST | `/approvals/events/inbound` | `events:publish` (service) | Event envelope. See [EVENT_CONTRACTS.md](EVENT_CONTRACTS.md). Never approves |
| GET | `/approvals/execution/queue` | `execution:read` (service) | Module 09 pulls pending execution requests and marks them picked up |
| POST | `/approvals/execution/results` | `execution:report` (service) | Module 09 reports `{eventId, executionRequestId, status, brokerTicket?, positionId?, filledVolume?, fillPrice?, message?}`. `status` is one of accepted, rejected, submitted, filled, partial, failed |
| GET | `/approvals/{id}` | view | Detail: request, idea, versions, decisions, risk assessments, modifications, handoff, related requests, disclaimer |
| GET | `/approvals/{id}/evidence` | view | `?snapshotId=`. Sections plus `hash`. The UI sends this hash back on approve |
| GET | `/approvals/{id}/history` | view | Audit rows for the request |
| POST | `/approvals/{id}/review` | `approvals:decide` (person) | `PENDING` → `UNDER_REVIEW`. Never approves |
| POST | `/approvals/{id}/approve` | `approvals:decide` (person) | See below |
| POST | `/approvals/{id}/reject` | `approvals:decide` (person) | `{clientRequestId, requestVersion, reasonCode, comment?}`. `other` needs a comment |
| POST | `/approvals/{id}/request-modification` | `approvals:decide` (person) | `{clientRequestId, requestVersion, kinds[], changes{entry?, stopLoss?, takeProfit?, positionSize?}, comment?}` → `{modificationId, status, newApproval?}` |
| POST | `/approvals/{id}/cancel` | `approvals:cancel` (person) | `{reason}`. Open or approved (not yet handed off) requests only |
| POST | `/approvals/{id}/revalidate-risk` | `approvals:revalidate` | New Module 06 assessment (the old one is cancelled first). Bumps the version, so a person must look again |

### Approve body
```json
{
  "clientRequestId": "5a0c…",
  "requestVersion": 2,
  "evidenceHash": "sha256 of the snapshot the person viewed",
  "acknowledgedWarnings": ["R041"],
  "comment": "optional",
  "confirmation": {
    "symbol": "EURUSD", "direction": "BUY", "entry": "1.085", "stopLoss": "1.083", "takeProfit": "1.09",
    "positionSize": "0.45", "maxRiskAmount": "97.65"
  }
}
```
On success the response is the request, usually already `EXECUTION_PENDING` because the handoff runs straight after
approval. If the handoff failed its final validation, the response is `INVALIDATED` with `statusReason`. On failure the
response is `409 APPROVAL_CHECKS_FAILED` with `error.details` listing every failed check (see
[APPROVAL_STATE_MACHINE.md](APPROVAL_STATE_MACHINE.md)).

## Trade management: `/trade-management`
| Method | Path | Permission | Notes |
|---|---|---|---|
| GET | `/trade-management/positions` | view | `{source, asOf, ageSeconds, stale, error, note, positions}`. Module 09 positions when it has reported any; otherwise the read-only account mirror (`sync-account`), labelled as such. `stale` is true after 5 minutes |
| GET | `/trade-management/positions/{positionId}` | view | One position with its management requests |
| GET | `/trade-management/requests` | view | `?status=&limit=` |
| POST | `/trade-management/requests` | `management:request` (person). `EMERGENCY_EXIT` also needs `management:emergency` | `{positionId, action, params, reason, clientRequestId}` |
| GET | `/trade-management/requests/{id}` | view | |
| POST | `/trade-management/requests/{id}/approve` | `management:decide` (person) | `{clientRequestId, comment?, confirmation: {action}}`, or `{confirmation: {phrase: "CLOSE EURUSD"}}` for an emergency exit |
| POST | `/trade-management/requests/{id}/reject` | `management:decide` (person) | `{clientRequestId, comment?}` |

See [TRADE_MANAGEMENT_GUIDE.md](TRADE_MANAGEMENT_GUIDE.md) for actions and parameters.

## Notifications (shared)
`GET /api/v1/notifications` (the existing system endpoint) also returns stored Module 07 notifications for a signed-in
session, with `source: "approvals"`, `notificationId`, `approvalId`, `managementRequestId` and `positionId`.

## Error codes
| Code | HTTP | Meaning |
|---|---|---|
| `NOT_AUTHENTICATED`, `INVALID_TOKEN` | 401 | No or expired session, or a bad service token |
| `FORBIDDEN`, `CSRF_FAILED` | 403 | Missing permission or human-only route, or a missing or wrong CSRF token |
| `NOT_FOUND`, `IDEA_NOT_FOUND` | 404 | |
| `APPROVAL_CHECKS_FAILED` | 409 | Approval refused. `details` lists each reason |
| `STALE_VERSION`, `CONCURRENT_CHANGE` | 409 | The request changed. Reload it |
| `NOT_PENDING`, `EXPIRED`, `ALREADY_DECIDED`, `ALREADY_HANDED_OFF`, `NOT_CANCELLABLE` | 409 | Wrong state |
| `REQUEST_ACTIVE`, `POSITION_NOT_OPEN` | 409 | Management conflicts |
| `IDEMPOTENCY_CONFLICT` | 409 | Same `clientRequestId` with a different body |
| `STALE_POLICY` | 409 | Policy changed since loaded |
| `DUPLICATE_IDEA` | 409 | Same source idea already open |
| `INVALID_*`, `REASON_REQUIRED`, `COMMENT_REQUIRED`, `KINDS_REQUIRED`, `CLIENT_REQUEST_ID_REQUIRED` | 422 | Validation |
| `STOP_WIDENING_NOT_ALLOWED`, `RISK_ASSESSMENT_REQUIRED`, `RISK_NOT_PASSED`, `STOP_THROUGH_PRICE`, `NOT_IN_PROFIT`, `NO_PRICE`, `NOT_AVAILABLE` | 422 | Management validation |
| `INVALID_EVENT`, `UNSUPPORTED_EVENT_VERSION`, `INVALID_RESULT` | 422 | Event and Module 09 contract validation |
| `RATE_LIMITED` | 429 | Too many decisions or sign-in attempts |
