"""/api/v1/mt5 - link the signed-in person's own MT5 DEMO account (read-only mirror).

GET  /mt5/status       whether the server has terminal slots, and how many are free
GET  /mt5/connection   this sign-in session's link (or null)
POST /mt5/connect      {login, password, server}  -> starts the link; poll /mt5/connection for CONNECTED / FAILED
POST /mt5/disconnect   ends the link and frees the terminal slot

Cookie sessions only (a service token cannot link an account); CSRF is required on POST. The password is passed to the
connector process and is never stored, logged or returned.
"""
from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Body, Depends, Request, Response
from fastapi.responses import JSONResponse

from aurum.api.app import AppState, camelize
from aurum.api.routes.risk import RateLimiter
from aurum.api.security import require
from aurum.auth import roles as perm
from aurum.auth.service import Principal
from aurum.mt5link.service import Mt5LinkError, Mt5LinkService


def service_getter(state: AppState, service: Any = None) -> Callable[[], Mt5LinkService]:
    holder: dict[str, Any] = {"svc": service}

    def svc() -> Mt5LinkService:
        if holder["svc"] is None:
            holder["svc"] = Mt5LinkService(state.engine, state.settings)
        return holder["svc"]
    return svc


def _err(exc: Mt5LinkError) -> JSONResponse:
    return JSONResponse(status_code=exc.status, content={"error": {"code": exc.code, "message": exc.message},
                                                         "detail": exc.message})


def build_router(state: AppState, svc: Callable[[], Mt5LinkService], connect_rate_per_minute: int = 6) -> APIRouter:
    r = APIRouter()
    limiter = RateLimiter(connect_rate_per_minute)
    person = Depends(require(perm.MT5_LINK, human=True))

    @r.get("/status")
    def status(p: Principal = person) -> Any:
        return camelize(svc().status())

    @r.get("/connection")
    def connection(response: Response, p: Principal = person) -> Any:
        response.headers["Cache-Control"] = "no-store"
        scope = list(p.account_scope) if p.account_scope is not None else None
        return camelize({"connection": svc().current(p), "account_scope": scope})

    @r.post("/connect")
    def connect(request: Request, response: Response, body: dict[str, Any] = Body(...), p: Principal = person) -> Any:
        if not limiter.allow(p.id):
            return JSONResponse(status_code=429, headers={"Retry-After": "60"},
                                content={"error": {"code": "RATE_LIMITED", "message": "Too many attempts; wait a minute."},
                                         "detail": "Too many attempts; wait a minute."})
        password = body.pop("password", None)
        try:
            out = svc().connect(p, body.get("login"), str(password or ""), str(body.get("server") or ""))
        except Mt5LinkError as exc:
            return _err(exc)
        finally:
            del password
        response.headers["Cache-Control"] = "no-store"
        return camelize({"connection": out})

    @r.post("/disconnect")
    def disconnect(p: Principal = person) -> Any:
        return camelize({"connection": svc().disconnect(p)})

    return r
