# Module 07 delivery report: Human Approval and Trade Management Engine

Date: 2026-11-09. Module version `7.0.0`, event contract `1.0`. Nothing has been committed. All changes are in the
working tree for review.

---

## A. Implementation summary

**What was built.** A complete human-in-the-loop layer between AI trade ideas (Module 05) and execution (Module 09):
- backend authentication with roles, sessions, CSRF and service tokens;
- intake from Module 05 and from events, with validation, deduplication and immutable versions;
- hashed evidence snapshots;
- Module 06 risk integration;
- an approval state machine enforced in code and in PostgreSQL;
- 14 server-side approval checks with an exact-parameter confirmation;
- expiry, multi-person approval, reviewer assignment, and modification by new version;
- final validation and execution handoff through a transactional outbox;
- the Module 09 pull and report contract;
- positions and trade-management requests (including emergency exit) with their own approval;
- in-app, email and Telegram notifications with retries and a dead letter;
- a hash-chained append-only audit;
- a background worker, CLI, REST API and SSE stream;
- a React UI (sign-in, Command Centre, review with confirmation, position management, notifications);
- n8n workflows, a Docker profile, 13 documents and 350 new automated tests (340 backend, 10 frontend).

**Major architectural decisions.**
1. **Same database, no new services.** 22 new tables in the existing TimescaleDB. An outbox table with webhook and
   pull delivery replaces a message bus.
2. **The backend is the single authority.** Decisions need a signed-in person. Service tokens can never hold a
   human-only permission, so n8n, Telegram and email can only inform.
3. **Defence in depth on state.**
   - The state machine runs in Python and also as a PostgreSQL trigger generated from the same table.
   - Optimistic versions, an advisory lock and partial unique indexes.
   - Append-only triggers on history tables.
4. **No duplicated logic.** All risk numbers come from Module 06 (`RiskGateway`), and all analysis from Modules 03–05.
5. **Decimal end to end.** Prices are `NUMERIC` in SQL and `Decimal` in Python. Confirmations compare decimals.
6. **No silent changes.** A modification creates a new idea version and a new request with a fresh assessment.
   Handoff refuses a changed size or new warnings.

**Features completed.** Everything in sections B–F is implemented and tested, apart from the items in K.

## B. File structure

**New backend files (`AURUM_AI/`):**
- `aurum/auth/`: `__init__.py`, `db_models.py`, `passwords.py`, `roles.py`, `service.py`.
- `aurum/approvals/`: `__init__.py`, `cli.py`, `db_models.py`, `evidence.py`, `gateways.py`, `intake.py`, `policy.py`,
  `repository.py`, `service.py`, `states.py`, `wiring.py`, `worker.py`.
- `aurum/api/routes/approvals.py` and `aurum/api/routes/auth.py`.
- `sql/migrations/0009_human_approval.sql`.
- `config/approvals.yaml` (seed policy).
- `tests/approvals/` (11 test files plus the harness and conftest).
- `n8n/workflows/aurum_approval_events_receiver.json` and `aurum_approval_expiry_reminder.json`.
- `Dockerfile`, `.dockerignore`, `docker-compose.approvals.yml`.
- `docs/MODULE_07_PLAN.md` and `docs/module07/*.md` (13 documents plus this report).

**Modified backend files:**
- `aurum/api/app.py`: auth and approval wiring.
- `aurum/api/routes/system.py`: the notifications merge.
- `aurum/cli.py`: `users` and `approvals` commands, and `sync-account --loop`.
- `aurum/config.py`: Module 07 settings.
- `.env.example`, `README.md`, `docs/ACCOUNT_API.md`.
- `tests/risk/test_safety.py`: the PostgreSQL harness fix (see G).

**New frontend files (`aurum-frontend/src/`):**
- Pages: `pages/ApprovalsPage.tsx`, `pages/ApprovalDetailPage.tsx`.
- Components (`components/approvals/`): `ApprovalBits.tsx`, `DecisionDialogs.tsx`, `EvidencePanel.tsx`,
  `PositionManagement.tsx`.
