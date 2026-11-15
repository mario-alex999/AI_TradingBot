"""WF08-05 Approval Response Handler: verification of Module 07's outcomes, recording once, API, installation.

Docx test stage covered here: (7) an approval, a rejection and an expiry are each verified and recorded once; an
approval whose idea, risk assessment or approver no longer holds is recorded as not valid and nothing is passed on.

Module 07 is real here (its service, tables, decisions and notifications). Module 06 is a stand-in.
"""
from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from m08_harness import ALICE
from test_wf08_03 import idea
from test_wf08_04 import RiskGateway, _Settings

from aurum.approvals.db_models import ApprovalDecision, ApprovalRequest, TradeIdea
from aurum.approvals.gateways import GatewayUnavailable, Module05Source
from aurum.approvals.service import ApprovalService
from aurum.auth.db_models import User
from aurum.auth.service import AuthService
from aurum.database.connection import session_scope
from aurum.decision.repository import DecisionRepository
from aurum.orchestration import approval_responses as ar
from aurum.orchestration.approval_responses import ApprovalResponseHandler, install
from aurum.orchestration.db_models import OrchAuditLog
from sqlalchemy import select, update
from types import SimpleNamespace

P = "/api/v1/orchestration"
PASSWORD = "correct-horse-battery-9"


class World:
    def __init__(self, h):
        self.h = h
        self.auth = AuthService(h.engine, iterations=1000, clock=h.clock)
        self.users = {}
        for name, role in (("alice", "admin"), ("tom", "trader")):
            self.auth.create_user(name, PASSWORD, role, name.title(), f"{name}@example.test")
            self.users[name] = self.auth.login(name, PASSWORD).principal
        decision = SimpleNamespace(repo=DecisionRepository(h.engine), _candidate=self.candidate,
                                   ai=SimpleNamespace(audit=SimpleNamespace(latest=lambda uid: None)))
        self.risk = RiskGateway(h.clock)
        self.approvals = ApprovalService(h.engine, _Settings(), self.risk, Module05Source(lambda: decision), None, None, clock=h.clock)
        self.repo = DecisionRepository(h.engine)
        self.handler = ApprovalResponseHandler(h.svc, approvals=lambda: self.approvals, deliver=lambda a: {"sent": 0})

    def candidate(self, uid: str) -> dict:
        from aurum.decision.db_models import TradeCandidate
        from aurum.decision.repository import to_dict
        with session_scope(self.h.engine) as s:
            return to_dict(s.execute(select(TradeCandidate).where(TradeCandidate.candidate_uid == uid)).scalar_one())

    def request(self, **kw) -> str:
        out = self.approvals.ingest_candidate(self.candidate(idea(self.h, **kw)))
        assert out.get("approval_id"), out
        return out["approval_id"]

    def body(self, aid: str) -> dict:
        r = self.approvals.detail(aid, self.users["alice"])["request"]
        return {"client_request_id": str(uuid.uuid4()), "request_version": r["version"], "evidence_hash": r["evidence_hash"],
                "acknowledged_warnings": [w["rule_id"] for w in r["risk_warnings"]],
                "confirmation": {"symbol": r["symbol"], "direction": r["direction"], "entry": str(r["entry"]),
                                 "stop_loss": str(r["stop_loss"]), "take_profit": str(r["take_profit"]),
                                 "position_size": None if r["position_size"] is None else str(r["position_size"]),
                                 "max_risk_amount": None if r["risk_amount"] is None else str(r["risk_amount"])}}

    def approve(self, aid: str, handoff: bool = True) -> dict:
        if handoff:
            return self.approvals.approve(aid, self.users["tom"], self.body(aid))
        real = self.approvals.handoff
        self.approvals.handoff = lambda approval_id: (_ for _ in ()).throw(GatewayUnavailable("Module 06 down"))
        try:
            return self.approvals.approve(aid, self.users["tom"], self.body(aid))
        finally:
            self.approvals.handoff = real

    def reject(self, aid: str) -> dict:
        reason = self.approvals.policy_obj().review.rejection_reasons[0]
        return self.approvals.reject(aid, self.users["tom"], {**self.body(aid), "reason_code": reason, "comment": "no"})

    def status_of(self, aid: str) -> str:
        with self.approvals.repo.read() as s:
            return s.get(ApprovalRequest, aid).status

    def audits(self) -> list:
        with self.h.svc.repo.read() as s:
            rows = s.execute(select(OrchAuditLog).where(OrchAuditLog.action == ar.AUDIT_ACTION).order_by(OrchAuditLog.seq)).scalars().all()
            return [(r.entity_id, r.outcome, r.details) for r in rows]


