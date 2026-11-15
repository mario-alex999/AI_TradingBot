"""WF08-06 Approved Trade Handoff: verification of Module 07's execution request, recording once, the approval guard, API.

Docx test stage covered here: (8) an approved trade's execution request is verified and its hand-off recorded once; a
request that no longer verifies is recorded as not valid; nothing is ever sent to a broker, and without Module 09 every
hand-off ends as waiting.

Module 07 is real here (its service, tables, execution queue and results). Module 06 and Module 09 are stand-ins.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from m08_harness import ALICE
from test_wf08_05 import World

from aurum.approvals.db_models import ExecutionHandoff, TradeIdea
from aurum.approvals.gateways import GatewayUnavailable
from aurum.orchestration import trade_handoff as th
from aurum.orchestration.db_models import OrchAuditLog
from aurum.orchestration.trade_handoff import TradeHandoff, install
from aurum.database.connection import session_scope
from sqlalchemy import select, update

P = "/api/v1/orchestration"
NOT_DEPLOYED = {"deployed": False, "status": "UNKNOWN", "detail": "Module 09 is not deployed."}
DEPLOYED = {"deployed": True, "status": "HEALTHY", "detail": None}


class W(World):
    def __init__(self, h):
        super().__init__(h)
        self.m09 = dict(NOT_DEPLOYED)
        self.handoff = TradeHandoff(h.svc, approvals=lambda: self.approvals, deliver=lambda a: {"sent": 0}, module_09=lambda: self.m09)

    def module_09(self):
        token, _ = self.auth.create_service_token(f"module09-{uuid.uuid4().hex[:6]}", "test", ["execution:read", "execution:report"])
        return self.auth.resolve_service_token(token)

    def request_row(self, aid: str):
        with self.approvals.repo.read() as s:
            row = s.execute(select(ExecutionHandoff).where(ExecutionHandoff.approval_id == aid)).scalar_one_or_none()
            if row is not None:
                s.expunge(row)
            return row

    def audits(self) -> list:
        with self.h.svc.repo.read() as s:
            rows = s.execute(select(OrchAuditLog).where(OrchAuditLog.action == th.AUDIT_ACTION).order_by(OrchAuditLog.seq)).scalars().all()
            return [(r.entity_id, r.outcome, r.details) for r in rows]


@pytest.fixture
def w(h):
    return W(h)


def set_cfg(h, **changes):
    cfg = h.svc.config().dump()
    cfg["trade_handoff"].update(changes)
    h.svc.put_config(ALICE, {"reason": "test", "config": cfg})


# ---- the hand-off -----------------------------------------------------------------------------------------------------------------
def test_without_module_09_a_valid_handoff_is_recorded_once_as_waiting_and_nothing_is_sent(w):
    aid = w.request()
    assert w.approve(aid)["status"] == "EXECUTION_PENDING"
    before = w.request_row(aid)
    st = w.handoff.status(aid)
    assert st["ok"] and st["verified"] and st["state"] == "awaiting_module_09" and st["acknowledged"] is False, st
    assert st["checks"] == {"request_intact": "ok", "approval_status": "ok", "risk_decision": "ok", "idea_version": "ok",
                            "idea_valid": "ok", "risk_valid": "ok", "authority": "ok"}
    assert [i["approval_id"] for i in w.handoff.pending()["items"]] == [aid]
    out = w.handoff.record(aid, execution_id="exec-1")
    assert out["status"] == "recorded" and out["result"] == "awaiting_module_09" and "nothing will be executed" in out["next_step"]
    assert out["execution_request_id"] == before.execution_request_id and out["deliveries"]["in_app"]
    (key, outcome, details), = w.audits()
    assert key == f"{before.execution_request_id}:awaiting_module_09" and outcome == "ok" and details["execution_id"] == "exec-1"
    # Once, and nothing in Module 07 changed: same single request, still pending, never picked up.
    assert w.handoff.record(aid)["status"] == "skipped" and len(w.audits()) == 1 and w.handoff.pending()["count"] == 0
    after = w.request_row(aid)
    assert (after.status, after.picked_up_at, after.payload_hash, after.updated_at) == \
        ("pending", None, before.payload_hash, before.updated_at)
    with w.approvals.repo.read() as s:
        assert len(s.execute(select(ExecutionHandoff)).all()) == 1
    assert w.status_of(aid) == "EXECUTION_PENDING"


def test_with_module_09_the_acknowledgement_and_the_outcome_are_followed(w):
    aid = w.request()
    w.approve(aid)
    w.m09 = dict(DEPLOYED)
    assert w.handoff.record(aid)["result"] == "awaiting_acknowledgement"
    m09 = w.module_09()
    queue = w.approvals.execution_queue(m09)                             # Module 09 reads the queue itself
    assert [q["execution_request_id"] for q in queue] == [w.request_row(aid).execution_request_id]
    out = w.handoff.record(aid)
    assert out["status"] == "recorded" and out["result"] == "acknowledged" and out["acknowledged"] is True
    w.approvals.report_execution(m09, {"event_id": "ev-1", "execution_request_id": queue[0]["execution_request_id"],
                                       "status": "filled", "broker_ticket": "T1", "position_id": "P1",
                                       "filled_volume": "0.10", "fill_price": "1.1001"})
    done = w.handoff.record(aid)
    assert done["result"] == "executed" and done["approval_status"] == "EXECUTED"
    assert [k.split(":")[1] for k, _, _ in w.audits()] == ["awaiting_acknowledgement", "acknowledged", "executed"]


# ---- a request that no longer verifies ------------------------------------------------------------------------------------------
def test_a_handoff_that_does_not_verify_is_recorded_as_not_valid_and_left_untouched(w):
    aid = w.request()
    w.approve(aid)
    row = w.request_row(aid)
    # 1. The trade idea changed after approval.
    with session_scope(w.h.engine) as s:
        s.execute(update(TradeIdea).values(current_version=TradeIdea.current_version + 1))
    st = w.handoff.status(aid)
    assert st["state"] == "not_valid" and st["checks"]["idea_version"] == "failed"
    with session_scope(w.h.engine) as s:
        s.execute(update(TradeIdea).values(current_version=TradeIdea.current_version - 1))
    # 2. Module 06 no longer stands by the assessment, or cannot be reached.
    w.risk.validate = lambda assessment_id: {"valid": False, "reasons": ["Assessment has expired; request a new one."]}
    assert w.handoff.status(aid)["problems"] == ["Module 06: Assessment has expired; request a new one."]
    w.risk.validate = lambda assessment_id: (_ for _ in ()).throw(GatewayUnavailable("down"))
    assert w.handoff.status(aid)["checks"]["risk_valid"] == "unavailable" and w.handoff.status(aid)["state"] == "not_valid"
    w.risk.validate = lambda assessment_id: {"valid": True, "reasons": []}
    assert w.handoff.status(aid)["verified"] is True
    # 3. The execution request was altered after Module 07 wrote it.
    payload = dict(row.payload, approved_volume="5.00")
    with session_scope(w.h.engine) as s:
        s.execute(update(ExecutionHandoff).values(payload=payload))
    st = w.handoff.status(aid)
    assert st["checks"]["request_intact"] == "failed" and any("hash" in p for p in st["problems"])
    assert any("approved_volume" in p for p in st["problems"])
    out = w.handoff.record(aid)
    assert out["status"] == "recorded" and out["result"] == "not_valid" and "must not be executed" in out["next_step"]
    (key, outcome, details), = w.audits()
    assert key.endswith(":not_valid") and outcome == "refused" and details["problems"]
    assert w.request_row(aid).status == "pending" and w.status_of(aid) == "EXECUTION_PENDING"   # only reported


def test_no_execution_request_unknown_approval_dry_run_and_the_off_switch(w):
    waiting = w.request()
    assert w.approve(waiting, handoff=False)["status"] == "APPROVED"     # Module 07 has not handed it on
    out = w.handoff.record(waiting)
    assert out["status"] == "skipped" and "created no execution request" in out["reason"]
    assert w.request_row(waiting) is None                                # WF08-06 never creates one
    assert "no such approval request" in w.handoff.record("no-such-approval")["reason"]
    aid = w.request(symbol="GBPUSD")
    w.approve(aid)
    dry = w.handoff.record(aid, dry_run=True)
    assert dry["status"] == "dry_run" and dry["result"] == "awaiting_module_09" and w.audits() == []
    set_cfg(w.h, enabled=False)
    assert w.handoff.preflight()["proceed"] is False and w.handoff.pending()["count"] == 0
    assert w.handoff.record(aid)["status"] == "skipped" and w.audits() == []
    set_cfg(w.h, enabled=True)
    pre = w.handoff.preflight()
    assert pre["proceed"] is True and pre["checks"]["module_09_deployed"] is False     # absent Module 09 does not stop it


# ---- installation and the approval guard -------------------------------------------------------------------------------------
def test_install_uses_the_reserved_registration_and_the_approval_guard_stays(h):
    from aurum.orchestration.catalogue import M07, Envelope
    h.n8n.workflows["n8nWF0806"] = {"id": "n8nWF0806", "active": True, "webhook_paths": [th.WEBHOOK_PATH]}
    assert h.svc.get_workflow(th.WORKFLOW_ID)["placeholder"] is True
    out = install(h.svc, ALICE, "n8nWF0806")
    wf = h.svc.get_workflow(th.WORKFLOW_ID)
    assert out["state"] == "ACTIVE" and wf["name"] == th.WORKFLOW_NAME and wf["category"] == "execution_handoff"
    assert wf["placeholder"] is False and [s["event_pattern"] for s in out["subscriptions"]] == ["trade.approved_for_execution"]
    again = install(h.svc, ALICE, "n8nWF0806")
    assert "subscription to trade.approved_for_execution already present" in again["steps"]
    h.approval_status["ap-handed"] = "EXECUTION_PENDING"
    h.approval_status["ap-rejected"] = "REJECTED"
    for aid in ("ap-handed", "ap-rejected", "ap-unknown"):
        env = Envelope(event_id=str(uuid.uuid4()), event_type="trade.approved_for_execution", event_version="1.0",
                       occurred_at=h.clock(), source_module=M07, correlation_id=f"c-{aid}", idempotency_key=f"t:{uuid.uuid4().hex}",
                       environment=h.svc.env, payload={"approval_id": aid}, published_at=h.clock())
        with h.svc.repo.write() as s:
            assert h.svc._insert_event(s, env, "internal") is not None
    h.svc.deliver_due()
    runs = h.svc.list_executions(workflow_id=th.WORKFLOW_ID)["items"]
    assert [r["approval_id"] for r in runs] == ["ap-handed"]             # Module 07 must show the approval
    # A person cannot start it without an approval either.
    from aurum.orchestration.common import OrchError
    with pytest.raises(OrchError) as e:
        h.svc.trigger(ALICE, {"workflow_id": th.WORKFLOW_ID, "trigger_type": "admin", "input": {"dryRun": True},
                              "idempotency_key": "x1"})
    assert e.value.code == "APPROVAL_REQUIRED"


# ---- HTTP API -----------------------------------------------------------------------------------------------------------------
def test_api_routes_who_may_record_and_no_order_endpoint(w, monkeypatch):
    from aurum.api.app import create_app
    from aurum.config import Settings
    monkeypatch.setattr(TradeHandoff, "approvals", property(lambda self: w.approvals))
    monkeypatch.setattr(TradeHandoff, "_send", lambda self: {"sent": 0})
    api = TestClient(create_app(settings=Settings(_env_file=None, database_url="sqlite://"), engine=w.h.engine,
                                auth_service=w.auth, orchestration_service=w.h.svc))
    aid = w.request()
    w.approve(aid)
    assert api.get(f"{P}/trade-handoff/pending").status_code == 401
    token, _ = w.auth.create_service_token("n8n-wf0806", "test", ["orchestration:view", "orchestration:callback"])
    hdr = {"Authorization": f"Bearer {token}"}
    cfg = api.get(f"{P}/trade-handoff/config", headers=hdr).json()
    assert cfg["triggerEvents"] == ["trade.approved_for_execution"] and cfg["module09"]["deployed"] is False
    assert api.get(f"{P}/trade-handoff/preflight", headers=hdr).json()["proceed"] is True
    assert api.get(f"{P}/trade-handoff/pending", headers=hdr).json()["items"][0]["approvalId"] == aid
    st = api.get(f"{P}/trade-handoff/status", params={"approvalId": aid}, headers=hdr).json()
    assert st["ok"] is True and st["state"] == "awaiting_module_09" and st["verified"] is True, st
    ro, _ = w.auth.create_service_token("read-only", "test", ["orchestration:view"])
    assert api.post(f"{P}/trade-handoff/record", json={"approvalId": aid}, headers={"Authorization": f"Bearer {ro}"}).status_code == 403
    assert api.post(f"{P}/trade-handoff/record", json={}, headers=hdr).status_code == 422
    r = api.post(f"{P}/trade-handoff/record", json={"approvalId": aid, "executionId": "exec-6"}, headers=hdr)
    assert r.status_code == 200 and r.json()["status"] == "recorded" and r.json()["result"] == "awaiting_module_09", r.text
    for path in ("execute", "send", "order", "submit"):                  # nothing here can send a trade
        assert api.post(f"{P}/trade-handoff/{path}", json={"approvalId": aid}, headers=hdr).status_code in (404, 405)
    # The n8n token cannot read Module 09's queue or report results.
    assert api.get("/api/v1/approvals/execution/queue", headers=hdr).status_code == 403


def test_the_module_never_calls_a_broker_or_creates_requests():
    import inspect
    src = inspect.getsource(th)
    code = "\n".join(line for line in src.split("\n") if not line.strip().startswith(("#", '"', "*")))
    for banned in ("order_send", "MetaTrader", "mt5", ".handoff(", "report_execution", "execution_queue", "requests.", "httpx"):
        assert banned not in code.split('"""', 2)[-1], banned


def test_real_wiring_on_an_empty_database(h):
    handoff = TradeHandoff(h.svc)
    pre = handoff.preflight()
    assert pre["proceed"] is True and pre["checks"]["module_07_schema"] and pre["checks"]["module_09_deployed"] is False, pre
    assert handoff.pending() == {"enabled": True, "items": [], "count": 0, "deferred": 0}
    assert handoff._deliveries("x") == {} and handoff.status("x")["exists"] is False
    assert handoff.module_09()["deployed"] is False