- Hooks: `hooks/useApprovals.ts`, `hooks/useSession.ts`.
- Services: `services/approvalService.ts`, `services/authService.ts`.
- Types and utilities: `types/approvals.ts`, `utils/approvals.ts`.
- Tests: `test/approvals.test.tsx`.

**Modified frontend files:** `App.tsx` (routes, session bootstrap, live stream), `config/navigation.ts`,
`pages/LoginPage.tsx`, `pages/NotificationsPage.tsx`, `pages/SettingsPage.tsx`, `pages/TradeManagementPage.tsx`,
`components/layout/UserMenu.tsx`, `services/api.ts` (CSRF header, error details), `stores/authStore.ts`,
`types/auth.ts`, `types/system.ts`, `test/render.tsx`. All of these extend the existing components and theme; nothing
was replaced.

**Configuration:** `.env.example` (auth, approvals, SMTP, Telegram), `config/approvals.yaml`,
`docker-compose.approvals.yml`.

## C. Database
- **Tables (22).** `users`, `auth_sessions`, `service_tokens`, `trade_ideas`, `trade_idea_versions`,
  `approval_requests`, `approval_decisions`, `approval_evidence_snapshots`, `approval_risk_snapshots`,
  `modification_requests`, `execution_handoffs`, `execution_results`, `managed_positions`,
  `trade_management_requests`, `trade_management_decisions`, `notifications`, `notification_deliveries`,
  `reviewer_assignments`, `approval_policies`, `approval_audit_logs`, `idempotency_records`, `outbox_events`.
- **Relationships.**
  - An idea has many immutable versions and many requests (only one active). A request can have a parent request (from
    a modification).
  - A request has many decisions (one final), snapshots, modifications and at most one handoff. A handoff has many
    results.
  - A position has management requests (one active), each with decisions (one final).
  - A notification has many deliveries.
  - See the ER diagram in [DATABASE_SCHEMA.md](DATABASE_SCHEMA.md).
- **Migration.** `0009_human_approval.sql` is additive and idempotent. It was applied twice to a scratch PostgreSQL 16
  + TimescaleDB database and to the development database (`init-db`, 2026-11-09).
- **Indexes.**
  - Queue indexes (status, expiry or next attempt).
  - Lookup indexes.
  - 4 partial unique safety indexes: one active request per idea, one final decision, one active management request
    per position, one final management decision.
  - 9 triggers: state transitions, plus 8 append-only.

## D. Backend
- **Services.**
  - `AuthService` handles users, PBKDF2 passwords, lockout, sessions and service tokens.
  - `ApprovalService` holds all business rules.
  - `ApprovalRepository` handles serialised writes, the audit chain, the outbox, notifications and idempotency.
  - `InProcessRiskGateway` connects to Module 06, and the Module 05 `IdeaSource` and feedback go through
    `DecisionService`.
- **APIs.**
  - `/api/v1/auth` (4 routes), `/api/v1/approvals` (31 routes, including SSE and the Module 09 contract) and
    `/api/v1/trade-management` (7 routes).
  - The existing `/api/v1/notifications` is extended.
  - See [API_DOCUMENTATION.md](API_DOCUMENTATION.md).
- **Background workers.**
  - `python -m aurum.cli approvals worker` ticks every 15 s: Module 05 sync, expiry and "expiring soon", handoff,
    Module 05 feedback, outbox webhook dispatch, and email/Telegram delivery. Each step is isolated, idempotent and safe
    to run in more than one copy.
  - The same worker is available as the `approvals-worker` container.
- **Event handlers.**
  - Inbound: `trade_idea.created/updated/cancelled`, `execution.*`, `position.updated`, and
    `risk.assessment.completed` / `market.data.updated` (recorded only).
  - Outbound: 18 event types, 17 published plus the internal `module05.feedback` (see
    [EVENT_CONTRACTS.md](EVENT_CONTRACTS.md)).

