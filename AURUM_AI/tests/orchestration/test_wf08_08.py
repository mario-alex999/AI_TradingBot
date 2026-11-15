"""WF08-08 Error Handling and Recovery: classification, what is retried, escalation, the n8n Error Trigger, API.

Docx test stage covered here: (10) a failed run is identified, classified and recorded once; a transient failure is
retried by the core within its limit and recorded as recovered when it then succeeds; a failure that is not safe to
retry (time-out with unknown outcome, retries used up, the execution hand-off) is escalated to the dashboard and never
retried by this workflow.

Module 08's core and Module 07's notifications are real here. n8n is the controlled test engine.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from m08_harness import ALICE
from test_wf08_04 import RiskGateway, _Settings

from aurum.approvals.db_models import Notification
from aurum.approvals.service import ApprovalService
from aurum.auth.service import AuthService
from aurum.orchestration import error_recovery as er
from aurum.orchestration.db_models import OrchAuditLog
from aurum.orchestration.error_recovery import ErrorRecovery, classify_engine_error, install
from sqlalchemy import select

P = "/api/v1/orchestration"
WID = "demo-flow"


class W:
    def __init__(self, h):
        self.h = h
        self.auth = AuthService(h.engine, iterations=1000, clock=h.clock)
        self.auth.create_user("alice", "correct-horse-battery-9", "admin", "Alice", "alice@example.test")
        self.approvals = ApprovalService(h.engine, _Settings(), RiskGateway(h.clock), None, None, None, clock=h.clock)
        self.rec = ErrorRecovery(h.svc, approvals=lambda: self.approvals, deliver=lambda a: {"sent": 0})
        h.register(WID)

    def start(self, wid=WID, **kw):
        """A run that n8n accepted and reported as started."""
        x, _ = self.h.trigger(None, {}, wid=wid, **kw)
        self.h.svc.dispatch_due()
        self.h.callback(x["execution_id"], "started", attempt=1, workflow_id=wid, n8nExecutionId=f"n8n-{uuid.uuid4().hex[:8]}")
        return x["execution_id"]

    def fail(self, eid, category, retryable=None, wid=WID, attempt=1):
        err = {"category": category, "message": f"{category} (test)"}
        if retryable is not None:
            err["retryable"] = retryable
        return self.h.callback(eid, "failed", attempt=attempt, workflow_id=wid, error=err)

    def status_of(self, eid):
        return self.h.svc.get_execution(eid)["status"]

    def audits(self, action=er.AUDIT_ACTION):
        with self.h.svc.repo.read() as s:
            rows = s.execute(select(OrchAuditLog).where(OrchAuditLog.action == action).order_by(OrchAuditLog.seq)).scalars().all()
            return [(r.entity_id, r.details.get("result"), r.outcome) for r in rows]

    def notes(self):
        with self.approvals.repo.read() as s:
            return [(n.event_type, n.priority, n.title) for n in s.execute(select(Notification)).scalars()]


@pytest.fixture
def w(h):
    return W(h)


def set_cfg(h, **changes):
    cfg = h.svc.config().dump()
    cfg["error_recovery"].update(changes)
    h.svc.put_config(ALICE, {"reason": "test", "config": cfg})


# ---- is a retry safe? ------------------------------------------------------------------------------------------------------------
def test_a_transient_failure_is_retried_by_the_core_and_recorded_as_recovered(w):
    eid = w.start()
    w.fail(eid, "DEPENDENCY_UNAVAILABLE", True)
    assert w.status_of(eid) == "RETRY_PENDING"                           # the core's retry, not this workflow's
    assert [i["execution_id"] for i in w.rec.pending()["items"]] == []     # a sweep lists finished failures; this one is retrying
    st = w.rec.status(eid)
    assert st["ok"] and st["state"] == "retry_scheduled" and st["retry_safe"] is True and st["error_kind"] == "transient", st
    assert st["workflow_id"] == WID and st["attempt"] == 1 and st["max_attempts"] >= 2
    out = w.rec.record(eid, run_id="run-1")
    assert out["status"] == "recorded" and out["result"] == "retry_scheduled" and out["escalated"] is False and out["notified"] == 0
    assert "Nothing to do" in out["next_step"] and w.notes() == []
    assert w.rec.record(eid)["status"] == "skipped"                      # once per state and attempt
    # The retry runs and succeeds.
    w.h.clock.advance(minutes=30)
    w.h.svc.dispatch_due()
    w.h.callback(eid, "started", attempt=2, workflow_id=WID)
    w.h.callback(eid, "completed", attempt=2, workflow_id=WID, output={})
    assert w.status_of(eid) == "SUCCEEDED"
    assert [i["execution_id"] for i in w.rec.pending()["items"]] == [eid]
    assert w.rec.record(eid)["result"] == "recovered"
    assert [r for _, r, _ in w.audits()] == ["retry_scheduled", "recovered"] and w.notes() == []
    assert w.rec.pending()["count"] == 0


def test_failures_that_are_not_safe_to_retry_are_escalated_and_never_retried_here(w):
    # 1. A permanent error goes straight to the dead-letter list.
    bad = w.start()
    w.fail(bad, "AUTH_FAILED")
    assert w.status_of(bad) == "DEAD_LETTERED"
    out = w.rec.record(bad)
    assert out["result"] == "escalated" and out["retry_safe"] is False and out["dead_letter_id"] and out["notified"] == 1
    assert "dead letter" in out["next_step"]
    # 2. A time-out: the outcome is unknown, so it is not sent again.
    lost = w.start()
    w.h.n8n.executions.pop(w.h.svc.get_execution(lost)["n8n_execution_id"], None)     # n8n cannot say what happened
    w.h.clock.advance(hours=3)
    w.h.svc.sweep_timeouts()
    assert w.status_of(lost) == "TIMED_OUT"
    out = w.rec.record(lost)
    assert out["result"] == "escalated" and out["retry_safe"] is False and "may still have run" in out["why"]
    assert "confirm the unknown outcome" in out["next_step"]
    assert w.status_of(lost) == "TIMED_OUT" and w.status_of(bad) == "DEAD_LETTERED"      # nothing was retried
    # 3. A business outcome is not a fault: recorded, not escalated.
    blocked = w.start()
    w.fail(blocked, "RISK_BLOCKED")
    out = w.rec.record(blocked)
    assert out["result"] == "closed" and out["escalated"] is False and out["notified"] == 0
    assert sorted(p for _, p, _ in w.notes()) == ["high", "high"]
    assert all(t == "orchestration.error_escalated" for t, _, _ in w.notes())
    assert [(r, o) for _, r, o in w.audits()] == [("escalated", "failed"), ("escalated", "failed"), ("closed", "failed")]


def test_unknown_run_healthy_run_dry_run_and_the_off_switch(w):
    assert w.rec.record("no-such-execution")["status"] == "skipped"
    ok = w.start()
    assert w.rec.status(ok)["state"] == "none" and w.rec.record(ok)["status"] == "skipped"       # running, no failure
    w.h.callback(ok, "completed", attempt=1, workflow_id=WID, output={})
    assert w.rec.record(ok)["status"] == "skipped" and w.rec.pending()["count"] == 0             # a plain success
    bad = w.start()
    w.fail(bad, "AUTH_FAILED")
    dry = w.rec.record(bad, dry_run=True)
    assert dry["status"] == "dry_run" and dry["result"] == "escalated" and w.audits() == [] and w.notes() == []
    assert [i["execution_id"] for i in w.rec.pending()["items"]] == [bad]
    set_cfg(w.h, notify_dashboard=False)
    assert w.rec.record(bad)["notified"] == 0 and w.notes() == [] and len(w.audits()) == 1       # recorded only
    set_cfg(w.h, enabled=False)
    assert w.rec.preflight()["proceed"] is False and w.rec.pending()["count"] == 0
    assert w.rec.report({"n8n_execution_id": "x", "message": "boom"})["status"] == "skipped"


# ---- the n8n Error Trigger -----------------------------------------------------------------------------------------------------
def test_classification_of_engine_errors():
    c = classify_engine_error
    assert c("connect ETIMEDOUT 10.0.0.1:443")[:2] == ("DEPENDENCY_UNAVAILABLE", True)
    assert c("Request failed with status code 503")[:2] == ("DEPENDENCY_UNAVAILABLE", True)
    assert c("429 Too Many Requests")[:2] == ("RATE_LIMITED", True)
    assert c("Authorization failed - 401")[:2] == ("AUTH_FAILED", False)
    assert c("Bad request - please check your parameters (422)")[:2] == ("INVALID_PAYLOAD", False)
    assert c("Cannot read properties of undefined")[:2] == ("WORKFLOW_ERROR", None)
    assert c("connect ETIMEDOUT", handoff=True)[:2] == ("OUTCOME_UNKNOWN", False)          # never retryable for a hand-off


def test_a_crash_reported_by_n8n_is_handed_to_the_core_at_once(w):
    eid = w.start()
    n8n_id = w.h.svc.get_execution(eid)["n8n_execution_id"]
    out = w.rec.report({"n8n_workflow_id": f"n8n-{WID}", "n8n_execution_id": n8n_id, "node": "Check system health",
                        "message": "connect ECONNREFUSED 127.0.0.1:8000"})
    assert out["matched"] is True and out["execution_id"] == eid and out["category"] == "DEPENDENCY_UNAVAILABLE", out
    assert out["result"] == "retry_scheduled" and out["retry_safe"] is True and w.status_of(eid) == "RETRY_PENDING"
    e = w.h.svc.get_execution(eid)
    assert e["error_category"] == "DEPENDENCY_UNAVAILABLE" and "Check system health" in e["error_message"]
    # Reported twice (n8n retried the error workflow): nothing changes the second time.
    again = w.rec.report({"n8n_workflow_id": f"n8n-{WID}", "n8n_execution_id": n8n_id, "message": "connect ECONNREFUSED"})
    assert again["result"] == "unchanged" and w.status_of(eid) == "RETRY_PENDING"
    # A refused request is not retried: dead-lettered.
    bad = w.start()
    out = w.rec.report({"n8n_execution_id": w.h.svc.get_execution(bad)["n8n_execution_id"], "message": "403 Forbidden"})
    assert out["result"] == "escalated" and out["retry_safe"] is False and w.status_of(bad) == "DEAD_LETTERED"
    # A crash that matches no tracked run is recorded and announced; nothing is retried.
    out = w.rec.report({"n8n_workflow_id": "some-other-workflow", "n8n_execution_id": "9999", "message": "boom"})
    assert out["matched"] is False and out["result"] == "escalated" and out["notified"] == 1
    assert len(w.audits(er.AUDIT_ENGINE)) == 4


def test_the_execution_handoff_is_never_classified_as_retryable(h):
    w = W(h)
    h.approval_status["ap-1"] = "EXECUTION_PENDING"
    h.register("handoff-flow", category="execution_handoff", triggers=["api", "event"])
    eid = w.start(wid="handoff-flow", approval_id="ap-1")
    n8n_id = h.svc.get_execution(eid)["n8n_execution_id"]
    out = w.rec.report({"n8n_workflow_id": "n8n-handoff-flow", "n8n_execution_id": n8n_id, "message": "connect ETIMEDOUT"})
    assert out["category"] == "OUTCOME_UNKNOWN" and out["retry_safe"] is False and out["result"] == "escalated"
    assert w.status_of(eid) == "DEAD_LETTERED"
    rec = w.rec.record(eid)
    assert rec["result"] == "escalated" and "nothing about a trade is retried" in rec["why"]
    assert ("orchestration.error_escalated", "critical") in [(t, p) for t, p, _ in w.notes()]


# ---- installation -------------------------------------------------------------------------------------------------------------
def test_install_and_no_run_is_started_by_its_own_failures(w):
    h = w.h
    h.n8n.workflows["n8nWF0808"] = {"id": "n8nWF0808", "active": True, "webhook_paths": [er.WEBHOOK_PATH]}
    out = install(h.svc, ALICE, "n8nWF0808")
    assert out["state"] == "ACTIVE" and "registered" in out["steps"] and len(out["subscriptions"]) == 3 + len(er.RECOVERY_ATTEMPTS)
    again = install(h.svc, ALICE, "n8nWF0808")
    assert "registered" not in again["steps"]
    assert len([s for s in h.svc.list_subscriptions()["items"] if s["workflow_id"] == er.WORKFLOW_ID]) == 7
    runs = lambda: h.svc.list_executions(workflow_id=er.WORKFLOW_ID)["items"]      # noqa: E731
    # Another workflow fails: one WF08-08 run, carrying the failed execution's id.
    eid = w.start()
    w.fail(eid, "AUTH_FAILED")
    h.svc.deliver_due()
    first = runs()
    assert len(first) == 2                                               # failed + dead_lettered
    ids = {h.svc.get_execution(r["execution_id"])["input"]["event"]["payload"]["execution_id"] for r in first}
    assert ids == {eid}
    # WF08-08's own run fails: that must not start another WF08-08 run.
    own = first[0]["execution_id"]
    h.svc.dispatch_due()
    h.callback(own, "failed", attempt=1, workflow_id=er.WORKFLOW_ID, error={"category": "AUTH_FAILED", "message": "x"})
    h.svc.deliver_due()
    h.svc.deliver_due()
    assert len(runs()) == 2
    assert w.rec.status(own)["ok"] is False and "its own runs" in w.rec.status(own)["reasons"][0]
    # A plain first-attempt success of another workflow starts nothing either.
    fine = w.start()
    h.callback(fine, "completed", attempt=1, workflow_id=WID, output={})
    h.svc.deliver_due()
    assert len(runs()) == 2


# ---- HTTP API -----------------------------------------------------------------------------------------------------------------
def test_api_routes_and_who_may_record_or_report(w, monkeypatch):
    from aurum.api.app import create_app
    from aurum.config import Settings
    monkeypatch.setattr(ErrorRecovery, "approvals", property(lambda self: w.approvals))
    monkeypatch.setattr(ErrorRecovery, "_send", lambda self: {"sent": 0})
    api = TestClient(create_app(settings=Settings(_env_file=None, database_url="sqlite://"), engine=w.h.engine,
                                auth_service=w.auth, orchestration_service=w.h.svc))
    eid = w.start()
    w.fail(eid, "AUTH_FAILED")
    assert api.get(f"{P}/error-recovery/pending").status_code == 401
    token, _ = w.auth.create_service_token("n8n-wf0808", "test", ["orchestration:view", "orchestration:callback"])
    hdr = {"Authorization": f"Bearer {token}"}
    assert api.get(f"{P}/error-recovery/config", headers=hdr).json()["triggerEvents"][0] == "orchestration.workflow.failed"
    assert api.get(f"{P}/error-recovery/preflight", headers=hdr).json()["proceed"] is True
    assert api.get(f"{P}/error-recovery/pending", headers=hdr).json()["items"][0]["executionId"] == eid
    st = api.get(f"{P}/error-recovery/status", params={"executionId": eid}, headers=hdr).json()
    assert st["ok"] is True and st["state"] == "escalated" and st["retrySafe"] is False, st
    ro, _ = w.auth.create_service_token("read-only", "test", ["orchestration:view"])
    rohdr = {"Authorization": f"Bearer {ro}"}
    assert api.post(f"{P}/error-recovery/record", json={"targetExecutionId": eid}, headers=rohdr).status_code == 403
    assert api.post(f"{P}/error-recovery/report", json={"n8nExecutionId": "1"}, headers=rohdr).status_code == 403
    assert api.post(f"{P}/error-recovery/record", json={}, headers=hdr).status_code == 422
    assert api.post(f"{P}/error-recovery/report", json={"message": "x"}, headers=hdr).status_code == 422
    r = api.post(f"{P}/error-recovery/record", json={"targetExecutionId": eid, "executionId": "run-8"}, headers=hdr)
    assert r.status_code == 200 and r.json()["status"] == "recorded" and r.json()["result"] == "escalated", r.text
    r = api.post(f"{P}/error-recovery/report", json={"n8nWorkflowId": "zzz", "n8nExecutionId": "777", "message": "boom"}, headers=hdr)
    assert r.status_code == 200 and r.json()["matched"] is False
    assert api.post(f"{P}/error-recovery/retry", json={"targetExecutionId": eid}, headers=hdr).status_code in (404, 405)
