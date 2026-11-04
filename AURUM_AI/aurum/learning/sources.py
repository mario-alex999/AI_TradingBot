"""Where closed trades come from: the MT5 account's deal history, or a CSV export."""
from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .models import Trade


def deals_to_trades(deals: list[dict[str, Any]], to_utc: Callable[[float], datetime]) -> list[Trade]:
    """Closed trades rebuilt from raw MT5 deals (see :func:`aurum.account.deals.build_positions`).

    Each closed position leg is one trade; net P/L adds the profit, swap, commission and fee of its deals.
    Open legs and balance/credit deals are ignored.
    """
    from aurum.account.deals import build_positions
    return sorted((Trade(ticket=p["id"], symbol=p["broker_symbol"], side=p["side"], volume=p["volume"],
                         open_time=p["open_time"], close_time=p["close_time"], open_price=p["open_price"],
                         close_price=p["close_price"], pnl=p["pnl"], strategy=p["strategy"])
                   for p in build_positions(deals, to_utc) if p["status"] == "closed"), key=lambda t: t.close_time)


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