## E. Frontend
- **Pages.**
  - **Sign-in:** a server account when the backend has users, with a demo-workspace fallback.
  - **Trade Approvals** (Command Centre): KPIs, Pending and All views, filters, pagination, recent handoffs and manual
    sync.
  - **Approval detail:** parameters, Module 06 assessment with revalidate, evidence tabs, decisions, handoff, versions
    and history.
  - **Trade Management:** existing page, extended with position management and change requests.
  - **Notifications:** existing page, extended with server read state and links.
  - **Settings:** existing page, extended with session information.
- **Components.**
  - Status, risk and direction badges, a live countdown, the modal and field rows, and a problem list.
  - `ApproveDialog`: exact parameter summary, a checkbox for each warning, confirmation, idempotent request id.
  - `RejectDialog` (reason codes, comment for "other") and `ModifyDialog` (new values per kind).
  - `EvidencePanel`, which labels each item as confirmed, AI interpretation, historical, contradictory, risk warning or
    missing.
  - `PositionManagement`, with request and decide dialogs and the emergency phrase.
- **Dashboard features.**
  - Live refresh over SSE.
  - Counts per status.
  - Expiry countdowns.
  - Clear "Module 09 is not built" messaging.
  - No invented data in demo mode.
- **User interactions.**
  - Opening a request marks it under review. It never approves.
  - Every disabled button gives its reason: role, BLOCK, evidence not seen, warning not acknowledged, confirmation not
    ticked.
  - Server refusals list every failed check.
  - Light and dark themes both work.

## F. Integrations
| Module | Status | Detail |
|---|---|---|
| 05 AI engine | **REAL** | Pulls `trade_candidates` via `DecisionService`, closes requests the source withdrew, and writes decisions back through `module05.feedback`. Automated tests use fakes for Module 05. **Verified read-only against the live development database (2026-11-09):** the real `DecisionService` returned 1 active candidate (GBPUSD), which was converted and correctly refused at intake as already expired (`IDEA_EXPIRED`). Nothing was ingested. Live write-back has not been exercised against a real candidate |
| 06 Risk | **REAL** | `RiskService` in-process: assess, validate, cancel, locks, quote time, account. `test_integration_module06.py` runs the real engine. BLOCK and ERROR are never approvable |
| 08 / n8n | **REAL transport, OPTIONAL setup** | Outbox webhook with retries and dead letter, pull API with service token, inbound events. Two workflows shipped (inactive, read or notify only, verified by tests). The live n8n container exists, but the workflows were **not imported or run** in it during this delivery |
| 09 Execution | **CONTRACT ONLY (blocked: module not built)** | Execution payload, queue, results, idempotency and position updates are implemented. Tested only with the labelled `FakeExecution` adapter. **Nothing is executed** |
| 10 Monitoring | **CONTRACT ONLY (module not built)** | `/approvals/health`, `/approvals/statistics`, outbox events, the `approvals status` CLI and the container healthcheck |

## G. Test results
All runs below were on 2026-11-09, on this machine.

| Suite | Tests | Passed | Failed | Skipped |
|---|---:|---:|---:|---:|
| Module 07 backend (`tests/approvals`) + Module 06 (`tests/risk`) | 493 | 491 | 0 | 2 (PostgreSQL URL) |
| PostgreSQL-only tests (`-k postgres`, scratch database) | 2 | 2 | 0 | 0 |
| Older backend suites (Modules 01–05, rest of `tests/`) | 715 | 712 | 0 | 3 (opt-in: Postgres URL, live AI, live CFTC) |
| Frontend (`vitest`, 16 files) | 115 | 115 | 0 | 0 |
| Frontend type check (`tsc -b`) and lint (`eslint --max-warnings=0`) | | clean | | |

