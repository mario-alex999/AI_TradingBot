# Module 07 environment variables

Set these in `AURUM_AI/.env` (never commit it). `.env.example` lists them with safe defaults. They are read by
`aurum/config.py` (`Settings`). Secrets are `SecretStr` and are masked in logs.

## Shared (existing)
| Variable | Default | Notes |
|---|---|---|
| `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` | `localhost`, `5432`, `aurum`, `aurum`, none | PostgreSQL / TimescaleDB. Module 07 uses the same database |
| `DATABASE_URL` | empty | Overrides the `DB_*` values when set (e.g. `sqlite:///data/aurum.db` for a quick start) |
| `API_HOST`, `API_PORT` | `127.0.0.1`, `8000` | API bind address |
| `API_CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | Origins allowed to send the session cookie |

## Sign-in
| Variable | Default | Notes |
|---|---|---|
| `AUTH_SESSION_IDLE_MINUTES` | `30` (5–1440) | Session ends after this much inactivity |
| `AUTH_SESSION_ABSOLUTE_HOURS` | `12` (1–168) | Hard limit on a session's lifetime |
| `AUTH_COOKIE_SECURE` | `false` | **Set `true` behind HTTPS.** `false` only for `http://localhost` |
| `AUTH_PASSWORD_ITERATIONS` | `600000` | PBKDF2-SHA256 iterations for new hashes. Existing hashes keep their own count |
| `AUTH_MAX_FAILED_LOGINS` | `5` | Failures before lockout |
| `AUTH_LOCKOUT_MINUTES` | `15` | Lockout length |

The first administrator is created with `python -m aurum.cli users create <name> --role admin`. The password is
prompted for. **There is no password environment variable, by design.**

## Approvals
| Variable | Default | Notes |
|---|---|---|
| `APPROVALS_CONFIG` | `config/approvals.yaml` | Seed policy, copied into the database as policy v1 on first use. Later changes go through `PUT /approvals/policy`, which is versioned and audited. Editing the YAML afterwards has no effect |
| `APPROVALS_WORKER_INTERVAL_SECONDS` | `15` (min 2) | Worker tick interval (`approvals worker --interval` overrides it) |
| `APPROVALS_EVENTS_WEBHOOK_URL` | empty | Where the worker POSTs outbox events, e.g. `http://localhost:5678/webhook/aurum-approval-events`. Empty: consumers poll `/approvals/events` |

## Notifications (optional)
| Variable | Default | Notes |
|---|---|---|
| `NOTIFY_EMAIL_ENABLED` | `false` | Also enable `email` in the policy's `notifications.channels` |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_FROM` | empty, `587`, empty | Required for email |
| `SMTP_USERNAME`, `SMTP_PASSWORD` | empty | `SMTP_PASSWORD` is a secret and is never logged |
| `SMTP_USE_TLS` | `true` | STARTTLS on 587 |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | empty | Also enable `telegram` in the policy. The token is a secret. Telegram messages are informational and contain no approve link or action |

If a channel is enabled in the policy but not configured, deliveries are recorded as `not_configured`, never as sent.

## Frontend (`aurum-frontend/.env`)
No new variables. The UI calls the same `VITE_API_BASE_URL` and sends cookies (`withCredentials`). Sign-in appears
when `GET /auth/status` reports `ready` and `hasUsers`. Otherwise the demo workspace is offered, and it never shows
approvals.

**Demo data mode must be off to see approvals.** `VITE_ENABLE_MOCK_DATA` defaults to `true`, and while demo data is on,
approval queries are deliberately empty, because there is no demo approval data. Turn it off under Settings → Data,
or set `VITE_ENABLE_MOCK_DATA=false`.

## Policy file (`config/approvals.yaml`)
These are settings rather than environment variables, listed here for completeness. The defaults are in
`aurum/approvals/policy.py`:
- `expiry`: `by_timeframe` minutes, symbol, strategy and high-volatility overrides, `min_minutes`, `max_minutes`,
  `honour_idea_expiry`, `expiring_soon_minutes`.
- `review`:
  - `risk_review_policy` (`acknowledge` | `block`), `risk_max_age_seconds` (300), `market_data_max_age_seconds` (120).
  - `require_comment_on_approve`, `rejection_reasons`, `required_evidence`.
  - `multi_person` (`enabled`, `required_approvals`, `min_risk_pct`), `reviewers_see_assigned_only`.
- `handoff`: `revalidate_risk` (`always` | `if_older_than`), `revalidate_if_older_than_seconds`, `allowed_accounts`.
- `management`: `expiry_minutes`, `allow_stop_widening` (false), `max_partial_close_fraction`, and `emergency`
  (`allow_self_authorisation`, `confirmation_phrase`, `expiry_minutes`).
- `notifications`: `channels` (`in_app`, `email`, `telegram`), `email_events`, `retry_max_attempts` (6).
