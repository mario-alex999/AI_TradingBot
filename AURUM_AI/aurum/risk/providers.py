"""Read-only adapters from Module 06 to the stored data of Modules 01-05 and the DEMO account mirror.

Each method returns what is stored, or None / an empty list when it is not; nothing here invents a value. The interface
(``RiskData``) is also implemented in memory by simulation.py and the tests, so the engine never knows which it is using.
Nothing here imports the MT5 gateway: Module 06 never talks to MetaTrader 5.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from statistics import median
from typing import Any, Protocol

import pandas as pd
from sqlalchemy import and_, func, select
from sqlalchemy.engine import Engine

from aurum.risk.account import BalanceOperation, ClosedTrade, EquityPoint, Snapshot
from aurum.risk.calculator import CalcError, InstrumentSpec, Quote, Rate
from aurum.risk.money import D, D_or_none
from aurum.risk.portfolio import Position

log = logging.getLogger(__name__)
# MT5 deal types that move money in or out of the account (deposit/withdrawal, credit, bonus). Other non-trade types
# (charges, commissions, interest, corrections) are costs and stay in the P/L.
CASH_FLOW_TYPES = (2, 3, 6)


class RiskData(Protocol):
    def account_snapshot(self) -> Snapshot | None: ...
    def equity_history(self, since: datetime, until: datetime) -> list[EquityPoint]: ...
    def open_positions(self) -> list[Position]: ...
    def closed_trades(self, since: datetime) -> list[ClosedTrade]: ...
    def opened_since(self, since: datetime) -> int: ...
    def balance_operations(self, since: datetime) -> list[BalanceOperation]: ...
    def symbols(self) -> dict[str, str]: ...                                  # symbol -> asset class
    def spec(self, symbol: str) -> InstrumentSpec: ...                       # raises CalcError
    def quote(self, symbol: str, now: datetime) -> Quote | None: ...
    def spread_stats(self, symbol: str, now: datetime) -> tuple[Decimal | None, Decimal | None]: ...
    def rate(self, from_ccy: str, to_ccy: str, now: datetime) -> Rate | None: ...
    def closes(self, symbol: str, timeframe: str, bars: int, now: datetime) -> pd.Series | None: ...
    def intelligence(self, symbol: str, now: datetime) -> tuple[dict[str, Any] | None, str | None]: ...
    def quality_issues(self, symbol: str, now: datetime) -> list[dict[str, Any]]: ...
    def trade_idea(self, idea_id: str) -> dict[str, Any] | None: ...


def _utc(t: datetime) -> datetime:
    return t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t.astimezone(timezone.utc)


def _side(value: str) -> str:
    return "BUY" if str(value).lower() in ("buy", "long") else "SELL"


class DatabaseRiskData:
    """Live implementation over the AURUM database."""

    def __init__(self, engine: Engine, settings: Any, intelligence_service: Any = None, quality_lookback_hours: int = 24):
        self.engine, self.settings = engine, settings
        self._intel = intelligence_service
        self.quality_lookback = timedelta(hours=quality_lookback_hours)
        self._spec_cache: dict[str, tuple[datetime, InstrumentSpec]] = {}

    def _session(self):
        from aurum.database.connection import session_scope
        return session_scope(self.engine)

    # ---- account mirror ------------------------------------------------------------------------------------------------
    def account_snapshot(self) -> Snapshot | None:
        from aurum.account.db_models import AccountSnapshot as S
        try:
            with self._session() as s:
                r = s.execute(select(S).order_by(S.captured_at.desc()).limit(1)).scalar_one_or_none()
        except Exception as exc:  # noqa: BLE001 - absent tables mean "no data", reported as such
            log.warning("Account snapshot unavailable", extra={"error": type(exc).__name__})
            return None
        if r is None:
            return None
        return Snapshot(captured_at=_utc(r.captured_at), login=r.login, currency=r.currency, trade_mode=r.trade_mode,
                        leverage=r.leverage, balance=D(r.balance), equity=D(r.equity), margin=D_or_none(r.margin),
                        margin_free=D_or_none(r.margin_free), margin_level=D_or_none(r.margin_level), floating=D_or_none(r.profit))

    def equity_history(self, since: datetime, until: datetime) -> list[EquityPoint]:
        from aurum.account.db_models import AccountSnapshot as S
        try:
            with self._session() as s:
                rows = s.execute(select(S.captured_at, S.equity, S.balance).where(and_(S.captured_at >= since, S.captured_at <= until))
                                 .order_by(S.captured_at)).all()
                peak = s.execute(select(func.max(S.equity))).scalar()
        except Exception:  # noqa: BLE001
            return []
        pts = [EquityPoint(_utc(t), D(e), D(b)) for t, e, b in rows]
        if peak is not None and (not pts or D(peak) > max(p.equity for p in pts)):
            # The all-time peak may predate the window; it is kept so drawdown is measured from it.
            pts.insert(0, EquityPoint(since - timedelta(days=3650), D(peak), D(peak)))
        return pts

    def open_positions(self) -> list[Position]:
        from aurum.account.db_models import TradeRecord as T
        classes = self.symbols()
        try:
            with self._session() as s:
                rows = s.execute(select(T).where(T.status == "open")).scalars().all()
        except Exception:  # noqa: BLE001
            return []
        return [Position(id=r.id, symbol=r.symbol, side=_side(r.side), volume=D(r.volume), open_price=D(r.open_price),
                         stop_loss=D_or_none(r.stop_loss) or None, take_profit=D_or_none(r.take_profit) or None,
                         open_time=_utc(r.open_time), strategy=r.strategy, asset_class=classes.get(r.symbol, "unknown"))
                for r in rows]

    def closed_trades(self, since: datetime) -> list[ClosedTrade]:
        from aurum.account.db_models import TradeRecord as T
        try:
            with self._session() as s:
                rows = s.execute(select(T).where(and_(T.status == "closed", T.close_time >= since))).scalars().all()
        except Exception:  # noqa: BLE001
            return []
        return [ClosedTrade(r.id, r.symbol, _utc(r.open_time), _utc(r.close_time), D(r.pnl or 0)) for r in rows if r.close_time]

    def opened_since(self, since: datetime) -> int:
        from aurum.account.db_models import TradeRecord as T
        try:
            with self._session() as s:
                return int(s.execute(select(func.count()).select_from(T).where(T.open_time >= since)).scalar() or 0)
        except Exception:  # noqa: BLE001
            return 0

    def balance_operations(self, since: datetime) -> list[BalanceOperation]:
        from aurum.account.db_models import AccountDeal as A
        try:
            with self._session() as s:
                rows = s.execute(select(A.ts_utc, A.profit).where(and_(A.type.in_(CASH_FLOW_TYPES), A.ts_utc >= since))).all()
        except Exception:  # noqa: BLE001
            return []
        return [BalanceOperation(_utc(t), D(p or 0)) for t, p in rows]

    # ---- Module 01 -----------------------------------------------------------------------------------------------------
    def symbols(self) -> dict[str, str]:
        from aurum.database.models import Instrument
        with self._session() as s:
            return {sym: cls for sym, cls in s.execute(select(Instrument.symbol, Instrument.asset_class)).all()}

    def spec(self, symbol: str) -> InstrumentSpec:
        from aurum.database.models import Instrument, InstrumentSpec as S
        with self._session() as s:
            row = s.execute(select(S, Instrument.asset_class).join(Instrument, Instrument.id == S.instrument_id)
                            .where(Instrument.symbol == symbol).order_by(S.captured_at.desc()).limit(1)).first()
        if row is None:
            raise CalcError("SPEC_MISSING", f"No stored instrument specification for {symbol}; run `python -m aurum.cli specs`.")
        spec_row, asset_class = row
        cached = self._spec_cache.get(symbol)
        if cached and cached[0] == spec_row.captured_at:
            return cached[1]
        data = {c: getattr(spec_row, c) for c in ("digits", "point", "tick_size", "tick_value", "contract_size", "volume_min",
                                                   "volume_max", "volume_step", "currency_base", "currency_profit", "raw")}
        data["captured_at"] = _utc(spec_row.captured_at)
        spec = InstrumentSpec.from_row(symbol, asset_class, data)
        self._spec_cache[symbol] = (spec_row.captured_at, spec)
        return spec

    def _instrument_id(self, symbol: str) -> int | None:
        from aurum.database.models import Instrument
        with self._session() as s:
            return s.execute(select(Instrument.id).where(Instrument.symbol == symbol)).scalar()

    def quote(self, symbol: str, now: datetime) -> Quote | None:
        from aurum.database.models import MarketCandle, MarketTick
        iid = self._instrument_id(symbol)
        if iid is None:
            return None
        with self._session() as s:
            t = s.execute(select(MarketTick).where(and_(MarketTick.instrument_id == iid, MarketTick.ts_utc <= now))
                          .order_by(MarketTick.ts_utc.desc()).limit(1)).scalar_one_or_none()
            if t is not None and t.bid and t.ask:
                return Quote(D(t.bid), D(t.ask), _utc(t.ts_utc), "module_01.market_ticks")
            c = s.execute(select(MarketCandle).where(and_(MarketCandle.instrument_id == iid, MarketCandle.timeframe == "M5",
                                                           MarketCandle.quality_status == "valid", MarketCandle.ts_utc <= now))
                          .order_by(MarketCandle.ts_utc.desc()).limit(1)).scalar_one_or_none()
        if c is None or not c.close or c.spread is None:
            return None
        try:
            point = self.spec(symbol).point
        except CalcError:
            return None
        bid = D(c.close)
        # A candle closes 5 minutes after its open; its close is the bid of that moment.
        return Quote(bid, bid + D(c.spread) * point, _utc(c.ts_utc) + timedelta(minutes=5), "module_01.market_candles.M5")

    def spread_stats(self, symbol: str, now: datetime) -> tuple[Decimal | None, Decimal | None]:
        from aurum.database.models import MarketCandle
        iid = self._instrument_id(symbol)
        if iid is None:
            return None, None
        with self._session() as s:
            spreads = [r for (r,) in s.execute(select(MarketCandle.spread).where(and_(
                MarketCandle.instrument_id == iid, MarketCandle.timeframe == "M5", MarketCandle.ts_utc <= now,
                MarketCandle.spread.is_not(None))).order_by(MarketCandle.ts_utc.desc()).limit(500)).all()]
        med = D(median(spreads)) if spreads else None
        q = self.quote(symbol, now)
        try:
            current = (q.spread / self.spec(symbol).point) if q is not None else (D(spreads[0]) if spreads else None)
        except CalcError:
            current = D(spreads[0]) if spreads else None
        return current, med

    def rate(self, from_ccy: str, to_ccy: str, now: datetime) -> Rate | None:
        """Conversion from a stored quote of a configured pair (direct or inverse). Mid price; None if no such pair."""
        if from_ccy == to_ccy:
            return Rate(from_ccy, to_ccy, D(1), now, "identity")
        from aurum.database.models import Instrument, InstrumentSpec as S
        with self._session() as s:
            pairs = s.execute(select(Instrument.symbol, S.currency_base, S.currency_profit)
                              .join(S, S.instrument_id == Instrument.id).distinct()).all()
        for sym, base, profit in pairs:
            if (base, profit) in ((from_ccy, to_ccy), (to_ccy, from_ccy)):
                q = self.quote(sym, now)
                if q is None:
                    continue
                mid = (q.bid + q.ask) / 2
                r = mid if base == from_ccy else D(1) / mid
                return Rate(from_ccy, to_ccy, r, q.at, f"{sym} mid ({q.source})")
        return None

    def closes(self, symbol: str, timeframe: str, bars: int, now: datetime) -> pd.Series | None:
        from aurum.database.models import MarketCandle
        iid = self._instrument_id(symbol)
        if iid is None:
            return None
        from aurum.config import TIMEFRAME_SECONDS
        closed_before = now - timedelta(seconds=TIMEFRAME_SECONDS[timeframe])
        with self._session() as s:
            rows = s.execute(select(MarketCandle.ts_utc, MarketCandle.close).where(and_(
                MarketCandle.instrument_id == iid, MarketCandle.timeframe == timeframe, MarketCandle.quality_status == "valid",
                MarketCandle.is_complete.is_(True), MarketCandle.ts_utc <= closed_before))
                .order_by(MarketCandle.ts_utc.desc()).limit(bars + 1)).all()
        if not rows:
            return None
        return pd.Series({pd.Timestamp(_utc(t)): float(c) for t, c in rows if c is not None}).sort_index()

    def quality_issues(self, symbol: str, now: datetime) -> list[dict[str, Any]]:
        from aurum.database.models import DataQualityIssue as Q
        with self._session() as s:
            rows = s.execute(select(Q).where(and_(Q.symbol == symbol, Q.resolved.is_(False), Q.severity.in_(("warning", "error")),
                                                  Q.issue_type.not_in(("candle_revision",)),
                                                  Q.last_seen_at >= now - self.quality_lookback))).scalars().all()
        return [{"issue_type": r.issue_type, "severity": r.severity, "timeframe": r.timeframe, "ts_utc": r.ts_utc} for r in rows]

    # ---- Modules 04 (with its Module 02 summary) and 05 --------------------------------------------------------------------------------------------
    def intelligence(self, symbol: str, now: datetime) -> tuple[dict[str, Any] | None, str | None]:
        try:
            if self._intel is None:
                from aurum.market_intelligence.service import MarketIntelligenceService
                self._intel = MarketIntelligenceService(self.settings, self.engine)
            return self._intel.analyze(symbol, now, include_module03=False), None
        except Exception as exc:  # noqa: BLE001 - reported as "not evaluated", never as safe
            log.warning("Module 04 unavailable for risk", extra={"symbol": symbol, "error": type(exc).__name__})
            return None, f"{type(exc).__name__}: {exc}"[:300]

    def trade_idea(self, idea_id: str) -> dict[str, Any] | None:
        from aurum.decision.db_models import TradeCandidate as C
        try:
            with self._session() as s:
                r = s.execute(select(C).where(C.candidate_uid == idea_id)).scalar_one_or_none()
        except Exception:  # noqa: BLE001
            return None
        if r is None:
            return None
        return {"trade_idea_id": r.candidate_uid, "symbol": r.symbol, "direction": _side(r.direction),
                "entry_low": r.entry_low, "entry_high": r.entry_high, "stop_loss": r.stop_loss, "take_profit": r.take_profit,
                "strategy": r.template, "timeframe": r.timeframe, "confidence": r.confidence, "status": r.status,
                "expires_at": _utc(r.expires_at)}
