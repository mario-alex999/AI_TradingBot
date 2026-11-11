from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from aurum.collectors.candle_collector import collect_candles, incremental_count, normalize_rates, rates_to_frame
from aurum.config import (Settings, SymbolMappingError, enabled_instruments, load_instruments)
from aurum.database.connection import init_database, make_engine
from aurum.database.repository import Repository
from aurum.gateways.mt5_gateway import BrokerClock, MT5Gateway
from aurum.services.market_data_service import MarketDataService
from module01_helpers import TUESDAY, FakeMT5, make_rates

ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------- UTC conversion
def test_broker_time_converted_to_utc_with_dst():
    clock = BrokerClock("Europe/Athens")   # UTC+2 winter, UTC+3 summer
    winter = datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc).timestamp()   # server wall 00:00
    summer = datetime(2026, 7, 1, 10, 0, tzinfo=timezone.utc).timestamp()
    assert clock.to_utc(winter) == datetime(2026, 1, 4, 22, 0, tzinfo=timezone.utc)
    assert clock.to_utc(summer) == datetime(2026, 7, 1, 7, 0, tzinfo=timezone.utc)
    assert clock.to_utc(summer).tzinfo is not None


def test_fixed_offset_clock_and_bad_timezone():
    assert BrokerClock(fixed_offset_seconds=7200).to_utc(7200) == datetime(1970, 1, 1, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="MT5_SERVER_TIMEZONE"):
        BrokerClock("Mars/Olympus")


def test_normalize_keeps_original_mt5_time_and_broker_symbol(eurusd):
    raw = rates_to_frame(make_rates(TUESDAY, 3, 3600, server_offset_s=7200))
    df = normalize_rates(raw, eurusd, "H1", BrokerClock(fixed_offset_seconds=7200), TUESDAY + timedelta(days=1))
    assert df["ts_utc"].iloc[0] == pd.Timestamp(TUESDAY)
    assert df["mt5_time"].iloc[0] == int(TUESDAY.timestamp()) + 7200
    assert (df["broker_symbol"] == "EURUSD").all() and str(df["ts_utc"].dt.tz) == "UTC"


# ---------------------------------------------------------------- forming candle / sorting
def _gateway(settings, rates):
    gw = MT5Gateway(settings, FakeMT5(rates=rates))
    gw.connect()
    return gw


def test_forming_candle_excluded(settings, eurusd):
    rates = make_rates(TUESDAY, 10, 3600)
    now = TUESDAY + timedelta(hours=9, minutes=30)      # last candle (09:00) is still forming
    gw = _gateway(settings, {("EURUSD", FakeMT5.TIMEFRAME_H1): rates})
    batch = collect_candles(gw, eurusd, "H1", count=5, exclude_forming=True, now_utc=now)
    assert batch.excluded_forming == 1
    assert len(batch.candles) == 5
    assert batch.candles["ts_utc"].max() == pd.Timestamp(TUESDAY + timedelta(hours=8))
    assert batch.candles["is_complete"].all()


def test_forming_candle_kept_but_flagged_when_not_excluded(settings, eurusd):
    rates = make_rates(TUESDAY, 10, 3600)
    gw = _gateway(settings, {("EURUSD", FakeMT5.TIMEFRAME_H1): rates})
    batch = collect_candles(gw, eurusd, "H1", 5, exclude_forming=False, now_utc=TUESDAY + timedelta(hours=9, minutes=30))
    assert batch.excluded_forming == 0 and len(batch.candles) == 5
    assert batch.candles["is_complete"].tolist() == [True] * 4 + [False]


def test_candles_sorted_after_validation(settings, eurusd):
    from aurum.services.validation_service import ValidationService
    rates = make_rates(TUESDAY, 6, 3600)[[3, 0, 5, 1, 4, 2]]
    raw = rates_to_frame(rates)
    df = normalize_rates(raw, eurusd, "H1", BrokerClock("Etc/UTC"), TUESDAY + timedelta(days=1))
    res = ValidationService().validate(df, "H1", eurusd.trading_hours, "EURUSD")
    assert res.candles["ts_utc"].is_monotonic_increasing
    assert "unordered_timestamps" in res.counts


