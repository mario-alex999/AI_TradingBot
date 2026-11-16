"""WF08-10 News and COT Update Scheduler: when each source is due, refresh, timestamp verification, alerts, API, install.

Docx test stage covered here: (12) COT reports are refreshed according to their publication schedule, not every few
minutes; the economic calendar is refreshed according to source frequency and event importance; each refresh is
verified by the stored data's own timestamps and its status recorded once.

The Module 08 core and Module 07's notifications are real here. Modules 02 and 04 are a controlled stand-in, except in
the last tests, which use the real adapter against the test database (no network).
"""
from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from m08_harness import ALICE
from test_wf08_04 import RiskGateway, _Settings

from aurum.approvals.db_models import Notification
from aurum.approvals.service import ApprovalService
from aurum.auth.service import AuthService
from aurum.orchestration import information_updates as iu
from aurum.orchestration.information_updates import InformationUpdates, ModuleSources, install
from sqlalchemy import select

P = "/api/v1/orchestration"


class Sources:
    """Stand-in for Modules 02 and 04: what is configured, the CFTC schedule state, refresh results, stored timestamps."""

    def __init__(self):
        self.avail = {k: True for k in iu.NAMES}
        self.cot = {f: "current" for f, _ in iu.COT_FAMILIES}
        self.soon: list[str] = []
        self.results: dict[str, dict] = {}
        self.state: dict[str, str] = {}
        self.refreshed: list[str] = []

    def available(self, task):
        return self.avail[task], ("configured" if self.avail[task] else f"{task} is switched off")

    def high_impact_soon(self, now, hours):
        return self.soon

    def cot_schedule(self, family, now):
        return {"status": self.cot[family], "series": [], "next_expected_release": "2026-11-13T20:30:00Z"}

    def refresh(self, task, backfill_years):
        self.refreshed.append(task)
        res = self.results.get(task, {"status": "ok", "changed": 3, "detail": "3 new."})
        if isinstance(res, Exception):
            raise res
        return res

    def timestamps(self, task, now, c):
        if task.startswith("cot_"):
            st = {"current": "fresh", "update_due": "waiting"}.get(self.cot[task[4:]], "stale")
            return {"state": self.state.get(task, st), "latest_data_at": "2026-11-03", "detail": "COT timestamps."}
        return {"state": self.state.get(task, "fresh"), "latest_data_at": now.isoformat(), "detail": f"{task} timestamps."}


class W:
    def __init__(self, h):
        self.h, self.src = h, Sources()
        self.auth = AuthService(h.engine, iterations=1000, clock=h.clock)
        self.auth.create_user("alice", "correct-horse-battery-9", "admin", "Alice", "alice@example.test")
        self.approvals = ApprovalService(h.engine, _Settings(), RiskGateway(h.clock), None, None, None, clock=h.clock)
        self.u = InformationUpdates(h.svc, sources=self.src, approvals=lambda: self.approvals, deliver=lambda a: {"sent": 0})

    def due(self):
        return [i["task"] for i in self.u.sources()["items"]]

    def cycle(self, task, run_id="run-1"):
        res = self.u.run(task, run_id=run_id)
        return self.u.record(task, run_id=run_id, result=res)

    def notes(self):
        with self.approvals.repo.read() as s:
            return [(n.event_type, n.title) for n in s.execute(select(Notification)).scalars()]


@pytest.fixture
def w(h):
    return W(h)


def set_cfg(h, **changes):
    cfg = h.svc.config().dump()
    cfg["information_updates"].update(changes)
    h.svc.put_config(ALICE, {"reason": "test", "config": cfg})


