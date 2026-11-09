"""Trading-day, week and month boundaries in an explicitly configured time zone (never the computer's local time).

Policy (FX convention by default): trading day D starts at ``rollover`` (17:00 New York) on the calendar day before D.
A timestamp's trading date is therefore ``(local_time - rollover + 1 day).date()``. A trading week starts at the start of
its Monday trading day (Sunday 17:00 New York). A trading month starts at the start of the first trading date of the
calendar month. ``zoneinfo`` handles daylight-saving changes, so 17:00 New York is 21:00 UTC in summer, 22:00 in winter.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from aurum.risk.config import TradingDay


@dataclass(frozen=True)
class Period:
    kind: str          # day | week | month
    label: str         # 2026-11-06 | 2026-W45 | 2026-11
    start: datetime    # UTC
    end: datetime      # UTC, exclusive


class TradingCalendar:
    def __init__(self, cfg: TradingDay):
        self.tz = ZoneInfo(cfg.timezone)
        h, m = cfg.rollover.split(":")
        self.rollover = time(int(h), int(m))

    def _offset(self) -> timedelta:
        return timedelta(hours=self.rollover.hour, minutes=self.rollover.minute)

    def trading_date(self, t: datetime) -> date:
        local = _utc(t).astimezone(self.tz)
        if self.rollover == time(0, 0):
            return local.date()
        return (local - self._offset() + timedelta(days=1)).date()

    def day_start(self, d: date) -> datetime:
        if self.rollover == time(0, 0):
            return datetime.combine(d, time(0, 0), tzinfo=self.tz).astimezone(timezone.utc)
        return datetime.combine(d - timedelta(days=1), self.rollover, tzinfo=self.tz).astimezone(timezone.utc)

    def day(self, t: datetime) -> Period:
        d = self.trading_date(t)
        return Period("day", d.isoformat(), self.day_start(d), self.day_start(d + timedelta(days=1)))

    def week(self, t: datetime) -> Period:
        d = self.trading_date(t)
        monday = d - timedelta(days=d.weekday())
        iso = monday.isocalendar()
        return Period("week", f"{iso[0]}-W{iso[1]:02d}", self.day_start(monday), self.day_start(monday + timedelta(days=7)))

    def month(self, t: datetime) -> Period:
        d = self.trading_date(t)
        first = d.replace(day=1)
        nxt = (first + timedelta(days=32)).replace(day=1)
        return Period("month", f"{first:%Y-%m}", self.day_start(first), self.day_start(nxt))

    def period(self, kind: str, t: datetime) -> Period:
        return {"day": self.day, "week": self.week, "month": self.month}[kind](t)

    def weekday(self, t: datetime) -> int:
        """Weekday of the trading date (0=Monday)."""
        return self.trading_date(t).weekday()


def _utc(t: datetime) -> datetime:
    if t.tzinfo is None:
        raise ValueError("Naive datetime: Module 06 only accepts timezone-aware times.")
    return t.astimezone(timezone.utc)