- Module 07 alone has **340 backend tests** (246 of them are the exhaustive state-machine pairs) and **10 frontend
  tests**.
- **Coverage:** not measured. No coverage tool is installed in the project's virtual environment, and it could not be
  installed (no package index reachable from this environment).
- **Outstanding issues.**
  - Both PostgreSQL-only tests (Module 07's and Module 06's `test_safety`) had a harness bug: `exec_driver_sql` made
    psycopg treat the `%` in trigger messages as placeholders. They had never run, because they skip without a
    database URL.
  - They now execute the SQL through the raw driver connection, as `init-db` does, and both pass. The migrations
    themselves were never affected.

## H. Security
**Implemented controls:**
- PBKDF2-SHA256 passwords (600k iterations, at least 12 characters), never in arguments, environment or logs.
- Lockout after 5 failures for 15 minutes, plus a sign-in rate limit.
- httpOnly `SameSite=Lax` session cookie with idle and absolute expiry. Only token hashes are stored.
- CSRF double-submit on every write.
- Service tokens are hashed, scoped, expiring and revocable, and can never hold a human-only permission.
- Five roles with account scoping.
- 30 decisions per minute per user.
- Append-only history and a hash-chained audit.
- Event payloads carry no personal data or evidence text.
- Secrets are masked in logs. Telegram errors never contain the token.
- The Docker image runs as a non-root user and contains no secrets and no MT5 package.

**Remaining risks:**
- No MFA.
- HTTP on localhost (set `AUTH_COOKIE_SECURE=true` behind TLS).
- Rate limits are held in process memory.
- Notification channels expose trade summaries to whoever reads them.
- Positions are only as fresh as `sync-account` until Module 09 exists.

See [SECURITY_GUIDE.md](SECURITY_GUIDE.md).

## I. How to run
From `AURUM_AI` in PowerShell unless noted.
```powershell
# Install dependencies
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
cd ..\aurum-frontend; npm install; cd ..\AURUM_AI

# Run migrations (database container first if it is not running)
docker compose --env-file .env up -d
python -m aurum.cli init-db

# First user (password prompted, hidden), optional service token for n8n
python -m aurum.cli users create alice --role admin
python -m aurum.cli users token-create n8n --scope approvals:view --scope events:publish

# Start backend
python -m aurum.api                                   # http://127.0.0.1:8000

# Start frontend (second terminal)
cd ..\aurum-frontend; npm run dev                     # http://localhost:5173

# Start background workers (third terminal), and keep the account mirror fresh (DEMO accounts only)
python -m aurum.cli approvals worker
python -m aurum.cli sync-account --loop

# Run tests
.\.venv\Scripts\python.exe -m pytest -q tests/approvals tests/risk
cd ..\aurum-frontend; npx tsc -b --noEmit; npx eslint src --max-warnings=0; npx vitest run

# Start Docker services (worker in a container; add --profile api for the API on :8001)
docker compose -f docker-compose.approvals.yml --env-file .env up -d --build
docker compose -f docker-compose.n8n.yml --env-file .env up -d          # n8n (optional)
```

## J. Manual testing guide
1. Run the commands in I. Create `alice` (admin) and `rita` (reviewer).
2. Open `http://localhost:5173`. The sign-in form appears. Sign in as `alice`.
3. **Settings → Data:** turn demo data **off**. Approvals are never shown from demo data.
4. **Trade Approvals → Sync from Module 05**, or wait for the worker. If Module 05 has no fresh candidates, submit one:
   ```powershell
   # as alice, from the browser console or any HTTP client with the session cookie + X-CSRF-Token:
   # POST /api/v1/approvals/ideas  {"ideaId":"manual-1","symbol":"EURUSD","market":"forex","direction":"BUY",
   #   "entry":"<near current price>","stopLoss":"<below>","takeProfits":["<above>"],"timeframe":"H1","strategy":"manual",
   #   "createdAt":"<now UTC>","expiresAt":"<now+1h>","analysisVersion":"manual","correlationId":"m1","schemaVersion":"1.0"}
   ```