@pytest.fixture
def w(h):
    return World(h)


def set_cfg(h, **changes):
    cfg = h.svc.config().dump()
    cfg["approval_responses"].update(changes)
    h.svc.put_config(ALICE, {"reason": "test", "config": cfg})


# ---- the three outcomes of the specification -------------------------------------------------------------------------------
def test_an_approval_module_07_handed_on_is_verified_and_recorded_once(w):
    aid = w.request()
    assert w.handler.status(aid)["ok"] is False and "nobody has decided" in w.handler.status(aid)["reasons"][0]
    assert w.handler.pending()["count"] == 0
    assert w.approve(aid)["status"] == "EXECUTION_PENDING"
    assert [i["approval_id"] for i in w.handler.pending()["items"]] == [aid]
    st = w.handler.status(aid)
    assert st["ok"] and st["verified"] and st["outcome"] == "approved" and st["decided_by"] == ["tom"], st
    assert st["checks"] == {"authority": "ok", "binding": "ok", "idea_valid": "ok", "risk_valid": "ok", "final_validation": "passed"}
    out = w.handler.record(aid, execution_id="exec-1")
    assert out["status"] == "recorded" and out["result"] == "approved" and out["execution_request_id"], out
    assert "WF08-06" in out["next_step"] and out["deliveries"]["in_app"]
    (key, outcome, details), = w.audits()
    assert key == f"{aid}:approved" and outcome == "ok" and details["execution_id"] == "exec-1" and details["decided_by"] == ["tom"]
    # Once: a repeat records nothing, and the request no longer counts as pending.
    assert w.handler.record(aid)["status"] == "skipped" and len(w.audits()) == 1 and w.handler.pending()["count"] == 0
    assert w.status_of(aid) == "EXECUTION_PENDING"                      # WF08-05 changed nothing in Module 07


def test_rejection_and_expiry_are_recorded_and_nothing_is_executed(w):
    rejected, expiring = w.request(), w.request(symbol="GBPUSD")
    w.reject(rejected)
    out = w.handler.record(rejected)
    assert out["status"] == "recorded" and out["result"] == "rejected" and out["verified"] and out["decided_by"] == ["tom"]
    assert out["next_step"] == "Nothing will be executed for this request."
    w.h.clock.advance(days=2)
    assert w.approvals.expire_due()["expired"] == 1
    st = w.handler.status(expiring)
    assert st["outcome"] == "expired" and st["verified"] and st["checks"] == {"authority": "ok"} and st["decided_by"] == []
    set_cfg(w.h, lookback_hours=24 * 30)
    assert w.handler.record(expiring)["result"] == "expired"
    assert sorted(k for k, _, _ in w.audits()) == sorted([f"{rejected}:rejected", f"{expiring}:expired"])


# ---- the critical rule: an approval is tied to the idea version and its risk assessment --------------------------------------
def test_an_approval_that_no_longer_holds_is_recorded_as_not_valid_and_passes_nothing_on(w):
    aid = w.request()
    assert w.approve(aid, handoff=False)["status"] == "APPROVED"
    ok = w.handler.status(aid)
    assert ok["verified"] and ok["checks"]["final_validation"] == "pending" and ok["checks"]["binding"] == "ok"
    assert "final validation releases it" in w.handler.record(aid, dry_run=True)["next_step"]
    # 1. The trade idea was changed after approval (a new version exists).
    with session_scope(w.h.engine) as s:
        s.execute(update(TradeIdea).values(current_version=TradeIdea.current_version + 1))
    st = w.handler.status(aid)
    assert not st["verified"] and st["checks"]["binding"] == "failed" and "changed after approval" in st["problems"][0]
    with session_scope(w.h.engine) as s:
        s.execute(update(TradeIdea).values(current_version=TradeIdea.current_version - 1))
    # 2. Module 06 no longer stands by the assessment.
    w.risk.validate = lambda assessment_id: {"valid": False, "reasons": ["Assessment has expired; request a new one."]}
    st = w.handler.status(aid)
    assert st["checks"]["risk_valid"] == "failed" and st["problems"] == ["Module 06: Assessment has expired; request a new one."]
    # 3. Module 06 cannot be reached: not confirmed is not valid.
    w.risk.validate = lambda assessment_id: (_ for _ in ()).throw(GatewayUnavailable("down"))
    assert w.handler.status(aid)["checks"]["risk_valid"] == "unavailable" and not w.handler.status(aid)["verified"]
    w.risk.validate = lambda assessment_id: {"valid": True, "reasons": []}
    # 4. The decision was made on a different risk assessment.
    with session_scope(w.h.engine) as s:
        s.execute(update(ApprovalDecision).values(risk_assessment_id="another-assessment"))
    st = w.handler.status(aid)
    assert st["checks"]["binding"] == "failed" and "different risk assessment" in st["problems"][0]
    # 5. The trade idea expired.
    w.h.clock.advance(days=3)
    st = w.handler.status(aid)
    assert st["checks"]["idea_valid"] == "failed"
    set_cfg(w.h, lookback_hours=24 * 30)
    out = w.handler.record(aid)
    assert out["status"] == "recorded" and out["result"] == "approved_not_valid" and out["verified"] is False, out
    assert "passes nothing on" in out["next_step"]
    (_, outcome, details), = w.audits()
    assert outcome == "refused" and details["result"] == "approved_not_valid" and details["problems"]
    assert w.status_of(aid) == "APPROVED"                               # only Module 07 may change the request


