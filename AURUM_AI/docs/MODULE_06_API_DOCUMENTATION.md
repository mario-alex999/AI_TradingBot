# Module 06 API

Base path: `/api/v1/risk`.
- Requests accept camelCase or snake_case keys. Responses are camelCase.
- **Admin** endpoints need the header `X-AURUM-Admin-Key: <AURUM_ADMIN_API_KEY>`. A missing or wrong key gets 401. If no
  key is configured on the server, admin endpoints return 503.
- Optional headers:
  - `X-Correlation-ID` is echoed back and stored.
  - `X-AURUM-Actor` names the person or system in the audit log (sanitised; default `admin-key`).
  - `Idempotency-Key` is accepted on `/assess`.
- `/assess` and `/position-size` are rate-limited to 60 requests per minute per client (429 beyond that).

## Endpoints

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/assess` | admin | Full decision for one proposed trade or a Module 05 `tradeIdeaId`. 201 |
| POST | `/position-size` | open | What-if size; trade rules only; stores nothing |
| GET | `/assessments/{id}` | open | Stored assessment |
| POST | `/assessments/{id}/validate` | open | Still usable? Checks decision, expiry, state, config version and locks |
| POST | `/assessments/{id}/consume` | admin | Mark as used (Module 09 after an order); frees the reservation |
| POST | `/assessments/{id}/cancel` | admin | Withdraw; frees the reservation |
| GET | `/account` | open | Account limits and overall status |
| GET | `/portfolio` | open | Exposure, reservations, correlations |
| GET | `/config` | open | Current config, version and history |
| PUT | `/config` | admin | New version (reason, base version, weakening acknowledgement) |
| GET | `/rules` | open | Rule catalogue with current severities and thresholds |
| GET | `/locks?history=true` | open | Active locks and history |
| POST | `/locks` | admin | Manual lock / kill switch. 201 |
| POST | `/locks/{id}/release` | admin | Release with a reason (at least 5 characters) |
| GET | `/history` | open | Assessments: `limit`, `offset`, `decision`, `instrument`, `rule`, `strategy`, `from`, `to` |
| GET | `/history/rules` | open | Counts per rule and outcome |
| GET | `/audit` | open | Audit log (`entityType`, `entityId`, `limit`) |
| GET | `/events?after=&type=` | open | Outbox events after a sequence number |
| POST | `/events/inbound` | admin | Inbox; duplicate `eventId`s are acknowledged and not reprocessed |
| GET | `/health` | open | Dependencies, data freshness, audit chain |

`GET /api/v1/health` also reports `modules.risk`.

## POST /assess
```http
POST /api/v1/risk/assess
X-AURUM-Admin-Key: ********
Idempotency-Key: n8n-run-4812
Content-Type: application/json

{ "symbol": "EURUSD", "direction": "BUY", "entry": 1.10010, "stopLoss": 1.09810, "takeProfit": 1.10510,
  "riskPct": 1.0, "strategy": "order_block_continuation" }
```
Optional fields:
- `requestedVolume`: checked against the budget and the broker limits.
- `tradeIdeaId`: instead of prices. Module 05's candidate is used.

Response (abridged):
```json
{
  "assessmentId": "e9e207c2-…", "decision": "PASS", "instrument": "EURUSD", "direction": "BUY",
  "recommendedVolume": 0.46, "finalVolume": 0.46, "riskPct": 0.9982, "monetaryRisk": 99.82,
  "potentialReward": 224.48, "riskReward": 2.249, "accountCurrency": "USD",
  "calculation": { "valuePerPriceUnitPerLot": 100000.0, "lossPerLotAtStop": 200.0, "costPerLot": 17.0,
                   "totalLossPerLot": 217.0, "rawVolume": 0.460829, "estimatedLoss": 99.82, "marginEstimate": 506.05, … },
  "accountStatus": { "status": "WITHIN_LIMITS", "summary": { … }, "checks": [ … ] },
  "portfolioStatus": { … }, "correlationStatus": { … }, "marketStatus": { … },
  "ruleViolations": [], "warnings": [], "errors": [], "information": [ { "ruleId": "R071", … } ],
  "passedChecks": [ … ], "skippedChecks": [ … ], "activeLocks": [],
  "configVersion": 1, "configProfile": "moderate", "engineVersion": "6.0.0",
  "timestamp": "2026-11-04T12:00:00+00:00", "expiresAt": "2026-11-04T12:15:00+00:00",
  "correlationId": "…", "createdBy": "Test Officer", "state": "active", "replayed": false,
  "module07": { "eligibleForHumanReview": true, "fullyRiskApproved": true, "mustDisplayWarnings": false,
                "hardBlock": false, "note": "Module 07 cannot override BLOCK or ERROR. Module 09 must revalidate before any order." },
  "disclaimer": "Estimated risk at the stop-loss. Gaps, slippage beyond the assumption, … Module 06 never places, modifies or closes orders."
}
```
Each rule result has this shape:
```json
{ "ruleId": "R030", "name": "Daily loss limit", "category": "account", "severity": "HARD_BLOCK",
  "outcome": "fail", "reasonCode": "DAILY_LOSS_LIMIT", "message": "Daily loss 3.1% reached the 3% limit.",
  "evidence": { … }, "thresholds": { "maxDailyLossPct": 3.0, "lossBasis": "equity" } }
