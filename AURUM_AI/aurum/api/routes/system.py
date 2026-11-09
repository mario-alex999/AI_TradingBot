"""GET /agents/status and GET /notifications.

/notifications merges two sources:
- computed alerts derived from stored module runs (read state kept in the browser, as before);
- for a signed-in person, Module 07's stored notifications (approval requests, expiries, executions), whose read state is
  kept on the server per person (POST /approvals/notifications/read). Those carry ``source: "approvals"``.
Without a session the response is exactly what it was before Module 07.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Query, Request

from aurum.api.system_queries import SystemQueries

log = logging.getLogger(__name__)

_SEVERITY = {"critical": "critical", "high": "warning", "normal": "info", "low": "info"}


def _category(event_type: str) -> str:
    if event_type.startswith("risk."):
        return "risk_limit"
    if event_type.startswith(("execution.", "management.")):
        return "signal_generated"
    return "approval_required"


def _stored(request: Request, limit: int) -> list[dict[str, Any]]:
    getter = getattr(request.app.state, "approvals_service", None)
    if getter is None or not request.cookies.get("aurum_session"):
        return []
    try:
        from aurum.api.security import auth_service
        p = auth_service(request).resolve_session(request.cookies.get("aurum_session"))
        if p is None:
            return []
        rows = getter().notifications(p, False, limit)
    except Exception as exc:  # noqa: BLE001 - the computed feed must keep working if Module 07 is unavailable
        log.info("Stored notifications unavailable", extra={"error": type(exc).__name__})
        return []
    return [{"id": f"m07-{n['id']}", "category": _category(n["event_type"]), "severity": _SEVERITY.get(n["priority"], "info"),
             "title": n["title"], "message": n["message"], "timestamp": n["created_at"].isoformat(), "read": n["read"],
             "source": "approvals", "notificationId": n["id"], "approvalId": n["approval_id"],
             "managementRequestId": n["management_request_id"], "positionId": n["position_id"]} for n in rows]


def build_router(state: Any) -> APIRouter:
    r = APIRouter()
    sq = SystemQueries(state.engine, state.settings)

    @r.get("/agents/status")
    def agents_status() -> list[dict[str, Any]]:
        return sq.agents()

    @r.get("/notifications")
    def notifications(request: Request, limit: int = Query(50, ge=1, le=200)) -> list[dict[str, Any]]:
        merged = _stored(request, limit) + sq.notifications(limit=limit)
        merged.sort(key=lambda n: n["timestamp"], reverse=True)
        return merged[:limit]

    return r
