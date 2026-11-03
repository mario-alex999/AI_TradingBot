# AURUM AI — Frontend

Institutional market-intelligence and AI-assisted trading command centre for Forex, commodities, indices and crypto.

This is the **React + TypeScript** frontend. It talks only to the AURUM AI **FastAPI** backend — never directly to MetaTrader 5 or the database — so broker access, credentials and trading safeguards stay server-side.

> **Release status: DEMO and READ-ONLY.** There is no order execution, no position modification and no broker order placement anywhere in this codebase.

---

## Quick start

Requires Node.js 20+ (tested on Node 22).

```bash
cd aurum-frontend
npm install
cp .env.example .env.local      # adjust VITE_API_BASE_URL if needed
npm run dev                     # http://localhost:5173
```

Sign in on the demo screen (choose a display name and role — no password is used or stored).

| Command | Purpose |
| --- | --- |
| `npm run dev` | Vite dev server with hot reload |
| `npm run build` | Type-check (`tsc -b`) and production build to `dist/` |
| `npm run preview` | Serve the production build locally |
| `npm run lint` | ESLint (zero warnings allowed) |
| `npm run typecheck` | TypeScript only |
| `npm run test` | Vitest + React Testing Library (single run) |
| `npm run test:watch` | Vitest in watch mode |

## Environment variables

| Variable | Default | Description |
| --- | --- | --- |
| `VITE_API_BASE_URL` | `http://localhost:8000/api/v1` | FastAPI base URL |
| `VITE_ENABLE_MOCK_DATA` | `true` | Start in demo-data mode (can be toggled at runtime in **Settings → Data**) |
| `VITE_API_TIMEOUT_MS` | `10000` | Per-request timeout |
| `VITE_REALTIME_URL` | _(empty)_ | Server-Sent Events endpoint for streamed quotes; empty disables streaming |

Every `VITE_*` value is compiled into public JavaScript. **Never** put broker passwords, database credentials or API secrets in them.

---

## Tech stack

React 18 · TypeScript (strict, `noUncheckedIndexedAccess`) · Vite 6 · Tailwind CSS 3 · React Router 6 · TanStack Query 5 · Zustand 5 · TradingView Lightweight Charts 4 · Recharts 2 · Lucide React · React Hook Form + Zod · Axios · date-fns · Vitest 3 + React Testing Library · ESLint 9.

## Design system

Tokens live in [`tailwind.config.ts`](tailwind.config.ts); global layers in [`src/styles/globals.css`](src/styles/globals.css).

