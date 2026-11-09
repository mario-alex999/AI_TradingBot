# Module 07 security guide

## Safety rules (enforced in code and covered by tests)
| Rule | Where it is enforced |
|---|---|
| No automatic approval. Only a signed-in person with `approvals:decide` can approve | `ApprovalService.approve` (`who.is_human`), route `require(DECIDE, human=True)`, `HUMAN_ONLY` permissions stripped from service tokens |
| A Module 06 `BLOCK` or `ERROR` can never be approved | Check 8 in `_approval_problems`. The UI disables Approve and explains why |
| No silent parameter changes | The exact confirmation is compared (check 11). Modifications create a new version and request. Handoff refuses a changed size or new warnings |
| No direct MT5 access and no orders | Module 07 imports no MT5 code. Its Docker image has no MetaTrader5 package. Execution is only a request for Module 09 |
| n8n, Telegram and email cannot decide | Inbound events are dispatched to intake, results or positions only. The notification channels are one-way |
| History cannot be rewritten | Append-only triggers. Hash-chained audit verified by `/approvals/health` and `approvals status` |
| Mock data is never shown as real | The UI shows no approvals without a server session. Positions carry their source and age. Module 09 is reported as not built |

## People: authentication
- **Passwords.** PBKDF2-HMAC-SHA256 with a per-user salt and 600,000 iterations (`AUTH_PASSWORD_ITERATIONS`), compared
  in constant time. The minimum is 12 characters with at least 6 distinct characters. Passwords are entered only at a
  hidden prompt (`users create`, `users reset-password`), never as a CLI argument or environment variable, and never
  logged.
- **Lockout.** After `AUTH_MAX_FAILED_LOGINS` (5) failures the account locks for `AUTH_LOCKOUT_MINUTES` (15). Sign-in
  attempts are also rate-limited per minute. Error messages never say which part of a credential was wrong.
- **Sessions.** A random 256-bit token is set as the `aurum_session` cookie: `HttpOnly`, `SameSite=Lax`, and `Secure`
  when `AUTH_COOKIE_SECURE=true` (required behind HTTPS). Only its SHA-256 is stored. Sessions have an idle timeout
  (`AUTH_SESSION_IDLE_MINUTES`, 30) and an absolute lifetime (`AUTH_SESSION_ABSOLUTE_HOURS`, 12). Logging out revokes
  the session on the server. Disabling a user or changing a role ends their sessions.
- **CSRF.** Every POST and PUT with a cookie session must send `X-CSRF-Token`. It is checked against the stored hash
  (double submit, constant-time). The browser keeps the token in memory only, and `/auth/me` reissues it after a
  reload.
- **Browser storage.** The UI stores only the display profile (name, role, permissions) in `sessionStorage`. It never
  stores the password, the session token or the CSRF token.

## Services: tokens
`python -m aurum.cli users token-create <name> --scope events:publish --scope execution:read` prints the token **once**.
Only its hash is stored. Scopes can narrow the service role (`approvals:view`, `events:publish`, `execution:read`,
`execution:report`) but never add a human-only permission. Tokens can expire (`--days`) and be revoked
(`token-revoke`). Send them as `Authorization: Bearer …`. In n8n, keep the token in a Header Auth credential, never in
the workflow.

## Roles
| Permission | admin | trader | reviewer | observer | service |
|---|:-:|:-:|:-:|:-:|:-:|
| `approvals:view` | ✓ | ✓ | ✓ | ✓ | ✓ |
| `approvals:decide` (approve/reject/modify) | ✓ | ✓ | ✓ | | |
| `approvals:create` (submit idea, sync) | ✓ | ✓ | | | |
| `approvals:cancel` | ✓ | ✓ | | | |
| `approvals:revalidate` | ✓ | ✓ | ✓ | | |
| `management:request`, `management:decide`, `management:emergency` | ✓ | ✓ | | | |
| `approvals:policy` (policy, reviewer assignments) | ✓ | | | | |
| `approvals:audit` (all audit records) | ✓ | | | | |
| `users:manage` | ✓ | | | | |
| `events:publish`, `execution:read`, `execution:report` | | | | | ✓ |

**Account scope.** A user can be limited to specific trading accounts. Requests and positions for other accounts are
hidden from them, and their decisions on those accounts are refused.

## Other controls
- **Rate limits.** 30 decisions per minute per user (`429 RATE_LIMITED`).
- **Concurrency.** Optimistic version checks, an advisory lock on writes and partial unique indexes (see
  [MODULE_07_ARCHITECTURE.md](MODULE_07_ARCHITECTURE.md)).
- **Logging.** The project's log filter masks secrets. Module 07 never logs passwords, session tokens, CSRF tokens,
  service tokens, the SMTP password or the Telegram bot token. Telegram failures are recorded as the exception type only,
  so the bot token (part of the request URL) can never reach a log or the database.
- **CORS.** Limited to `API_CORS_ORIGINS`, with credentials allowed only for those origins.
- **Event payloads** carry identifiers and parameters only: no evidence text and no personal data.

## Remaining risks
- **Single factor.** There is no MFA. Use strong passwords and keep the API on localhost or behind a TLS reverse proxy.
- **Local development uses HTTP.** Set `AUTH_COOKIE_SECURE=true` whenever the UI is served over HTTPS.
- **In-memory rate limits.** They are per process, so they reset on restart and are not shared between API replicas.
- **Telegram and email are plain notifications.** Anyone who can read the chat or mailbox sees trade summaries. Never
  route them to shared channels.
- **The account mirror is only as fresh as `sync-account`.** Approvals check quote freshness. Position data shows its age.
