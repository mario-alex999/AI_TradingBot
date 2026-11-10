from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from aurum.config import TradingHours
from aurum.services.validation_service import ValidationService, is_market_open
from module01_helpers import TUESDAY, candle_frame

V = ValidationService()


def _types(res):
    return set(res.counts)


def test_clean_data_has_no_issues(fx_hours):
    res = V.validate(candle_frame(TUESDAY, 48), "H1", fx_hours)
    assert not [i for i in res.issues if i.severity != "info"]
    assert (res.candles["quality_status"] == "valid").all()


def test_invalid_ohlc_detected_and_kept(fx_hours):
    df = candle_frame(TUESDAY, 20)
    df.loc[5, "high"] = df.loc[5, "low"] - 0.001          # high below everything
    df.loc[7, "low"] = df.loc[7, "high"] + 0.001          # low above everything
    res = V.validate(df, "H1", fx_hours)
    bad = [i for i in res.issues if i.issue_type == "invalid_ohlc"]
    assert len(bad) == 2
    assert len(res.candles) == 20                          # not deleted
    assert res.candles.loc[5, "quality_status"] == "invalid"
    assert "invalid_ohlc" in res.candles.loc[7, "quality_flags"]
    assert bad[0].details["high"] == df.loc[5, "high"]    # evidence retained


def test_non_positive_and_missing_values(fx_hours):
    df = candle_frame(TUESDAY, 20)
    df.loc[3, "open"] = 0.0
    df.loc[4, "close"] = -1.0
    df.loc[6, "high"] = np.nan
    res = V.validate(df, "H1", fx_hours)
    assert res.counts["non_positive_price"] == 2
    assert res.counts["missing_values"] == 1
    assert (res.candles.loc[[3, 4, 6], "quality_status"] == "invalid").all()


def test_missing_timestamp(fx_hours):
    df = candle_frame(TUESDAY, 10)
    df.loc[2, "ts_utc"] = pd.NaT
    res = V.validate(df, "H1", fx_hours)
    assert res.counts["missing_timestamp"] == 1


def test_duplicates_identical_and_conflicting(fx_hours):
    df = candle_frame(TUESDAY, 10)
    dup_same = df.iloc[[3]]
    dup_conflict = df.iloc[[5]].copy()
    dup_conflict["close"] += 0.01
    res = V.validate(pd.concat([df, dup_same, dup_conflict], ignore_index=True), "H1", fx_hours)
    assert res.counts["duplicate_candle"] == 1
    assert res.counts["conflicting_duplicate"] == 1
    assert len(res.candles) == 10 and len(res.duplicates) == 4
    assert res.candles["ts_utc"].is_unique
    kept = res.candles[res.candles["ts_utc"] == df.loc[5, "ts_utc"]].iloc[0]
    assert kept["close"] == df.loc[5, "close"] and "duplicate" in kept["quality_flags"]


def test_unexpected_missing_candles_detected(fx_hours):
    df = candle_frame(TUESDAY, 24).drop(index=[10, 11, 12]).reset_index(drop=True)
    res = V.validate(df, "H1", fx_hours)
    miss = [i for i in res.issues if i.issue_type == "missing_candles"]
    assert len(miss) == 1 and miss[0].details["unexpected_candles"] == 3
    assert miss[0].ts_utc == TUESDAY + timedelta(hours=10)


def test_weekend_gap_is_market_closure_not_error(fx_hours):
    fri = datetime(2026, 3, 6, 15, 0, tzinfo=timezone.utc)
    sun = datetime(2026, 3, 8, 21, 0, tzinfo=timezone.utc)
    df = pd.concat([candle_frame(fri, 6), candle_frame(sun, 6)], ignore_index=True)   # Fri 15:00-20:00, Sun 21:00+
    res = V.validate(df, "H1", fx_hours)
    assert "missing_candles" not in res.counts
    assert res.counts["market_closure_gap"] == 1


def test_daily_break_gap_is_closure():
    hours = TradingHours.from_dict({"daily_breaks": [["21:00", "22:00"]]})
    df = candle_frame(TUESDAY, 30).drop(index=[21]).reset_index(drop=True)   # 21:00 bar missing
    res = V.validate(df, "H1", hours)
    assert "missing_candles" not in res.counts and res.counts["market_closure_gap"] == 1