# ---- when each source is due ----------------------------------------------------------------------------------------------
def test_cot_follows_the_cftc_publication_schedule_not_the_clock(w):
    assert not any(t.startswith("cot_") for t in w.due())                   # all current: nothing to fetch
    row = next(r for r in w.u.sources()["tasks"] if r["task"] == "cot_legacy")
    assert row["due"] is False and row["next_due_at"] == "2026-11-13T20:30:00Z" and "CFTC schedule" in row["reason"]
    w.src.cot["legacy"] = "update_due"                                       # Friday's report should now be public
    assert "cot_legacy" in w.due()
    out = w.cycle("cot_legacy")
    assert out["outcome"] == "waiting" and out["notified"] == 0              # CFTC late: not an alert
    w.h.clock.advance(hours=1)
    assert "cot_legacy" not in w.due()                                       # not retried every few minutes
    w.h.clock.advance(hours=2, minutes=1)
    assert "cot_legacy" in w.due()                                           # retried after cot_retry_hours
    w.src.cot["legacy"] = "current"
    assert w.cycle("cot_legacy", "run-2")["outcome"] == "updated"
    w.src.cot["tff"] = "no_data"
    assert "cot_tff" in w.due() and "No COT report" in next(i for i in w.u.sources()["items"] if i["task"] == "cot_tff")["reason"]


def test_the_calendar_is_refreshed_more_often_when_a_high_importance_event_is_near(w):
    assert {"calendar", "news", "macro"} <= set(w.due())                     # never refreshed by WF08-10
    for t in ("calendar", "news", "macro"):
        assert w.cycle(t)["outcome"] == "updated"
    assert not {"calendar", "news", "macro"} & set(w.due())
    w.h.clock.advance(minutes=61)
    assert "news" in w.due() and "calendar" not in w.due()                   # news hourly, calendar every 6 h
    w.src.soon = ["USD Nonfarm Payrolls at 2026-11-09T13:30:00Z"]
    row = next(r for r in w.u.sources()["tasks"] if r["task"] == "calendar")
    assert row["due"] is True and "high-importance" in row["reason"] and row["high_impact_soon"]
    w.src.soon = []
    w.h.clock.advance(hours=5)
    assert "calendar" in w.due() and "macro" not in w.due()                  # 6 h for the calendar, 24 h for macro


def test_a_source_that_is_not_configured_is_not_run(w):
    w.src.avail["news"] = False
    out = w.u.sources()
    assert "news" not in [i["task"] for i in out["items"]]
    assert any(n["task"] == "news" and "switched off" in n["reason"] for n in out["not_due"])
    assert "news" not in w.u.sources(force=True)["items"]                     # force does not run an unconfigured source
    assert [i["task"] for i in w.u.sources(["macro"])["items"]] == ["macro"]


# ---- refresh, verification and recording ---------------------------------------------------------------------------------
def test_outcomes_come_from_the_stored_timestamps(w):
    w.src.results["news"] = {"status": "ok", "changed": 0, "detail": "Nothing new."}
    out = w.cycle("news")
    assert out["outcome"] == "current" and out["verified"]["state"] == "fresh" and "news timestamps" in out["detail"]
    w.src.state["macro"] = "stale"                                           # the refresh "worked" but the data is old
    out = w.cycle("macro")
    assert out["outcome"] == "stale" and out["notified"] == 1
    assert ("orchestration.information_update", "Central-bank and macro data is out of date") in w.notes()


def test_a_failing_source_is_alerted_once_retried_later_and_its_recovery_recorded(w):
    w.src.results["calendar"] = RuntimeError("calendar host unreachable")
    out = w.cycle("calendar")
    assert out["outcome"] == "failed" and out["notified"] == 1 and "unreachable" in out["detail"]
    assert "calendar" not in w.due()                                          # retry_after_minutes
    w.h.clock.advance(minutes=31)
    assert "calendar" in w.due()
    again = w.cycle("calendar", "run-2")
    assert again["outcome"] == "failed" and again["notified"] == 0 and not again["newly_failing"]
    w.src.results.pop("calendar")
    ok = w.cycle("calendar", "run-3")
    assert ok["outcome"] == "updated" and ok["recovered"] is True
    assert len([n for n in w.notes() if "calendar" in n[1].lower()]) == 1


def test_dry_run_and_the_off_switch(w):
    assert w.u.run("news", run_id="d", dry_run=True)["status"] == "dry_run" and w.src.refreshed == []
    assert w.u.record("news", run_id="d", dry_run=True, result={"status": "ok"})["status"] == "dry_run"
    assert "news" in w.due()                                                  # a dry run records nothing
    set_cfg(w.h, enabled=False)
    assert w.u.preflight()["proceed"] is False and w.u.sources()["count"] == 0
    assert w.u.run("news")["status"] == "skipped" and w.src.refreshed == []
    assert w.u.run("unknown")["status"] == "skipped"


