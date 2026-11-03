"""Session engine: IANA time zones, DST, midnight crossing, weekends, holidays, overlaps, kill zones."""
from datetime import date, datetime, timedelta, timezone

import pytest

from aurum.config import TradingHours
from aurum.market_intelligence.config import ConfigError, build_config
from aurum.market_intelligence.sessions import (current_occurrence, next_occurrence, overlap_intervals,
                                                previous_occurrence, session_history, sessions_snapshot,
                                                window_status)
from mi_helpers import cfg

UTC = timezone.utc
C = cfg()
FX = TradingHours.from_dict({"weekly_close": "FRI 21:00", "weekly_open": "SUN 21:00"})


def occ(name: str, day: date):
    s, e = C.windows()[name].occurrence(day)
    return s.astimezone(UTC).strftime("%H:%M"), e.astimezone(UTC).strftime("%H:%M")


def test_london_follows_bst_and_gmt():
    assert occ("london", date(2026, 1, 14)) == ("08:00", "17:00")        # GMT
    assert occ("london", date(2026, 7, 14)) == ("07:00", "16:00")        # BST (UTC+1)
    # UK DST starts 2026-03-29: the Friday before is GMT, the Monday after is BST
    assert occ("london", date(2026, 3, 27)) == ("08:00", "17:00")
    assert occ("london", date(2026, 3, 30)) == ("07:00", "16:00")


def test_new_york_follows_us_dst_independently_of_london():
    # US DST starts 2026-03-08 (three weeks before the UK): New York moves, London does not
    assert occ("new_york", date(2026, 3, 6)) == ("13:00", "22:00")
    assert occ("new_york", date(2026, 3, 9)) == ("12:00", "21:00")
    assert occ("london", date(2026, 3, 9)) == ("08:00", "17:00")
    # US DST ends 2026-11-01
    assert occ("new_york", date(2026, 10, 30)) == ("12:00", "21:00")
    assert occ("new_york", date(2026, 11, 2)) == ("13:00", "22:00")


def test_sydney_southern_hemisphere_dst():
    assert occ("sydney", date(2026, 1, 14)) == ("20:00", "05:00")        # AEDT (UTC+11): 07:00 local = 20:00 UTC prev day
    assert occ("sydney", date(2026, 6, 15)) == ("21:00", "06:00")        # AEST (UTC+10)


def test_midnight_crossing_kill_zone_spans_two_utc_dates():
    s, e = C.windows()["asian_kz"].occurrence(date(2026, 2, 2))         # Monday 20:00-00:00 New York (EST)
    assert s == datetime(2026, 2, 3, 1, 0, tzinfo=UTC) and e == datetime(2026, 2, 3, 5, 0, tzinfo=UTC)
    assert e - s == timedelta(hours=4)


def test_weekend_and_holidays_have_no_occurrence():
    w = C.windows()["london"]
    assert w.occurrence(date(2026, 2, 7)) is None                       # Saturday
    assert w.occurrence(date(2026, 12, 25)) is None                     # configured holiday
    assert C.windows()["new_york"].occurrence(date(2026, 11, 26)) is None  # Thanksgiving (configured)


def test_status_open_pre_closed_and_market_closed():
    w = C.windows()["london"]
    assert window_status(w, datetime(2026, 2, 4, 10, tzinfo=UTC))["status"] == "open"
    assert window_status(w, datetime(2026, 2, 4, 7, 30, tzinfo=UTC))["status"] == "pre_session"
    assert window_status(w, datetime(2026, 2, 4, 18, tzinfo=UTC))["status"] == "closed"
    assert window_status(w, datetime(2026, 2, 7, 10, tzinfo=UTC), FX)["status"] == "closed"   # Saturday: no session
    # Sydney's Monday session opens Sunday 20:00 UTC (AEDT) - before the FX market reopens at 21:00 UTC
    syd = window_status(C.windows()["sydney"], datetime(2026, 1, 11, 20, 30, tzinfo=UTC), FX)
    assert syd["status"] == "market_closed" and syd["active"] is False and syd["market_open"] is False
    assert window_status(C.windows()["sydney"], datetime(2026, 1, 11, 21, 30, tzinfo=UTC), FX)["status"] == "open"


def test_previous_next_and_current_occurrence():
    w, t = C.windows()["london"], datetime(2026, 2, 4, 10, tzinfo=UTC)
    assert current_occurrence(w, t).start == datetime(2026, 2, 4, 8, tzinfo=UTC)
    assert previous_occurrence(w, t, completed=True).start == datetime(2026, 2, 3, 8, tzinfo=UTC)
    assert next_occurrence(w, t).start == datetime(2026, 2, 5, 8, tzinfo=UTC)
    # Friday evening -> next London is Monday
    assert next_occurrence(w, datetime(2026, 2, 6, 18, tzinfo=UTC)).start == datetime(2026, 2, 9, 8, tzinfo=UTC)


def test_london_new_york_overlap_changes_with_dst_gap():
    o = next(x for x in C.overlaps if x.name == "london_new_york")
    jan = overlap_intervals(C, o, datetime(2026, 1, 14, tzinfo=UTC), datetime(2026, 1, 15, tzinfo=UTC))
    assert [(i.start.hour, i.end.hour) for i in jan] == [(13, 17)]
    gap = overlap_intervals(C, o, datetime(2026, 3, 16, tzinfo=UTC), datetime(2026, 3, 17, tzinfo=UTC))
    assert [(i.start.hour, i.end.hour) for i in gap] == [(12, 17)]        # US on EDT, UK still on GMT


def test_asian_london_transition_window_when_sessions_do_not_overlap():
    o = next(x for x in C.overlaps if x.name == "asian_london")
    iv = overlap_intervals(C, o, datetime(2026, 1, 14, tzinfo=UTC), datetime(2026, 1, 15, tzinfo=UTC))
    assert len(iv) == 1 and iv[0].kind == "transition"
    assert (iv[0].start.strftime("%H:%M"), iv[0].end.strftime("%H:%M")) == ("06:30", "08:30")


def test_instrument_specific_kill_zones():
    assert [k.name for k in C.kill_zones_for("US30")] == ["new_york_kz", "london_close_kz"]
    assert {k.name for k in C.kill_zones_for("EURUSD")} == {"asian_kz", "london_kz", "new_york_kz", "london_close_kz"}


def test_snapshot_active_sessions_overlap_and_kill_zone():
    snap = sessions_snapshot(C, datetime(2026, 1, 14, 14, tzinfo=UTC), "EURUSD", FX)
    assert set(snap["active_sessions"]) == {"london", "new_york"}
    assert snap["in_overlap"] is True and "new_york_kz" in snap["active_kill_zones"]
    assert snap["upcoming_session"]["name"] in {"sydney", "asian", "tokyo"}


def test_session_history_is_sorted_and_bounded():
    h = session_history(C, datetime(2026, 1, 12, tzinfo=UTC), datetime(2026, 1, 17, tzinfo=UTC), ["london"])
    assert [x.start.day for x in h] == [12, 13, 14, 15, 16] and all(x.name == "london" for x in h)


def test_invalid_timezone_is_rejected():
    with pytest.raises(ConfigError):
        build_config({"sessions": {"x": {"timezone": "Mars/Olympus", "open": "08:00", "close": "17:00"}}})
