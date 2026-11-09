"""The ten end-to-end scenarios of the Module 07 specification (section 23 H). Module 06 is the controlled FakeRisk, Module 09
is the FakeExecution test adapter (public contract only). Everything else is the real service and database."""
from __future__ import annotations

import threading
import uuid
from datetime import timedelta
from decimal import Decimal

import pytest

from aurum.approvals.service import ApprovalError
from m07_harness import FakeExecution, idea


def events(h, type_):
    return [e for e in h.svc.repo.events(0, 1000) if e["event_type"] == type_]


def test_s1_pass_to_module09_handoff(h):
    aid = h.ingest(1)["approval_id"]
    assert h.view(aid)["risk_decision"] == "PASS"
    res = h.approve(aid)
    assert res["status"] == "EXECUTION_PENDING"
    handoff = events(h, "trade.approved_for_execution")
    assert len(handoff) == 1
    p = handoff[0]["payload"]
    assert p["approval_id"] == aid and Decimal(p["approved_volume"]) == Decimal("0.10")
    assert p["stop_loss"] and p["risk_assessment_id"] and p["idempotency_key"] == f"exec:{aid}"
    assert p["approver_ids"] == [h.users["tom"].id]
    assert h.risk.calls.count("assess") == 2          # intake + fresh assessment at final validation
    assert len(FakeExecution(h).take()) == 1


def test_s2_block_can_never_be_approved(h):
    h.risk.decision = "BLOCK"
    h.risk.violations = [{"rule_id": "R010", "message": "Daily loss limit reached"}]
    aid = h.ingest(2)["approval_id"]
    v = h.view(aid)
    assert v["status"] == "PENDING" and v["risk_decision"] == "BLOCK" and v["can_approve"] is False
    for who in ("tom", "alice"):                       # not even an administrator
        with pytest.raises(ApprovalError) as e:
            h.approve(aid, who)
        assert e.value.code == "APPROVAL_CHECKS_FAILED"
        assert any("never be approved" in d for d in e.value.details)
    assert h.view(aid)["status"] == "PENDING"
    assert not events(h, "trade.approved_for_execution")
    assert any(n["event_type"] == "risk.blocked" for n in h.svc.notifications(h.users["tom"]))


def test_s3_expired_request_cannot_be_approved(h):
    aid = h.ingest(3)["approval_id"]          # H1: 60 minutes
    body = h.approve_body(aid)
    h.clock.advance(minutes=61)
    with pytest.raises(ApprovalError) as e:
        h.svc.approve(aid, h.users["tom"], body)
    assert e.value.code == "EXPIRED"
    assert h.view(aid)["status"] == "EXPIRED"
    assert not events(h, "trade.approved_for_execution")


def test_s4_modification_creates_new_version_and_fresh_assessment(h):
    aid = h.ingest(4)["approval_id"]
    first_assessment = h.view(aid)["risk_assessment_id"]
    out = h.svc.request_modification(aid, h.users["tom"], {
        "client_request_id": "m1", "request_version": h.view(aid)["version"], "kinds": ["change_stop_loss"],
        "changes": {"stop_loss": "1.09850"}, "comment": "Tighter stop below the H1 low."})
    assert h.view(aid)["status"] == "MODIFICATION_REQUESTED"
    new = out["new_approval"]
    assert new["status"] == "PENDING" and new["idea_version"] == 2 and new["parent_approval_id"] == aid
    assert Decimal(str(new["stop_loss"])) == Decimal("1.09850")
    assert new["risk_assessment_id"] != first_assessment
    assert first_assessment in h.risk.cancelled            # old reservation freed in Module 06
    detail = h.svc.detail(new["approval_id"], h.users["tom"])
    assert [v["version"] for v in detail["versions"]] == [1, 2]       # original kept
    assert Decimal(str(detail["versions"][0]["stop_loss"])) == Decimal("1.09810")
    assert h.approve(new["approval_id"])["status"] == "EXECUTION_PENDING"


def test_s5_two_people_approve_at_once_only_one_decision(h):
    aid = h.ingest(5)["approval_id"]
    bodies = {u: h.approve_body(aid) for u in ("tom", "alice")}
    results, errors = [], []
    gate = threading.Barrier(2)

    def go(u):
        gate.wait()
        try:
            results.append(h.svc.approve(aid, h.users[u], bodies[u]))
        except ApprovalError as exc:
            errors.append(exc.code)
    threads = [threading.Thread(target=go, args=(u,)) for u in bodies]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(results) == 1 and len(errors) == 1
    assert errors[0] in ("NOT_PENDING", "STALE_VERSION", "CONCURRENT_CHANGE")
    d = h.svc.detail(aid, h.users["alice"])
    assert len([x for x in d["decisions"] if x["decision"] == "APPROVED"]) == 1
    assert len(events(h, "trade.approved_for_execution")) == 1