# ---- the real adapter (Modules 02 and 04 against the test database; nothing is downloaded) ---------------------------------
def test_real_sources_report_configuration_schedule_and_timestamps(h):
    src = ModuleSources(h.engine, h.svc.settings)
    for task in iu.NAMES:
        ok, why = src.available(task)
        assert isinstance(ok, bool) and why
    sch = src.cot_schedule("legacy", h.clock())
    assert sch["status"] == "no_data" and sch["series"]                       # nothing stored in the test database
    c = h.svc.config().information_updates
    assert src.timestamps("cot_legacy", h.clock(), c)["state"] == "stale"
    for task in ("calendar", "news", "macro"):
        ts = src.timestamps(task, h.clock(), c)
        assert ts["state"] == "stale" and "never" in ts["detail"]
    u = InformationUpdates(h.svc, sources=src)
    assert {i["task"] for i in u.sources()["items"]} >= {"cot_legacy", "calendar"}


# ---- installation and HTTP API --------------------------------------------------------------------------------------------------
def test_install_registers_the_workflow_with_its_schedule(h):
    h.n8n.workflows["n8nWF0810"] = {"id": "n8nWF0810", "active": True, "webhook_paths": [iu.WEBHOOK_PATH]}
    out = install(h.svc, ALICE, "n8nWF0810")
    assert out["state"] == "ACTIVE" and "registered" in out["steps"] and out["schedule"]["expression"] == "17 * * * *"
    again = install(h.svc, ALICE, "n8nWF0810")
    assert "registered" not in again["steps"] and any(s.startswith("schedule already present") for s in again["steps"])


def test_api_routes_and_who_may_run(w, monkeypatch):
    from aurum.api.app import create_app
    from aurum.config import Settings
    real_init = InformationUpdates.__init__

    def init(self, svc, sources=None, approvals=None, deliver=None):
        real_init(self, svc, sources=w.src, approvals=lambda: w.approvals, deliver=lambda a: {"sent": 0})
    monkeypatch.setattr(InformationUpdates, "__init__", init)
    api = TestClient(create_app(settings=Settings(_env_file=None, database_url="sqlite://"), engine=w.h.engine,
                                auth_service=w.auth, orchestration_service=w.h.svc))
    assert api.get(f"{P}/information-updates/sources").status_code == 401
    token, _ = w.auth.create_service_token("n8n-wf0810", "test", ["orchestration:view", "orchestration:callback"])
    hdr = {"Authorization": f"Bearer {token}"}
    assert api.get(f"{P}/information-updates/config", headers=hdr).json()["tasks"][0] == "calendar"
    assert api.get(f"{P}/information-updates/preflight", headers=hdr).json()["proceed"] is True
    src = api.get(f"{P}/information-updates/sources", headers=hdr).json()
    assert {"calendar", "news", "macro"} <= {i["task"] for i in src["items"]} and "notDue" in src
    ro, _ = w.auth.create_service_token("read-only", "test", ["orchestration:view"])
    assert api.post(f"{P}/information-updates/run", json={"task": "news"},
                    headers={"Authorization": f"Bearer {ro}"}).status_code == 403
    assert api.post(f"{P}/information-updates/run", json={}, headers=hdr).status_code == 422
    r = api.post(f"{P}/information-updates/run", json={"task": "news", "executionId": "run-9"}, headers=hdr)
    assert r.status_code == 200 and r.json()["status"] == "ok", r.text
    r = api.post(f"{P}/information-updates/record", json={"task": "news", "executionId": "run-9", "result": r.json()}, headers=hdr)
    assert r.status_code == 200 and r.json()["outcome"] == "updated", r.text
    for path in ("execute", "orders", "trade"):                               # public information only
        assert api.post(f"{P}/information-updates/{path}", json={}, headers=hdr).status_code in (404, 405)
