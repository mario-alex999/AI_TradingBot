"""COT publication timing - the look-ahead-bias guard.

A COT report describes positions *as of* its report date (normally a Tuesday) but only becomes public when
the CFTC releases it (normally Friday 15:30 US/Eastern). Every analytical query must use the release
timestamp, never the report date, to decide what was knowable at a given moment.

Resolution order for a report date:
1. ``release.overrides`` in config/cot.yaml (official CFTC schedules, e.g. holiday weeks, the 2025 catch-up);
2. rule: the Friday of the report week at 15:30 ET; if a US federal holiday falls Tuesday-Friday of that week
   the release moves to the next business day after that Friday (matches every 2026 holiday case);
3. dates inside ``uncertain_ranges`` (known lapses without an official catch-up table) get the rule date and are
   flagged ``release_uncertain`` so analyses can show the limitation.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo

from aurum.cot.config import ReleaseConfig


@dataclass(frozen=True)
class ReleaseInfo:
    report_date: date
    release_date: date
    release_timestamp: datetime          # UTC-aware
    method: str                          # override | rule | rule_holiday
    uncertain: bool


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    d = date(year, month, 1)
    d += timedelta(days=(weekday - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def _last_weekday(year: int, month: int, weekday: int) -> date:
    d = date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _observed(d: date) -> date:
    return d - timedelta(days=1) if d.weekday() == 5 else d + timedelta(days=1) if d.weekday() == 6 else d


@lru_cache(maxsize=64)
def us_federal_holidays(year: int) -> frozenset[date]:
    """Observed US federal holidays (OPM rules). Juneteenth from 2021."""
    h = {
        _observed(date(year, 1, 1)), _nth_weekday(year, 1, 0, 3), _nth_weekday(year, 2, 0, 3),
        _last_weekday(year, 5, 0), _observed(date(year, 7, 4)), _nth_weekday(year, 9, 0, 1),
        _nth_weekday(year, 10, 0, 2), _observed(date(year, 11, 11)), _nth_weekday(year, 11, 3, 4),
        _observed(date(year, 12, 25)),
    }
    if year >= 2021:
        h.add(_observed(date(year, 6, 19)))
    # New Year's Day of the following year can be observed on Dec 31 of this year
    nxt = _observed(date(year + 1, 1, 1))
    if nxt.year == year:
        h.add(nxt)
    return frozenset(h)


def is_federal_holiday(d: date) -> bool:
    return d in us_federal_holidays(d.year)


def _next_business_day(d: date) -> date:
    d += timedelta(days=1)
    while d.weekday() >= 5 or is_federal_holiday(d):
        d += timedelta(days=1)
    return d


def rule_release_date(report_date: date) -> tuple[date, str]:
    """Friday of the report's week; delayed to the next business day if a holiday hits Tue-Fri."""
    friday = report_date + timedelta(days=(4 - report_date.weekday()) % 7)
    tuesday = friday - timedelta(days=3)
    if any(is_federal_holiday(tuesday + timedelta(days=i)) for i in range(4)):
        return _next_business_day(friday), "rule_holiday"
    return friday, "rule"


def release_info(report_date: date, cfg: ReleaseConfig) -> ReleaseInfo:
    if report_date in cfg.overrides:
        rd, method = cfg.overrides[report_date], "override"
    else:
        rd, method = rule_release_date(report_date)
    uncertain = method != "override" and any(a <= report_date <= b for a, b in cfg.uncertain_ranges)
    ts = datetime.combine(rd, cfg.time, tzinfo=ZoneInfo(cfg.timezone)).astimezone(timezone.utc)
    return ReleaseInfo(report_date, rd, ts, method, uncertain)


def is_available(release_timestamp: datetime, as_of: datetime) -> bool:
    """True when a report released at ``release_timestamp`` was public at ``as_of`` (both aware)."""
    if as_of.tzinfo is None or release_timestamp.tzinfo is None:
        raise ValueError("is_available needs timezone-aware datetimes")
    return release_timestamp <= as_of
