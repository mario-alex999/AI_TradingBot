# Module 07 testing guide

## Backend (pytest)
From `AURUM_AI`:
```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/approvals                 # Module 07 only (about 1 minute)
.\.venv\Scripts\python.exe -m pytest -q tests/approvals tests/risk      # Module 07 + Module 06 (about 2 minutes)
.\.venv\Scripts\python.exe -m pytest -q                                 # everything (long: about 10+ minutes)
```

Tests use SQLite templates, so they need no database server, network, MT5 or real money. The test harness
(`tests/approvals/m07_harness.py`) provides:
- `H`: a service, auth and database built on a fresh template.
- `Clock`: controllable time.
- `FakeRisk`: a scripted Module 06 with PASS, REVIEW, BLOCK, ERROR and unavailable modes.
- `StaticPositions` and `FakeExecution`: a **labelled test adapter** standing in for Module 09.
- Users alice (admin), tom (trader), rita (reviewer) and olly (observer).

| File | Tests | Covers |
|---|---:|---|
| `test_state_machine.py` | 246 | Every (from, to) pair for approvals and management. Terminal states. Unknown statuses |
| `test_approval_rules.py` | 25 | The 14 approval checks, BLOCK never approvable, confirmation mismatch, stale evidence or version, multi-person, idempotent retries, forward-only execution results |
| `test_intake.py` | 16 | Payload validation, Decimal conversion, SL/TP sides, duplicates, versions, Module 05 normalisation |
| `test_management_worker_failures.py` | 18 | Management actions and validation, emergency phrase, stop widening, worker ticks, outbox retry and dead letter, email and Telegram retries, `not_configured`, Module 06 outage at handoff |
| `test_api_security.py` | 14 | Sign-in required, wrong password and lockout, httpOnly cookie and CSRF, idle timeout, logout, observer cannot decide, service token cannot approve even with the scope, structured BLOCK error, camelCase read-only detail, Module 09 contract endpoints and inbound events, management and dashboard endpoints, notifications merge, decision rate limit (429), SSE stream delivers camelCase outbox events |
| `test_e2e_scenarios.py` | 10 | The 10 end-to-end scenarios of the specification (S1–S10) |
| `test_integration_module06.py` | 4 | **Real Module 06 `RiskService`**: PASS flow, lock blocks approval, revalidation frees the reservation, BLOCK on a bad stop |
| `test_migration.py` | 3 | The SQL matches the models. Trigger pairs match `states.py`. Additive only. **PostgreSQL** state-machine and append-only enforcement (needs `AURUM_TEST_DATABASE_URL`) |
| `test_n8n_workflows.py` | 3 | Shipped n8n workflows are inactive, read-only and free of secrets |
| `test_smoke.py` | 1 | Import and wiring |
| **Total** | **340** | |

Concurrency is tested with real threads racing on the same request against one database:
- two people approving at once (`test_s5_…`): exactly one decision, and one `trade.approved_for_execution` event;
- an approval racing the expiry job (`test_concurrent_approve_and_expiry`): the request ends `EXPIRED` and nothing is
  handed off.

Other conflicting pairs (reject against approve, revalidate against approve) go through the same conditional update
(`_move`) and the same database lock. They are not raced with threads. A stale `requestVersion` is tested directly
(`STALE_VERSION`).

### PostgreSQL-only tests
They need a **throwaway** database, because they insert rows that deliberately break the audit chain:
```powershell
docker exec aurum-timescaledb psql -U aurum -d aurum -c "CREATE DATABASE aurum_m07_scratch"
$env:AURUM_TEST_DATABASE_URL = "postgresql+psycopg://aurum:<password>@localhost:5432/aurum_m07_scratch"
.\.venv\Scripts\python.exe -m pytest -q tests/approvals/test_migration.py tests/risk/test_safety.py -k postgres
Remove-Item Env:AURUM_TEST_DATABASE_URL
docker exec aurum-timescaledb psql -U aurum -d aurum -c "DROP DATABASE aurum_m07_scratch"
```
**Never point `AURUM_TEST_DATABASE_URL` at the real `aurum` database.**

## Frontend (vitest)
From `aurum-frontend`:
```powershell
npx tsc -b --noEmit              # type check
npx eslint src --max-warnings=0  # lint
npx vitest run                   # all tests
npx vitest run src/test/approvals.test.tsx
```
`src/test/approvals.test.tsx` has 10 tests:
- server sign-in (and that the password is not kept in the browser);
- the wrong-password message;
- the demo fallback when the backend is down;
- demo users see "Sign in with a backend account";
- the pending list for a server session;
- Approve stays disabled until the evidence is loaded, warnings are acknowledged and the confirmation is ticked, and
  the exact values are sent;
- every failed server check is shown;
- BLOCK can never be approved;
- reject needs a reason (and a comment for "other");
- observers get no decision controls.

## Known gaps in automated coverage
- Line coverage is not measured: no coverage tool is installed in the project environment, and it could not be
  installed offline.
- Module 09 exists only as the test adapter, so no test touches a real execution path. None exists yet.

## Manual end-to-end check
See the manual testing guide in [MODULE_07_DELIVERY_REPORT.md](MODULE_07_DELIVERY_REPORT.md#j-manual-testing-guide).
