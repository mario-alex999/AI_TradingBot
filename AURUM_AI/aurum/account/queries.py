"""Read-only queries behind /account, /trades, /risk and /performance.

Every figure comes from stored rows (account snapshots, positions rebuilt from MT5 deals, stored ticks and
specifications). Where an input is missing the value is ``None`` instead of an estimate.
"""
from __future__ import annotations

import math
from bisect import bisect_right
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from itertools import accumulate
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.engine import Engine

from aurum.account.db_models import AccountDeal, AccountSnapshot, TradeRecord
from aurum.account.deals import CANCELED_TYPES, TRADE_TYPES
from aurum.api.market_queries import MarketQueries, _iso, _utc, asset_class
from aurum.config import Settings
from aurum.database.connection import session_scope
from aurum.learning.features import session_of

# Sessions come from the learning agent's DST-aware classifier (each market's local clock). The performance filter
# keys asia / london / newyork; the London-New York overlap counts for both London and New York.
SESSION_LABELS = {"asia": "Asian", "london": "London", "newyork": "New York",
                  "london-newyork overlap": "London-New York overlap", "off-hours": "Off-hours"}
SESSION_FILTER = {"asia": {"asia"}, "london": {"london", "london-newyork overlap"},
                  "newyork": {"newyork", "london-newyork overlap"}}
MIN_SHARPE_DAYS = 20


def _r(v: float | None, n: int = 2) -> float | None:
    return None if v is None or (isinstance(v, float) and not math.isfinite(v)) else round(v, n)


def _max_drawdown_pct(values: list[float]) -> float:
    peak, worst = -math.inf, 0.0
    for v in values:
        peak = max(peak, v)
        if peak > 0:
            worst = max(worst, (peak - v) / peak * 100)
    return worst