def test_authority_is_checked_against_the_recorded_decisions(w):
    aid = w.request()
    w.approve(aid, handoff=False)
    with session_scope(w.h.engine) as s:
        s.execute(update(User).where(User.username == "tom").values(is_active=False))
    st = w.handler.status(aid)
    assert st["checks"]["authority"] == "failed" and "deactivated" in st["problems"][0]
    with session_scope(w.h.engine) as s:
        s.execute(update(User).where(User.username == "tom").values(is_active=True))
        s.execute(update(ApprovalDecision).values(role="observer"))
    st = w.handler.status(aid)
    assert st["checks"]["authority"] == "failed" and "without approval permission" in st["problems"][0]
    with session_scope(w.h.engine) as s:
        s.execute(update(ApprovalDecision).values(role="trader", is_final=False))
    assert "1 with a final one are required" in w.handler.status(aid)["problems"][0]


# ---- skips, dry run, off switch --------------------------------------------------------------------------------------------------
def test_unknown_and_undecided_requests_dry_run_and_the_off_switch(w):
    aid = w.request()
    assert w.handler.record("no-such-approval")["status"] == "skipped"
    assert "no such approval request" in w.handler.record("no-such-approval")["reason"]
    assert w.handler.record(aid)["status"] == "skipped"                 # still PENDING
    listed = w.handler.pending(["no-such-approval", aid, aid])
    assert [i["approval_id"] for i in listed["items"]] == ["no-such-approval", aid]
    w.reject(aid)
    dry = w.handler.record(aid, dry_run=True)
    assert dry["status"] == "dry_run" and dry["result"] == "rejected" and w.audits() == []
    set_cfg(w.h, enabled=False)
    assert w.handler.preflight()["proceed"] is False and w.handler.pending()["count"] == 0
    assert w.handler.record(aid)["status"] == "skipped" and w.audits() == []
    set_cfg(w.h, enabled=True, lookback_hours=1)
    w.h.clock.advance(hours=2)
    assert w.handler.pending()["count"] == 0                             # outside the look-back window
    assert w.handler.pending([aid])["count"] == 1                        # but a named request is always answered


def test_module_07_expiry_pass_runs_on_the_tick_only_when_switched_on(w):
    from aurum.orchestration.worker import Worker
    aid = w.request()
    w.h.clock.advance(days=2)
    assert w.handler.expire_overdue() == {"skipped": True} and w.status_of(aid) == "PENDING"
    assert "approval_expiry" not in Worker(w.h.svc).tick()
    set_cfg(w.h, expire_overdue_requests=True)
    assert w.handler.expire_overdue()["expired"] == 1 and w.status_of(aid) == "EXPIRED"


