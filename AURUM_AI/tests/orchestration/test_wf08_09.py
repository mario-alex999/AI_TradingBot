"""WF08-09 System Health Watchdog: per-service status, staleness by last update, credentials, alerts, API, install.

Docx test stage covered here: (11) every service is classified as online, offline, degraded, responding but stale or
unavailable because credentials are missing; market data is judged by its last update, not by an HTTP 200; a service
that becomes unhealthy is announced once and its recovery is recorded.

The Module 08 core and Module 07's notifications are real here. Module checks and n8n are controlled.
"""
from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from m08_harness import ALICE, NOW
from test_wf08_04 import RiskGateway, _Settings

from aurum.account.db_models import AccountSnapshot
from aurum.approvals.db_models import Notification
from aurum.approvals.service import ApprovalService
from aurum.auth.service import AuthService
from aurum.database.connection import session_scope
from aurum.database.models import Instrument, MarketCandle
from aurum.orchestration import health_watchdog as hw
from aurum.orchestration.db_models import OrchAuditLog, OrchHealthCheck
from aurum.orchestration.engines import EngineHealth
from aurum.orchestration.health_watchdog import HealthWatchdog, install, market_open
from sqlalchemy import select

P = "/api/v1/orchestration"


class W:
    def __init__(self, h, monkeypatch):
        self.h, self.calls = h, []
        self.auth = AuthService(h.engine, iterations=1000, clock=h.clock)
        self.auth.create_user("alice", "correct-horse-battery-9", "admin", "Alice", "alice@example.test")
        self.approvals = ApprovalService(h.engine, _Settings(), RiskGateway(h.clock), None, None, None, clock=h.clock)
        self.wd = HealthWatchdog(h.svc, approvals=lambda: self.approvals, deliver=lambda a: {"sent": 0})
        self.modules = {m: ("HEALTHY", f"{m} ok") for m, _ in hw.MODULES}
        self.modules["module_09"] = ("UNKNOWN", "Module 09 - MT5 Execution is not deployed yet (no URL configured).")

        def check(mid, actor=None):
            self.calls.append(mid)
            st, detail = self.modules[mid]
            return {"integration_id": mid, "status": st, "detail": detail, "latency_ms": 5.0, "http_status": 200}
        monkeypatch.setattr(h.svc, "check_integration", check)
        self.store(minutes_ago=3)

    def store(self, minutes_ago=3, account_minutes_ago=None):
        """A closed M5 candle and an account snapshot, as Module 01 stores them."""
        at = self.h.clock() - timedelta(minutes=minutes_ago)
        with session_scope(self.h.engine) as s:
            ins = s.execute(select(Instrument).where(Instrument.symbol == "EURUSD")).scalar_one_or_none()
            if ins is None:
                ins = Instrument(symbol="EURUSD", broker_symbol="EURUSD", asset_class="forex")
                s.add(ins)
                s.flush()
            s.add(MarketCandle(instrument_id=ins.id, timeframe="M5", ts_utc=at, open=1.1, high=1.1, low=1.1, close=1.1,
                               is_complete=True, quality_status="valid", broker_symbol="EURUSD", mt5_time=int(at.timestamp())))
            s.add(AccountSnapshot(captured_at=self.h.clock() - timedelta(minutes=account_minutes_ago or minutes_ago), login=1001,
                                  currency="USD", trade_mode="DEMO", leverage=100, balance=10000.0, equity=10000.0,
                                  margin=0.0, margin_free=10000.0))

    def notes(self):
        with self.approvals.repo.read() as s:
            return [(n.event_type, n.priority, n.title) for n in s.execute(select(Notification)).scalars()]


@pytest.fixture
def w(h, monkeypatch):
    return W(h, monkeypatch)


def set_cfg(h, **changes):
    cfg = h.svc.config().dump()
    cfg["health_watchdog"].update(changes)
    h.svc.put_config(ALICE, {"reason": "test", "config": cfg})


def test_market_hours():
    assert market_open(NOW) is True                                          # Monday noon
    assert market_open(NOW.replace(day=14)) is False                         # Saturday
    assert market_open(NOW.replace(day=15, hour=21)) is False and market_open(NOW.replace(day=15, hour=22)) is True
    assert market_open(NOW.replace(day=13, hour=21, minute=59)) is True and market_open(NOW.replace(day=13, hour=22)) is False


# ---- the five kinds of status ------------------------------------------------------------------------------------------------------
def test_a_healthy_platform_is_recorded_without_an_alert(w):
    out = w.wd.evaluate(run_id="run-1")
    assert out["status"] == "recorded" and out["overall"] == "HEALTHY" and out["unhealthy"] == [] and out["notified"] == 0, out
    st = out["statuses"]
    assert st["n8n"] == st["fastapi"] == st["database"] == st["mt5_data"] == "ONLINE"
    assert all(st[m] == "ONLINE" for m, _ in hw.MODULES)
    assert st["module_09"] == "NOT_DEPLOYED" and st["ai_provider"] == "NOT_USED"
    mt5 = next(s for s in out["services"] if s["service"] == "mt5_data")
    assert mt5["last_candle_at"] and mt5["last_success_at"] and "3 min ago" in mt5["detail"]     # last update, not "answered"
    assert w.notes() == []


