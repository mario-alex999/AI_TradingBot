"""FastAPI application factory. Dependencies (settings, COT config, engine, CFTC client) are injectable for tests."""
from __future__ import annotations

import hmac
import logging
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.engine import Engine

from aurum.api.jobs import JobManager
from aurum.config import Settings, get_settings
from aurum.cot.client import CftcClient
from aurum.cot.config import CotConfig, load_cot_config
from aurum.cot.ingestion import CotService

log = logging.getLogger(__name__)
API_PREFIX = "/api/v1"


@dataclass
class AppState:
    settings: Settings
    cfg: CotConfig
    engine: Engine
    service: CotService
    jobs: JobManager


def camelize(obj: Any) -> Any:
    """snake_case keys -> camelCase recursively; dates/datetimes -> ISO strings (UTC 'Z' kept as +00:00)."""
    if isinstance(obj, dict):
        return {_camel(str(k)): camelize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [camelize(v) for v in obj]
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    if hasattr(obj, "item") and not isinstance(obj, (str, bytes)):
        try:
            return obj.item()
        except (ValueError, AttributeError):
            return obj
    try:
        import math
        if isinstance(obj, float) and math.isnan(obj):
            return None
    except TypeError:
        pass
    return obj


def _camel(s: str) -> str:
    if "_" not in s:
        return s
    head, *rest = s.split("_")
    return head + "".join(p[:1].upper() + p[1:] for p in rest)


def create_app(settings: Settings | None = None, cfg: CotConfig | None = None, engine: Engine | None = None,
               client: CftcClient | None = None) -> FastAPI:
    from aurum.database.connection import engine_from_settings
    settings = settings or get_settings()
    cfg = cfg or load_cot_config()
    engine = engine or engine_from_settings(settings)
    service = CotService(settings, cfg, engine, client=client)
    state = AppState(settings, cfg, engine, service, JobManager())

    app = FastAPI(title="AURUM AI API", version="0.2.0",
                  description="AURUM AI backend. Module 02: COT Institutional Intelligence Engine (read-only research; "
                              "no trading endpoints). Admin operations need the X-AURUM-Admin-Key header.")
    app.state.aurum = state
    app.add_middleware(CORSMiddleware, allow_origins=settings.api_cors_origins, allow_credentials=True,
                       allow_methods=["GET", "POST"], allow_headers=["*"])

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):  # never leak internals / credentials
        log.exception("Unhandled API error", extra={"path": request.url.path})
        return JSONResponse(status_code=500, content={"detail": "Internal server error (see server log)."})

    @app.on_event("shutdown")
    def _stop() -> None:
        state.jobs.shutdown()

    @app.get(f"{API_PREFIX}/health", tags=["system"])
    def health() -> dict[str, Any]:
        from aurum.database.connection import check_database
        try:
            db = check_database(engine)
            db_ok = True
        except Exception as exc:  # noqa: BLE001
            db, db_ok = {"error": type(exc).__name__}, False
        return camelize({"status": "ok" if db_ok else "degraded", "database": {"ok": db_ok,
                         "dialect": db.get("dialect"), "timescaledb": bool(db.get("timescaledb"))},
                         "modules": {"cot": service.repo.schema_ready() if db_ok else False}})

    from aurum.api.routes.cot import build_router
    app.include_router(build_router(state), prefix=f"{API_PREFIX}/cot", tags=["cot"])
    return app


def require_admin(request: Request, x_aurum_admin_key: str | None = Header(default=None)) -> None:
    """Admin guard: constant-time comparison with AURUM_ADMIN_API_KEY. Disabled (503) when no key is configured."""
    state: AppState = request.app.state.aurum
    configured = state.settings.aurum_admin_api_key
    if configured is None or not configured.get_secret_value():
        raise HTTPException(status_code=503, detail="Admin operations are disabled: set AURUM_ADMIN_API_KEY on the server.")
    if not x_aurum_admin_key or not hmac.compare_digest(x_aurum_admin_key.encode(),
                                                        configured.get_secret_value().encode()):
        raise HTTPException(status_code=401, detail="Missing or invalid X-AURUM-Admin-Key header.")


AdminDep = Depends(require_admin)
