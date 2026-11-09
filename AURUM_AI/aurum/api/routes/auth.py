"""/api/v1/auth - sign-in for people.

POST /auth/login     {username, password} -> sets the httpOnly session cookie; returns the user and a CSRF token
POST /auth/logout    ends the session (CSRF required)
GET  /auth/me        the signed-in user, their permissions and a fresh CSRF token
GET  /auth/status    whether any user exists (the sign-in page explains how to create the first administrator)

Sign-in attempts are rate limited per client address, and accounts lock after repeated failures.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Request, Response
from fastapi.responses import JSONResponse

from aurum.api.app import AppState, camelize
from aurum.api.routes.risk import RateLimiter
from aurum.api.security import SESSION_COOKIE, auth_error_response, auth_service, authenticate
from aurum.auth.passwords import csrf_for
from aurum.auth.service import AuthError


def build_router(state: AppState, login_rate_per_minute: int = 10) -> APIRouter:
    r = APIRouter()
    limiter = RateLimiter(login_rate_per_minute)
    secure = bool(state.settings.auth_cookie_secure)

    def set_cookie(response: Response, token: str, max_age: int) -> None:
        response.set_cookie(SESSION_COOKIE, token, max_age=max_age, httponly=True, secure=secure, samesite="lax", path="/")

    @r.post("/login")
    def login(request: Request, response: Response, body: dict[str, Any] = Body(...)) -> Any:
        client = request.client.host if request.client else "unknown"
        if not limiter.allow(client):
            return JSONResponse(status_code=429, headers={"Retry-After": "60"},
                                content={"error": {"code": "RATE_LIMITED", "message": "Too many sign-in attempts; wait a minute."},
                                         "detail": "Too many sign-in attempts; wait a minute."})
        try:
            issued = auth_service(request).login(str(body.get("username", "")), str(body.get("password", "")),
                                                 client, request.headers.get("User-Agent"))
        except AuthError as exc:
            return auth_error_response(exc)
        svc = auth_service(request)
        set_cookie(response, issued.token, int(svc.absolute.total_seconds()))
        response.headers["Cache-Control"] = "no-store"
        return camelize({"user": issued.principal.public(), "csrf_token": issued.csrf, "expires_at": issued.expires_at,
                         "idle_timeout_minutes": int(svc.idle.total_seconds() // 60)})

    @r.post("/logout")
    def logout(request: Request, response: Response) -> Any:
        try:
            p = authenticate(request)
        except AuthError:
            p = None
        if p is not None and p.session_id:
            auth_service(request).logout(p.session_id)
        response.delete_cookie(SESSION_COOKIE, path="/")
        return {"signedOut": True}

    @r.get("/me")
    def me(request: Request, response: Response) -> Any:
        try:
            p = authenticate(request)
        except AuthError as exc:
            return auth_error_response(exc)
        response.headers["Cache-Control"] = "no-store"
        out: dict[str, Any] = {"user": p.public()}
        if p.session_id:
            out["csrf_token"] = csrf_for(request.cookies.get(SESSION_COOKIE, ""))
        return camelize(out)

    @r.get("/status")
    def status(request: Request) -> Any:
        try:
            has_users = auth_service(request).has_users()
        except Exception:  # noqa: BLE001 - table missing before init-db
            return {"ready": False, "hasUsers": False, "message": "Run `python -m aurum.cli init-db` first."}
        return {"ready": True, "hasUsers": has_users,
                "message": None if has_users else "No users yet. Create the first administrator with "
                                                  "`python -m aurum.cli users create --role admin`."}

    return r
