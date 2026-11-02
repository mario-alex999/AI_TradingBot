from __future__ import annotations

import logging
import re
from pathlib import Path

import pytest
from pydantic import SecretStr

from aurum.gateways.mt5_gateway import (MT5ConnectionError, MT5Gateway, NonDemoAccountError, ReadOnlyMT5,
                                        ReadOnlyViolation)
from conftest import FakeMT5

PKG = Path(__file__).resolve().parent.parent / "aurum"


def test_demo_account_accepted(settings):
    fake = FakeMT5(trade_mode=0)
    with MT5Gateway(settings, fake) as gw:
        assert gw.report.trade_mode == "DEMO"
        assert gw.report.login == 12345678
    assert fake.shutdown_called == 1


@pytest.mark.parametrize("mode,name", [(2, "REAL"), (1, "CONTEST")])
def test_non_demo_account_rejected_and_disconnected(settings, mode, name):
    fake = FakeMT5(trade_mode=mode)
    gw = MT5Gateway(settings, fake)
    with pytest.raises(NonDemoAccountError, match=name):
        gw.connect()
    assert fake.shutdown_called == 1 and not gw.connected


def test_initialize_failure_raises_clear_error(settings):
    fake = FakeMT5(init_ok=False)
    with pytest.raises(MT5ConnectionError, match="initialize\\(\\) failed.*-10003"):
        MT5Gateway(settings, fake).connect()
    assert fake.shutdown_called == 1


def test_terminal_not_connected(settings):
    with pytest.raises(MT5ConnectionError, match="not connected to the broker"):
        MT5Gateway(settings, FakeMT5(terminal_connected=False)).connect()


def test_initialize_exception_is_wrapped(settings):
    class Boom(FakeMT5):
        def initialize(self, **kw):
            raise OSError("pipe broken")
    with pytest.raises(MT5ConnectionError, match="pipe broken"):
        MT5Gateway(settings, Boom()).connect()


def test_login_requires_password_and_server(settings):
    settings.mt5_login = 123
    with pytest.raises(MT5ConnectionError, match="MT5_PASSWORD and MT5_SERVER"):
        MT5Gateway(settings, FakeMT5()).connect()


def test_password_passed_to_mt5_but_never_logged(settings, caplog):
    settings.mt5_login, settings.mt5_server = 123, "Broker-Demo"
    settings.mt5_password = SecretStr("SuperSecret!42")
    fake = FakeMT5()
    with caplog.at_level(logging.DEBUG):
        with MT5Gateway(settings, fake):
            pass
    assert fake.calls[0][1]["password"] == "SuperSecret!42"
    assert "SuperSecret!42" not in caplog.text
    assert all("SuperSecret!42" not in str(r.__dict__) for r in caplog.records)
    assert "SuperSecret!42" not in repr(settings)


def test_unavailable_symbol_returns_none(settings):
    with MT5Gateway(settings, FakeMT5()) as gw:
        assert gw.symbol_spec("NOPE") is None
        assert gw.tick("NOPE") is None
        assert gw.copy_rates("NOPE", "H1", 0, 10) is None
        assert gw.symbol_spec("EURUSD")["digits"] == 5


def test_hidden_symbol_is_selected_in_market_watch(settings):
    fake = FakeMT5()
    with MT5Gateway(settings, fake) as gw:
        assert gw.ensure_symbol("XAUUSD") is not None
    assert fake.selected == ["XAUUSD"]


def test_calls_before_connect_are_rejected(settings):
    with pytest.raises(MT5ConnectionError):
        MT5Gateway(settings, FakeMT5()).copy_rates("EURUSD", "H1", 0, 1)


def test_read_only_proxy_blocks_trading_functions():
    class WithTrading(FakeMT5):
        def order_send(self, request):  # pragma: no cover - must never be reached
            raise AssertionError("order_send reached!")
    proxy = ReadOnlyMT5(WithTrading())
    for name in ("order_send", "order_check", "positions_get", "orders_get", "Buy", "Sell", "Close"):
        with pytest.raises(ReadOnlyViolation):
            getattr(proxy, name)
    assert proxy.TIMEFRAME_H1 == 16385


def test_no_trading_calls_anywhere_in_source():
    forbidden = re.compile(r"\b(order_send|order_check|order_calc_\w+|positions_get|orders_get|TRADE_ACTION_\w+)\s*\(")
    offenders = [str(p) for p in PKG.rglob("*.py") if forbidden.search(p.read_text(encoding="utf-8"))]
    assert offenders == []


