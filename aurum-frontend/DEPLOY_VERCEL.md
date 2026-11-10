# Deploying the AURUM frontend to Vercel

Only the **frontend** (`aurum-frontend/`) runs on Vercel. The backend (FastAPI, PostgreSQL/TimescaleDB, the MT5 terminals
and the per-person terminal slots) needs Windows and MetaTrader 5, so it keeps running on the trading PC or a Windows
server. The Vercel site forwards `/api/v1/*` to it through `api/proxy.ts`, so the browser only ever talks to the Vercel
domain and the httpOnly sign-in cookie keeps working.

```
browser ──https──▶ your-app.vercel.app ──/api/v1/*──▶ api/proxy.ts (Edge) ──https──▶ AURUM backend (AURUM_BACKEND_URL)
                     └─ everything else: static React app (dist/)
```

## 1. Make the backend reachable over HTTPS

Vercel cannot reach `localhost`. Pick one:

* **Cloudflare Tunnel** (no open ports): `cloudflared tunnel --url http://localhost:8000` for a quick test URL, or a
  named tunnel with your own hostname for something permanent.
* A Windows VPS with a reverse proxy (Caddy / IIS / nginx) terminating HTTPS in front of `python -m aurum.api`.

Then in `AURUM_AI/.env` on the backend machine:

```
AUTH_COOKIE_SECURE=true        # the site is served over HTTPS
```

Check it: `https://<backend-host>/api/v1/health` should return `{"status":"ok", ...}`.

## 2. Import the project in Vercel

1. vercel.com → **Add New… → Project** → import `mario-alex999/AI_TradingBot`.
2. **Root Directory: `aurum-frontend`** (important — the repository also holds the backend).
3. Framework preset: **Vite** (read from `vercel.json`; build `npm run build`, output `dist`).
4. **Environment Variables**:

| Name | Value | Notes |
| --- | --- | --- |
| `AURUM_BACKEND_URL` | `https://<backend-host>` | Server-side only (used by the proxy). No `/api` suffix. |
| `VITE_API_BASE_URL` | `/api/v1` | Same-origin: goes through the proxy. |
| `VITE_ENABLE_MOCK_DATA` | `false` | `true` shows the labelled demo data instead of the backend. |
| `VITE_REALTIME_URL` | *(empty)* or `off` | Empty = backend quote stream through the proxy. |

Never put passwords, database URLs or API keys in `VITE_*` variables: they are compiled into the public JavaScript.

5. **Deploy.** Vercel builds the branch you choose; the repository's default branch is the production branch.

## 3. Check

* `https://<app>.vercel.app/api/v1/health` → backend health JSON (proves the proxy and `AURUM_BACKEND_URL`).
* Open the site, sign in, and the **Connect your MT5 account** pop-up appears.
* If `AURUM_BACKEND_URL` is missing the API answers `503 BACKEND_NOT_CONFIGURED`; if the backend is down, `502
  BACKEND_UNREACHABLE`. The demo workspace still works in both cases.

## Notes and limits

* Deep links (`/approvals/123`) work: every non-`/api/` path falls back to `index.html`.
* The backend's sign-in rate limit is per client address. Behind the proxy, requests arrive from Vercel's addresses, so
  many people signing in at the same moment can share one limit (10/min). Fine for a small team.
* Live quote streaming goes through an Edge Function, which Vercel may cut after a few minutes; the app reconnects
  automatically.
* The backend PC must stay on (and logged in, for the MT5 terminals) for the deployed site to show live data.