def test_incremental_count():
    now = TUESDAY + timedelta(hours=10)
    assert incremental_count(None, "H1", 500, now) == 500
    assert incremental_count(TUESDAY + timedelta(hours=7), "H1", 500, now) == 3 + 5
    assert incremental_count(TUESDAY - timedelta(days=365), "M5", 500, now) == 500


# ---------------------------------------------------------------- symbol mapping
def test_symbol_mapping_from_yaml(tmp_path):
    p = tmp_path / "inst.yaml"
    p.write_text("instruments:\n  EURUSD: {broker_symbol: EURUSD.m, asset_class: forex}\n"
                 "  US30: {broker_symbol: US30.cash, asset_class: index,"
                 " trading_hours: {daily_breaks: [['21:00','22:00']]}}\n")
    inst = load_instruments(p)
    assert inst["EURUSD"].broker_symbol == "EURUSD.m"
    assert inst["US30"].broker_symbol == "US30.cash"
    assert len(inst["US30"].trading_hours.daily_breaks) == 1


def test_default_instruments_yaml_has_four_symbols():
    inst = load_instruments(ROOT / "config" / "instruments.yaml")
    assert set(inst) >= {"EURUSD", "GBPUSD", "XAUUSD", "US30"}


def test_missing_mapping_is_reported(tmp_path):
    p = tmp_path / "inst.yaml"
    p.write_text("instruments:\n  EURUSD: {asset_class: forex}\n")
    with pytest.raises(SymbolMappingError, match="no broker_symbol"):
        load_instruments(p)
    p.write_text("instruments:\n  EURUSD: {broker_symbol: EURUSD}\n")
    s = Settings(_env_file=None, enabled_instruments="EURUSD,GBPUSD")
    with pytest.raises(SymbolMappingError, match="GBPUSD"):
        enabled_instruments(s, load_instruments(p))


def test_settings_parse_lists_and_reject_bad_timeframe():
    s = Settings(_env_file=None, enabled_instruments="eurusd, xauusd", enabled_timeframes="h1,M5")
    assert s.enabled_instruments == ["EURUSD", "XAUUSD"] and s.enabled_timeframes == ["H1", "M5"]
    with pytest.raises(ValueError):
        Settings(_env_file=None, enabled_timeframes="H2")


# ---------------------------------------------------------------- end-to-end service (SQLite)
def test_service_collects_and_skips_unavailable_symbol(settings, eurusd):
    from aurum.config import InstrumentConfig
    rates = {("EURUSD", FakeMT5.TIMEFRAME_H1): make_rates(TUESDAY, 30, 3600),
             ("EURUSD", FakeMT5.TIMEFRAME_M15): make_rates(TUESDAY, 30, 900)}
    engine = make_engine("sqlite:///:memory:")
    init_database(engine)
    repo = Repository(engine)
    missing = InstrumentConfig("GBPUSD", "GBPUSD.x", "forex", "", eurusd.trading_hours)
    gw = _gateway(settings, rates)
    now = TUESDAY + timedelta(days=2)
    summary = MarketDataService(settings, gw, repo).run([eurusd, missing], ["H1", "M15"], now_utc=now)
    assert "GBPUSD" in summary.unavailable
    assert {(r.timeframe, r.status) for r in summary.series} == {("H1", "success"), ("M15", "success")}
    assert summary.status == "partial"
    assert len(repo.load_candles("EURUSD", "H1")) == 30
    raw = list((settings.data_dir / "raw").rglob("EURUSD_H1.parquet"))
    processed = list((settings.data_dir / "processed").rglob("EURUSD_H1.parquet"))
    assert raw and processed
    # second run is idempotent: no duplicates
    MarketDataService(settings, gw, repo).run([eurusd], ["H1"], now_utc=now)
    assert len(repo.load_candles("EURUSD", "H1")) == 30
