"""MT5 deals -> positions. Pure functions (no database, no MT5), shared by the account mirror and the learning agent.

MetaTrader5 deal constants: types BUY=0, SELL=1, BUY_CANCELED=13, SELL_CANCELED=14, every other type is a balance
operation (deposit, credit, commission, interest, dividend, tax...). Entries IN=0, OUT=1, INOUT=2 (netting
reversal: closes the position and opens the opposite side with the remaining volume), OUT_BY=3.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any, Callable

BUY, SELL = 0, 1
TRADE_TYPES = (BUY, SELL)
CANCELED_TYPES = (13, 14)
IN, OUT, INOUT, OUT_BY = 0, 1, 2, 3
CASH_KINDS = {2: "balance", 3: "credit", 4: "charge", 5: "correction", 6: "bonus", 7: "commission",
              8: "commission_daily", 9: "commission_monthly", 10: "agent_daily", 11: "agent_monthly", 12: "interest",
              15: "dividend", 16: "dividend_franked", 17: "tax"}
EPS = 1e-9


def num(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def deal_time(d: dict[str, Any], to_utc: Callable[[float], datetime]) -> datetime:
    sec = (d.get("time_msc") or 0) / 1000.0 or float(d.get("time", 0))
    return to_utc(sec)


def amount(d: dict[str, Any]) -> float:
    """Everything a deal moved on the balance: profit, swap, commission and fee."""
    return sum(num(d.get(k)) for k in ("profit", "swap", "commission", "fee"))


def is_cash(d: dict[str, Any]) -> bool:
    t = d.get("type")
    return t is not None and t not in TRADE_TYPES and t not in CANCELED_TYPES


def cash_flows(deals: list[dict[str, Any]], to_utc: Callable[[float], datetime]) -> list[dict[str, Any]]:
    return [{"ticket": int(d.get("ticket") or 0), "ts_utc": deal_time(d, to_utc), "amount": round(amount(d), 2),
             "kind": CASH_KINDS.get(d.get("type"), f"type_{d.get('type')}")} for d in deals if is_cash(d)]


class _Leg:
    def __init__(self, d: dict[str, Any], volume: float, at: datetime):
        self.first = d
        self.side = "buy" if d.get("type") == BUY else "sell"
        self.entries: list[tuple[float, float]] = [(volume, num(d.get("price")))]
        self.exits: list[tuple[float, float]] = []
        self.open_volume = volume
        self.open_time = at
        self.close_time: datetime | None = None
        self.pnl = 0.0

    @staticmethod
    def _vwap(fills: list[tuple[float, float]]) -> float | None:
        vol = sum(v for v, _ in fills)
        return sum(v * p for v, p in fills) / vol if vol > EPS else None

    def to_dict(self, leg_id: str, position_id: int, canonical: dict[str, str]) -> dict[str, Any]:
        closed = self.open_volume <= EPS
        broker = str(self.first.get("symbol", ""))
        comment = (self.first.get("comment") or "").strip()
        magic = int(self.first.get("magic") or 0)
        return {"id": leg_id, "position_id": position_id, "symbol": canonical.get(broker, broker), "broker_symbol": broker,
                "side": self.side, "status": "closed" if closed else "open",
                "volume": round(sum(v for v, _ in self.entries) if closed else self.open_volume, 4),
                "open_time": self.open_time, "open_price": self._vwap(self.entries),
                "close_time": self.close_time if closed else None, "close_price": self._vwap(self.exits) if closed else None,
                "stop_loss": num(self.first.get("sl")) or None, "take_profit": num(self.first.get("tp")) or None,
                "pnl": round(self.pnl, 2), "strategy": comment or (f"magic {magic}" if magic else None), "magic": magic or None}


def build_positions(deals: list[dict[str, Any]], to_utc: Callable[[float], datetime],
                    canonical: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """Every position leg (open or closed) that can be rebuilt from ``deals``, oldest first.

    Deals are replayed per ``position_id`` in time order. A netting reversal (INOUT) closes the current leg and
    starts a new one on the other side; later legs get ids ``<position_id>-2``, ``-3``... A position whose history
    starts with an exit (its entry is older than the deals given) cannot be rebuilt and is left out.
    """
    canonical = canonical or {}
    by_pos: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for d in deals:
        if d.get("type") in TRADE_TYPES and d.get("position_id"):
            by_pos[int(d["position_id"])].append(d)

    out: list[dict[str, Any]] = []
    for pos, ds in by_pos.items():
        ds.sort(key=lambda d: (d.get("time_msc") or num(d.get("time")) * 1000, int(d.get("ticket") or 0)))
        legs: list[_Leg] = []
        cur: _Leg | None = None
        complete = True
        for d in ds:
            entry, vol, at = d.get("entry"), num(d.get("volume")), deal_time(d, to_utc)
            if entry == IN:
                if cur is None:
                    cur = _Leg(d, vol, at)
                else:
                    cur.entries.append((vol, num(d.get("price"))))
                    cur.open_volume += vol
                cur.pnl += amount(d)
                continue
            if entry not in (OUT, INOUT, OUT_BY):
                continue
            if cur is None:
                complete = False  # exit without a known entry: history is cut
                break
            closing = min(vol, cur.open_volume) if entry == INOUT else vol
            cur.exits.append((closing, num(d.get("price"))))
            cur.open_volume = max(0.0, cur.open_volume - closing)
            cur.pnl += amount(d)
            if cur.open_volume <= EPS:
                cur.close_time = at
                legs.append(cur)
                cur = None
            if entry == INOUT and vol - closing > EPS:
                cur = _Leg(d, vol - closing, at)  # the reversal opens the opposite side
        if not complete:
            continue
        if cur is not None:
            legs.append(cur)
        out += [leg.to_dict(str(pos) if i == 0 else f"{pos}-{i + 1}", pos, canonical) for i, leg in enumerate(legs)]
    return sorted(out, key=lambda p: p["open_time"])
