# AURUM AI

AI-assisted multi-asset trading intelligence platform (Forex, commodities, indices, crypto).

| Folder | Contents |
| --- | --- |
| [`aurum-frontend/`](aurum-frontend/README.md) | React + TypeScript trading dashboard (demo, read-only) |
| `aurum-backend/` | Python + MT5 + database + FastAPI *(planned)* |

The frontend communicates only with the FastAPI backend — never directly with MetaTrader 5 or the database.
See [`aurum-frontend/README.md`](aurum-frontend/README.md) for setup, API contracts and backend integration.