The UI follows the **"data terminal"** app style from [taste-skill](https://github.com/Leonxlnx/taste-skill): true neutral darks, colour only on data, status and the one selection line, no shadows on resting surfaces, sentence-case labels, and every figure in a monospace.

| Token | Value | Use |
| --- | --- | --- |
| `base` / `base-card` / `base-raised` / `base-elevated` | `#171719` / `#121215` / `#1C1C1F` / `#212023` | Ground (page, sidebar, top bar) / cards, one step darker / fields and wells / menus and hover |
| `gold` | `#D6AD60` | The single accent: the 1px selection line, the main data series, the logo. Never a button or panel fill |
| `up` / `down` | `#16C784` / `#EA3943` | Market movement (only on the "what moved" figures and candles) |
| `down-text` | `#F2606A` | Small negative text (keeps 4.5:1 contrast) |
| `ink` / `ink-muted` / `neutral` | `#F2F2F3` / `#B4B5BA` / `#8A8C92` | Primary, secondary and meta text, all ≥ 4.5:1 on cards |
| `line` / `line-soft` / `line-control` | `#26262A` / `#1E1E22` / `#3A3A3E` | Card edges / row rules / control borders |

Typography: **Public Sans** for text, **JetBrains Mono** with tabular figures (`.num` class) for every price, percentage and metric. Missing values print as a grey `—`; real zeros always print.

The backend CLIs share the same rules through `AURUM_AI/aurum/ui.py`: one themed console, one table style, sentence-case status words.

Reusable components (`src/components/common`): `Button` (with `disabledReason` tooltips), `Card`/`CardHeader`/`CardBody`, `Badge`, `StatusIndicator`, `DataSourceBadge`, `MetricCard`, `DataTable` (sortable, keyboard-accessible rows), `Pagination`, `SegmentedControl`, `Select`, `Switch`, `Tabs`, `Popover`, `ConfirmDialog`, `Toaster`, `Skeleton`, `EmptyState`, `ErrorState`, `QueryState`, `FeaturePending`, `PermissionGate`, `Logo`.

---

## Folder structure

```
aurum-frontend/
├── public/                 logo.svg, favicon.svg
├── src/
│   ├── App.tsx             routes (lazy-loaded secondary pages)
│   ├── main.tsx            providers (QueryClient, Router)
│   ├── components/
│   │   ├── layout/         AppLayout, Sidebar, Topbar, MobileNavigation, NavList, UserMenu, ProtectedRoute
│   │   ├── common/         design-system primitives (see above)
│   │   ├── dashboard/      Welcome, AccountSummary, MarketCards, SessionPanel, DataHealthPanel, IntelligenceSummary, RecentAlerts
│   │   ├── market/         QuoteCard
│   │   ├── charts/         CandlestickChart, IndicatorPane, ChartToolbar, ChartWorkspace, OverlayPanel, theme + indicator builders
│   │   ├── data-centre/    ConnectionStatus, CollectionControls, InstrumentConfigTable, TimeframeMonitor, DataQuality, CollectionLogs
│   │   ├── cot/            CotCharts, CotBreakdown, CotInterpretation, CotControls
│   │   ├── analytics/      ChartCard, PerformanceView, chartStyles
│   │   ├── signals/        SignalCard
│   │   ├── risk/           PositionSizeCalculator
│   │   ├── agents/         SeverityIcon
│   │   └── settings/       SessionConfigForm
│   ├── pages/              one file per route (16 sections + Login, InstrumentDetail, NotFound)
│   ├── services/           api.ts (Axios client + ApiError), source.ts (demo/live switch), *Service.ts, realtime.ts (SSE)
│   ├── hooks/              queries.ts (all TanStack Query hooks), useSessions, useClock, useDataActions, usePermission, …
│   ├── stores/             Zustand: settings, ui, auth, chart, toast, notification read-state
│   ├── mocks/              DEMO DATA layer (deterministic, clearly labelled)
│   ├── config/             env, navigation, instruments, sessions, permissions, query client, labels
│   ├── types/              market, system, trading, analysis, auth, api
│   ├── utils/              format, sessions (DST engine), indicators, cot, risk, errors, status, download
│   ├── styles/globals.css
│   └── test/               setup, mocks and test suites
├── .env.example  eslint.config.js  tailwind.config.ts  vite.config.ts  tsconfig*.json
```

### Data flow

```
Page component ──► hooks/queries.ts (TanStack Query, key includes mock flag)
                     └─► services/*Service.ts ──► resolveSource(mock?)
                                                   ├─ demo → src/mocks/*   (tagged source: 'demo')
                                                   └─ live → services/api.ts (Axios → FastAPI, tagged source: 'live')
```

Pages never call Axios directly, so switching from demo data to the real API needs **no page changes**. Every dataset carries `source` and `fetchedAt`, and every panel renders a `DataSourceBadge` (**Demo data** or **Backend + age**). There is deliberately **no silent fallback** from a failing backend to demo data.

---

## Pages

| Route | Page | Notes |
| --- | --- | --- |
| `/` | Dashboard | Welcome, account summary (`--` until the backend responds), quote cards, session panel with timezone selector, data health, intelligence summary, recent alerts |
| `/market` | Market Overview | Search, asset-class and status filters, sortable table; row → instrument detail |
| `/market/:symbol` | Instrument detail | Quote header, chart, contract specification |
| `/charts` | Charts & Analysis | Lightweight Charts candlesticks + volume, D1/H4/H1/M15/M5, SMA/EMA/Bollinger/VWAP overlays, RSI/MACD/ATR panes, fullscreen, reset, PNG export, backend-only structure overlays |
| `/structure` | Market Structure | Per-timeframe bias summary and event matrix; clearly marked illustrative layout |
| `/institutional` | Institutional Intelligence | Commercial / large-spec / non-reportable panels, percentile, OI, **Informed money analysis**; report vs publication dates |
| `/cot` | COT Analysis | Overview, breakdown, 5 charts, 12M/2Y/3Y/4Y/custom range, interpretation panel (never generated from demo or missing data) |
| `/sessions` | Trading Sessions | Session timeline, overlap monitoring, high/low/range/volatility derived from stored candles |
| `/signals` | AI Signals | Filters, signal cards, confidence disclaimer, informative empty state |
| `/trades` | Trade Management | Read-only open / pending / history tables |
| `/risk` | Risk Management | Risk metrics, exposure charts, backend-enforced limits, informational position-size calculator |
| `/backtesting` | Backtesting | Validated configuration form, results clearly labelled "Backtest · simulated" |
| `/performance` | Performance Analytics | Filters (sent to the API), metrics and charts |
| `/data` | Data Centre | Connection status, instrument configuration (edit + confirm via API), timeframe monitoring, collection controls, data quality, searchable/paginated logs |
| `/agents` | Agent Centre | 8 agents with status, last run/result, errors, config and logs |
| `/notifications` | Notifications | Severity, category and read filters; mark read/unread/all |
| `/settings` | Settings | General, Market (incl. session hours), Data (demo mode), Notifications, Security (profile, session, role matrix) |

---

## API contract

All endpoints are **proposed contracts** relative to `VITE_API_BASE_URL`. None are claimed to exist yet; the UI shows *“Awaiting backend module”* for 404/405/501 responses and *“Backend disconnected”* when the server cannot be reached.

| Method & path | Used by | Demo layer |
| --- | --- | --- |
| `GET /health` | Topbar, Data Centre, capability gating | Always live (never mocked) |
| `GET /market/instruments` | Market Overview, selectors | ✅ |
| `GET /market/quotes?symbols=` | Dashboard, Market Overview | ✅ |
| `GET /market/candles?symbol=&timeframe=&limit=` | Charts, Sessions | ✅ |
| `GET /market/specifications` | Data Centre, instrument detail | ✅ |
| `PUT /market/specifications/{symbol}` | Data Centre edit | live only |
| `GET /market/data-quality` | Dashboard, Data Centre | ✅ |
| `GET /market/collection-logs?page=&page_size=&search=&instrument=&timeframe=&status=` | Data Centre | ✅ |
| `POST /market/collect` | Data Centre | live only |
| `GET /market/export?format=csv\|parquet` | Data Centre | live only |
| `GET /cot/reports`, `GET /cot/positions?instrument=&weeks=` | COT, Institutional | ✅ synthetic, labelled "not CFTC" |
| `GET /analysis/market-structure?instrument=[&timeframe=&view=overlays]` | Structure, chart overlays | ❌ never mocked |
| `GET /analysis/signals` | AI Signals | ❌ never mocked |
| `GET /account/summary` | Dashboard | ❌ never mocked |
| `GET /trades/open`, `GET /trades/history` | Trade Management | ❌ never mocked |
| `GET /risk/overview` | Risk Management | ❌ never mocked |
| `GET /performance/summary`, `GET /backtests/latest` | Performance, Backtesting | ❌ never mocked |
| `GET /agents/status` | Agent Centre | ✅ (idle/disabled only) |
| `GET /notifications` | Topbar, Notifications | ✅ sample alerts prefixed "DEMO:" |

Response shapes are the TypeScript interfaces in [`src/types`](src/types). JSON keys are camelCase except `/health`, which accepts snake_case (`broker_name`, `terminal_version`).

### `/health` and capability gating

Operational buttons are enabled only when **all** of these hold: demo mode is off, the backend is reachable, the backend advertises the capability, and the user's role allows it. The disabled button explains which condition failed.

```json
{
  "status": "ok",
  "version": "0.1.0",
  "environment": "demo",
  "mt5": "connected",
  "database": "connected",
  "collection": "collecting",
  "broker_name": "Example Broker Ltd",
  "terminal_version": "5.00 build 4xxx",
  "capabilities": ["market.collect", "market.export.csv", "market.export.parquet", "market.specifications.write"]
}
```

Connection states: `connected | disconnected | collecting | warning | failed | unknown`. The topbar shows **LIVE ACCOUNT** only when `environment` is exactly `"live"`; otherwise it shows **DEMO ENVIRONMENT**.

### Networking behaviour

- One central Axios client (`withCredentials: true`, so the backend can use httpOnly session cookies).
- Errors are normalised into `ApiError` kinds: `network`, `timeout`, `http`, `not_implemented`, `cancelled`, `validation`.
- Retries happen only for transient failures (network, timeout, 5xx), at most twice with exponential backoff.
- Requests are cancelled through TanStack Query's `AbortSignal` when a component unmounts or its query key changes.
- No polling except a single 60 s `/health` heartbeat. Quotes update on demand or via SSE (`VITE_REALTIME_URL`, `event: quote`, `data: <Quote JSON>`).

---

## Connecting the Python backend

1. Serve FastAPI under `/api/v1` (for example `uvicorn app.main:app --port 8000`).
2. Allow the frontend origin with CORS, including credentials:
   ```python
   from fastapi.middleware.cors import CORSMiddleware
   app.add_middleware(
       CORSMiddleware,
       allow_origins=["http://localhost:5173", "http://localhost:4173"],
       allow_credentials=True,
       allow_methods=["*"],
       allow_headers=["*"],
   )
   ```
3. Implement `GET /api/v1/health` first (shape above). The topbar and Data Centre start reflecting real status immediately, even in demo mode.
4. Implement the Module 01 endpoints (`/market/*`), returning the shapes in `src/types/market.ts` and `src/types/system.ts`.
5. Set `VITE_ENABLE_MOCK_DATA=false` in `.env.local` (or switch off **Settings → Data → Use demo data**) and restart `npm run dev`.
6. Add capabilities to `/health` as each action endpoint goes live. The matching buttons enable automatically for permitted roles.

---

## Security model

- Frontend permissions (`src/config/permissions.ts`) only decide what is **shown**. The backend must authorise every request.
- Roles: Administrator, Trader, Analyst, Viewer. Navigation, routes and buttons are role-aware.
- No passwords, tokens or secrets are stored. The demo profile lives in `sessionStorage`; preferences in `localStorage` contain no credentials.
- The position-size calculator and other client-side maths are informational and **not** risk controls.
- Structure overlays (order blocks, FVGs, BOS/CHoCH, …), signals, COT interpretations, account figures and performance are never generated in the browser.

---

## Testing

```bash
npm run lint && npm run test && npm run build
```

There are 7 suites with 49 tests (`src/test`), run in jsdom. Lightweight Charts is replaced by a recording stub, and HTTP goes through a fake Axios adapter that is offline by default. Coverage includes:

- **Navigation:** login redirect, the validated login form, sidebar navigation with the active state, sidebar collapse, the mobile drawer, and role-hidden navigation plus the route guard.
- **Dashboard and states:** "--" for account figures, demo labelling, the analysis-pending state, sessions, the loading skeleton, the disconnected backend, and live data tagged as backend data.
- **Charts:** rendering from candle data, timeframe and instrument switching, indicators drawn only when enabled, and overlays disabled until the backend supplies them.
- **Data Centre:** actions disabled in demo mode, disabled when the endpoint is missing, permission-gated, and a confirmed collection POST; log pagination and filters.
- **Services:** error normalisation, demo/live switching, no fabricated account or signal data, no silent fallback, and tolerant `/health` parsing.
- **Utilities:** indicator maths, DST-correct London and New York sessions, overlaps, FX market hours, formatting, position sizing and COT derivations.

---

## Awaiting backend development

| Feature | Needs |
| --- | --- |
| Live quotes, candles, specs, data quality, logs, collection, export | Module 01 `/market/*` endpoints |
| Account summary, positions, history | `/account/summary`, `/trades/*` |
| Market structure summary and chart overlays (S/R, OB, FVG, liquidity, PDH/PDL, PWH/PWL, session H/L, BOS, CHoCH, EQH/EQL) | Market Structure / ICT-SMC agents |
| Real COT data, positioning divergence, AI interpretation | COT Intelligence Agent, `/cot/*` |
| Session liquidity conditions, volatility regime, economic calendar | Session agent, calendar integration |
| AI signals and the approve/reject workflow | Strategy engine + authorised approval endpoint |
| Risk metrics, exposure and limits | Risk Management Agent, `/risk/overview` |
| Backtests and live performance | Strategy Evaluation Agent, `/backtests/*`, `/performance/*` |
| Agent control and configuration | `/agents/*` write endpoints |
| Real authentication (login, password management, sessions) | Backend auth issuing httpOnly cookies |
| Server-side notification read state | `PATCH /notifications/{id}` |
| Chart drawing tools | Annotation persistence API |