5. Open the request. Its status becomes **Under review**. Check the parameters, the Module 06 assessment and each
   evidence tab. Missing sections say why.
6. Click **Approve…**. The button stays disabled, with a tooltip giving the reason, until the evidence has loaded, every
   risk warning is ticked and the confirmation is ticked. The dialog shows the exact values and the maximum loss.
7. Approve. Expected results:
   - `Awaiting execution` (EXECUTION_PENDING), and the toast "Handed to Module 09".
   - History shows `status approved`, then `status execution pending`.
   - `GET /api/v1/approvals/execution/queue` (with a service token that has `execution:read`) shows the payload.
   - **No order is placed anywhere.**
8. Negative paths:
   - Wait more than 5 minutes, then approve another request: "The risk assessment is older than 300 s". Click
     **Revalidate risk**, then approve.
   - A trade Module 06 blocks: Approve is disabled, and the red banner says it can never be approved.
   - Sign in as `rita` in a second browser and approve the same request at the same moment: one wins, the other sees
     "This request changed…".
   - Reject with "Other" and no comment: Reject stays disabled.
9. **Trade Management:** with a DEMO position in the account mirror, choose **Request change → Move stop-loss**
   (tighter), then approve it with the confirmation. A wider stop is refused. **Emergency exit** needs the phrase
   `CLOSE EURUSD`.
10. **Notifications:** approval notifications appear with **Open request**. Marking them read persists on the server.
11. `python -m aurum.cli approvals status` reports a valid audit chain and the outbox counts.

## K. Remaining work
| Item | Why |
|---|---|
| Real execution, fills and live positions from Module 09 | **Blocked:** Module 09 does not exist. The contract is ready and tested with an adapter |
| Pending-order management (cancel or modify a pending order) | **Blocked:** no source reports pending orders before Module 09. The API returns `NOT_AVAILABLE`, and the UI does not offer the action |
| Module 10 consumption of health and events | **Blocked:** Module 10 does not exist. The endpoints and events are ready |
| Importing and activating the n8n workflows in the running n8n | Needs a person: choosing the credential in the n8n UI. Workflow files ship inactive by design |
| Live email and Telegram delivery | Needs SMTP and Telegram credentials from the owner. The adapters are tested with fakes, and a missing configuration is recorded as `not_configured` |
| Line-coverage figure | No coverage tool available offline in this environment |
| MFA, and a shared (Redis) rate limit for multi-replica deployments | Out of scope for a single-user local deployment. Listed as remaining risks |
| `Idempotency-Key` HTTP header | Idempotency is provided by `clientRequestId` (decisions and management) and `eventId` (events and results). The header named in the plan is not read |

