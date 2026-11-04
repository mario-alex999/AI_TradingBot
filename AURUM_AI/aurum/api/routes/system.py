"""GET /agents/status and GET /notifications (read-only, derived from stored module runs)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from aurum.api.system_queries import SystemQueries


def build_router(state: Any) -> APIRouter:
    r = APIRouter()
    sq = SystemQueries(state.engine, state.settings)

    @r.get("/agents/status")
    def agents_status() -> list[dict[str, Any]]:
        return sq.agents()

    @r.get("/notifications")
    def notifications(limit: int = Query(50, ge=1, le=200)) -> list[dict[str, Any]]:
        return sq.notifications(limit=limit)

    return r
