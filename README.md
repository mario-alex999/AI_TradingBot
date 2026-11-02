# AURUM AI

AI-assisted multi-asset trading intelligence platform (Forex, commodities, indices, crypto).

| Folder | Contents |
| --- | --- |
| [`AURUM_AI/`](AURUM_AI/README.md) | Python backend: Module 01 read-only MT5 market data, Module 02 COT institutional intelligence, Module 03 technical analysis & SMC engine; FastAPI (`python -m aurum.api`) |
| [`aurum-frontend/`](aurum-frontend/README.md) | React + TypeScript trading dashboard (demo, read-only) |

The frontend communicates only with a FastAPI layer — never directly with MetaTrader 5 or the database.
The backend API (`python -m aurum.api`, `/api/v1`) serves Module 02 (`/cot/*`) and Module 03 (`/analysis/*`,
including the `/analysis/market-structure` contract the dashboard already calls). Pages whose endpoints do not
exist yet run on the frontend's clearly labelled DEMO DATA layer.
