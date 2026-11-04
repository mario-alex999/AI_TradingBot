"""Account mirror API (read-only): /account/summary, /trades/open, /trades/history, /risk/overview,
/performance/summary. Data comes from `aurum.cli sync-account` / `stream-ticks`; nothing here talks to MT5."""
from __future__ import annotations

from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Query

from aurum.account.queries import AccountQueries


def build_router(state: Any) -> APIRouter:
    r = APIRouter()
    aq = AccountQueries(state.engine, state.settings)

    @r.get("/account/summary")
    def account_summary() -> dict[str, Any] | None:
        """Latest stored account snapshot; ``null`` until the first sync."""
        return aq.summary()

    @r.get("/trades/open")
    def trades_open() -> list[dict[str, Any]]:
        return aq.open_trades()

    @r.get("/trades/history")
    def trades_history(limit: int = Query(500, ge=1, le=5000)) -> list[dict[str, Any]]:
        return aq.history(limit)

    @r.get("/risk/overview")
    def risk_overview() -> dict[str, Any]:
        return aq.risk()

    @r.get("/performance/summary")
    def performance_summary(from_: date | None = Query(None, alias="from"), to: date | None = None,
                            strategy: str | None = None, instrument: str | None = None,
                            timeframe: str | None = Query(None, description="Accepted for compatibility; trades carry no "
                                                                            "timeframe, so it does not filter."),
                            session: Literal["asia", "london", "newyork"] | None = None) -> dict[str, Any] | None:
        """Closed-trade performance; ``null`` when no closed trade matches."""
        return aq.performance(from_, to, strategy, instrument, session)

    return r