# ---- installation ---------------------------------------------------------------------------------------------------------------
def test_install_registers_the_workflow_and_each_outcome_event_starts_one_run(h):
    from aurum.orchestration.catalogue import M07, Envelope
    h.n8n.workflows["n8nWF0805"] = {"id": "n8nWF0805", "active": True, "webhook_paths": [ar.WEBHOOK_PATH]}
    out = install(h.svc, ALICE, "n8nWF0805")
    assert out["state"] == "ACTIVE" and "registered" in out["steps"]
    assert sorted(s["event_pattern"] for s in out["subscriptions"]) == sorted(ar.TRIGGER_EVENTS)
    again = install(h.svc, ALICE, "n8nWF0805")
    assert "registered" not in again["steps"] and len([s for s in h.svc.list_subscriptions()["items"]
                                                        if s["workflow_id"] == ar.WORKFLOW_ID]) == len(ar.TRIGGER_EVENTS)
    for etype, aid in (("approval.approved", "ap-1"), ("approval.rejected", "ap-2"), ("approval.requested", "ap-3")):
        env = Envelope(event_id=str(uuid.uuid4()), event_type=etype, event_version="1.0", occurred_at=h.clock(), source_module=M07,
                       correlation_id="same-correlation", idempotency_key=f"t:{uuid.uuid4().hex}", environment=h.svc.env,
                       payload={"approval_id": aid, "status": etype.split(".")[1].upper()}, published_at=h.clock())
        with h.svc.repo.write() as s:
            assert h.svc._insert_event(s, env, "internal") is not None
    h.svc.deliver_due()
    runs = h.svc.list_executions(workflow_id=ar.WORKFLOW_ID)["items"]
    assert len(runs) == 2                                                # one per outcome event; a new request is not one
    inputs = sorted(h.svc.get_execution(r["execution_id"])["input"]["event"]["payload"]["approval_id"] for r in runs)
    assert inputs == ["ap-1", "ap-2"]


# ---- HTTP API -----------------------------------------------------------------------------------------------------------------
def test_api_routes_and_who_may_record(w, monkeypatch):
    from aurum.api.app import create_app
    from aurum.config import Settings
    monkeypatch.setattr(ApprovalResponseHandler, "approvals", property(lambda self: w.approvals))
    monkeypatch.setattr(ApprovalResponseHandler, "_send", lambda self: {"sent": 0})
    api = TestClient(create_app(settings=Settings(_env_file=None, database_url="sqlite://"), engine=w.h.engine,
                                auth_service=w.auth, orchestration_service=w.h.svc))
    aid = w.request()
    w.reject(aid)
    assert api.get(f"{P}/approval-responses/pending").status_code == 401
    token, _ = w.auth.create_service_token("n8n-wf0805", "test", ["orchestration:view", "orchestration:callback"])
    hdr = {"Authorization": f"Bearer {token}"}
    assert api.get(f"{P}/approval-responses/config", headers=hdr).json()["triggerEvents"] == list(ar.TRIGGER_EVENTS)
    assert api.get(f"{P}/approval-responses/preflight", headers=hdr).json()["proceed"] is True
    assert api.get(f"{P}/approval-responses/pending", headers=hdr).json()["items"][0]["approvalId"] == aid
    st = api.get(f"{P}/approval-responses/status", params={"approvalId": aid}, headers=hdr).json()
    assert st["ok"] is True and st["outcome"] == "rejected" and st["verified"] is True, st
    ro, _ = w.auth.create_service_token("read-only", "test", ["orchestration:view"])
    assert api.post(f"{P}/approval-responses/record", json={"approvalId": aid}, headers={"Authorization": f"Bearer {ro}"}).status_code == 403
    assert api.post(f"{P}/approval-responses/record", json={}, headers=hdr).status_code == 422
    r = api.post(f"{P}/approval-responses/record", json={"approvalId": aid, "executionId": "exec-5"}, headers=hdr)
    assert r.status_code == 200 and r.json()["status"] == "recorded" and r.json()["result"] == "rejected", r.text
    assert api.get(f"{P}/approval-responses/pending", headers=hdr).json()["count"] == 0
    for path in ("approve", "reject"):                                   # no way to decide through this API
        assert api.post(f"{P}/approval-responses/{path}", json={"approvalId": aid}, headers=hdr).status_code in (404, 405)


def test_real_wiring_on_an_empty_database(h):
    handler = ApprovalResponseHandler(h.svc)
    pre = handler.preflight()
    assert pre["proceed"] is True and pre["checks"]["module_07_schema"], pre
    assert handler.pending() == {"enabled": True, "items": [], "count": 0, "deferred": 0}
    assert handler._deliveries("x") == {} and handler._recorded(["x"]) == set()
    assert handler.status("x")["exists"] is False
