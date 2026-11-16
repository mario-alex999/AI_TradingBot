"""Account mirror API (read-only): /account/summary, /account/accounts, /trades/open, /trades/history, /risk/overview,
/performance/summary. Data comes from `aurum.cli sync-account` / `stream-ticks` and the per-person MT5 links; nothing here
talks to MT5.

Every route needs a signed-in person or service token (permission ``approvals:view``) and covers ONE account:

1. ``?login=`` when the caller may see that account (403 otherwise);
2. else the account linked in this sign-in session (Connect your MT5 account);
3. else the first account in the caller's account scope;
4. else - people without a scope, such as administrators - the account synced most recently.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select

from aurum.account.queries import AccountQueries
from aurum.api.security import require
from aurum.auth import roles as perm
from aurum.auth.service import Principal
from aurum.database.connection import session_scope


def linked_login(engine: Any, p: Principal) -> int | None:
    """The account this sign-in session authorised with Connect your MT5 account, while the link is live."""
    if not p.session_id:
        return None
    from aurum.mt5link.db_models import Mt5Connection as C
    try:
        with session_scope(engine) as s:
            return s.execute(select(C.login).where(C.session_id == p.session_id, C.status.in_(("CONNECTING", "CONNECTED")))
                             .order_by(C.created_at.desc()).limit(1)).scalar()
    except Exception:  # noqa: BLE001 - table not migrated yet: no link
        return None


def resolve_login(engine: Any, p: Principal, requested: str | None) -> int | None:
    """The account a caller sees. None means 'the account synced most recently' (callers without a scope only)."""
    if requested:
        if not requested.isdigit():
            raise HTTPException(422, "login must be an MT5 account number.")
        if not p.may_access_account(requested):
            raise HTTPException(403, "That account is not one of yours.")
        return int(requested)
    linked = linked_login(engine, p)
    if linked is not None and p.may_access_account(linked):
        return int(linked)
    if p.account_scope:
        return int(p.account_scope[0])
    return None


def build_router(state: Any) -> APIRouter:
    r = APIRouter()
    aq = AccountQueries(state.engine, state.settings)
    viewer = Depends(require(perm.VIEW))

    def scoped(p: Principal, login: str | None) -> AccountQueries:
        return aq.for_account(resolve_login(state.engine, p, login))

    Login = Query(None, max_length=20, description="MT5 account number; defaults to your linked account.")

    @r.get("/account/accounts")
    def account_list(p: Principal = viewer) -> dict[str, Any]:
        """The mirrored accounts this caller may see, and which one the other routes use by default."""
        items = [a for a in aq.accounts() if p.may_access_account(a["login"])]
        current = scoped(p, None).account()
        return {"items": items, "current": str(current) if current is not None else None,
                "linked": str(linked_login(state.engine, p)) if linked_login(state.engine, p) is not None else None}

    @r.get("/account/summary")
    def account_summary(login: str | None = Login, p: Principal = viewer) -> dict[str, Any] | None:
        """Latest stored snapshot of the caller's account; ``null`` until its first sync."""
        return scoped(p, login).summary()

    @r.get("/trades/open")
    def trades_open(login: str | None = Login, p: Principal = viewer) -> list[dict[str, Any]]:
        return scoped(p, login).open_trades()

    @r.get("/trades/history")
    def trades_history(limit: int = Query(500, ge=1, le=5000), login: str | None = Login,
                       p: Principal = viewer) -> list[dict[str, Any]]:
        return scoped(p, login).history(limit)

    @r.get("/risk/overview")
    def risk_overview(login: str | None = Login, p: Principal = viewer) -> dict[str, Any]:
        return scoped(p, login).risk()

    @r.get("/performance/summary")
    def performance_summary(from_: date | None = Query(None, alias="from"), to: date | None = None,
                            strategy: str | None = None, instrument: str | None = None,
                            timeframe: str | None = Query(None, description="Accepted for compatibility; trades carry no "
                                                                            "timeframe, so it does not filter."),
                            session: Literal["asia", "london", "newyork"] | None = None,
                            login: str | None = Login, p: Principal = viewer) -> dict[str, Any] | None:
        """Closed-trade performance of the caller's account; ``null`` when no closed trade matches."""
        return scoped(p, login).performance(from_, to, strategy, instrument, session)

    return r
