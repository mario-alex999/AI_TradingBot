# AURUM AI

AI-assisted multi-asset trading intelligence platform (Forex, commodities, indices, crypto).

| Folder | Contents |
| --- | --- |
| [`AURUM_AI/`](AURUM_AI/README.md) | Python backend: Module 01 read-only MT5 market data, Module 02 COT institutional intelligence, Module 03 technical analysis & SMC engine; FastAPI (`python -m aurum.api`) |
| [`aurum-frontend/`](aurum-frontend/README.md) | React + TypeScript trading dashboard (demo, read-only) |

The frontend communicates only with the FastAPI layer — never directly with MetaTrader 5 or the database.
The backend API (`python -m aurum.api`, `/api/v1`) serves Module 01 market data (`/market/*`, with a live quote
stream; see [`AURUM_AI/docs/MARKET_API.md`](AURUM_AI/docs/MARKET_API.md)), Module 02 (`/cot/*`), Module 03
(`/analysis/*`, including the `/analysis/market-structure` contract the dashboard calls) and the Trade Learning
Agent (`/agents/learning`). Pages whose endpoints do not exist yet run on the frontend's clearly labelled DEMO
DATA layer.
