"""Turns MT5 account_info / history_deals_get output into stored rows. Read-only towards MT5.

Open positions are derived from the deal history (entry deals not yet covered by exit deals), so the
recorder never needs position or order queries.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.engine import Engine

from aurum.account.db_models import AccountCashFlow, AccountSnapshot, TradeRecord
from aurum.database.connection import session_scope
from aurum.database.models import Instrument

log = logging.getLogger(__name__)

# MetaTrader5 deal constants
_BUY, _SELL = 0, 1
_IN, _OUT, _INOUT, _OUT_BY = 0, 1, 2, 3
CASH_KINDS = {2: "balance", 3: "credit", 4: "charge", 5: "correction", 6: "bonus", 7: "commission"}
TRADE_MODES = {0: "DEMO", 1: "CONTEST", 2: "REAL"}


def _f(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _deal_time(d: dict[str, Any], to_utc: Callable[[float], datetime]) -> datetime:
    sec = (d.get("time_msc") or 0) / 1000.0 or float(d.get("time", 0))
    return to_utc(sec)


def snapshot_from_account(acc: dict[str, Any], at: datetime) -> dict[str, Any]:
    level = acc.get("margin_level")
    return {"captured_at": at, "login": int(acc.get("login") or 0), "server": acc.get("server"),
            "currency": str(acc.get("currency") or ""),
            "trade_mode": TRADE_MODES.get(acc.get("trade_mode"), str(acc.get("trade_mode"))),
            "leverage": int(acc.get("leverage") or 0) or None, "balance": _f(acc.get("balance")),
            "equity": _f(acc.get("equity")), "margin": _f(acc.get("margin")), "margin_free": _f(acc.get("margin_free")),
            "margin_level": _f(level) if level and _f(acc.get("margin")) > 0 else None, "profit": _f(acc.get("profit"))}


def positions_from_deals(deals: list[dict[str, Any]], to_utc: Callable[[float], datetime],
                         canonical: dict[str, str] | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Group deals by position. Returns (positions with an entry deal, exit-only groups, cash flows)."""
    canonical = canonical or {}
    by_pos: dict[int, list[dict[str, Any]]] = defaultdict(list)
    cash: list[dict[str, Any]] = []
    for d in deals:
        t = d.get("type")
        if t in CASH_KINDS:
            cash.append({"deal_ticket": int(d.get("ticket") or 0), "ts_utc": _deal_time(d, to_utc), "amount": _f(d.get("profit")),
                         "kind": CASH_KINDS[t], "comment": (d.get("comment") or None)})
        elif t in (_BUY, _SELL) and d.get("position_id"):
            by_pos[int(d["position_id"])].append(d)

    positions, orphans = [], []
    for pos, ds in by_pos.items():
        ds.sort(key=lambda d: (d.get("time_msc") or d.get("time", 0) * 1000))
        entries = [d for d in ds if d.get("entry") == _IN]
        exits = [d for d in ds if d.get("entry") in (_OUT, _OUT_BY, _INOUT)]
        net = sum(_f(d.get(k)) for d in ds for k in ("profit", "swap", "commission", "fee"))
        vol_out = sum(_f(d.get("volume")) for d in exits)
        avg_out = sum(_f(d.get("price")) * _f(d.get("volume")) for d in exits) / vol_out if vol_out else None
        if not entries:
            if exits:
                orphans.append({"position_id": pos, "volume_out": vol_out, "close_price": avg_out,
                                "close_time": _deal_time(exits[-1], to_utc), "pnl": round(net, 2)})
            continue
        first = entries[0]
        vol_in = sum(_f(d.get("volume")) for d in entries)
        closed = vol_out + 1e-9 >= vol_in
        broker = str(first.get("symbol", ""))
        comment = (first.get("comment") or "").strip()
        magic = int(first.get("magic") or 0)
        positions.append({
            "position_id": pos, "symbol": canonical.get(broker, broker), "broker_symbol": broker,
            "side": "buy" if first.get("type") == _BUY else "sell", "status": "closed" if closed else "open",
            "volume": round(vol_in if closed else vol_in - vol_out, 4), "open_time": _deal_time(first, to_utc),
            "open_price": sum(_f(d.get("price")) * _f(d.get("volume")) for d in entries) / vol_in if vol_in else _f(first.get("price")),
            "close_time": _deal_time(exits[-1], to_utc) if closed else None, "close_price": avg_out if closed else None,
            "stop_loss": _f(first.get("sl")) or None, "take_profit": _f(first.get("tp")) or None, "pnl": round(net, 2),
            "strategy": comment or (f"magic {magic}" if magic else None), "magic": magic or None})
    return positions, orphans, cash


class AccountRecorder:
    def __init__(self, engine: Engine):
        self.engine = engine

    def canonical_map(self) -> dict[str, str]:
        with session_scope(self.engine) as s:
            return {b: c for c, b in s.execute(select(Instrument.symbol, Instrument.broker_symbol)).all()}

    def record_snapshot(self, account: dict[str, Any], at: datetime | None = None) -> dict[str, Any]:
        row = snapshot_from_account(account, at or datetime.now(timezone.utc))
        if row["trade_mode"] != "DEMO":  # Module 01 refuses non-demo accounts; never mirror one either.
            raise ValueError(f"Refusing to record a {row['trade_mode']} account; AURUM mirrors DEMO accounts only.")
        with session_scope(self.engine) as s:
            s.add(AccountSnapshot(**row))
        return row

    def store_deals(self, deals: list[dict[str, Any]], to_utc: Callable[[float], datetime]) -> dict[str, int]:
        positions, orphans, cash = positions_from_deals(deals, to_utc, self.canonical_map())
        closed_late = 0
        with session_scope(self.engine) as s:
            for p in positions:
                s.merge(TradeRecord(**p))
            for o in orphans:  # entry deal fell outside the window: close the position we already know as open
                rec = s.get(TradeRecord, o["position_id"])
                if rec is not None and rec.status == "open" and o["volume_out"] + 1e-9 >= rec.volume:
                    rec.status, rec.close_time, rec.close_price = "closed", o["close_time"], o["close_price"]
                    rec.pnl = round((rec.pnl or 0) + o["pnl"], 2)
                    closed_late += 1
            for c in cash:
                if c["deal_ticket"]:
                    s.merge(AccountCashFlow(**c))
        return {"positions": len(positions), "open": sum(p["status"] == "open" for p in positions),
                "closed_late": closed_late, "cash_flows": len(cash)}

    def sync(self, gateway: Any, lookback_days: int = 90, now: datetime | None = None) -> dict[str, Any]:
        """One read-only pass: an account snapshot plus the deal history of the last ``lookback_days``."""
        now = now or datetime.now(timezone.utc)
        snap = self.record_snapshot(gateway.account_snapshot(), now)
        counts = self.store_deals(gateway.deals(now - timedelta(days=lookback_days), now + timedelta(days=1)),
                                  gateway.clock.to_utc)
        log.info("Account synced", extra={"equity": snap["equity"], **counts})
        return {"equity": snap["equity"], "currency": snap["currency"], **counts}