class AccountQueries:
    def __init__(self, engine: Engine, settings: Settings):
        self.engine = engine
        self.settings = settings
        self.mq = MarketQueries(engine)

    # ------------------------------------------------------------------ raw reads
    def latest_snapshot(self) -> AccountSnapshot | None:
        with session_scope(self.engine) as s:
            snap = s.execute(select(AccountSnapshot).order_by(AccountSnapshot.captured_at.desc()).limit(1)).scalar()
            if snap is not None:
                s.expunge(snap)
            return snap

    def _equity_at(self, start: datetime) -> float | None:
        """Equity of the last snapshot before ``start``, else the first one after it."""
        with session_scope(self.engine) as s:
            before = s.execute(select(AccountSnapshot.equity).where(AccountSnapshot.captured_at < start)
                               .order_by(AccountSnapshot.captured_at.desc()).limit(1)).scalar()
            if before is not None:
                return before
            return s.execute(select(AccountSnapshot.equity).where(AccountSnapshot.captured_at >= start)
                             .order_by(AccountSnapshot.captured_at).limit(1)).scalar()

    def _equity_history(self) -> list[float]:
        # Only one column is read; the recorder thins snapshots older than 7 days to one per hour.
        with session_scope(self.engine) as s:
            return list(s.execute(select(AccountSnapshot.equity).order_by(AccountSnapshot.captured_at)).scalars().all())

    def _open_count(self) -> int:
        with session_scope(self.engine) as s:
            return s.execute(select(func.count()).select_from(TradeRecord).where(TradeRecord.status == "open")).scalar_one()

    def _trades(self, status: str) -> list[TradeRecord]:
        with session_scope(self.engine) as s:
            order = TradeRecord.close_time if status == "closed" else TradeRecord.open_time
            rows = list(s.execute(select(TradeRecord).where(TradeRecord.status == status).order_by(order)).scalars().all())
            s.expunge_all()
            return rows

    def _specs(self) -> dict[str, dict[str, Any]]:
        return {s["symbol"]: s for s in self.mq.repo.latest_specs()}

    # ------------------------------------------------------------------ /account/summary
    def summary(self) -> dict[str, Any] | None:
        snap = self.latest_snapshot()
        if snap is None:
            return None
        return {"environment": "demo" if snap.trade_mode == "DEMO" else "live", "currency": snap.currency,
                "balance": _r(snap.balance), "equity": _r(snap.equity), "freeMargin": _r(snap.margin_free),
                "marginLevel": _r(snap.margin_level), "openPositions": self._open_count(),
                "pendingApprovals": self._pending_approvals(), "updatedAt": _iso(snap.captured_at)}

    # ------------------------------------------------------------------ /trades/*
    def _pending_approvals(self) -> int:
        """Trade ideas waiting for a person: the Module 07 approval queue (pending / under review) once Module 07 is in use
        (it holds at least one request), otherwise Module 05 candidates still open for review. Zero when neither exists."""
        from sqlalchemy.exc import SQLAlchemyError

        from aurum.decision.db_models import TradeCandidate
        from aurum.decision.lifecycle import ACTIVE
        try:
            from aurum.approvals.db_models import ApprovalRequest
            from aurum.approvals.states import OPEN
            with session_scope(self.engine) as s:
                if s.execute(select(ApprovalRequest.approval_id).limit(1)).first() is not None:
                    return s.execute(select(func.count()).select_from(ApprovalRequest)
                                     .where(ApprovalRequest.status.in_([x.value for x in OPEN]))).scalar_one()
        except SQLAlchemyError:
            pass                                         # Module 07 not initialised: fall back to Module 05
        try:
            with session_scope(self.engine) as s:
                return s.execute(select(func.count()).select_from(TradeCandidate).where(TradeCandidate.status.in_(ACTIVE))).scalar_one()
        except SQLAlchemyError:
            return 0

    def _money_per_price_unit(self, rec: TradeRecord, spec: dict[str, Any]) -> float | None:
        ts, tv = spec.get("tick_size"), spec.get("tick_value")
        return tv / ts * rec.volume if ts and tv else None

    def open_trades(self, now: datetime | None = None) -> list[dict[str, Any]]:
        quotes = {q["symbol"]: q for q in self.mq.quotes(now=now)}
        specs = self._specs()
        out = []
        for rec in self._trades("open"):
            q = quotes.get(rec.symbol)
            price = None
            if q and q["status"] == "open":  # only a fresh tick is a current price
                price = q["bid"] if rec.side == "buy" else q["ask"]
            per_unit = self._money_per_price_unit(rec, specs.get(rec.symbol, {}))
            floating = None
            if price is not None and per_unit is not None:
                direction = 1 if rec.side == "buy" else -1
                floating = round((price - rec.open_price) * direction * per_unit + (rec.pnl or 0), 2)
            out.append({"ticket": rec.id, "instrument": rec.symbol, "side": rec.side, "volume": rec.volume,
                        "entryPrice": rec.open_price, "stopLoss": rec.stop_loss, "takeProfit": rec.take_profit,
                        "currentPrice": price, "floatingPnl": floating, "openedAt": _iso(rec.open_time), "kind": "position"})
        return out

    def history(self, limit: int = 500) -> list[dict[str, Any]]:
        with session_scope(self.engine) as s:
            rows = list(s.execute(select(TradeRecord).where(TradeRecord.status == "closed")
                                  .order_by(TradeRecord.close_time.desc()).limit(limit)).scalars().all())
            s.expunge_all()
        return [{"ticket": r.id, "instrument": r.symbol, "side": r.side, "volume": r.volume,
                 "entryPrice": r.open_price, "stopLoss": r.stop_loss, "takeProfit": r.take_profit, "currentPrice": None,
                 "floatingPnl": None, "openedAt": _iso(r.open_time), "closedAt": _iso(r.close_time),
                 "closePrice": r.close_price, "realisedPnl": _r(r.pnl), "kind": "closed"} for r in rows]

    # ------------------------------------------------------------------ balance reconstruction
    def _balance_fn(self):
        """balance(t) from the latest snapshot, moved by realised P/L and cash flows between t and the snapshot.
        Without a snapshot it is the running sum of the stored history (exact only if that history is complete)."""
        events: list[tuple[datetime, float]] = [(r.close_time, r.pnl or 0.0) for r in self._trades("closed")]
        with session_scope(self.engine) as s:
            cash = select(AccountDeal.ts_utc, AccountDeal.profit, AccountDeal.swap, AccountDeal.commission, AccountDeal.fee).where(
                AccountDeal.type.not_in(TRADE_TYPES + CANCELED_TYPES))
            events += [(_utc(t), sum(v or 0.0 for v in vals)) for t, *vals in s.execute(cash).all()]
        events.sort(key=lambda e: e[0])
        times = [_utc(t) for t, _ in events]
        cum = list(accumulate(a for _, a in events))
        snap = self.latest_snapshot()

        def total(t: datetime) -> float:
            i = bisect_right(times, t)
            return cum[i - 1] if i else 0.0

        if snap is None:
            return total
        anchor_t, anchor_b = _utc(snap.captured_at), snap.balance
        return lambda t: anchor_b + total(t) - total(anchor_t)

    # ------------------------------------------------------------------ /performance/summary
    def performance(self, date_from: date | None = None, date_to: date | None = None, strategy: str | None = None,
                    instrument: str | None = None, session: str | None = None) -> dict[str, Any] | None:
        trades = self._trades("closed")
        if date_from:
            trades = [t for t in trades if _utc(t.close_time).date() >= date_from]
        if date_to:
            trades = [t for t in trades if _utc(t.close_time).date() <= date_to]
        if strategy:
            trades = [t for t in trades if (t.strategy or "").lower() == strategy.lower()]
        if instrument:
            trades = [t for t in trades if t.symbol.upper() == instrument.upper()]
        if session:
            trades = [t for t in trades if session_of(_utc(t.open_time)) in SESSION_FILTER.get(session, {session})]
        if not trades:
            return None

        pnls = [t.pnl or 0.0 for t in trades]
        wins, losses = [p for p in pnls if p > 0], [p for p in pnls if p < 0]
        gross_win, gross_loss = sum(wins), -sum(losses)
        balance = self._balance_fn()

        start_t = _utc(trades[0].close_time) - timedelta(microseconds=1)
        start = balance(start_t)
        curve = [{"time": _iso(start_t), "equity": round(start, 2)}]
        level = start
        for t in trades:
            level += t.pnl or 0.0
            curve.append({"time": _iso(t.close_time), "equity": round(level, 2)})

        monthly_pnl: dict[str, float] = defaultdict(float)
        first_close: dict[str, datetime] = {}
        for t in trades:
            key = _utc(t.close_time).strftime("%Y-%m")
            monthly_pnl[key] += t.pnl or 0.0
            first_close.setdefault(key, _utc(t.close_time))
        monthly = []
        for month, pnl in sorted(monthly_pnl.items()):
            y, m = map(int, month.split("-"))
            base = balance(datetime(y, m, 1, tzinfo=timezone.utc) - timedelta(microseconds=1))
            if base <= 0:  # account funded during the month: measure from just before its first trade
                base = balance(first_close[month] - timedelta(microseconds=1))
            if base > 0:
                monthly.append({"month": month, "returnPct": round(pnl / base * 100, 2)})

        def grouped(key) -> list[dict[str, Any]]:
            acc: dict[str, float] = defaultdict(float)
            for t in trades:
                acc[key(t)] += t.pnl or 0.0
            return sorted(({"name": k, "pnl": round(v, 2)} for k, v in acc.items()), key=lambda x: -x["pnl"])

        return {
            "kind": "live",
            "metrics": {
                "totalTrades": len(trades), "winningTrades": len(wins), "losingTrades": len(losses),
                "winRate": round(len(wins) / len(trades) * 100, 2),
                # No losing trade means the ratio is undefined; null rather than infinity.
                "profitFactor": round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
                "averageProfit": round(gross_win / len(wins), 2) if wins else 0.0,
                "averageLoss": round(-gross_loss / len(losses), 2) if losses else 0.0,
                "expectancy": round(sum(pnls) / len(pnls), 2),
                "maxDrawdownPct": round(_max_drawdown_pct([p["equity"] for p in curve]), 2),
                "sharpe": self._sharpe(trades, balance),
            },
            "equityCurve": curve,
            "monthly": monthly,
            "byInstrument": grouped(lambda t: t.symbol),
            "bySession": grouped(lambda t: SESSION_LABELS.get(session_of(_utc(t.open_time)), "Off-hours")),
            "byStrategy": grouped(lambda t: t.strategy or "Unlabelled"),
        }

    @staticmethod
    def _sharpe(trades: list[TradeRecord], balance) -> float | None:
        """Annualised daily Sharpe (risk-free 0) over every weekday in the range; None below 20 days."""
        daily: dict[date, float] = defaultdict(float)
        for t in trades:
            daily[_utc(t.close_time).date()] += t.pnl or 0.0
        first, last = min(daily), max(daily)
        rets = []
        d = first
        while d <= last:
            if d.weekday() < 5 or d in daily:
                base = balance(datetime(d.year, d.month, d.day, tzinfo=timezone.utc) - timedelta(microseconds=1))
                if base > 0:
                    rets.append(daily.get(d, 0.0) / base)
            d += timedelta(days=1)
        if len(rets) < MIN_SHARPE_DAYS:
            return None
        mean = sum(rets) / len(rets)
        sd = math.sqrt(sum((r - mean) ** 2 for r in rets) / (len(rets) - 1))
        return round(mean / sd * math.sqrt(252), 2) if sd > 0 else None

    # ------------------------------------------------------------------ /risk/overview
    def limits(self) -> dict[str, Any]:
        s = self.settings
        return {"maxRiskPerTradePct": s.risk_max_per_trade_pct, "maxDailyLossPct": s.risk_max_daily_loss_pct,
                "maxOpenPositions": s.risk_max_open_positions, "maxTotalExposurePct": s.risk_max_total_exposure_pct,
                "maxCorrelatedExposurePct": s.risk_max_correlated_exposure_pct, "enforcedBy": "backend"}

    def risk(self, now: datetime | None = None) -> dict[str, Any]:
        return self._risk_state(now)[0]

    def _risk_state(self, now: datetime | None = None) -> tuple[dict[str, Any], int]:
        """The risk overview plus the open-position count, computed once (breaches() reuses both)."""
        now = now or datetime.now(timezone.utc)
        snap = self.latest_snapshot()
        out: dict[str, Any] = {"accountRiskPct": None, "riskPerTradePct": None, "dailyDrawdownPct": None,
                               "weeklyDrawdownPct": None, "maxDrawdownPct": None, "openRisk": None,
                               "exposureByInstrument": [], "exposureByAssetClass": [], "limits": self.limits()}
        if snap is None:
            return out, self._open_count()
        equity = snap.equity
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_start = day_start - timedelta(days=day_start.weekday())

        def drawdown_since(start: datetime) -> float | None:
            ref = self._equity_at(start)
            if ref is None or ref <= 0:
                return None
            return round(max(0.0, (ref - equity) / ref * 100), 2)

        out["dailyDrawdownPct"] = drawdown_since(day_start)
        out["weeklyDrawdownPct"] = drawdown_since(week_start)
        out["maxDrawdownPct"] = round(_max_drawdown_pct(self._equity_history()), 2)

        specs = self._specs()
        opens = self._trades("open")
        risks: list[float | None] = []
        by_inst: dict[str, float] = defaultdict(float)
        by_class: dict[str, float] = defaultdict(float)
        for rec in opens:
            spec = specs.get(rec.symbol, {})
            per_unit = self._money_per_price_unit(rec, spec)
            if rec.stop_loss and per_unit is not None:
                adverse = (rec.open_price - rec.stop_loss) if rec.side == "buy" else (rec.stop_loss - rec.open_price)
                risks.append(max(0.0, adverse) * per_unit)
            else:
                risks.append(None)  # no stop loss or no specification: the risk cannot be bounded
            cs = spec.get("contract_size")
            notional = None
            if cs and equity > 0:
                if spec.get("currency_profit") == snap.currency:
                    notional = rec.volume * cs * rec.open_price
                elif spec.get("currency_base") == snap.currency:
                    notional = rec.volume * cs
            if notional is not None:
                pct = notional / equity * 100
                by_inst[rec.symbol] += pct
                by_class[asset_class(spec.get("asset_class"))] += pct
        if not opens:
            out.update(openRisk=0.0, accountRiskPct=0.0)
        elif all(r is not None for r in risks) and equity > 0:
            total = sum(risks)  # type: ignore[arg-type]
            out.update(openRisk=round(total, 2), accountRiskPct=round(total / equity * 100, 2),
                       riskPerTradePct=round(max(risks) / equity * 100, 2))  # type: ignore[type-var]
        out["exposureByInstrument"] = [{"name": k, "value": round(v, 2)} for k, v in sorted(by_inst.items(), key=lambda x: -x[1])]
        out["exposureByAssetClass"] = [{"name": k, "value": round(v, 2)} for k, v in sorted(by_class.items(), key=lambda x: -x[1])]
        return out, len(opens)

    def breaches(self, now: datetime | None = None) -> list[tuple[str, str, str]]:
        """(key, severity, message) for each configured limit the stored account currently exceeds."""
        r, n_open = self._risk_state(now)
        lim = r["limits"]
        found = []
        if lim["maxDailyLossPct"] and r["dailyDrawdownPct"] is not None and r["dailyDrawdownPct"] >= lim["maxDailyLossPct"]:
            found.append(("daily-loss", "critical", f"Daily drawdown {r['dailyDrawdownPct']}% reached the {lim['maxDailyLossPct']}% limit."))
        if lim["maxOpenPositions"] and n_open > lim["maxOpenPositions"]:
            found.append(("open-positions", "warning", f"{n_open} open positions exceed the limit of {lim['maxOpenPositions']}."))
        total_exp = sum(x["value"] for x in r["exposureByInstrument"])
        if lim["maxTotalExposurePct"] and total_exp > lim["maxTotalExposurePct"]:
            found.append(("exposure", "warning", f"Total exposure {total_exp:.1f}% of equity exceeds {lim['maxTotalExposurePct']}%."))
        if lim["maxRiskPerTradePct"] and r["riskPerTradePct"] is not None and r["riskPerTradePct"] > lim["maxRiskPerTradePct"]:
            found.append(("risk-per-trade", "warning",
                          f"A position risks {r['riskPerTradePct']}% of equity (limit {lim['maxRiskPerTradePct']}%)."))
        return found
