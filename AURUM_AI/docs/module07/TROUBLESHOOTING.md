# Module 07 troubleshooting

Start with `python -m aurum.cli approvals status` (or `GET /api/v1/approvals/health`). It shows schema readiness, the
audit chain, dependencies and the outbox backlog.

| Symptom | Cause | Fix |
|---|---|---|
| `Run python -m aurum.cli init-db to create the Module 07 tables.` | Migration 0009 not applied to this database | `python -m aurum.cli init-db` (it only adds tables) |
| The login page shows only the demo workspace and says "No accounts exist yet" | No users | `python -m aurum.cli users create <name> --role admin` |
| The login page says "the backend is not reachable" | API not running, wrong `VITE_API_BASE_URL`, or CORS | Start `python -m aurum.api`. Check that `API_CORS_ORIGINS` includes the UI origin |
| Signed in, but Trade Approvals is empty although requests exist | Demo data mode is on (approval queries are deliberately empty in demo mode) | Settings → Data → turn demo data off, or set `VITE_ENABLE_MOCK_DATA=false` |
| "Sign in with a backend account" on Trade Approvals | You are in the demo workspace | Sign out, then sign in with a server account |
| `403 CSRF_FAILED` | The page lost its in-memory CSRF token, or the session changed | Reload the page (`/auth/me` reissues the token). If it persists, sign in again |
| `401` shortly after signing in | Cookie not stored: `AUTH_COOKIE_SECURE=true` on plain HTTP, or UI and API on different sites | Use `false` on `http://localhost`, and serve both from `localhost` (not a mix of `127.0.0.1` and `localhost`) |
| "Username or password is incorrect, or the account is locked" | Wrong password, or locked after 5 failures | Wait `AUTH_LOCKOUT_MINUTES`, or `python -m aurum.cli users reset-password <name>` |
| Approve stays disabled with "Open and review the evidence first" | The evidence did not load, so its hash is unknown | Check the Evidence card for an error. Missing data in a section does not block loading |
| `409 APPROVAL_CHECKS_FAILED`, "Market data is stale" | No quote newer than `market_data_max_age_seconds` (120 s) | Make sure market data collection is running (Module 01). This check protects you; do not raise the limit casually |
| "The risk assessment is older than 300 s" | The assessment aged while you reviewed | Click **Revalidate risk**, review the updated request, then approve |
| `409 STALE_VERSION` | The request changed (revalidation, another reviewer) | Reload the request and review again |
| "Module 06 decision is BLOCK" | Module 06 blocked the trade | It cannot be approved. Reject it, or revalidate if conditions changed |
| "A risk lock is active" | A Module 06 lock (daily loss, drawdown…) | See the Risk Management page. Locks are cleared there, not here |
| Requests stay `APPROVED` | The worker is not running, or Module 06 is unavailable at handoff | Start `python -m aurum.cli approvals worker`. Check `approvals status` → Module 06 |
| Requests stay `EXECUTION_PENDING` | Module 09 is not built, so nobody executes | Expected. The execution request waits in `/approvals/execution/queue` |
| Requests never expire | Worker not running | Start the worker. The approve call also refuses expired requests itself |
| No new requests from Module 05 | Worker not running, no new candidates, or candidates fail validation | `python -m aurum.cli approvals sync` shows `ingested`, `closed` and `errors`. The audit log records rejections with the reason |
| Positions list is empty or "stale" | The account mirror has not been refreshed | `python -m aurum.cli sync-account --loop` keeps it fresh (DEMO accounts only) |
| `422 STOP_WIDENING_NOT_ALLOWED` | The new stop increases risk | By policy, widening is off. If it is allowed, it needs a Module 06 PASS at the new stop |
| `422 NOT_AVAILABLE` for pending-order actions | No source reports pending orders before Module 09 exists | Expected |
| Outbox "dead" count grows | Webhook unreachable (n8n down or wrong URL) | Fix `APPROVALS_EVENTS_WEBHOOK_URL` and check that n8n is running and the workflow is active. Dead events stay readable at `/approvals/events` |
| Email or Telegram deliveries are `not_configured` | Channel enabled in the policy but not in `.env` | Set the `SMTP_*` or `TELEGRAM_*` values (and `NOTIFY_EMAIL_ENABLED=true`). Restart the worker |
| `audit chain: invalid` | A row was altered outside the application | Investigate. On PostgreSQL the append-only triggers block normal updates, so this indicates direct database tampering. Do not "fix" the rows |
| Docker worker cannot connect to the database | `DATABASE_URL` in `.env` points at `localhost` | Leave it empty (the `DB_*` values are used, with `DB_HOST` overridden), or point it at `host.docker.internal` |
| The frontend dev server or API stops under heavy load | Low memory on the machine | Close other heavy processes. Restart `npm run dev` and `python -m aurum.api` |
