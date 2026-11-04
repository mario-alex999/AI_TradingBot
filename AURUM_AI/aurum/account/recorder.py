"""Turns MT5 account_info / history_deals_get output into stored rows. Read-only towards MT5.

Raw deals are stored once (keyed by deal ticket); every position touched by a sync is then rebuilt from ALL of its
stored deals, so overlapping windows, partial closes and netting reversals never double-count. Open positions come
from the deal history, so no position or order query is ever needed.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy import delete, func, select
from sqlalchemy.engine import Engine

from aurum.account.db_models import AccountDeal, AccountSnapshot, TradeRecord
from aurum.account.deals import build_positions, deal_time, num
from aurum.database.connection import session_scope
from aurum.database.models import Instrument

log = logging.getLogger(__name__)

TRADE_MODES = {0: "DEMO", 1: "CONTEST", 2: "REAL"}
RAW_SNAPSHOT_DAYS = 7  # older snapshots are thinned to the last one of each UTC hour


def _utc_from_seconds(sec: float) -> datetime:
    return datetime.fromtimestamp(sec, tz=timezone.utc)


def snapshot_from_account(acc: dict[str, Any], at: datetime) -> dict[str, Any]:
    level = acc.get("margin_level")
    return {"captured_at": at, "login": int(acc.get("login") or 0), "server": acc.get("server"),
            "currency": str(acc.get("currency") or ""),
            "trade_mode": TRADE_MODES.get(acc.get("trade_mode"), str(acc.get("trade_mode"))),
            "leverage": int(acc.get("leverage") or 0) or None, "balance": num(acc.get("balance")),
            "equity": num(acc.get("equity")), "margin": num(acc.get("margin")), "margin_free": num(acc.get("margin_free")),
            "margin_level": num(level) if level and num(acc.get("margin")) > 0 else None, "profit": num(acc.get("profit"))}


def _deal_row(d: dict[str, Any], to_utc: Callable[[float], datetime]) -> dict[str, Any]:
    return {"ticket": int(d["ticket"]), "position_id": int(d.get("position_id") or 0) or None, "ts_utc": deal_time(d, to_utc),
            "type": int(d.get("type") or 0), "entry": d.get("entry"), "symbol": d.get("symbol") or None,
            "volume": num(d.get("volume")), "price": num(d.get("price")), "profit": num(d.get("profit")),
            "swap": num(d.get("swap")), "commission": num(d.get("commission")), "fee": num(d.get("fee")),
            "sl": num(d.get("sl")) or None, "tp": num(d.get("tp")) or None, "magic": int(d.get("magic") or 0) or None,
            "comment": (d.get("comment") or None)}


def _as_deal(r: AccountDeal) -> dict[str, Any]:
    """A stored row back in MT5 deal form (times already UTC)."""
    return {"ticket": r.ticket, "position_id": r.position_id, "time_msc": r.ts_utc.timestamp() * 1000, "type": r.type,
            "entry": r.entry, "symbol": r.symbol, "volume": r.volume, "price": r.price, "profit": r.profit, "swap": r.swap,
            "commission": r.commission, "fee": r.fee, "sl": r.sl, "tp": r.tp, "magic": r.magic, "comment": r.comment}


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
        rows = [_deal_row(d, to_utc) for d in deals if d.get("ticket")]
        touched = {r["position_id"] for r in rows if r["position_id"] and r["type"] in (0, 1)}
        with session_scope(self.engine) as s:
            for r in rows:
                s.merge(AccountDeal(**r))
            s.flush()
            legs: list[dict[str, Any]] = []
            if touched:
                stored = s.execute(select(AccountDeal).where(AccountDeal.position_id.in_(touched))).scalars().all()
                legs = build_positions([_as_deal(r) for r in stored], _utc_from_seconds, self.canonical_map())
                s.execute(delete(TradeRecord).where(TradeRecord.position_id.in_(touched)))
                s.add_all(TradeRecord(**leg) for leg in legs)
        return {"deals": len(rows), "positions": len(legs), "open": sum(p["status"] == "open" for p in legs),
                "cash_flows": sum(r["type"] not in (0, 1, 13, 14) for r in rows)}

    def sync_window_start(self, now: datetime, lookback_days: int) -> datetime:
        """Read back to the newest stored deal (minus a day) when that is older than the lookback, so a long pause
        never leaves a close unseen."""
        with session_scope(self.engine) as s:
            latest = s.execute(select(func.max(AccountDeal.ts_utc))).scalar()
        start = now - timedelta(days=lookback_days)
        return min(start, latest - timedelta(days=1)) if latest else start

    def sync_deals(self, gateway: Any, lookback_days: int = 30, now: datetime | None = None) -> dict[str, int]:
        now = now or datetime.now(timezone.utc)
        start = self.sync_window_start(now, lookback_days)
        return self.store_deals(gateway.deals(start, now + timedelta(days=1)), gateway.clock.to_utc)

    def compact(self, now: datetime | None = None, raw_days: int = RAW_SNAPSHOT_DAYS) -> int:
        """Keep every snapshot of the last ``raw_days``; older ones are thinned to the last snapshot of each UTC hour.
        Bounds the table at ~24 rows/day of history (a 10 s cadence would otherwise add ~8,600 rows/day)."""
        cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=raw_days)
        with session_scope(self.engine) as s:
            rows = s.execute(select(AccountSnapshot.id, AccountSnapshot.captured_at)
                             .where(AccountSnapshot.captured_at < cutoff).order_by(AccountSnapshot.captured_at)).all()
            keep: dict[datetime, int] = {}
            for id_, at in rows:
                keep[at.replace(minute=0, second=0, microsecond=0)] = id_
            kept = set(keep.values())
            drop = [id_ for id_, _ in rows if id_ not in kept]
            for i in range(0, len(drop), 500):
                s.execute(delete(AccountSnapshot).where(AccountSnapshot.id.in_(drop[i:i + 500])))
        return len(drop)

    def sync(self, gateway: Any, lookback_days: int = 90, now: datetime | None = None) -> dict[str, Any]:
        """One read-only pass: an account snapshot plus the deal history since the last sync (at least ``lookback_days``)."""
        now = now or datetime.now(timezone.utc)
        snap = self.record_snapshot(gateway.account_snapshot(), now)
        counts = self.sync_deals(gateway, lookback_days, now)
        self.compact(now)
        log.info("Account synced", extra={"equity": snap["equity"], **counts})
        return {"equity": snap["equity"], "currency": snap["currency"], **counts}