# --------------------------------------------------------------------------- broker clock / DST verification
from datetime import datetime, timedelta, timezone  # noqa: E402

from aurum.config import InstrumentConfig, Settings, TradingHours  # noqa: E402
from conftest import Tick  # noqa: E402

FX = InstrumentConfig("EURUSD", "EURUSD", "forex", "Euro",
                      TradingHours.from_dict({"weekly_close": "FRI 21:00", "weekly_open": "SUN 21:00"}))


class ClockFake(FakeMT5):
    """FakeMT5 whose tick carries a chosen *server wall-clock* time."""

    def __init__(self, server_epoch: float):
        super().__init__()
        self.server_epoch = server_epoch

    def symbol_info_tick(self, name):
        return Tick(int(self.server_epoch), 1.1, 1.1002, 0.0, 0, int(self.server_epoch * 1000), 6)


def _clock_check(tmp_path, tz: str, now: datetime, server_offset_h: float):
    s = Settings(_env_file=None, mt5_server_timezone=tz, data_dir=tmp_path / "d", log_dir=tmp_path / "l",
                 database_url="sqlite:///:memory:")
    fake = ClockFake(now.timestamp() + server_offset_h * 3600 - 5)   # tick 5s old
    with MT5Gateway(s, fake) as gw:
        return gw.verify_clock([FX], now_utc=now)


TUE_WINTER = datetime(2026, 1, 6, 12, tzinfo=timezone.utc)
TUE_SUMMER = datetime(2026, 7, 7, 12, tzinfo=timezone.utc)


def test_clock_ok_for_utc_broker(tmp_path):
    assert _clock_check(tmp_path, "Etc/UTC", TUE_WINTER, 0).status == "ok"


def test_clock_follows_dst_for_iana_zone(tmp_path):
    # Athens-clock broker: UTC+2 in winter, UTC+3 in summer - both accepted with Europe/Athens
    assert _clock_check(tmp_path, "Europe/Athens", TUE_WINTER, 2).status == "ok"
    assert _clock_check(tmp_path, "Europe/Athens", TUE_SUMMER, 3).status == "ok"


def test_clock_mismatch_when_zone_wrong(tmp_path):
    # UTC broker configured as Europe/Athens (the Exness case): ticks look 2-3h old while market is open
    cc = _clock_check(tmp_path, "Europe/Athens", TUE_SUMMER, 0)
    assert cc.status == "mismatch" and not cc.ok and cc.expected_offset_h == 3.0 and cc.observed_offset_h == 0.0
    # Athens broker configured as UTC: ticks are "in the future"
    assert _clock_check(tmp_path, "Etc/UTC", TUE_SUMMER, 3).status == "mismatch"


def test_clock_mismatch_when_fixed_offset_crosses_dst(tmp_path):
    s = Settings(_env_file=None, mt5_server_timezone="Etc/UTC", data_dir=tmp_path / "d", log_dir=tmp_path / "l",
                 database_url="sqlite:///:memory:")
    from aurum.gateways.mt5_gateway import BrokerClock
    with MT5Gateway(s, ClockFake(TUE_WINTER.timestamp() + 2 * 3600)) as gw:
        gw.clock = BrokerClock(fixed_offset_seconds=3 * 3600)      # "auto" detected in summer
        assert gw.verify_clock([FX], now_utc=TUE_WINTER).status == "mismatch"


def test_clock_unknown_when_market_closed_and_tick_stale(tmp_path):
    saturday = datetime(2026, 1, 10, 12, tzinfo=timezone.utc)
    s = Settings(_env_file=None, mt5_server_timezone="Etc/UTC", data_dir=tmp_path / "d", log_dir=tmp_path / "l",
                 database_url="sqlite:///:memory:")
    last_friday_tick = (saturday - timedelta(hours=15)).timestamp()
    with MT5Gateway(s, ClockFake(last_friday_tick)) as gw:
        cc = gw.verify_clock([FX], now_utc=saturday)
    assert cc.status == "unknown" and cc.ok


def test_stale_ticks_while_open_are_not_a_clock_mismatch(tmp_path):
    # 42-minute-old tick during market hours (feed lag) -> unknown, collection continues
    cc = _clock_check(tmp_path, "Etc/UTC", TUE_WINTER, -0.7)
    assert cc.status == "unknown" and cc.ok and "stale feed" in cc.message
    # a whole-hour offset during market hours is still a mismatch
    assert _clock_check(tmp_path, "Etc/UTC", TUE_WINTER, -2).status == "mismatch"