def test_stale_data_offline_and_degraded_services_are_told_apart_and_alerted_once(w):
    w.h.clock.advance(hours=2)                                               # the collector stopped two hours ago
    w.modules["module_02"] = ("UNAVAILABLE", "Module 02 - COT Intelligence: ConnectError: the request was not delivered")
    w.modules["module_04"] = ("DEGRADED", "Module 04 reports one dependency failing")
    w.h.n8n.healthy = EngineHealth("DEGRADED", "n8n is up; the public API key is not configured, so execution status is unavailable.")
    out = w.wd.evaluate(run_id="run-1")
    st = out["statuses"]
    assert (st["mt5_data"], st["module_02"], st["module_04"], st["n8n"]) == ("STALE", "OFFLINE", "DEGRADED", "DEGRADED")
    assert out["overall"] == "UNHEALTHY" and sorted(out["newly_unhealthy"]) == sorted(["mt5_data", "module_02", "module_04", "n8n"])
    assert "Credentials missing: N8N_API_KEY" in next(s for s in out["services"] if s["service"] == "n8n")["detail"]
    assert "Responding but stale" in next(s for s in out["services"] if s["service"] == "mt5_data")["detail"]
    (etype, prio, title), = w.notes()
    assert etype == "orchestration.health_alert" and prio == "high" and "MT5 data service" in title
    # The next run with the same problems alerts nobody again.
    again = w.wd.evaluate(run_id="run-2")
    assert again["newly_unhealthy"] == [] and again["notified"] == 0 and len(w.notes()) == 1
    # Data flows again and Module 02 answers: recorded as recovered, without an alert.
    w.store(minutes_ago=1)
    w.modules["module_02"] = ("HEALTHY", "ok")
    third = w.wd.evaluate(run_id="run-3")
    assert sorted(third["recovered"]) == ["module_02", "mt5_data"] and third["notified"] == 0


def test_market_closed_is_not_stale(w):
    w.h.clock.advance(days=5)                                                # Saturday: last candle five days old
    out = w.wd.evaluate()
    assert out["statuses"]["mt5_data"] == "MARKET_CLOSED" and out["overall"] == "HEALTHY"


def test_missing_credentials_are_their_own_status(w, monkeypatch):
    w.modules["module_07"] = ("UNKNOWN", "Module 07 - Human Approval needs a service token: set ORCHESTRATION_SERVICE_TOKEN.")
    monkeypatch.setattr(w.h.svc.settings, "ai_review_enabled", True, raising=False)
    monkeypatch.setattr(w.h.svc.settings, "anthropic_api_key", None, raising=False)
    out = w.wd.evaluate()
    assert out["statuses"]["module_07"] == "NO_CREDENTIALS" and out["statuses"]["ai_provider"] == "NO_CREDENTIALS"
    assert [u["service"] for u in out["unhealthy"]] == ["module_07"]        # the AI review is optional: reported, not counted
    assert out["overall"] == "UNHEALTHY"


def test_an_unreachable_internal_address_on_a_serverless_host_is_explained(w, monkeypatch):
    from aurum.orchestration.db_models import OrchIntegration
    monkeypatch.setenv("VERCEL", "1")
    w.modules["module_01"] = ("UNAVAILABLE", "Module 01 - Market Data: ConnectError: the request was not delivered")
    with w.h.svc.repo.write() as s:
        row = s.get(OrchIntegration, "module_01")
        if row is not None:
            row.base_url = "http://127.0.0.1:8000/api/v1"
    r = w.wd.check("module_01", dry_run=True)
    assert r["status"] == "OFFLINE" and "AURUM_INTERNAL_BASE_URL" in r["detail"]


# ---- the workflow's run --------------------------------------------------------------------------------------------------------
def test_the_run_checks_each_service_once_and_evaluates_its_own_checks(w):
    run = str(uuid.uuid4())
    for item in w.wd.services()["items"]:
        assert w.wd.check(item["service"], run_id=run)["status"] in hw.SEVERITY
    assert sorted(w.calls) == sorted([m for m, _ in hw.MODULES] + ["module_09"])
    out = w.wd.evaluate(run_id=run)
    assert out["overall"] == "HEALTHY" and len(w.calls) == 8                 # nothing checked twice
    with w.h.svc.repo.read() as s:
        rows = s.execute(select(OrchAuditLog.action).where(OrchAuditLog.entity_type == hw.AUDIT_ENTITY)).scalars().all()
    assert rows.count(hw.AUDIT_CHECK) == len(hw.SERVICES) and rows.count(hw.AUDIT_EVAL) == 1
    assert w.wd.check("no-such-service")["status"] == "skipped"


