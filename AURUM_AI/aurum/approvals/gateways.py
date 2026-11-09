"""Adapters between Module 07 and the modules around it. Each is a small protocol so tests can substitute a controlled
implementation, and so a module can later move behind HTTP without changing the approval logic.

- ``RiskGateway``  -> Module 06 (in-process ``RiskService``). Module 07 never computes risk itself.
- ``IdeaSource``   -> Module 05 trade candidates (read) and status write-back.
- ``PositionSource`` -> positions: Module 09 when it reports them; until then the read-only account mirror.
- ``EvidenceSource`` -> Modules 02-04 context through Module 04's analysis.

None of these can place, modify or close an order.
"""
from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from typing import Any, Callable, Protocol

log = logging.getLogger(__name__)


class GatewayUnavailable(RuntimeError):
    """A module Module 07 depends on cannot answer. Callers fail safe: no approval or handoff proceeds."""


# ---- Module 06 ------------------------------------------------------------------------------------------------------------
class RiskGateway(Protocol):
    def assess(self, proposal: dict[str, Any], actor: str, correlation_id: str, idempotency_key: str) -> dict[str, Any]: ...
    def validate(self, assessment_id: str) -> dict[str, Any]: ...
    def cancel(self, assessment_id: str, actor: str, reason: str, correlation_id: str | None) -> None: ...
    def normalised_entry(self, candidate_uid: str) -> Decimal | None: ...
    def quote_time(self, symbol: str, now: datetime) -> datetime | None: ...
    def symbols(self) -> dict[str, str]: ...
    def account_login(self) -> str | None: ...
    def active_locks(self, symbol: str | None = None, strategy: str | None = None) -> list[dict[str, Any]]: ...


class InProcessRiskGateway:
    """Module 06 through its service object (same process, same database)."""

    def __init__(self, service: Callable[[], Any]):
        self._svc = service

    def _call(self, fn: Callable[[], Any]) -> Any:
        from aurum.risk.service import RiskError
        try:
            return fn()
        except RiskError:
            raise
        except Exception as exc:  # noqa: BLE001 - Module 06 failure: fail safe upstream
            raise GatewayUnavailable(f"Module 06 is unavailable: {type(exc).__name__}") from exc

    def assess(self, proposal: dict[str, Any], actor: str, correlation_id: str, idempotency_key: str) -> dict[str, Any]:
        return self._call(lambda: self._svc().assess(proposal, actor, correlation_id, idempotency_key))

    def validate(self, assessment_id: str) -> dict[str, Any]:
        return self._call(lambda: self._svc().validate_assessment(assessment_id))

    def cancel(self, assessment_id: str, actor: str, reason: str, correlation_id: str | None) -> None:
        from aurum.risk.service import RiskError
        try:
            self._call(lambda: self._svc().set_assessment_state(assessment_id, "cancelled", actor, reason, correlation_id))
        except RiskError as exc:          # already consumed/cancelled/expired: nothing reserved any more
            if exc.code not in ("STATE_FINAL", "NOT_FOUND"):
                raise

    def normalised_entry(self, candidate_uid: str) -> Decimal | None:
        try:
            raw = self._svc().engine.proposal_from_idea(candidate_uid, {})
            return Decimal(str(raw["entry"]))
        except Exception as exc:  # noqa: BLE001 - spec missing etc.: the zone mid-point is used and Module 06 judges it
            log.info("Module 06 could not normalise the entry", extra={"candidate": candidate_uid, "error": type(exc).__name__})
            return None

    def quote_time(self, symbol: str, now: datetime) -> datetime | None:
        q = self._call(lambda: self._svc().data.quote(symbol, now))
        return q.at if q is not None else None

    def symbols(self) -> dict[str, str]:
        return self._call(lambda: self._svc().data.symbols())

    def account_login(self) -> str | None:
        snap = self._call(lambda: self._svc().data.account_snapshot())
        return str(snap.login) if snap is not None and snap.login is not None else None

    def active_locks(self, symbol: str | None = None, strategy: str | None = None) -> list[dict[str, Any]]:
        locks = self._call(lambda: self._svc().locks(False))["active"]
        out = []
        for lk in locks:
            scope, value = lk.get("scope"), lk.get("scope_value")
            if scope in ("global", "account") or (scope == "instrument" and value == symbol) or (scope == "strategy" and value == strategy):
                out.append(lk)
        return out


# ---- Module 05 ------------------------------------------------------------------------------------------------------------
class IdeaSource(Protocol):
    def active_candidates(self) -> list[dict[str, Any]]: ...
    def candidate(self, uid: str) -> dict[str, Any] | None: ...
    def evidence(self, uid: str) -> list[dict[str, Any]]: ...
    def ai_review(self, uid: str) -> dict[str, Any] | None: ...
    def gates(self, uid: str) -> list[dict[str, Any]]: ...
    def feedback(self, uid: str, decision: str, actor: str, note: str) -> str: ...


