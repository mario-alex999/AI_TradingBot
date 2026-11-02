"""Shared fixtures. FakeMT5 imitates the MetaTrader5 module so tests never need a terminal."""
from __future__ import annotations

import sys
from collections import namedtuple
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aurum.config import InstrumentConfig, Settings, TradingHours  # noqa: E402

RATE_DTYPE = np.dtype([("time", "<i8"), ("open", "<f8"), ("high", "<f8"), ("low", "<f8"), ("close", "<f8"),
                       ("tick_volume", "<u8"), ("spread", "<i4"), ("real_volume", "<u8")])
Account = namedtuple("Account", "login trade_mode server company currency leverage")
Terminal = namedtuple("Terminal", "connected name build path")
SymbolInfo = namedtuple("SymbolInfo", "name visible digits point trade_contract_size trade_tick_size trade_tick_value "
                                      "volume_min volume_max volume_step spread trade_mode currency_base "
                                      "currency_profit description path bid ask")
Tick = namedtuple("Tick", "time bid ask last volume time_msc flags")


def make_rates(start: datetime, n: int, step_s: int, price: float = 1.1000, server_offset_s: int = 0) -> np.ndarray:
    """Valid synthetic rates; ``time`` is broker SERVER epoch (UTC + offset)."""
    rows = []
    for k in range(n):
        o = price + k * 0.0001
        rows.append((int(start.timestamp()) + server_offset_s + k * step_s, o, o + 0.0005, o - 0.0005, o + 0.0001,
                     100 + k, 2, 0))
    return np.array(rows, dtype=RATE_DTYPE)


class FakeMT5:
    TIMEFRAME_M1, TIMEFRAME_M5, TIMEFRAME_M15, TIMEFRAME_M30 = 1, 5, 15, 30
    TIMEFRAME_H1, TIMEFRAME_H4, TIMEFRAME_D1, TIMEFRAME_W1 = 16385, 16388, 16408, 32769
    ACCOUNT_TRADE_MODE_DEMO, ACCOUNT_TRADE_MODE_CONTEST, ACCOUNT_TRADE_MODE_REAL = 0, 1, 2

    def __init__(self, trade_mode: int = 0, init_ok: bool = True, terminal_connected: bool = True,
                 symbols: dict | None = None, rates: dict | None = None):
        self.trade_mode, self.init_ok, self.terminal_connected = trade_mode, init_ok, terminal_connected
        self.symbols = symbols if symbols is not None else {"EURUSD": 5, "XAUUSD": 2}
        self.rates = rates or {}
        self.calls: list[tuple] = []
        self.shutdown_called = 0
        self.selected: list[str] = []

    def initialize(self, **kw):
        self.calls.append(("initialize", kw))
        return self.init_ok

    def last_error(self):
        return (-10003, "IPC initialize failed, MetaTrader 5 x64 not found") if not self.init_ok else (1, "Success")

    def shutdown(self):
        self.shutdown_called += 1

    def terminal_info(self):
        return Terminal(self.terminal_connected, "FakeTerminal", 4500, "C:/MT5")

    def account_info(self):
        return Account(12345678, self.trade_mode, "Broker-Demo", "Fake Broker Ltd", "USD", 100)

    def symbol_info(self, name):
        if name not in self.symbols:
            return None
        digits = self.symbols[name]
        return SymbolInfo(name, name in self.selected or name != "XAUUSD", digits, 10 ** -digits, 100000.0,
                          10 ** -digits, 1.0, 0.01, 100.0, 0.01, 12, 4, name[:3], "USD", f"{name} desc", "Forex\\" + name,
                          1.1, 1.1002)

    def symbol_select(self, name, enable):
        self.selected.append(name)
        return name in self.symbols

    def symbol_info_tick(self, name):
        if name not in self.symbols:
            return None
        return Tick(1_767_225_600, 1.1000, 1.1002, 0.0, 0, 1_767_225_600_123, 6)

    def copy_rates_from_pos(self, name, tf, start, count):
        self.calls.append(("copy_rates_from_pos", name, tf, start, count))
        if name not in self.symbols:
            return None
        arr = self.rates.get((name, tf))
        return None if arr is None else arr[-count:]


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(_env_file=None, mt5_server_timezone="Etc/UTC", data_dir=tmp_path / "data",
                    log_dir=tmp_path / "logs", database_url="sqlite:///:memory:")


@pytest.fixture
def fx_hours() -> TradingHours:
    return TradingHours.from_dict({"weekly_close": "FRI 21:00", "weekly_open": "SUN 21:00"})


@pytest.fixture
def eurusd(fx_hours) -> InstrumentConfig:
    return InstrumentConfig("EURUSD", "EURUSD", "forex", "Euro", fx_hours)


def candle_frame(start: datetime, n: int, step_min: int = 60, tf: str = "H1") -> pd.DataFrame:
    ts = pd.date_range(start, periods=n, freq=f"{step_min}min", tz="UTC")
    o = 1.1 + np.arange(n) * 0.0001
    return pd.DataFrame({"symbol": "EURUSD", "broker_symbol": "EURUSD", "timeframe": tf, "ts_utc": ts, "open": o,
                         "high": o + 0.0005, "low": o - 0.0005, "close": o + 0.0001, "tick_volume": 100,
                         "real_volume": 0, "spread": 2, "mt5_time": ts.astype("int64") // 10**9, "is_complete": True})


# a Tuesday 00:00 UTC, well inside the trading week
TUESDAY = datetime(2026, 3, 3, 0, 0, tzinfo=timezone.utc)