## L. Completion status
| Requirement (specification §30 and rules §29) | Status | Evidence |
|---|---|---|
| Repository inspection complete | COMPLETED | `docs/MODULE_07_PLAN.md` §1 |
| Architecture report documented | COMPLETED | `MODULE_07_ARCHITECTURE.md` |
| Database models implemented | COMPLETED | `aurum/approvals/db_models.py`, `aurum/auth/db_models.py`. `test_migration.py` |
| Database migrations run successfully | COMPLETED | Applied on scratch PostgreSQL (twice) and on the dev database. PostgreSQL tests pass |
| Trade idea intake works | COMPLETED | `test_intake.py`, E2E S1 and S9 |
| Evidence retrieval works | COMPLETED | Evidence API and UI. Hash checked at approval |
| Module 06 integration works | COMPLETED | `test_integration_module06.py` (real `RiskService`) |
| Approval state machine | COMPLETED | 246 transition tests and the PostgreSQL trigger test |
| Approve, reject, modify, cancel | COMPLETED | `test_approval_rules.py`, E2E S1–S10 |
| Expiry | COMPLETED | Expiry tests, worker, approve-time check |
| Final validation and execution handoff | COMPLETED | Handoff tests (pass, invalidate, deferred, deadline) |
| Module 09 execution integration | BLOCKED | Module 09 not built. Contract implemented and tested with an adapter only |
| Trade management requests and approval | COMPLETED | `test_management_worker_failures.py` and UI |
| Pending-order management | BLOCKED | No pending-order source before Module 09 |
| Emergency exit | COMPLETED | Phrase, permission, supersede, 5-minute expiry tests |
| Notifications: in-app | COMPLETED | Server read state, UI merge |
| Notifications: email and Telegram | PARTIALLY COMPLETED | Adapters, retries and `not_configured` tested with fakes. Not sent live (no credentials) |
| Outbox and events | COMPLETED | Outbox, webhook retry and dead letter, pull, SSE (tested) |
| n8n integration | PARTIALLY COMPLETED | Transport and workflows done and tested. Not imported into the live n8n |
| Module 05 integration (in and feedback) | COMPLETED | Sync and feedback tests (fakes). Real read and intake path verified read-only against the dev database. Feedback never changes the Module 07 decision |
| Module 10 integration | PARTIALLY COMPLETED | Health, statistics and events exposed. No consumer exists |
| Audit trail | COMPLETED | Hash chain, tamper detection test, append-only triggers |
| Authentication, roles, CSRF, service tokens | COMPLETED | `test_api_security.py` |
| Approval dashboard functional | COMPLETED | `ApprovalsPage`. Frontend tests |
| Approval review interface functional | COMPLETED | `ApprovalDetailPage` and dialogs. Frontend tests |
| Open position interface functional | COMPLETED | Positions from the account mirror, labelled with source and age |
| Trade management interface functional | COMPLETED | `PositionManagement` |
| Admin configuration works | PARTIALLY COMPLETED | Policy and assignments via API (versioned, audited) and the CLI for users. No dedicated policy editor screen in the UI |
| API documentation complete | COMPLETED | `API_DOCUMENTATION.md` |
| Event contracts documented | COMPLETED | `EVENT_CONTRACTS.md` |
| Security tests pass | COMPLETED | 14 API security tests plus rules tests |
| Concurrency tests pass | COMPLETED | Threaded double-approve and approve-against-expiry tests. Other races rely on the same conditional update (see TESTING_GUIDE) |
| Integration tests pass | COMPLETED | Module 06 real-engine tests. Module 05 sync tests |
| End-to-end tests pass | COMPLETED | 10 scenarios (S1–S10) |
| Docker configuration works | COMPLETED | Image built, and `approvals status` ran in the container against the live database |
| Existing modules remain functional | COMPLETED | Older suites 712 passed, 0 failed. Module 06 suite passes. Frontend 115/115 |
| No direct MT5 execution in Module 07 | COMPLETED | No MT5 import in `aurum/approvals` or `aurum/auth`. The image has no MT5 package |
| No automatic trade approval | COMPLETED | Human-only decide permission. Review never approves. Tests |
| BLOCK never overridable | COMPLETED | Check 8, UI, tests |
| No silent parameter changes | COMPLETED | Confirmation match, new version on modify, handoff checks |
| No hard-coded secrets | COMPLETED | Settings from the environment. Password prompt only. n8n test checks the workflows |
| No non-functional buttons | COMPLETED | Every control calls a real endpoint or is disabled with its reason. Pending-order actions are not offered |
| Mock data never shown as real | COMPLETED | No demo approvals. Sources and ages labelled |
| 13 documents with the 6 diagrams | COMPLETED | `docs/module07/` (architecture, lifecycle, handoff, management, integration, failure) |
| All known limitations documented | COMPLETED | Section K, `SECURITY_GUIDE.md`, `TROUBLESHOOTING.md` |