```

## POST /position-size
The body is the same as `/assess`, plus optional `equity` and `accountCurrency` (alias `currency`) for a what-if
without the account mirror. The response has `valid`, `equitySource` (`request` or `account_mirror`), `calculation`,
`checks`, `failedChecks`, `warnings`, `note` and `disclaimer`.

If `equity` is sent without a currency while no account snapshot exists, the result is invalid with reason code
`ACCOUNT_CURRENCY_UNKNOWN`.

## PUT /config
```json
{ "baseVersion": 3, "reason": "tighten per-trade risk", "changes": { "riskPerTradePct": 0.5,
  "maxSpreadPoints": { "XAUUSD": 400 } } }
```
- To switch profile, send `"profile": "conservative"`.
- Add `"acknowledgeWeakening": true` when loosening a limit.
- The response is the new config view with `version` and `weakened`.

## POST /locks, POST /locks/{id}/release
```json
{ "scope": "global", "reason": "news spike, stop all" }
{ "scope": "instrument", "scopeValue": "XAUUSD", "reason": "spreads unreliable today" }
{ "reason": "reviewed with the desk, safe to resume" }
```
Scopes are `global`, `account`, `instrument` and `strategy`. Releasing a loss or drawdown lock adds a `note` saying
that the limit itself still applies.

## Errors
Every error has the same shape, with `detail` kept for older clients:
```json
{ "error": { "code": "WEAKENING_NOT_ACKNOWLEDGED",
             "message": "These changes loosen risk limits (max_daily_loss_pct: 3.0 -> 5.0). Resend with acknowledge_weakening=true to confirm.",
             "details": ["max_daily_loss_pct: 3.0 -> 5.0"],
             "correlationId": "…" }, "detail": "…" }
```

| Status | Codes |
|---|---|
| 401 | Missing or wrong admin key |
| 404 | `NOT_FOUND` |
| 409 | `IDEMPOTENCY_CONFLICT`, `VERSION_CONFLICT`, `WEAKENING_NOT_ACKNOWLEDGED`, `NO_CHANGE`, `LOCK_NOT_ACTIVE`, `STATE_FINAL` (already consumed/cancelled), `ASSESSMENT_NOT_VALID` (consuming a BLOCK/ERROR or expired assessment) |
| 422 | `INVALID_PROPOSAL`, `INVALID_CONFIG`, `INVALID_CHANGES`, `BASE_VERSION_REQUIRED`, `RULE_NOT_DISABLEABLE`, `REASON_REQUIRED`, `INVALID_SCOPE`, `SCOPE_VALUE_REQUIRED`, `INVALID_EXPIRY`, `INVALID_STATE`, `INVALID_EVENT`, `NO_WEBHOOK` |
| 429 | Rate limit |
| 503 | `SCHEMA_NOT_READY` (run `init-db`); admin key not configured |

A BLOCK or ERROR **decision** is not an HTTP error: it is a normal 201 response with `decision` set.

## Events (outbox)
`GET /risk/events` returns:
```json
[{ "sequence": 12, "eventId": "…", "eventType": "risk.assessment.blocked", "schemaVersion": 1,
   "timestamp": "…", "correlationId": "…", "idempotencyKey": "assessment.blocked:…", "entityId": "<assessment id>",
   "sourceModule": "module_06_risk", "status": "pending", "payload": { … } }]
```

Event types:
- `risk.assessment.completed`, `risk.assessment.blocked`, `risk.assessment.requires_review`, `risk.assessment.failed`
- `risk.limit.reached`, `risk.lock.activated`, `risk.lock.released`
- `risk.configuration.updated`
- `risk.portfolio.exposure.updated`

Inbound types handled:
- `trade_idea.created` runs an assessment.
- `account.snapshot.updated`, `position.updated`, `market.data.updated` and `market.intelligence.updated` refresh the
  account status.
- Anything else is stored as `ignored`.
