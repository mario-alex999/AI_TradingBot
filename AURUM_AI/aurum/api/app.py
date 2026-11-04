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
               client: CftcClient | None = None, analysis_service: Any = None,
               intelligence_service: Any = None, ai_review_service: Any = None) -> FastAPI:
    from aurum.database.connection import engine_from_settings
    settings = settings or get_settings()
    cfg = cfg or load_cot_config()
    engine = engine or engine_from_settings(settings)
    service = CotService(settings, cfg, engine, client=client)
    state = AppState(settings, cfg, engine, service, JobManager())

    app = FastAPI(title="AURUM AI API", version="0.4.0",
                  description="AURUM AI backend. Module 02: COT Institutional Intelligence Engine; Module 03: Technical Analysis and SMC "
                              "Engine; Module 04: Trading Sessions and Market Intelligence (read-only research; no trading "
                              "endpoints). Admin operations need the X-AURUM-Admin-Key header.")
    app.state.aurum = state
    # Added first so it sits INSIDE the CORS middleware: a 500 then still carries CORS headers and the browser
    # reports a server error instead of a misleading "blocked by CORS policy".
    app.add_middleware(_CatchUnhandled)
    app.add_middleware(CORSMiddleware, allow_origins=settings.api_cors_origins, allow_credentials=True,
                       allow_methods=["GET", "POST"], allow_headers=["*"])

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
        feed = {"mt5": "unknown", "collection": "unknown"}
        if db_ok:
            try:
                from aurum.api.market_queries import MarketQueries
                feed = MarketQueries(engine).feed_state(settings.collection_interval_seconds)
            except Exception:  # noqa: BLE001 - Module 01 tables may be absent on a COT-only database
                pass
        return camelize({"status": "ok" if db_ok else "degraded", "database": {"ok": db_ok,
                         "dialect": db.get("dialect"), "timescaledb": bool(db.get("timescaledb"))},
                         # Module 01 only ever connects to DEMO accounts (non-demo accounts are rejected).
                         "environment": "demo",
                         "mt5": feed["mt5"], "collection": feed["collection"],
                         # Endpoints the frontend may enable buttons for. Collection itself runs in the
                         # Module 01 service on the MT5 machine, so it is not triggered from the API.
                         "capabilities": ["market.export.csv", "market.export.parquet"] if db_ok else [],
                         "modules": {"cot": service.repo.schema_ready() if db_ok else False,
                                     "intelligence": _mi_ready(engine) if db_ok else False,
                                     "account": _account_ready(engine) if db_ok else False}})

    from aurum.api.routes.cot import build_router
    app.include_router(build_router(state), prefix=f"{API_PREFIX}/cot", tags=["cot"])
    from aurum.api.routes.technical_analysis import build_router as build_analysis_router
    app.include_router(build_analysis_router(state, analysis_service), prefix=f"{API_PREFIX}/analysis",
                       tags=["technical-analysis"])
    from aurum.api.routes.market import build_router as build_market_router
    app.include_router(build_market_router(state), prefix=f"{API_PREFIX}/market", tags=["market"])
    from aurum.api.routes.market_intelligence import build_router as build_mi_router
    app.include_router(build_mi_router(state, intelligence_service), prefix=f"{API_PREFIX}/intelligence",
                       tags=["market-intelligence"])
    from aurum.api.routes.learning import build_router as build_learning_router
    app.include_router(build_learning_router(state), prefix=f"{API_PREFIX}/agents", tags=["agents"])
    from aurum.api.routes.trading import build_router as build_trading_router
    app.include_router(build_trading_router(state), prefix=API_PREFIX, tags=["account"])
    from aurum.api.routes.ai_review import build_router as build_ai_review_router
    app.include_router(build_ai_review_router(state, ai_review_service, intelligence_service, analysis_service),
                       prefix=f"{API_PREFIX}/ai-review", tags=["ai-review"])
    from aurum.api.routes.system import build_router as build_system_router
    app.include_router(build_system_router(state), prefix=API_PREFIX, tags=["system"])
    return app


class _CatchUnhandled:
    """Pure ASGI middleware (keeps SSE streaming intact): unhandled errors become a generic 500 JSON body;
    internals and credentials never leak, the traceback goes to the server log."""

    def __init__(self, app: Any):
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = False

        async def _send(message: dict) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, _send)
        except Exception:
            log.exception("Unhandled API error", extra={"path": scope.get("path")})
            if started:  # headers already sent (e.g. mid-stream): nothing sensible to replace them with
                raise
            await JSONResponse(status_code=500, content={"detail": "Internal server error (see server log)."})(scope, receive, send)


def _account_ready(engine: Engine) -> bool:
    """True once at least one account snapshot is stored (sync-account / stream-ticks has run)."""
    try:
        from sqlalchemy import select

        from aurum.account.db_models import AccountSnapshot
        from aurum.database.connection import session_scope
        with session_scope(engine) as s:
            return s.execute(select(AccountSnapshot.id).limit(1)).first() is not None
    except Exception:  # noqa: BLE001
        return False


def _mi_ready(engine: Engine) -> bool:
    try:
        from aurum.market_intelligence.repository import MiRepository
        return MiRepository(engine).schema_ready()
    except Exception:  # noqa: BLE001
        return False


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
