# Module 07 architecture

## Components
| Layer | Code | Responsibility |
|---|---|---|
| Authentication | `aurum/auth/` (`service.py`, `passwords.py`, `roles.py`, `db_models.py`), `aurum/api/security.py`, `aurum/api/routes/auth.py` | Users (PBKDF2-SHA256), sessions (httpOnly cookie + CSRF), service tokens, roles and permissions, lockout |
| Intake | `aurum/approvals/intake.py` | Validates an idea payload: prices as `Decimal`, SL/TP on the right side, timeframe, expiry. Normalises Module 05 rows |
| Evidence | `aurum/approvals/evidence.py` | Builds the six evidence sections from Modules 03–06 and hashes the snapshot |
| Gateways | `aurum/approvals/gateways.py` | `RiskGateway` / `InProcessRiskGateway` (Module 06), `IdeaSource` (Module 05), quote time, locks |
| Policy | `aurum/approvals/policy.py`, `config/approvals.yaml` | Validated, versioned policy: expiry, review rules, handoff, management, notifications |
| State machine | `aurum/approvals/states.py` + PostgreSQL trigger | Allowed transitions for approvals and management requests |
| Repository | `aurum/approvals/repository.py` | Serialised writes (process lock + `pg_advisory_xact_lock`), audit hash chain, outbox, notifications, idempotency |
| Service | `aurum/approvals/service.py` | All business rules: intake, review, approve/reject/modify/cancel, expiry, handoff, Module 09 results, positions, management, inbound events, views |
| Worker | `aurum/approvals/worker.py` | Periodic tick: Module 05 sync, expiry, handoff, Module 05 feedback, outbox dispatch, email/Telegram delivery |
| Wiring | `aurum/approvals/wiring.py` | `build_service(engine, settings)` with lazy Module 04, 05 and 06 services |
| API | `aurum/api/routes/approvals.py`, `aurum/api/routes/system.py` (notifications merge) | `/api/v1/approvals/*`, `/api/v1/trade-management/*`, SSE stream |
| CLI | `aurum/approvals/cli.py` | `users …` and `approvals …` commands |
| Frontend | `aurum-frontend/src/pages/{LoginPage,ApprovalsPage,ApprovalDetailPage,TradeManagementPage,NotificationsPage}.tsx`, `src/components/approvals/*`, `src/hooks/useApprovals.ts`, `src/hooks/useSession.ts` | Sign-in, Command Centre, review with final confirmation, position management, notifications |

## Architecture
```mermaid
flowchart LR
  subgraph Sources
    M05[Module 05<br/>trade_candidates]
    N8N[n8n / Module 08]
  end
  subgraph M07[Module 07]
    API[FastAPI routes<br/>auth, approvals, trade-management]
    SVC[ApprovalService<br/>business rules]
    SM[State machine<br/>+ DB trigger]
    REPO[(Repository<br/>audit chain, outbox,<br/>notifications)]
    W[Worker<br/>tick every 15 s]
  end
  M06[Module 06<br/>RiskService]
  M09[Module 09<br/>not built]
  UI[React UI<br/>human decisions]
  DB[(PostgreSQL / TimescaleDB<br/>migration 0009)]

  M05 -- candidates --> W --> SVC
  N8N -- inbound events, service token --> API
  UI -- session cookie + CSRF --> API --> SVC
  SVC --> SM
  SVC <--> M06
  SVC --> REPO --> DB
  W -- outbox webhook --> N8N
  M09 -- pull queue, report results, service token --> API
  API -- SSE --> UI
```

## Approval lifecycle
```mermaid
sequenceDiagram
  participant M5 as Module 05
  participant W as Worker
  participant S as ApprovalService
  participant R as Module 06
  participant P as Person (UI)
  participant Q as Module 09 queue
  M5->>W: new trade candidate
  W->>S: ingest (validate, dedupe, version 1)
  S->>R: assess(proposal)
  R-->>S: PASS / REVIEW / BLOCK / ERROR
  S->>S: evidence snapshot + hash, request PENDING, notify
  P->>S: open request (UNDER_REVIEW)
  P->>S: approve(exact parameters, evidence hash, acknowledgements)
  S->>S: 14 checks, then APPROVED (or 409 listing every failed check)
  S->>R: validate + fresh assessment (handoff)
  S->>Q: EXECUTION_PENDING + trade.approved_for_execution (one transaction)
  Q-->>S: execution results (submitted / filled / failed)
```
After a final approval, the handoff is attempted immediately. If that attempt fails (for example, Module 06 is briefly
unavailable), the approval stands and the worker retries it on every tick until the handoff deadline.

