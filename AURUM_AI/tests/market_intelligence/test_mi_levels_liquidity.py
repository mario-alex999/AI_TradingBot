"""Session levels and session-liquidity classification, including no-look-ahead checks."""
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from aurum.market_intelligence.candles import FrameCandleSource
from aurum.market_intelligence.levels import all_levels, previous_trading_day, trading_day_period
from aurum.market_intelligence.liquidity import classify_interaction, pair_level_period, session_liquidity
from aurum.market_intelligence.module_adapters import NullModule03
from aurum.market_intelligence.sessions import current_occurrence
from mi_helpers import cfg, resample, source, walk

UTC = timezone.utc
C = cfg()
AS_OF = datetime(2026, 3, 11, 11, 7, tzinfo=UTC)            # Wednesday, London open (GMT), mid-candle


def _by_type(levels):
    return {lv["level_type"]: lv for lv in levels}


def test_previous_session_levels_match_raw_candles():
    src = source()
    lv = _by_type(all_levels(C, src, "EURUSD", AS_OF)["levels"])
    m5 = src.frame("EURUSD", "M5", AS_OF)
    prev = m5[(m5.ts >= "2026-03-10T08:00Z") & (m5.ts < "2026-03-10T17:00Z")]
    assert lv["previous_london_high"]["price"] == pytest.approx(prev.high.max())
    assert lv["previous_london_low"]["price"] == pytest.approx(prev.low.min())
    assert lv["previous_london_high"]["timeframe"] == "M5" and lv["previous_london_high"]["provisional"] is False


def test_current_session_levels_are_provisional_and_use_only_closed_candles():
    src = source()
    lv = _by_type(all_levels(C, src, "EURUSD", AS_OF)["levels"])
    cur = lv["current_london_high"]
    assert cur["provisional"] is True and cur["state"] == "provisional"
    m5 = src.frame("EURUSD", "M5", AS_OF)
    closed = m5[(m5.ts >= "2026-03-11T08:00Z") & (m5.close_t <= AS_OF)]
    assert cur["price"] == pytest.approx(closed.high.max())


def test_levels_have_no_look_ahead():
    """Levels at as_of are identical whether or not later candles exist (and an open candle's spike is ignored)."""
    full = walk(datetime(2026, 2, 2, tzinfo=UTC), datetime(2026, 3, 14, tzinfo=UTC), 5, step=0.00015, tf="M5")
    spike_at = pd.Timestamp("2026-03-11T11:05Z")              # candle 11:05-11:10 is still open at 11:07
    full.loc[full.ts_utc >= spike_at, ["high"]] += 0.05
    full.loc[full.ts_utc >= spike_at, ["low"]] -= 0.05
    trunc = full[full.ts_utc < pd.Timestamp("2026-03-11T11:05Z")]
    a = all_levels(C, FrameCandleSource({("EURUSD", "M5"): full, ("EURUSD", "D1"): resample(full, "1D", "D1")}), "EURUSD", AS_OF)
    b = all_levels(C, FrameCandleSource({("EURUSD", "M5"): trunc, ("EURUSD", "D1"): resample(trunc, "1D", "D1")}), "EURUSD", AS_OF)
    pa = {x["level_type"]: x["price"] for x in a["levels"] if x["timeframe"] != "D1"}
    pb = {x["level_type"]: x["price"] for x in b["levels"] if x["timeframe"] != "D1"}
    assert pa == pb
    assert max(pa.values()) < 1.2                              # the spike (+0.05) never leaked in


def test_trading_day_rolls_at_17_new_york():
    p = trading_day_period(C, datetime(2026, 3, 11, 12, tzinfo=UTC))
    assert p.start == datetime(2026, 3, 10, 21, tzinfo=UTC) and p.end == datetime(2026, 3, 11, 21, tzinfo=UTC)  # EDT
    winter = trading_day_period(C, datetime(2026, 2, 11, 12, tzinfo=UTC))
    assert winter.start.hour == 22                                                                                # EST
    monday = previous_trading_day(C, datetime(2026, 3, 9, 12, tzinfo=UTC))     # Monday -> previous is Friday's day
    assert monday.end == datetime(2026, 3, 6, 22, tzinfo=UTC)


