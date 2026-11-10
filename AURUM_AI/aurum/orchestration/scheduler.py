"""Schedule arithmetic: cron (5 fields), fixed intervals and one-time runs, in a named time zone.

Times are stored in UTC; cron expressions are read in the schedule's time zone (default Africa/Lagos, UTC+1, no DST).
Cron fields: minute hour day-of-month month day-of-week, with ``*``, lists ``a,b``, ranges ``a-b`` and steps ``*/n`` or
``a-b/n``. Day-of-week 0 or 7 is Sunday. When both day fields are restricted, a day matches if either matches (as cron).
Local times that do not exist in a DST change are skipped; repeated ones fire once (first occurrence).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_TZ = "Africa/Lagos"
_RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))
_NAMES = ("minute", "hour", "day of month", "month", "day of week")


class ScheduleError(ValueError):
    pass


def zone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or DEFAULT_TZ)
    except (ZoneInfoNotFoundError, ValueError):
        raise ScheduleError(f"Unknown time zone '{name}'.") from None


def _field(text: str, idx: int) -> frozenset[int]:
    lo, hi = _RANGES[idx]
    out: set[int] = set()
    for part in text.split(","):
        step = 1
        if "/" in part:
            part, s = part.split("/", 1)
            if not s.isdigit() or int(s) < 1:
                raise ScheduleError(f"Bad step in the {_NAMES[idx]} field.")
            step = int(s)
        if part == "*":
            a, b = lo, hi
        elif "-" in part:
            x, y = part.split("-", 1)
            if not (x.isdigit() and y.isdigit()):
                raise ScheduleError(f"Bad range in the {_NAMES[idx]} field.")
            a, b = int(x), int(y)
        elif part.isdigit():
            a = b = int(part)
        else:
            raise ScheduleError(f"Bad value '{part}' in the {_NAMES[idx]} field.")
        if a < lo or b > hi or a > b:
            raise ScheduleError(f"The {_NAMES[idx]} field must be within {lo}-{hi}.")
        out.update(range(a, b + 1, step))
    if idx == 4 and 7 in out:
        out.discard(7)
        out.add(0)
    return frozenset(out)


@dataclass(frozen=True)
class Cron:
    minutes: frozenset[int]
    hours: frozenset[int]
    days: frozenset[int]
    months: frozenset[int]
    weekdays: frozenset[int]          # 0 = Sunday
    dom_any: bool
    dow_any: bool

    @classmethod
    def parse(cls, expr: str) -> "Cron":
        parts = expr.split()
        if len(parts) != 5:
            raise ScheduleError("A cron expression has 5 fields: minute hour day-of-month month day-of-week.")
        f = [_field(p, i) for i, p in enumerate(parts)]
        return cls(f[0], f[1], f[2], f[3], f[4], parts[2] == "*", parts[4] == "*")

    def _day_ok(self, d: datetime) -> bool:
        if d.month not in self.months:
            return False
        dom = d.day in self.days
        dow = ((d.weekday() + 1) % 7) in self.weekdays
        if self.dom_any and self.dow_any:
            return True
        if self.dom_any:
            return dow
        if self.dow_any:
            return dom
        return dom or dow

    def next_after(self, after: datetime, tz: ZoneInfo) -> datetime:
        """The first matching time strictly after ``after`` (UTC-aware), returned in UTC."""
        local = after.astimezone(tz)
        day = local.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
        for _ in range(366 * 5):
            if self._day_ok(day):
                for h in sorted(self.hours):
                    for m in sorted(self.minutes):
                        naive = day.replace(hour=h, minute=m)
                        cand = naive.replace(tzinfo=tz, fold=0)
                        if cand.astimezone(timezone.utc).astimezone(tz).replace(tzinfo=None) != naive:
                            continue                       # does not exist (DST gap)
                        if cand.astimezone(timezone.utc) > after.astimezone(timezone.utc):
                            return cand.astimezone(timezone.utc)
            day += timedelta(days=1)
        raise ScheduleError("The cron expression never matches.")


def validate(kind: str, expression: str, tz_name: str | None) -> None:
    zone(tz_name)
    if kind == "cron":
        Cron.parse(expression)
    elif kind == "interval":
        try:
            seconds = int(expression)
        except ValueError:
            raise ScheduleError("An interval is a whole number of seconds.") from None
        if seconds < 60:
            raise ScheduleError("The shortest interval is 60 seconds.")
    elif kind == "once":
        parse_once(expression, tz_name)
    else:
        raise ScheduleError("kind must be cron, interval or once.")


def parse_once(expression: str, tz_name: str | None) -> datetime:
    try:
        t = datetime.fromisoformat(expression.replace("Z", "+00:00"))
    except ValueError:
        raise ScheduleError("A one-time schedule needs an ISO-8601 date and time.") from None
    if t.tzinfo is None:
        t = t.replace(tzinfo=zone(tz_name))
    return t.astimezone(timezone.utc)


def next_run(kind: str, expression: str, tz_name: str | None, after: datetime, *, anchor: datetime | None = None) -> datetime | None:
    """Next run strictly after ``after``. Intervals are aligned to ``anchor`` (creation time) so restarts do not drift."""
    if kind == "cron":
        return Cron.parse(expression).next_after(after, zone(tz_name))
    if kind == "interval":
        step = timedelta(seconds=int(expression))
        base = anchor or after
        if after < base:
            return base
        n = int((after - base) / step) + 1
        return base + n * step
    if kind == "once":
        t = parse_once(expression, tz_name)
        return t if t > after else None
    raise ScheduleError("kind must be cron, interval or once.")
