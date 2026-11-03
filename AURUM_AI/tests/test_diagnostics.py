"""`python -m aurum.cli diagnose`: each failure mode is named precisely, nothing is changed, nothing secret is shown.
MT5 is faked (conftest.FakeMT5); the database cases use SQLite and a closed local port - no real services."""
from __future__ import annotations

import socket
from collections import namedtuple

import pytest
from pydantic import SecretStr

from aurum.config import Settings
from aurum.diagnostics import FAIL, OK, WARN, classify_db_error, diagnose_database, diagnose_mt5
from aurum.gateways.mt5_gateway import MT5ConnectionError, MT5Gateway, explain_mt5_error
from conftest import FakeMT5

Sym = namedtuple("Sym", "name")


def _step(steps, name):
    return next(s for s in steps if s.name == name)


class AuthFailMT5(FakeMT5):
    def __init__(self, **kw):
        super().__init__(init_ok=False, **kw)

    def last_error(self):
        return (-6, "Terminal: Authorization failed")


class FullMT5(FakeMT5):
    def version(self):
        return (500, 6296, "01 Nov 2026")

    def symbols_get(self, group=None):
        base = (group or "").strip("*")
        return [Sym(n) for n in ("EURUSDm", "EURUSD.a", "GBPUSDm") if base in n]

    def copy_rates_from_pos(self, name, tf, start, count):
        return None if name not in self.symbols else [0] * count


def _mt5(settings, fake, eurusd, running=("C:/MT5/terminal64.exe",)):
    return diagnose_mt5(settings, [eurusd], fake, process_lister=lambda: list(running))


def test_minus_6_is_explained_and_names_the_manual_fix(settings, eurusd):
    steps = _mt5(settings, AuthFailMT5(), eurusd)
    s = _step(steps, "Connect (initialize)")
    assert s.status == FAIL and "-6" in s.detail
    assert "File > Login to Trade Account" in s.hint and "DEMO" in s.hint and "Save password" in s.hint
    assert steps[-1] is s                                         # stops: nothing after a failed connect


def test_gateway_error_message_carries_the_explanation(settings):
    with pytest.raises(MT5ConnectionError, match=r"-6: Terminal: Authorization failed.*Login to Trade Account.*diagnose"):
        MT5Gateway(settings, AuthFailMT5()).connect()
    assert "terminal64.exe" in explain_mt5_error(-10003) and explain_mt5_error(None) == ""


def test_terminal_running_but_not_connected(settings, eurusd):
    s = _step(_mt5(settings, type("T", (FullMT5,), {})(terminal_connected=False), eurusd), "Terminal status")
    assert s.status == FAIL and "NOT connected" in s.detail


def test_non_demo_account_is_reported_and_not_changed(settings, eurusd):
    fake = FullMT5(trade_mode=2)
    steps = _mt5(settings, fake, eurusd)
    s = _step(steps, "Account authorisation")
    assert s.status == FAIL and "REAL" in s.detail and "will not change the account" in s.hint
    assert not any(c[0] == "initialize" and "login" in c[1] for c in fake.calls)   # no login attempted


def test_healthy_terminal_reports_mapping_visibility_and_history(settings, eurusd):
    steps = _mt5(settings, FullMT5(), eurusd)
    assert _step(steps, "Account authorisation").status == OK
    assert _step(steps, "Symbol EURUSD").status == OK
    h = _step(steps, "History EURUSD")
    assert h.status == OK and "M15: 10 recent bars" in h.detail


def test_missing_broker_symbol_lists_candidates_but_never_substitutes(settings, eurusd):
    from dataclasses import replace
    wrong = replace(eurusd, broker_symbol="EURUSD.pro")
    fake = FullMT5()
    steps = diagnose_mt5(settings, [wrong], fake, process_lister=lambda: [])
    s = _step(steps, "Symbol EURUSD")
    assert s.status == FAIL and "EURUSD.pro" in s.detail
    assert "EURUSD.a, EURUSDm" in s.hint and "never substitutes" in s.hint
    assert fake.selected == []                                    # Market Watch untouched


def test_hidden_symbol_is_a_warning_and_is_not_selected(settings, fx_hours):
    from aurum.config import InstrumentConfig
    fake = FullMT5()
    steps = diagnose_mt5(settings, [InstrumentConfig("XAUUSD", "XAUUSD", "metal", "Gold", fx_hours)], fake,
                         process_lister=lambda: [])
    assert _step(steps, "Symbol XAUUSD").status == WARN and fake.selected == []
    assert _step(steps, "Terminal process").status == WARN        # no terminal process listed


def test_terminal_path_problems(settings, eurusd, tmp_path):
    settings.mt5_terminal_path = str(tmp_path / "MetaTrader 5")              # a folder, the classic -10003 cause
    (tmp_path / "MetaTrader 5").mkdir()
    s = _step(_mt5(settings, FullMT5(), eurusd), "Terminal path")
    assert s.status == FAIL and "not the folder" in s.hint


def test_password_is_never_shown(settings, eurusd):
    settings.mt5_login, settings.mt5_server = 123, "Broker-Demo"
    settings.mt5_password = SecretStr("Hunter2!secret")

    class Leaky(AuthFailMT5):
        def last_error(self):
            return (-6, "Authorization failed for password Hunter2!secret")
    steps = _mt5(settings, Leaky(), eurusd)
    text = " ".join(s.detail + s.hint for s in steps)
    assert "Hunter2!secret" not in text and "***" in text
    assert "password set" in _step(steps, "Login configuration").detail


# ------------------------------------------------------------------------------------------- database
@pytest.mark.parametrize("msg, category", [
    ('FATAL:  password authentication failed for user "aurum"', "authentication"),
    ('FATAL:  role "bob" does not exist', "authentication"),
    ('FATAL:  database "aurum2" does not exist', "missing database"),
    ("connection timeout expired", "network timeout"),
    ("connection to server at 127.0.0.1, port 5432 failed: Connection refused", "network"),
    ("permission denied for table market_candles", "permissions"),
])
def test_database_errors_are_classified(msg, category):
    assert classify_db_error(msg)[0] == category


def test_missing_database_is_never_created():
    assert "never creates or recreates" in classify_db_error('database "x" does not exist')[1]


def _closed_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_service_not_running_is_a_network_failure_and_password_hidden(tmp_path):
    s = Settings(_env_file=None, database_url=None, db_host="127.0.0.1", db_port=_closed_port(), db_name="aurum",
                 db_user="aurum", db_password=SecretStr("pw-Secret-9"), data_dir=tmp_path, log_dir=tmp_path)
    steps = diagnose_database(s, tcp_timeout=5, docker_status=lambda: None)
    net = _step(steps, "Network (TCP)")
    # Windows retries a refused local SYN for ~2 s, so either "refused" or a timeout is a correct reading here
    assert net.status == FAIL and ("refused" in net.detail or "no answer" in net.detail) and "Docker" in net.hint
    assert steps[-1] is net                                       # stops before trying to log in
    assert _step(steps, "Database service (Docker)").status == "skipped"
    assert all("pw-Secret-9" not in st.detail + st.hint for st in steps)
    assert "user=aurum" in _step(steps, "Connection string").detail


def test_sqlite_without_schema_points_to_init_db(tmp_path):
    s = Settings(_env_file=None, database_url=f"sqlite:///{(tmp_path / 'a.db').as_posix()}", data_dir=tmp_path,
                 log_dir=tmp_path)
    steps = diagnose_database(s)
    assert _step(steps, "Login / database").status == OK
    m = _step(steps, "Migrations")
    assert m.status == WARN and "init-db" in m.hint
