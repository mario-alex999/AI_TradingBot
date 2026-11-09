"""Request authentication for routes that need a known person or service (Module 07 onwards).

- People: ``aurum_session`` httpOnly cookie, plus the ``X-CSRF-Token`` header on every state-changing request
  (double submit, checked against the session's stored hash).
- Services: ``Authorization: Bearer <service token>``. No CSRF (no cookie is involved).

Use ``require(permission)`` or ``require_human(permission)`` as a FastAPI dependency. Failures are structured errors with a
code; they never say which part of a credential was wrong.
"""
from __future__ import annotations

import hmac
from typing import Any, Callable

from fastapi import Depends, Request
from fastapi.responses import JSONResponse

from aurum.auth import passwords
from aurum.auth.service import AuthError, AuthService, Principal

SESSION_COOKIE = "aurum_session"
CSRF_HEADER = "X-CSRF-Token"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def auth_service(request: Request) -> AuthService:
    return request.app.state.auth


def authenticate(request: Request) -> Principal:
    """The caller, or AuthError(401). Cookie sessions must present a valid CSRF token on unsafe methods."""
    svc = auth_service(request)
    header = request.headers.get("Authorization", "")
    if header.lower().startswith("bearer "):
        p = svc.resolve_service_token(header[7:].strip())
        if p is None:
            raise AuthError(401, "INVALID_TOKEN", "The service token is missing, revoked or expired.")
        return p
    p = svc.resolve_session(request.cookies.get(SESSION_COOKIE))
    if p is None:
        raise AuthError(401, "NOT_AUTHENTICATED", "Sign in to continue. Your session may have expired.")
    if request.method.upper() not in SAFE_METHODS:
        sent = request.headers.get(CSRF_HEADER, "")
        if not sent or not p.csrf_hash or not hmac.compare_digest(passwords.token_hash(sent), p.csrf_hash):
            raise AuthError(403, "CSRF_FAILED", "The request is missing its security token. Reload the page and try again.")
    return p


def require(permission: str, *, human: bool = False) -> Callable[[Request], Principal]:
    def dep(request: Request) -> Principal:
        p = authenticate(request)
        if human and not p.is_human:
            raise AuthError(403, "HUMAN_REQUIRED", "This action needs a signed-in person; services cannot perform it.")
        if not p.can(permission):
            raise AuthError(403, "FORBIDDEN", "Your role does not allow this action.")
        request.state.principal = p
        return p
    return dep


def require_human(permission: str) -> Callable[[Request], Principal]:
    return require(permission, human=True)


def Requires(permission: str, *, human: bool = False) -> Any:   # noqa: N802 - reads like a type in signatures
    return Depends(require(permission, human=human))


def auth_error_response(exc: AuthError, correlation_id: str | None = None) -> JSONResponse:
    headers = {"X-Correlation-ID": correlation_id} if correlation_id else {}
    if exc.status == 401:
        headers["WWW-Authenticate"] = 'Bearer realm="aurum"'
    return JSONResponse(status_code=exc.status, headers=headers,
                        content={"error": {"code": exc.code, "message": exc.message, "correlationId": correlation_id},
                                 "detail": exc.message})