def test_s6_execution_failure_updates_status_and_notifies(h):
    aid = h.ingest(6)["approval_id"]
    h.approve(aid)
    m09 = FakeExecution(h)
    job = m09.take()[0]
    m09.report(job["execution_request_id"], "accepted")
    m09.report(job["execution_request_id"], "failed", message="Broker rejected: market closed")
    assert h.view(aid)["status"] == "EXECUTION_FAILED"
    notes = [n for n in h.svc.notifications(h.users["tom"]) if n["event_type"] == "execution.failed"]
    assert notes and notes[0]["priority"] == "critical" and "market closed" in notes[0]["message"]
    assert events(h, "execution.failed")


def _open_position(h, **over):
    row = {"position_id": "9001", "account_login": "1001", "symbol": "EURUSD", "side": "BUY", "volume": Decimal("0.10"),
           "entry_price": Decimal("1.10000"), "current_price": Decimal("1.10200"), "stop_loss": Decimal("1.09800"),
           "take_profit": Decimal("1.10500"), "unrealized_pnl": Decimal("20"), "realized_pnl": None, "status": "OPEN",
           "opened_at": h.clock() - timedelta(hours=1)}
    row.update(over)
    h.positions.rows = [row]
    return row


def test_s7_stop_loss_move_approved_and_handed_off(h):
    _open_position(h)
    r = h.svc.create_management_request(h.users["tom"], {"position_id": "9001", "action": "MOVE_STOP_LOSS",
                                                          "params": {"stop_loss": "1.10000"}, "reason": "Lock in breakeven",
                                                          "client_request_id": "mg1"})
    assert r["status"] == "PENDING" and r["risk_implications"]["stop_change"] == "reduces risk"
    out = h.svc.approve_management(r["request_id"], h.users["alice"], {"client_request_id": "d1", "request_version": r["version"],
                                                                        "confirmation": {"action": "MOVE_STOP_LOSS"}})
    assert out["status"] == "EXECUTION_PENDING"
    m09 = FakeExecution(h)
    job = [j for j in m09.take() if j["kind"] == "management"][0]
    assert job["payload"]["params"]["stop_loss"] == "1.10000"
    m09.report(job["execution_request_id"], "filled")
    assert h.svc.management_request(r["request_id"], h.users["tom"])["status"] == "EXECUTED"


def test_s8_emergency_exit_requires_typed_authorisation(h):
    _open_position(h)
    r = h.svc.create_management_request(h.users["tom"], {"position_id": "9001", "action": "EMERGENCY_EXIT",
                                                          "reason": "News spike", "client_request_id": "em1"})
    assert r["priority"] == "emergency"
    with pytest.raises(ApprovalError) as e:
        h.svc.approve_management(r["request_id"], h.users["tom"], {"client_request_id": "x1", "request_version": r["version"],
                                                                    "confirmation": {"phrase": "close eurusd"}})
    assert e.value.code == "APPROVAL_CHECKS_FAILED"
    out = h.svc.approve_management(r["request_id"], h.users["tom"], {"client_request_id": "x2", "request_version": r["version"],
                                                                      "confirmation": {"phrase": "CLOSE EURUSD"}})
    assert out["status"] == "EXECUTION_PENDING"
    job = [j for j in FakeExecution(h).take() if j["kind"] == "management"][0]
    assert job["payload"]["action"] == "EMERGENCY_EXIT" and job["payload"]["priority"] == "emergency"


def test_s9_duplicate_event_creates_one_request(h):
    svc_p = h.service_principal(["events:publish"])
    env = {"event_id": "evt-1", "event_type": "trade_idea.created", "event_version": "1.0",
           "occurred_at": h.clock().isoformat(), "source_module": "module_05", "correlation_id": "c-9",
           "payload": idea(9, h.clock)}
    first = h.svc.receive_event(svc_p, env)
    second = h.svc.receive_event(svc_p, env)
    assert first["duplicate"] is False and second["duplicate"] is True
    again = h.svc.receive_event(svc_p, {**env, "event_id": "evt-2"})     # same idea, new event id
    assert again["result"]["duplicate"] is True
    assert h.svc.list_requests(h.users["tom"])["total"] == 1


def test_s10_stale_market_data_blocks_approval(h):
    aid = h.ingest(10)["approval_id"]
    h.risk.quote_age = timedelta(minutes=10)               # policy limit is 120 s
    with pytest.raises(ApprovalError) as e:
        h.approve(aid)
    assert e.value.code == "APPROVAL_CHECKS_FAILED" and any("stale" in d for d in e.value.details)
    assert h.view(aid)["status"] == "PENDING"
    # data recovers, but the market went stale again between approval and handoff -> invalidated, nothing published
    h.risk.quote_age = timedelta(seconds=5)
    orig = h.risk.quote_time
    calls = {"n": 0}

    def flaky(symbol, now):
        calls["n"] += 1
        return orig(symbol, now) if calls["n"] == 1 else h.clock() - timedelta(minutes=10)
    h.risk.quote_time = flaky
    res = h.approve(aid)
    assert res["status"] == "INVALIDATED" and "stale" in res["status_reason"]
    assert not events(h, "trade.approved_for_execution")