class Module05Source:
    """Reads Module 05 through its repository and writes status back through its own service, so Module 05's rules
    (status machine, gate re-checks) stay in charge of Module 05's data."""

    def __init__(self, decision_service: Callable[[], Any]):
        self._svc = decision_service

    def active_candidates(self) -> list[dict[str, Any]]:
        return [r for r in self._svc().repo.active() if r["status"] in ("new", "under_review")]

    def candidate(self, uid: str) -> dict[str, Any] | None:
        try:
            return self._svc()._candidate(uid)
        except Exception:  # noqa: BLE001 - unknown uid
            return None

    def evidence(self, uid: str) -> list[dict[str, Any]]:
        return self._svc().repo.evidence(uid)

    def ai_review(self, uid: str) -> dict[str, Any] | None:
        try:
            return self._svc().ai.audit.latest(uid)
        except Exception:  # noqa: BLE001 - AI review is optional
            return None

    def gates(self, uid: str) -> list[dict[str, Any]]:
        try:
            return self._svc().repo.gates(uid)
        except Exception:  # noqa: BLE001
            return []

    def feedback(self, uid: str, decision: str, actor: str, note: str) -> str:
        """Mirror a Module 07 outcome into Module 05. Returns what happened; never raises into the approval flow."""
        svc = self._svc()
        try:
            if decision == "APPROVED":
                svc.approve(uid, actor, note)
            elif decision in ("REJECTED",):
                svc.reject(uid, actor, note)
            else:
                return "not_applicable"
            return "applied"
        except Exception as exc:  # noqa: BLE001 - e.g. Module 05 gate fails now, or the candidate already moved on
            return f"not_applied: {exc}"[:300]


# ---- positions ------------------------------------------------------------------------------------------------------------
class PositionSource(Protocol):
    name: str

    def positions(self, now: datetime) -> tuple[list[dict[str, Any]], datetime | None]: ...


class AccountMirrorPositions:
    """Open positions from the account mirror (``trade_records``), filled by the read-only ``sync-account``. Used until
    Module 09 reports positions itself. Freshness is the time of the latest account snapshot."""
    name = "account_mirror"

    def __init__(self, engine: Any, quote: Callable[[str, datetime], Any] | None = None):
        self.engine, self.quote = engine, quote

    def positions(self, now: datetime) -> tuple[list[dict[str, Any]], datetime | None]:
        from sqlalchemy import select

        from aurum.account.db_models import AccountSnapshot, TradeRecord
        from aurum.database.connection import session_scope
        with session_scope(self.engine) as s:
            snap = s.execute(select(AccountSnapshot).order_by(AccountSnapshot.captured_at.desc()).limit(1)).scalar_one_or_none()
            rows = s.execute(select(TradeRecord).where(TradeRecord.status == "open")).scalars().all()
            out = []
            for r in rows:
                price = None
                if self.quote is not None:
                    try:
                        q = self.quote(r.symbol, now)
                        price = (q.bid if r.side == "buy" else q.ask) if q is not None else None
                    except Exception:  # noqa: BLE001 - no quote: shown as unavailable
                        price = None
                out.append({
                    "position_id": str(r.position_id), "account_login": str(snap.login) if snap and snap.login else None,
                    "symbol": r.symbol, "side": "BUY" if r.side == "buy" else "SELL", "volume": Decimal(str(r.volume)),
                    "entry_price": Decimal(str(r.open_price)), "current_price": Decimal(str(price)) if price is not None else None,
                    "stop_loss": Decimal(str(r.stop_loss)) if r.stop_loss else None,
                    "take_profit": Decimal(str(r.take_profit)) if r.take_profit else None,
                    "unrealized_pnl": Decimal(str(r.pnl)), "realized_pnl": None, "status": "OPEN", "opened_at": r.open_time})
            return out, (snap.captured_at if snap else None)


# ---- evidence (Modules 02-04) ----------------------------------------------------------------------------------------------
class EvidenceSource(Protocol):
    def market_context(self, symbol: str, now: datetime) -> dict[str, Any] | None: ...


class Module04Evidence:
    """Module 04's analysis, which also carries the Module 02 (COT) and Module 03 (structure) integrations."""

    def __init__(self, intelligence: Callable[[], Any]):
        self._svc = intelligence

    def market_context(self, symbol: str, now: datetime) -> dict[str, Any] | None:
        try:
            return self._svc().analyze(symbol, now, include_module03=True)
        except Exception as exc:  # noqa: BLE001 - reported as missing evidence, never invented
            log.info("Module 04 analysis unavailable for evidence", extra={"symbol": symbol, "error": type(exc).__name__})
            return None