def test_dry_run_and_the_off_switch(w):
    w.h.clock.advance(hours=2)
    dry = w.wd.evaluate(dry_run=True)
    assert dry["status"] == "dry_run" and dry["overall"] == "DEGRADED" and w.notes() == []
    with w.h.svc.repo.read() as s:
        assert s.execute(select(OrchAuditLog).where(OrchAuditLog.action == hw.AUDIT_EVAL)).first() is None
    set_cfg(w.h, notify_dashboard=False)
    assert w.wd.evaluate()["notified"] == 0 and w.notes() == []
    set_cfg(w.h, enabled=False)
    assert w.wd.preflight()["proceed"] is False and w.wd.services()["count"] == 0
    assert w.wd.evaluate()["status"] == "skipped" and w.wd.check("database")["status"] == "skipped"


# ---- the worker keeps its health interval across fresh workers ---------------------------------------------------------------
def test_worker_health_interval_holds_for_a_fresh_worker_per_tick(h, monkeypatch):
    from aurum.orchestration.worker import Worker
    cfg = h.svc.config().dump()
    cfg["health"]["interval_seconds"] = 900
    h.svc.put_config(ALICE, {"reason": "test", "config": cfg})
    ran = []
    monkeypatch.setattr(h.svc, "check_all", lambda: ran.append(1) or {})
    monkeypatch.setattr(h.svc, "evaluate_workflow_health", lambda: {})
    Worker(h.svc).tick()                                                     # nothing stored yet: due
    with h.svc.repo.write() as s:
        s.add(OrchHealthCheck(integration_id="database", checked_at=h.clock(), status="HEALTHY", latency_ms=1.0))
    h.clock.advance(minutes=1)
    assert "health" not in Worker(h.svc).tick()                              # a fresh worker, one minute later: not due
    h.clock.advance(minutes=15)
    assert "health" in Worker(h.svc).tick() and len(ran) == 2


# ---- installation and HTTP API --------------------------------------------------------------------------------------------------
def test_install_registers_the_workflow_with_its_schedule(h):
    h.n8n.workflows["n8nWF0809"] = {"id": "n8nWF0809", "active": True, "webhook_paths": [hw.WEBHOOK_PATH]}
    out = install(h.svc, ALICE, "n8nWF0809", schedule="7 */2 * * *")
    assert out["state"] == "ACTIVE" and "registered" in out["steps"] and out["schedule"]["expression"] == "7 */2 * * *"
    assert out["subscriptions"] == []
    again = install(h.svc, ALICE, "n8nWF0809")
    assert "registered" not in again["steps"] and any(s.startswith("schedule already present") for s in again["steps"])


def test_api_routes_and_who_may_check(w, monkeypatch):
    from aurum.api.app import create_app
    from aurum.config import Settings
    monkeypatch.setattr(HealthWatchdog, "approvals", property(lambda self: w.approvals))
    api = TestClient(create_app(settings=Settings(_env_file=None, database_url="sqlite://"), engine=w.h.engine,
                                auth_service=w.auth, orchestration_service=w.h.svc))
    assert api.get(f"{P}/health-watchdog/services").status_code == 401
    token, _ = w.auth.create_service_token("n8n-wf0809", "test", ["orchestration:view", "orchestration:callback"])
    hdr = {"Authorization": f"Bearer {token}"}
    assert api.get(f"{P}/health-watchdog/config", headers=hdr).json()["services"][0] == "n8n"
    assert api.get(f"{P}/health-watchdog/preflight", headers=hdr).json()["proceed"] is True
    assert api.get(f"{P}/health-watchdog/services", headers=hdr).json()["count"] == len(hw.SERVICES)
    ro, _ = w.auth.create_service_token("read-only", "test", ["orchestration:view"])
    assert api.post(f"{P}/health-watchdog/check", json={"service": "database"}, headers={"Authorization": f"Bearer {ro}"}).status_code == 403
    assert api.post(f"{P}/health-watchdog/check", json={}, headers=hdr).status_code == 422
    r = api.post(f"{P}/health-watchdog/check", json={"service": "database", "executionId": "run-9"}, headers=hdr)
    assert r.status_code == 200 and r.json()["status"] == "ONLINE", r.text
    r = api.post(f"{P}/health-watchdog/evaluate", json={"executionId": "run-9"}, headers=hdr)
    assert r.status_code == 200 and r.json()["overall"] == "HEALTHY", r.text
    for path in ("restart", "repair", "fix"):                                # it reads and reports only
        assert api.post(f"{P}/health-watchdog/{path}", json={}, headers=hdr).status_code in (404, 405)
