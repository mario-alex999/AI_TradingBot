"""WF08-07 Trade Monitoring Orchestrator: what is watched, which changes are recorded, notifications, API, installation.

Docx test stage covered here: (9) open positions are followed from Module 07's records; each change (opened, levels
changed, volume changed, closed, no stop-loss, stale data, risk lock) is recorded once and announced on the dashboard;
an unchanged position records nothing; no trade action is ever taken.

Module 07 is real here (its service, position records and notifications). The position source and Module 06 are stand-ins.
"""
from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from m08_harness import ALICE
from test_wf08_04 import RiskGateway, _Settings

from aurum.approvals.db_models import ManagedPosition, Notification, TradeManagementRequest
from aurum.approvals.service import ApprovalService
from aurum.auth.service import AuthService
from aurum.orchestration import trade_monitoring as tm
from aurum.orchestration.db_models import OrchAuditLog
from aurum.orchestration.trade_monitoring import TradeMonitor, install
from sqlalchemy import select

P = "/api/v1/orchestration"


class Source:
    """The position source as Module 07 sees it (Module 09, or the account mirror)."""
    name = "account_mirror"

    def __init__(self, clock):
        self.clock, self.rows, self.as_of, self.fail = clock, {}, None, False

    def put(self, pid="7001", **over):
        self.rows[pid] = {"position_id": pid, "account_login": "1001", "symbol": "EURUSD", "side": "BUY", "volume": Decimal("0.10"),
                          "entry_price": Decimal("1.1000"), "current_price": Decimal("1.1010"), "stop_loss": Decimal("1.0950"),
                          "take_profit": Decimal("1.1100"), "unrealized_pnl": Decimal("10"), "realized_pnl": None,
                          "status": "OPEN", "opened_at": self.clock(), **over}
        return pid

    def positions(self, now):
        if self.fail:
            raise RuntimeError("source down")
        return list(self.rows.values()), (self.as_of or self.clock())


class Risk(RiskGateway):
    locks: list = []

    def active_locks(self, symbol=None, strategy=None):
        return list(self.locks)


class W:
    def __init__(self, h):
        self.h = h
        self.auth = AuthService(h.engine, iterations=1000, clock=h.clock)
        for name, role in (("alice", "admin"), ("tom", "trader")):
            self.auth.create_user(name, "correct-horse-battery-9", role, name.title(), f"{name}@example.test")
        self.src, self.risk = Source(h.clock), Risk(h.clock)
        self.approvals = ApprovalService(h.engine, _Settings(), self.risk, None, None, self.src, clock=h.clock)
        self.mon = TradeMonitor(h.svc, approvals=lambda: self.approvals, deliver=lambda a: {"sent": 0},
                                module_09=lambda: {"deployed": False, "status": "UNKNOWN", "detail": "not deployed"})

    def run(self, **kw):
        """One monitoring pass, as the workflow does it: list, then record each."""
        listed = self.mon.open_trades()
        return [self.mon.record(i["position_id"], **kw) for i in listed["items"]]

    def audits(self):
        with self.h.svc.repo.read() as s:
            rows = s.execute(select(OrchAuditLog).where(OrchAuditLog.action == tm.AUDIT_ACTION).order_by(OrchAuditLog.seq)).scalars().all()
            return [(r.entity_id, [e["type"] for e in r.details["events"]]) for r in rows]

    def notes(self):
        with self.approvals.repo.read() as s:
            return [(n.event_type, n.priority, n.position_id) for n in
                    s.execute(select(Notification).order_by(Notification.created_at, Notification.id)).scalars()]


@pytest.fixture
def w(h):
    return W(h)


def set_cfg(h, **changes):
    cfg = h.svc.config().dump()
    cfg["trade_monitoring"].update(changes)
    h.svc.put_config(ALICE, {"reason": "test", "config": cfg})