## Trade execution handoff
```mermaid
flowchart TD
  A[APPROVED] --> B{Final validation}
  B -- idea changed, withdrawn or deadline passed --> X[INVALIDATED<br/>nothing published]
  B -- Module 06 unavailable, before deadline --> D[stays APPROVED<br/>audit handoff.deferred, retry next tick]
  B -- fresh decision not PASS/REVIEW, new warnings,<br/>size changed, stale quote, account changed --> X
  B -- all pass --> C[EXECUTION_PENDING<br/>execution_handoffs row + outbox event]
  C --> E[Module 09 pulls the queue]
  E --> F{reported result}
  F -- submitted --> G[EXECUTION_SUBMITTED]
  F -- filled / partial --> H[EXECUTED + managed position]
  F -- rejected / failed --> I[EXECUTION_FAILED + critical notification]
```

## Trade management lifecycle
```mermaid
flowchart LR
  P[Open position<br/>account mirror or Module 09] --> R[Person requests a change<br/>validated against the position]
  R --> PEND[PENDING]
  PEND -- second look: position unchanged,<br/>still valid, confirmation --> APP[APPROVED]
  PEND --> REJ[REJECTED]
  PEND -- expiry_minutes --> EXP[EXPIRED]
  APP --> EP[EXECUTION_PENDING<br/>trade_management.approved]
  EP --> M9[Module 09] --> RES[EXECUTED / EXECUTION_FAILED]
```

## Module integration
```mermaid
flowchart LR
  M05[Module 05 AI engine] -- trade_candidates / trade_idea.* --> M07
  M07[Module 07] -- module05.feedback: approved, rejected, modification --> M05
  M07 -- assess / validate / cancel --> M06[Module 06 risk]
  M07 -- outbox events, webhook or pull --> M08[Module 08 / n8n]
  M08 -- inbound events, service token, never a decision --> M07
  M07 -- trade.approved_for_execution, trade_management.approved --> M09[Module 09 execution<br/>not built]
  M09 -- execution results, position.updated --> M07
  M07 -- operational events, /statistics, /health --> M10[Module 10 monitoring]
```

## Failure handling
```mermaid
flowchart LR
  T[one transaction:<br/>state + audit + outbox + notification] --> C[(commit)]
  C --> O[outbox: pending]
  O -->|worker POST| WH[webhook]
  WH -- 2xx --> SENT[delivered]
  WH -- error --> RETRY[backoff 30 s to 30 min] --> O
  RETRY -- max attempts --> DEAD[dead letter<br/>visible in /events]
  N[notification delivery] -- channel not configured --> NC[recorded not_configured<br/>never 'sent']
  N -- SMTP/Telegram error --> NR[retry with backoff] -- max --> ND[dead]
```
- **Concurrency.** Every status change is a conditional `UPDATE … WHERE version = :v AND status = :s`. A lost race returns
  `409 CONCURRENT_CHANGE`. Writes are serialised with a process lock and a PostgreSQL advisory lock. Partial unique
  indexes allow only one active request per idea, one final decision per request and one active management request per
  position.
- **Idempotency.** Decisions carry `clientRequestId`, inbound events and execution results carry `eventId`, and outbox
  rows carry a unique `idempotency_key`. A retry returns the original result instead of acting twice.
- **Worker isolation.** Each step of a tick is independent. A failing step is logged and the others still run.
- **Module 06 unavailable.** At approval, the approval is refused with the reason. At handoff, it is deferred until the
  deadline and then invalidated. It is never handed off unchecked.

## Key decisions
1. **Same database, no new services.** The outbox table, with pull and webhook delivery, replaces a message bus. The
   worker is a CLI loop and can also run as a container.
2. **The service is the only authority.** n8n, Telegram and email can only notify. Every decision endpoint requires a
   signed-in person, and `HUMAN_ONLY` permissions are stripped from service tokens.
3. **Decimal everywhere.** Prices and volumes are `Decimal` in Python and `NUMERIC` in SQL. Confirmations are compared
   as decimals.
4. **No silent changes.** A modification creates a new idea version and a new request with a fresh risk assessment. The
   original request is kept as it was.
5. **No duplicated logic.** Risk numbers always come from Module 06, and analysis always comes from Modules 03–05.
