"""Turns a trade into the categorical features patterns are built from (all DST-aware)."""
from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

from .models import Trade

_SESSIONS = (
    ("asia", ZoneInfo("Asia/Tokyo"), time(9), time(18)),
    ("london", ZoneInfo("Europe/London"), time(8), time(17)),
    ("newyork", ZoneInfo("America/New_York"), time(8), time(17)),
)
_WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def session_of(ts_utc: datetime) -> str:
    """Trading session at an instant, using each market's local clock (so DST is automatic)."""
    open_ = [name for name, tz, start, end in _SESSIONS if start <= ts_utc.astimezone(tz).time() < end]
    if "london" in open_ and "newyork" in open_:
        return "london-newyork overlap"
    # When Tokyo's afternoon overlaps London's morning, the session that opened last is the active one.
    for name in ("newyork", "london", "asia"):
        if name in open_:
            return name
    return "off-hours"


def holding_bucket(minutes: float) -> str:
    if minutes < 60:
        return "under 1h"
    if minutes < 240:
        return "1-4h"
    if minutes < 1440:
        return "4-24h"
    return "over 1 day"


def features_of(t: Trade) -> dict[str, str]:
    held = (t.close_time - t.open_time).total_seconds() / 60
    return {
        "symbol": t.symbol,
        "side": t.side,
        "session": session_of(t.open_time),
        "weekday": _WEEKDAYS[t.open_time.weekday()],
        "strategy": t.strategy or "untagged",
        "holding": holding_bucket(held),
    }


def matches(t: Trade, pattern: tuple[tuple[str, str], ...]) -> bool:
    f = features_of(t)
    return all(f.get(k) == v for k, v in pattern)