# ---- following a position ---------------------------------------------------------------------------------------------------------
def test_a_position_is_recorded_when_it_opens_and_then_only_when_something_changes(w):
    assert w.mon.open_trades() == {"enabled": True, "items": [], "count": 0, "deferred": 0, "open": 0,
                                   "feed": {"source": "account_mirror", "as_of": w.mon.open_trades()["feed"]["as_of"],
                                            "stale": False, "error": None}}
    pid = w.src.put()
    listed = w.mon.open_trades()
    assert [i["position_id"] for i in listed["items"]] == [pid] and listed["open"] == 1
    st = w.mon.status(pid)
    assert st["ok"] and st["previous"] is None and [e["type"] for e in st["events"]] == ["position_opened"]
    assert st["linked_to_approval"] is False and "not linked to an AURUM approval" in st["events"][0]["message"]
    assert st["stop_loss"] == "1.095" and st["stale"] is False and st["risk_lock"] == {"active": False, "reasons": []}
    out, = w.run(execution_id="exec-1")
    assert out["status"] == "recorded" and out["event_types"] == ["position_opened"] and out["notified"] == 1
    assert w.notes() == [("position.position_opened", "normal", pid)]
    # Price and profit move, nothing else: unchanged, nothing recorded, nobody notified.
    w.src.put(current_price=Decimal("1.1055"), unrealized_pnl=Decimal("55"))
    again, = w.run()
    assert again["status"] == "unchanged" and len(w.audits()) == 1 and len(w.notes()) == 1
    # The stop is moved and half is closed (by a person, in the broker terminal).
    w.src.put(stop_loss=Decimal("1.1000"), volume=Decimal("0.05"))
    out, = w.run()
    assert out["event_types"] == ["levels_changed", "volume_changed"] and out["notified"] == 2 and out["attention"] is False
    assert "1.095 -> 1.1" in out["events"][0]["message"] and "0.1 -> 0.05" in out["events"][1]["message"]
    # It closes: gone from the source, Module 07 marks it CLOSED, the close is recorded once.
    w.src.rows.clear()
    out, = w.run()
    assert out["status"] == "recorded" and out["event_types"] == ["position_closed"] and out["position_status"] == "CLOSED"
    assert "not reported by the position source" in out["events"][0]["message"]       # no guessed stop-loss / take-profit
    assert w.run() == [] and w.mon.open_trades()["count"] == 0
    assert w.audits() == [(pid, ["position_opened"]), (pid, ["levels_changed", "volume_changed"]), (pid, ["position_closed"])]


def test_risk_and_data_events_are_raised_once_and_cleared(w):
    pid = w.src.put(stop_loss=None)
    out, = w.run()
    assert out["event_types"] == ["position_opened", "stop_loss_missing"] and out["attention"] is True
    assert ("position.stop_loss_missing", "high", pid) in w.notes()
    # The feed stops updating (collector or broker connection down).
    w.src.as_of = w.h.clock()
    w.h.clock.advance(minutes=20)
    out, = w.run()
    assert out["event_types"] == ["data_stale"] and out["stale"] is True and "20 min ago" in out["events"][0]["message"]
    w.h.clock.advance(minutes=20)
    assert w.run()[0]["status"] == "unchanged"                            # still stale: said once
    w.src.as_of = None
    out, = w.run()
    assert out["event_types"] == ["data_fresh"] and out["notified"] == 0  # recorded, not announced
    # Module 06 raises a risk lock for the instrument, then releases it.
    w.risk.locks = [{"reason": "Daily loss limit reached", "scope": "account"}]
    out, = w.run()
    assert out["event_types"] == ["risk_lock_active"] and "Daily loss limit reached" in out["events"][0]["message"]
    w.risk.locks = []
    assert w.run()[0]["event_types"] == ["risk_lock_released"]
    # An unknown state from the source is flagged, not interpreted.
    w.src.put(stop_loss=None, status="REQUOTE_PENDING")
    out, = w.run()
    assert out["event_types"] == ["unexpected_state"] and out["attention"] is True
    # The source itself fails: the run still lists the stored record and says so.
    w.src.fail = True
    assert "Position source unavailable" in w.mon.open_trades()["feed"]["error"]


def test_no_trade_action_is_ever_taken(w):
    pid = w.src.put(stop_loss=None)
    w.run()
    w.src.put(stop_loss=None, volume=Decimal("0.20"))
    w.run()
    with w.approvals.repo.read() as s:
        assert s.execute(select(TradeManagementRequest)).first() is None  # no stop moved, nothing closed or requested
        row = s.get(ManagedPosition, pid)
        assert row.stop_loss is None and row.source == "account_mirror" and row.status == "OPEN"
    import inspect
    code = inspect.getsource(tm).split('"""', 2)[-1]
    for banned in ("order_send", "MetaTrader", "create_management_request", "approve_management", "report_execution",
                   "requests.", "httpx"):
        assert banned not in code, banned


def test_dry_run_unknown_position_named_run_and_the_off_switch(w):
    pid = w.src.put()
    w.mon.open_trades()
    dry = w.mon.record(pid, dry_run=True)
    assert dry["status"] == "dry_run" and dry["event_types"] == ["position_opened"] and w.audits() == [] and w.notes() == []
    assert w.mon.record("no-such-position")["status"] == "skipped"
    named = w.mon.open_trades(["no-such-position", pid])
    assert [(i["position_id"], i["status"]) for i in named["items"]] == [("no-such-position", None), (pid, "OPEN")]
    set_cfg(w.h, notify_dashboard=False)
    assert w.mon.record(pid)["notified"] == 0 and len(w.audits()) == 1 and w.notes() == []    # recorded only
    set_cfg(w.h, enabled=False)
    assert w.mon.preflight()["proceed"] is False and w.mon.open_trades()["count"] == 0
    w.src.put(volume=Decimal("0.30"))
    assert w.mon.record(pid)["status"] == "skipped" and len(w.audits()) == 1