def test_previous_month_falls_back_to_d1():
    src = source(start=datetime(2026, 2, 20, tzinfo=UTC))
    # remove intraday coverage of February so only D1 can supply it
    m5 = src.frame("EURUSD", "M5", AS_OF)
    d1_only = walk(datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 3, 12, tzinfo=UTC), 60 * 24, tf="D1", skip_weekends=False)
    s = FrameCandleSource({("EURUSD", "M5"): m5.rename(columns={"ts": "ts_utc"}).assign(is_complete=True), ("EURUSD", "D1"): d1_only})
    lv = _by_type(all_levels(C, s, "EURUSD", AS_OF)["levels"])
    assert lv["previous_month_high"]["timeframe"] == "D1"
    assert "UTC calendar month from D1" in lv["previous_month_high"]["method"]   # months are UTC: D1 is exact, no note


def _bars(rows, start="2026-03-11T08:00Z", minutes=15):
    t0 = pd.Timestamp(start)
    return pd.DataFrame([{"ts": t0 + pd.Timedelta(minutes=minutes * i), "close_t": t0 + pd.Timedelta(minutes=minutes * (i + 1)),
                          "open": o, "high": h, "low": low, "close": c} for i, (o, h, low, c) in enumerate(rows)])


LEVEL = 1.1000


def test_wick_sweep():
    ev = classify_interaction(_bars([(1.0990, 1.1005, 1.0985, 1.0995), (1.0995, 1.0998, 1.0990, 1.0992)]), LEVEL, "high", 2, 3)
    assert [e["event_type"] for e in ev] == ["wick_sweep"]
    assert ev[0]["closed_beyond"] is False and ev[0]["extreme"] == pytest.approx(1.1005)


def test_confirmed_breakout_needs_follow_through():
    rows = [(1.0995, 1.1010, 1.0990, 1.1008)] + [(1.1008, 1.1015, 1.1004, 1.1010)] * 3
    ev = classify_interaction(_bars(rows), LEVEL, "high", 2, 3)
    assert ev[-1]["event_type"] == "confirmed_breakout" and ev[-1]["returned_inside"] is False


def test_false_breakout_candidate_when_close_returns_inside():
    rows = [(1.0995, 1.1010, 1.0990, 1.1008), (1.1008, 1.1009, 1.0990, 1.0994), (1.0994, 1.0996, 1.0990, 1.0992)]
    ev = classify_interaction(_bars(rows), LEVEL, "high", 2, 3)
    assert ev[-1]["event_type"] == "false_breakout_candidate" and ev[-1]["returned_inside"] is True


def test_unconfirmed_when_not_enough_bars_have_closed():
    rows = [(1.0995, 1.1010, 1.0990, 1.1008), (1.1008, 1.1012, 1.1004, 1.1010)]
    ev = classify_interaction(_bars(rows), LEVEL, "high", 2, 3)
    assert ev[-1]["event_type"] == "unconfirmed" and ev[-1]["confirmation_time"] is None


def test_low_side_breakout_is_symmetric():
    rows = [(1.1005, 1.1008, 1.0990, 1.0992)] + [(1.0992, 1.0996, 1.0985, 1.0990)] * 3
    ev = classify_interaction(_bars(rows), LEVEL, "low", 2, 3)
    assert ev[-1]["event_type"] == "confirmed_breakout"


def test_new_york_vs_london_pair_cuts_london_at_new_york_open():
    ny = current_occurrence(C.windows()["new_york"], datetime(2026, 1, 14, 15, tzinfo=UTC))
    p = pair_level_period(C.windows()["london"], ny)
    assert p.start == datetime(2026, 1, 14, 8, tzinfo=UTC)
    assert p.end == datetime(2026, 1, 14, 13, tzinfo=UTC)       # same-day London, up to the NY open (not yesterday's)


def test_session_liquidity_events_never_after_as_of():
    src = source()
    lv = all_levels(C, src, "EURUSD", AS_OF)["levels"]
    r = session_liquidity(C, src, "EURUSD", AS_OF, lv, NullModule03().context("EURUSD", "M15", AS_OF))
    assert r["module03"]["available"] is False
    for e in r["events"]:
        assert e["event_time"] <= AS_OF
        assert e["confirmation_time"] is None or e["confirmation_time"] <= AS_OF
        assert e["direction"] is None or isinstance(e["direction"], str)
    assert "not" in r["interpretation"].lower()                 # explicitly not a signal
