# Module 07: Human Approval and Trade Management Engine

Module 07 is where a person decides. Module 05 proposes a trade idea and Module 06 assesses its risk. Module 07 then
shows the evidence and asks an authorised person to **approve, reject or request changes**. Only an explicit human
approval, re-checked at handoff, becomes an execution request for Module 09.

> **Module 07 never places, modifies or closes an order, and never connects to MetaTrader 5.** An approval is an
> execution *request* that Module 09 must validate again with Module 06 before trading. Module 09 is not built yet, so
> approved requests wait in its queue (`GET /api/v1/approvals/execution/queue`).

## What it does
- **Intake.** It takes trade ideas from Module 05: the worker pulls `trade_candidates`, a service can send
  `trade_idea.created` events, and a person can submit one manually. Ideas are validated, deduplicated and stored as
  immutable versions.
- **Evidence.** It builds an evidence snapshot covering the trade, technical, market, institutional, AI and risk
  sections, with a content hash. Missing data is shown as missing, never invented.
- **Risk.** It gets a Module 06 assessment through `RiskGateway`. `BLOCK` and `ERROR` can never be approved by anyone.
- **Review.** It runs the state machine with optimistic locking, expiry and optional multi-person approval. Approval
  runs **14 server-side checks**, including confirmation of the exact parameters and the evidence hash.
- **Handoff.** Final validation runs first: a fresh Module 06 check, market data age and the account. Then
  `trade.approved_for_execution` is published through the transactional outbox.
- **Trade management.** A person requests a change to an open position: move the stop or take-profit, breakeven,
  trailing stop, partial close, close, or emergency exit. A person approves it, and it goes to Module 09 the same way.
- **Notifications.** In-app notifications are always on. Email and Telegram are sent when configured and are
  informational only.
- **Audit.** The audit log is hash-chained and append-only. Decisions, versions, snapshots and results are also
  append-only.

## Documents
| Document | Contents |
|---|---|
| [MODULE_07_ARCHITECTURE.md](MODULE_07_ARCHITECTURE.md) | Components, data flow, diagrams |
| [DATABASE_SCHEMA.md](DATABASE_SCHEMA.md) | Tables in migration 0009, keys, indexes, triggers |
| [API_DOCUMENTATION.md](API_DOCUMENTATION.md) | Every endpoint, permissions, request and response shapes, errors |
| [EVENT_CONTRACTS.md](EVENT_CONTRACTS.md) | Outbox envelope, outbound and inbound events, the Module 09 contract |
| [APPROVAL_STATE_MACHINE.md](APPROVAL_STATE_MACHINE.md) | States, transitions, guarantees, the 14 approval checks |
| [TRADE_MANAGEMENT_GUIDE.md](TRADE_MANAGEMENT_GUIDE.md) | Position actions, validation, emergency exit |
| [SECURITY_GUIDE.md](SECURITY_GUIDE.md) | Authentication, roles, CSRF, tokens, safety rules |
| [TESTING_GUIDE.md](TESTING_GUIDE.md) | Test suites and how to run them |
| [DOCKER_SETUP.md](DOCKER_SETUP.md) | Worker and API containers, n8n |
| [ENVIRONMENT_VARIABLES.md](ENVIRONMENT_VARIABLES.md) | Every Module 07 setting |
| [MODULE_07_INTEGRATION_GUIDE.md](MODULE_07_INTEGRATION_GUIDE.md) | Modules 05, 06, 08 (n8n), 09, 10 |
| [TROUBLESHOOTING.md](TROUBLESHOOTING.md) | Common problems and fixes |
| [MODULE_07_DELIVERY_REPORT.md](MODULE_07_DELIVERY_REPORT.md) | Final delivery report (sections A–L) |

## Quick start (Windows PowerShell, from `AURUM_AI`)
```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m aurum.cli init-db                               # applies migration 0009 and any earlier ones
python -m aurum.cli users create alice --role admin       # the password is prompted for, hidden
python -m aurum.api                                       # API on http://127.0.0.1:8000
python -m aurum.cli approvals worker                      # second terminal: expiry, handoff, outbox, notifications
cd ..\aurum-frontend; npm run dev                         # http://localhost:5173 -> Sign in -> Trade Approvals
```