# ---- installation -------------------------------------------------------------------------------------------------------------
def test_install_registers_the_workflow_with_its_schedule_and_events(h):
    h.n8n.workflows["n8nWF0807"] = {"id": "n8nWF0807", "active": True, "webhook_paths": [tm.WEBHOOK_PATH]}
    out = install(h.svc, ALICE, "n8nWF0807", schedule="41 */4 * * 1-5")
    assert out["state"] == "ACTIVE" and "registered" in out["steps"] and out["schedule"]["expression"] == "41 */4 * * 1-5"
    assert sorted(s["event_pattern"] for s in out["subscriptions"]) == sorted(tm.TRIGGER_EVENTS)
    again = install(h.svc, ALICE, "n8nWF0807")
    assert "registered" not in again["steps"] and any(s.startswith("schedule already present") for s in again["steps"])
    assert len([x for x in h.svc.list_schedules()["items"] if x["workflow_id"] == tm.WORKFLOW_ID]) == 1
    wf = h.svc.get_workflow(tm.WORKFLOW_ID)
    assert wf["category"] == "other" and "schedule" in wf["trigger_types"]
    x, _ = h.svc.trigger(ALICE, {"workflow_id": tm.WORKFLOW_ID, "trigger_type": "admin", "input": {"dryRun": True},
                                 "idempotency_key": f"t:{uuid.uuid4()}"})
    assert x["status"] == "QUEUED"                                        # a manual run needs no approval: it only reads


# ---- HTTP API -----------------------------------------------------------------------------------------------------------------
def test_api_routes_who_may_record_and_no_trade_endpoint(w, monkeypatch):
    from aurum.api.app import create_app
    from aurum.config import Settings
    monkeypatch.setattr(TradeMonitor, "approvals", property(lambda self: w.approvals))
    monkeypatch.setattr(TradeMonitor, "_send", lambda self: {"sent": 0})
    api = TestClient(create_app(settings=Settings(_env_file=None, database_url="sqlite://"), engine=w.h.engine,
                                auth_service=w.auth, orchestration_service=w.h.svc))
    pid = w.src.put()
    assert api.get(f"{P}/trade-monitoring/open-trades").status_code == 401
    token, _ = w.auth.create_service_token("n8n-wf0807", "test", ["orchestration:view", "orchestration:callback"])
    hdr = {"Authorization": f"Bearer {token}"}
    cfg = api.get(f"{P}/trade-monitoring/config", headers=hdr).json()
    assert cfg["triggerEvents"] == list(tm.TRIGGER_EVENTS) and cfg["module09"]["deployed"] is False
    assert api.get(f"{P}/trade-monitoring/preflight", headers=hdr).json()["proceed"] is True
    listed = api.get(f"{P}/trade-monitoring/open-trades", headers=hdr).json()
    assert listed["items"][0]["positionId"] == pid and listed["feed"]["source"] == "account_mirror"
    st = api.get(f"{P}/trade-monitoring/status", params={"positionId": pid}, headers=hdr).json()
    assert st["ok"] is True and st["events"][0]["type"] == "position_opened", st
    ro, _ = w.auth.create_service_token("read-only", "test", ["orchestration:view"])
    assert api.post(f"{P}/trade-monitoring/record", json={"positionId": pid}, headers={"Authorization": f"Bearer {ro}"}).status_code == 403
    assert api.post(f"{P}/trade-monitoring/record", json={}, headers=hdr).status_code == 422
    r = api.post(f"{P}/trade-monitoring/record", json={"positionId": pid, "executionId": "exec-7"}, headers=hdr)
    assert r.status_code == 200 and r.json()["status"] == "recorded" and r.json()["eventTypes"] == ["position_opened"], r.text
    assert api.post(f"{P}/trade-monitoring/record", json={"positionId": pid}, headers=hdr).json()["status"] == "unchanged"
    for path in ("close", "modify", "move-stop", "order"):               # nothing here can act on a trade
        assert api.post(f"{P}/trade-monitoring/{path}", json={"positionId": pid}, headers=hdr).status_code in (404, 405)


def test_real_wiring_on_an_empty_database(h):
    mon = TradeMonitor(h.svc)
    pre = mon.preflight()
    assert pre["proceed"] is True and pre["checks"]["module_07_schema"] and pre["checks"]["module_09_deployed"] is False, pre
    out = mon.open_trades()
    assert out["count"] == 0 and out["items"] == [] and out["feed"]["source"] in ("account_mirror", "none")
    assert mon.status("x")["exists"] is False and mon._last(["x"]) == {}
