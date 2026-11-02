"""Publication timing - Tuesday positions are only usable from the CFTC release (Friday 15:30 ET)."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from aurum.cot.release import is_available, release_info, rule_release_date, us_federal_holidays
from cot_helpers import cot_cfg

REL = cot_cfg().release


def test_normal_week_is_friday_1530_eastern_in_utc():
    r = release_info(date(2026, 10, 27), REL)                     # EDT (UTC-4)
    assert r.release_date == date(2026, 10, 30) and r.method == "rule" and not r.uncertain
    assert r.release_timestamp == datetime(2026, 10, 30, 19, 30, tzinfo=timezone.utc)
    w = release_info(date(2026, 11, 3), REL)                      # EST (UTC-5) after the DST change
    assert w.release_timestamp == datetime(2026, 11, 6, 20, 30, tzinfo=timezone.utc)


@pytest.mark.parametrize("report,release", [
    ("2025-12-30", "2026-01-05"), ("2026-06-16", "2026-06-22"), ("2026-06-30", "2026-07-06"),
    ("2026-11-10", "2026-11-16"), ("2026-11-24", "2026-11-30"), ("2026-12-22", "2026-12-28"),
])
def test_holiday_rule_matches_official_2026_schedule_without_overrides(report, release):
    assert rule_release_date(date.fromisoformat(report))[0] == date.fromisoformat(release)


def test_whole_2026_schedule_matches_rule():
    special = {date(2026, 6, 16), date(2026, 6, 30), date(2026, 11, 10), date(2026, 11, 24), date(2026, 12, 22)}
    d = date(2026, 1, 6)
    while d <= date(2026, 12, 22):
        rd, method = rule_release_date(d)
        assert (rd != d + timedelta(days=3)) == (d in special), d
        d += timedelta(weeks=1)


def test_monday_holiday_does_not_delay():
    assert rule_release_date(date(2026, 1, 20))[0] == date(2026, 1, 23)    # MLK day Monday 19th
    assert rule_release_date(date(2026, 9, 8))[0] == date(2026, 9, 11)     # Labor Day Monday 7th


def test_official_override_for_shutdown_catch_up():
    r = release_info(date(2025, 10, 7), REL)
    assert r.release_date == date(2025, 11, 21) and r.method == "override" and not r.uncertain


def test_uncertain_range_is_flagged():
    r = release_info(date(2019, 1, 8), REL)
    assert r.uncertain and r.method.startswith("rule")


def test_federal_holidays_observed_rules():
    h = us_federal_holidays(2026)
    assert date(2026, 7, 3) in h            # July 4 is a Saturday -> observed Friday
    assert date(2026, 6, 19) in h and date(2026, 11, 26) in h and date(2026, 12, 25) in h
    assert date(2020, 6, 19) not in us_federal_holidays(2020)              # Juneteenth only from 2021


def test_tuesday_data_not_available_before_release():
    r = release_info(date(2026, 10, 27), REL)
    assert not is_available(r.release_timestamp, datetime(2026, 10, 27, 23, 0, tzinfo=timezone.utc))
    assert not is_available(r.release_timestamp, r.release_timestamp - timedelta(seconds=1))
    assert is_available(r.release_timestamp, r.release_timestamp)
    with pytest.raises(ValueError):
        is_available(r.release_timestamp, datetime(2026, 11, 1))
