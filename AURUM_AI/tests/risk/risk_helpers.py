"""Fixtures for Module 06. Everything runs on MemoryRiskData (MOCKED inputs: no broker, no live data) and a temporary SQLite
database through the real engine, rules and repository. Specifications are the illustrative SAMPLE_SPECS, not broker facts."""
from __future__ import annotations

import warnings
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest

from aurum.database.connection import init_database, make_engine
from aurum.risk.account import EquityPoint
from aurum.risk.calendar import TradingCalendar
from aurum.risk.config import TradingDay
from aurum.risk.service import RiskService
from aurum.risk.simulation import MemoryRiskData

warnings.filterwarnings("ignore", message=".*Decimal objects natively.*")
NOW = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)   # Wednesday, London session (EST in New York)

EURUSD_BUY = {"symbol": "EURUSD", "direction": "BUY", "entry": "1.10010", "stop_loss": "1.09810", "take_profit": "1.10510"}
EURUSD_SELL = {"symbol": "EURUSD", "direction": "SELL", "entry": "1.10010", "stop_loss": "1.10210", "take_profit": "1.09510"}


class Clock:
    def __init__(self, t: datetime):
        self.t = t

    def __call__(self) -> datetime:
        return self.t

    def advance(self, **kw: Any) -> None:
        self.t += timedelta(**kw)


def seed_history(data: MemoryRiskData, equity: Decimal | None = None) -> None:
    cal = TradingCalendar(TradingDay())
    eq = equity if equity is not None else data.equity
    d = cal.trading_date(cal.month(data.now).start) - timedelta(days=1)
    while d <= cal.trading_date(data.now):
        data.history.append(EquityPoint(cal.day_start(d), eq, data.balance))
        d += timedelta(days=1)


_TEMPLATE: dict[str, Any] = {}


def _template_db(tmp_root: Any) -> Any:
    """Create the full schema once per test session; each test gets a copy (create_all on every table takes seconds)."""
    if "path" not in _TEMPLATE:
        path = tmp_root / "risk-template.db"
        eng = make_engine(f"sqlite:///{path.as_posix()}")
        init_database(eng)
        eng.dispose()
        _TEMPLATE["path"] = path
    return _TEMPLATE["path"]


@pytest.fixture(scope="session")
def template_root(tmp_path_factory: Any) -> Any:
    return tmp_path_factory.mktemp("risk-template")


class Harness:
    template_root: Any = None

    def __init__(self, tmp_path: Any, **kw: Any):
        import shutil
        self.clock = Clock(kw.pop("now", NOW))
        path = tmp_path / "risk.db"
        shutil.copyfile(_template_db(Harness.template_root or tmp_path), path)
        self.db_url = f"sqlite:///{path.as_posix()}"
        self.engine = make_engine(self.db_url)
        self.data = MemoryRiskData(now=self.clock.t, **kw)
        seed_history(self.data)
        self.svc = RiskService(self.engine, None, data=self.data, clock=self.clock)

    def assess(self, body: dict[str, Any] | None = None, key: str | None = None, **over: Any) -> dict[str, Any]:
        b = dict(body or EURUSD_BUY)
        b.update(over)
        return self.svc.assess(b, "tester", None, key)

    def config(self, reason: str = "test change", ack: bool = True, **changes: Any) -> dict[str, Any]:
        v = self.svc.config()["version"]
        return self.svc.update_config({"base_version": v, "reason": reason, "changes": changes, "acknowledge_weakening": ack},
                                      "tester", None)

    def restart(self) -> "Harness":
        """A fresh engine and service on the same database file, as after an application restart."""
        self.engine = make_engine(self.db_url)
        self.svc = RiskService(self.engine, None, data=self.data, clock=self.clock)
        return self


@pytest.fixture(autouse=True)
def _template(template_root: Any) -> None:
    Harness.template_root = template_root


@pytest.fixture
def h(tmp_path: Any) -> Harness:
    return Harness(tmp_path)


def ids(result: dict[str, Any], key: str) -> list[str]:
    return [v["rule_id"] for v in result[key]]
