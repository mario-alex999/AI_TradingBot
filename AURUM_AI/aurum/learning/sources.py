"""Where closed trades come from: the MT5 account's deal history, or a CSV export."""
from __future__ import annotations

import csv
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .models import Trade

# MT5 deal constants (MetaTrader5 package): DEAL_TYPE_BUY=0, DEAL_TYPE_SELL=1,
# DEAL_ENTRY_IN=0, DEAL_ENTRY_OUT=1, DEAL_ENTRY_INOUT=2, DEAL_ENTRY_OUT_BY=3.
_BUY, _SELL = 0, 1
_IN, _OUT, _INOUT, _OUT_BY = 0, 1, 2, 3


def deals_to_trades(deals: list[dict[str, Any]], to_utc: Callable[[float], datetime]) -> list[Trade]:
    """Rebuild closed trades from raw MT5 deals, grouped by ``position_id``.

    A position counts as closed when its exit volume covers its entry volume. Net P/L adds the
    profit, swap, commission and fee of every deal in the position. Balance/credit deals are ignored.
    """
    by_pos: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for d in deals:
        if d.get("type") not in (_BUY, _SELL) or not d.get("position_id"):
            continue
        by_pos[int(d["position_id"])].append(d)

    trades: list[Trade] = []
    for pos, ds in by_pos.items():
        ds.sort(key=lambda d: (d.get("time_msc") or d.get("time", 0) * 1000))
        entries = [d for d in ds if d.get("entry") == _IN]
        exits = [d for d in ds if d.get("entry") in (_OUT, _OUT_BY, _INOUT)]
        if not entries or not exits:
            continue  # still open, or history window cut the position
        vol_in = sum(float(d.get("volume", 0)) for d in entries)
        vol_out = sum(float(d.get("volume", 0)) for d in exits)
        if vol_out + 1e-9 < vol_in:
            continue  # partially closed: not finished yet
        first, last = entries[0], exits[-1]
        net = sum(float(d.get(k, 0) or 0) for d in ds for k in ("profit", "swap", "commission", "fee"))

        def ts(d: dict[str, Any]) -> datetime:
            sec = (d.get("time_msc") or 0) / 1000.0 or float(d.get("time", 0))
            return to_utc(sec)

        avg_in = sum(float(d["price"]) * float(d["volume"]) for d in entries) / vol_in if vol_in else float(first["price"])
        avg_out = sum(float(d["price"]) * float(d["volume"]) for d in exits) / vol_out if vol_out else float(last["price"])
        comment = (first.get("comment") or "").strip()
        magic = first.get("magic") or 0
        trades.append(Trade(
            ticket=str(pos),
            symbol=str(first.get("symbol", "")),
            side="buy" if first.get("type") == _BUY else "sell",
            volume=round(vol_in, 4),
            open_time=ts(first),
            close_time=ts(last),
            open_price=avg_in,
            close_price=avg_out,
            pnl=round(net, 2),
            strategy=comment or (f"magic {magic}" if magic else None),
        ))
    return sorted(trades, key=lambda t: t.close_time)


REQUIRED = ("ticket", "symbol", "side", "open_time", "close_time", "pnl")


def _parse_time(v: str) -> datetime:
    """ISO 8601 (``2026-03-02T09:15:00Z`` or ``2026-03-02 09:15``); naive times are taken as UTC."""
    text = v.strip().replace("Z", "+00:00")
    if "T" not in text and " " in text:
        text = text.replace(" ", "T", 1)
    dt = datetime.fromisoformat(text)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def load_csv(path: str | Path) -> list[Trade]:
    """Load closed trades from a CSV with columns: ticket, symbol, side (buy/sell), open_time, close_time,
    pnl, and optionally volume, open_price, close_price, strategy. Times are ISO 8601; naive times are UTC."""
    trades: list[Trade] = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        missing = [c for c in REQUIRED if c not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"{path}: missing column(s) {', '.join(missing)}. Required: {', '.join(REQUIRED)}.")
        for i, row in enumerate(reader, start=2):
            try:
                side = row["side"].strip().lower()
                if side not in ("buy", "sell"):
                    raise ValueError(f"side must be buy or sell, got {row['side']!r}")
                trades.append(Trade(
                    ticket=row["ticket"].strip(),
                    symbol=row["symbol"].strip().upper(),
                    side=side,
                    volume=float(row.get("volume") or 0),
                    open_time=_parse_time(row["open_time"]),
                    close_time=_parse_time(row["close_time"]),
                    open_price=float(row.get("open_price") or 0),
                    close_price=float(row.get("close_price") or 0),
                    pnl=float(row["pnl"]),
                    strategy=(row.get("strategy") or "").strip() or None,
                ))
            except (ValueError, KeyError) as exc:
                raise ValueError(f"{path}, line {i}: {exc}") from exc
    return trades
