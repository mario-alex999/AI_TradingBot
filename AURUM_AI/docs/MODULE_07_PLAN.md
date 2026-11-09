# Module 07 plan: Human Approval and Trade Management Engine

Written 2026-11-09, before implementation, as section 3 of the Module 07 specification requires. It records what the
repository inspection found and the decisions taken. The build follows the three checkpoints suggested with the
specification.

## 1. Repository architecture report

| Area | What exists | Consequence for Module 07 |
|---|---|---|
| Backend | Python 3.11, FastAPI app factory (`aurum/api/app.py`), routers under `/api/v1`, camelCase JSON, Typer CLI (`aurum/cli.py`), pydantic `Settings` from `.env` | New package `aurum/approvals/`, router `/api/v1/approvals` and `/api/v1/trade-management`, CLI group `approvals` |
| Database | PostgreSQL 16 + TimescaleDB in Docker; SQLAlchemy models on one `Base`; numbered SQL migrations applied by `init-db` (0002–0008); shared types `UTCDateTime`, `JsonType`, `BigIntPK`; Module 06 `Money = NUMERIC(24,8)` | Migration `0009_human_approval.sql`; the same types; Decimal for all money and prices |
| **Authentication** | **None on the backend.** Reads are open; writes need one shared `X-AURUM-Admin-Key`. The frontend "sign in" only picks a local demo profile, and its store says the backend should later issue an httpOnly session cookie | **Blocker for attributable approvals.** Module 07 adds real backend users, sessions and service tokens (`aurum/auth/`). There is no existing user model, so this is not a duplicate. The admin key keeps working for Modules 01–06 |
| Module 05 | `trade_candidates` (+ evidence, gates, AI review) with its own status machine `new → under_review → approved/rejected/expired`. Its "approve" records agreement with the analysis and sends nothing to MT5. Prices are FLOAT. It publishes no events | Intake reads candidates through an adapter (and accepts `trade_idea.*` events). Module 07 decisions are written back through Module 05's own service, not by editing its tables. Prices are converted to Decimal at intake |
| Module 06 | `RiskService.assess / validate_assessment / set_assessment_state`, PASS/REVIEW/BLOCK/ERROR, `module07` contract block, locks, outbox/inbox | Called in-process through a `RiskGateway` adapter. No risk maths is repeated in Module 07 |
| Modules 02–04 | COT, technical analysis (structure, SMC), market intelligence services with API routes | The evidence service reads them and labels each section confirmed / AI interpretation / missing |
| Events | Module 06 has its own outbox (`risk_events`) and inbox, webhook dispatch with backoff. No shared event bus | Module 07 gets the generic `outbox_events` + `idempotency_records` tables from the specification, with the same reliable pattern. n8n consumes them |
| Notifications | `GET /api/v1/notifications` is *computed* from system state; read state lives in the browser | Module 07 stores notifications with server-side read state and merges them into the same feed, so the existing page keeps working |
| Positions / Module 09 | Module 09 does not exist. The account mirror (`trade_records`, filled by read-only `sync-account`) holds real DEMO positions | A `PositionSource` adapter: the account mirror now (labelled as such, with its age), Module 09 later. Module 07 never touches MT5 |
| Frontend | React + Vite + TS, React Query, Tailwind tokens (light and dark themes), `DataTable`, `Drawer`, `ConfirmDialog`, `Tabs`, `Badge`; pages for AI Signals, Trade Management, Notifications | New pages in the same style (standing rule: extend, never replace). The Trade Management and Notifications pages are extended, not rebuilt |
| Tests | pytest with SQLite template databases; vitest with `mockApi` | Same patterns. Postgres-only tests are opt-in |
| Docker | Compose runs only the database; the backend runs on Windows because the MT5 package is Windows-only; n8n has its own compose file | API and worker for Module 07 can run in Docker without MT5. A compose profile is added (Checkpoint 3) |

## 2. Implementation plan (three checkpoints)
1. **Checkpoint 1: core engine.**
   - Authentication, roles and service tokens.
   - Database and migration.
   - Intake with validation and idempotency.
   - Evidence snapshots; Module 06 integration.
   - State machine; approve, reject and modify.
   - Expiry; final validation and execution handoff; outbox; audit; notifications (stored).
   - API and CLI.
   - Backend tests: unit, state machine, security, concurrency, expiry, end to end.
