# Market-data API (Module 01 → frontend)

Read-only FastAPI endpoints under `/api/v1`, computed only from what Module 01 has stored. Nothing here
talks to MT5 or writes to the database.

| Endpoint | Returns |
| --- | --- |
| `GET /health` | Database state, `environment: "demo"`, `mt5` (connected when a tick was stored in the last 2 min), `collection` (connected / collecting / warning when runs are late / failed), `capabilities` |
| `GET /market/instruments` | Configured instruments with frontend asset classes (`metal` → commodities, `index` → indices) |
| `GET /market/quotes?symbols=` | Latest stored tick per instrument; day range from today's M5 candles; change vs the previous completed D1 close; `status: open` while ticks are fresh |
| `GET /market/candles?symbol=&timeframe=&limit=` | Valid candles, oldest first (invalid-quality rows are excluded) |
| `GET /market/specifications` | Latest specification snapshot per instrument (missing values stay `null`) |
| `GET /market/data-quality` | Candle counts, open quality issues by type, last run result and latency, per-timeframe freshness |
| `GET /market/collection-logs?page=&page_size=&search=&instrument=&timeframe=&status=` | Paginated candle-collection logs (`passed` / `warning` / `failed`) |
| `GET /market/export?format=csv\|parquet&symbol=&timeframe=` | Stored, validated candles as a file |
| `GET /market/stream/quotes?symbols=` | Server-Sent Events: `event: quote` with a quote whenever one changes, keep-alive every 15 s |
| `GET /agents/learning` | Trade Learning Agent lineage |

Account, trades, risk, performance, agent status and notifications: see `ACCOUNT_API.md`.

## Running the live stack (on the MT5 Windows machine)

```bash
python -m aurum.cli init-db          # once
python -m aurum.cli collect --loop   # candles, specs and validation every COLLECTION_INTERVAL_SECONDS
python -m aurum.cli stream-ticks     # live bid/ask every second + DEMO account mirror (separate terminal)
python -m aurum.api                  # the API on API_HOST:API_PORT (separate terminal)
```

Then in `aurum-frontend/.env.local` set `VITE_ENABLE_MOCK_DATA=false` (and `VITE_API_BASE_URL` if the
API is not on `http://localhost:8000/api/v1`), and run `npm run dev`. The frontend subscribes to
`/market/stream/quotes` automatically; set `VITE_REALTIME_URL=off` to disable streaming.

If the frontend runs on another origin, add it to `API_CORS_ORIGINS` (comma-separated), e.g.
`API_CORS_ORIGINS=http://localhost:5173,http://192.168.1.20:5173`.

## Known limits

- The day's high/low come from stored M5 candles plus the latest tick, so a spike between two stored
  ticks inside the still-forming candle can be missed until that candle is collected.
- "Start collection" stays disabled in the UI: collection runs in the Module 01 service on the MT5
  machine, not inside the API process.
