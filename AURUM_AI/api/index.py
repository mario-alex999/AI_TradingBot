"""Vercel entry point for the AURUM AI backend API (FastAPI as one Python function).

Every /api/v1/* request is rewritten here (vercel.json). Settings come from the Vercel project's environment variables
(DATABASE_URL, AURUM_ADMIN_API_KEY, ...); nothing is read from a .env file in the deployment.

What runs here: the API and everything it reads from or writes to the database. What does not: MetaTrader 5 (Windows
only), the Module 01 collector and the long-running workers. Those keep running on the MT5 machine and write to the same
database, so the deployed API sees their data.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aurum.api.app import create_app  # noqa: E402
from aurum.config import get_settings  # noqa: E402
from aurum.database.connection import engine_from_settings, init_database, pending_migrations  # noqa: E402
from aurum.logging_config import setup_logging  # noqa: E402

settings = get_settings()
_secrets = [x.get_secret_value() for x in (settings.mt5_password, settings.db_password, settings.database_url,
                                           settings.aurum_admin_api_key, settings.economic_calendar_api_key,
                                           settings.fred_api_key) if x is not None]
setup_logging(settings.log_level, None, _secrets)   # console only: the deployment's disk is read-only
log = logging.getLogger("aurum.vercel")

engine = engine_from_settings(settings)
try:
    # Apply missing tables once per cold start, only when something is pending (idempotent SQL, recorded in
    # schema_migrations), so a fresh database becomes usable without a separate init-db step.
    if pending_migrations(engine):
        log.info("Applying database migrations", extra={"steps": init_database(engine)})
except Exception as exc:  # noqa: BLE001 - the API still starts; /api/v1/health then reports the database problem
    log.error("Database migration check failed: %s", type(exc).__name__)

app = create_app(settings, engine=engine)
