# AURUM AI

AI-assisted multi-asset trading intelligence platform (Forex, commodities, indices, crypto).

| Folder | Contents |
| --- | --- |
| [`AURUM_AI/`](AURUM_AI/README.md) | Module 01 — read-only MT5 market-data engine (Python, PostgreSQL/TimescaleDB, CLI) |
| [`aurum-frontend/`](aurum-frontend/README.md) | React + TypeScript trading dashboard (demo, read-only) |

The frontend communicates only with a FastAPI layer — never directly with MetaTrader 5 or the database.
Module 01 does not expose HTTP endpoints yet; the next integration step is a FastAPI app in front of its
repository/services that serves the contracts listed in [`aurum-frontend/README.md`](aurum-frontend/README.md#api-contract)
(starting with `GET /api/v1/health` and the `/market/*` endpoints). Until then the frontend runs on its
clearly labelled DEMO DATA layer.
