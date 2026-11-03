# AURUM AI

AI-assisted multi-asset trading intelligence platform (Forex, commodities, indices, crypto).

| Folder | Contents |
| --- | --- |
| [`AURUM_AI/`](AURUM_AI/README.md) | Module 01 — read-only MT5 market-data engine (Python, PostgreSQL/TimescaleDB, CLI) |
| [`aurum-frontend/`](aurum-frontend/README.md) | React + TypeScript trading dashboard (demo, read-only) |

The frontend communicates only with the FastAPI layer — never directly with MetaTrader 5 or the database.
Module 01's market data is served read-only under `/api/v1/market/*`, with a live quote stream; see
[`AURUM_AI/docs/MARKET_API.md`](AURUM_AI/docs/MARKET_API.md) for the endpoints and how to run the live stack.
Without a backend, the frontend runs on its clearly labelled DEMO DATA layer.