2. **Checkpoint 2: user interface.**
   - Real sign-in.
   - Pages: Approval Command Centre, Pending, Review (with final confirmation), Approval detail.
   - Open positions; Trade Management; the Notifications centre with server read state; History; Settings.
   - Real-time updates (SSE); frontend tests.
3. **Checkpoint 3: integration and validation.**
   - Module 05 write-back; n8n workflows; Module 09 contract endpoints.
   - Management handoff; email and Telegram adapters.
   - Failure tests; Docker profile; the 13 documents; the delivery report.

## 3. Required integrations
| Module | Direction | How |
|---|---|---|
| 05 | in: ideas; out: status, rejection reason, modification feedback | Adapter over `trade_candidates` + `trade_idea.*` inbound events; write-back via `DecisionService`; outbox events |
| 06 | request assessment / validation; receive PASS/REVIEW/BLOCK/ERROR | `RiskGateway` (in-process `RiskService`) |
| 08 / n8n | events both ways; scheduling | Outbox pull/webhook; `POST /approvals/events/inbound` (service token). n8n cannot approve: human endpoints refuse service tokens |
| 09 | out: `trade.approved_for_execution`, management requests; in: execution results, positions | Outbox + pull endpoints and result endpoint for a service token. **Module 09 is not built; tested with a test adapter only** |
| 10 | out: operational events and metrics | Outbox events + `/approvals/statistics` |

## 4. Database design (migration 0009)
The 19 tables named in the specification are:
- Ideas and approvals: `trade_ideas`, `trade_idea_versions`, `approval_requests`, `approval_decisions`,
  `approval_evidence_snapshots`, `approval_risk_snapshots`, `modification_requests`.
- Execution and positions: `execution_handoffs`, `execution_results`, `managed_positions`, `trade_management_requests`,
  `trade_management_decisions`.
- Notifications: `notifications`, `notification_deliveries`.
- Administration and audit: `reviewer_assignments`, `approval_policies`, `approval_audit_logs`.
- Infrastructure: `idempotency_records`, `outbox_events`.

Authentication adds `users`, `auth_sessions` and `service_tokens`.

**Safeguards**
- One decision per approval: a unique constraint on `approval_decisions.approval_id`.
- One handoff per approval: unique `execution_handoffs.approval_id`.
- An optimistic `version` column; status changes are conditional updates.
- Transition and append-only triggers on PostgreSQL.
- Hash-chained audit log.
- Evidence snapshot hashes.

## 5. API contract plan
The routes listed in section 17 of the specification are adapted as follows:
- `/api/v1/auth/*` for login, logout, session and CSRF.
- `/api/v1/approvals/*` and `/api/v1/trade-management/*`.
- `/api/v1/notifications` is extended.
- Module 09 contract endpoints.
- An SSE stream.

Conventions:
- Requests and responses use camelCase.
- Errors are structured (`code`, `message`, `details`).
- `Idempotency-Key` header support.
- Rate limiting.
- Correlation IDs.

## 6. Frontend plan
See Checkpoint 2. All screens use the existing components and tokens. Both light and dark themes are supported, and
dark is available from the top-right toggle.

## 7. Testing plan
- Backend (pytest, SQLite templates, plus opt-in Postgres): unit, state machine, security, concurrency (threads against
  one database), expiry, failure, and the 10 end-to-end scenarios of section 23.
- Frontend (vitest).
- External modules that do not exist (Module 09) use labelled test adapters.

## 8. Dependencies and blockers
- **Module 09 does not exist.** The handoff is real (outbox and pull API), but execution results can only come from
  tests until Module 09 is built.
- **No backend authentication existed.** It is built here.
- **Positions come from the account mirror,** which is as fresh as the last `sync-account`.
- **Email needs SMTP settings.** Without them, email delivery is recorded as `not_configured`, never as sent.
- **Module 05 prices are FLOAT.** They are converted to Decimal at intake and the original value is kept in the payload.