def test_d1_dst_spacing_is_not_a_gap(fx_hours):
    ts = [TUESDAY, TUESDAY + timedelta(hours=25), TUESDAY + timedelta(hours=48)]
    df = candle_frame(TUESDAY, 3, tf="D1")
    df["ts_utc"] = pd.to_datetime(ts, utc=True)
    assert "missing_candles" not in V.validate(df, "D1", fx_hours).counts


def test_abnormal_gap_flagged_as_suspect(fx_hours):
    df = candle_frame(TUESDAY, 40)
    df.loc[30:, ["open", "high", "low", "close"]] += 0.05   # 100x the typical candle range
    res = V.validate(df, "H1", fx_hours)
    gap = [i for i in res.issues if i.issue_type == "abnormal_gap"]
    assert len(gap) == 1 and gap[0].details["multiple"] > 8
    assert res.candles.loc[30, "quality_status"] == "suspect"


def test_incomplete_candle_reported(fx_hours):
    df = candle_frame(TUESDAY, 5)
    df.loc[4, "is_complete"] = False
    res = V.validate(df, "H1", fx_hours)
    assert res.counts["incomplete_candle"] == 1


def test_no_data(fx_hours):
    res = V.validate(candle_frame(TUESDAY, 0), "H1", fx_hours, "EURUSD")
    assert res.counts == {"no_data": 1} and res.has_errors


def test_market_hours():
    h = TradingHours.from_dict({"weekly_close": "FRI 21:00", "weekly_open": "SUN 22:00",
                                "daily_breaks": [["21:00", "22:00"]], "holidays": ["2026-12-25"]})
    assert is_market_open(datetime(2026, 3, 3, 12, tzinfo=timezone.utc), h)
    assert not is_market_open(datetime(2026, 3, 3, 21, 30, tzinfo=timezone.utc), h)   # daily break
    assert not is_market_open(datetime(2026, 3, 7, 12, tzinfo=timezone.utc), h)       # Saturday
    assert is_market_open(datetime(2026, 3, 8, 22, 30, tzinfo=timezone.utc), h)       # Sunday open
    assert not is_market_open(datetime(2026, 12, 25, 12, tzinfo=timezone.utc), h)     # holiday


def test_market_hours_follow_dst_in_exchange_timezone():
    # 17:00 New York = 21:00 UTC in summer (EDT) and 22:00 UTC in winter (EST).
    h = TradingHours.from_dict({"timezone": "America/New_York", "weekly_close": "FRI 17:00",
                                "weekly_open": "SUN 18:00", "daily_breaks": [["17:00", "18:00"]],
                                "holidays": ["2026-04-03"]})
    utc = timezone.utc
    # Summer (EDT): break is 21:00-22:00 UTC
    assert not is_market_open(datetime(2026, 10, 27, 21, 30, tzinfo=utc), h)
    assert is_market_open(datetime(2026, 10, 27, 22, 30, tzinfo=utc), h)
    # Winter (EST, after 2026-11-01): break is 22:00-23:00 UTC; Sunday open is 23:00 UTC
    assert is_market_open(datetime(2026, 11, 3, 21, 30, tzinfo=utc), h)
    assert not is_market_open(datetime(2026, 11, 3, 22, 30, tzinfo=utc), h)
    assert not is_market_open(datetime(2026, 11, 1, 22, 30, tzinfo=utc), h)
    assert is_market_open(datetime(2026, 11, 1, 23, 30, tzinfo=utc), h)
    # Holiday dates are local dates (Good Friday 2026)
    assert not is_market_open(datetime(2026, 4, 3, 14, tzinfo=utc), h)
    # Naive datetimes are treated as UTC
    assert not is_market_open(datetime(2026, 11, 3, 22, 30), h)


def test_unknown_trading_hours_timezone_rejected():
    with pytest.raises(ValueError, match="Unknown trading_hours timezone"):
        TradingHours.from_dict({"timezone": "Mars/Olympus"})


def test_shipped_instruments_yaml_loads():
    from aurum.config import PROJECT_ROOT, load_instruments
    inst = load_instruments(PROJECT_ROOT / "config" / "instruments.yaml")
    assert set(inst) >= {"EURUSD", "GBPUSD", "XAUUSD", "US30"}
    assert all(i.trading_hours.timezone == "America/New_York" for i in inst.values())
