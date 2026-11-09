"""Module 07 with the REAL Module 06 RiskService (in-process gateway, same database). Market and account inputs are
Module 06's MemoryRiskData simulation (MOCKED prices and account: no broker). This checks the actual contract: assessment
ids, decisions, validate/cancel, R047 duplicate handling and BLOCK propagation."""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from aurum.approvals.gateways import InProcessRiskGateway
from aurum.approvals.policy import Policy
from aurum.approvals.service import ApprovalError, ApprovalService
from aurum.risk.account import EquityPoint
from aurum.risk.calendar import TradingCalendar
from aurum.risk.config import TradingDay
from aurum.risk.service import RiskService
from aurum.risk.simulation import MemoryRiskData


@pytest.fixture
def real(h):
    data = MemoryRiskData(now=h.clock())
    cal = TradingCalendar(TradingDay())
    d = cal.trading_date(cal.month(data.now).start) - timedelta(days=1)
    while d <= cal.trading_date(data.now):
        data.history.append(EquityPoint(cal.day_start(d), data.equity, data.balance))
        d += timedelta(days=1)
    risk = RiskService(h.engine, None, data=data, clock=h.clock)
    svc = ApprovalService(h.engine, h.settings, InProcessRiskGateway(lambda: risk), None, None, h.positions, clock=h.clock,
                          seed_policy=Policy())
    h.svc = svc
    return h, risk, data


def test_real_module06_pass_flow(real):
    h, risk, _ = real
    out = h.ingest(1)
    v = h.view(out["approval_id"])
    assert v["risk_decision"] in ("PASS", "REVIEW"), v
    assert v["risk_assessment_id"] and v["position_size"] and Decimal(str(v["position_size"])) > 0
    res = h.approve(out["approval_id"])
    assert res["status"] == "EXECUTION_PENDING", res
    # the intake assessment was released before the fresh handoff assessment (otherwise R047 would block it)
    first = risk.validate_assessment(v["risk_assessment_id"])
    assert first["valid"] is False
    handoff = risk.validate_assessment(h.view(out["approval_id"])["risk_assessment_id"])
    assert handoff["valid"] is True


def test_real_module06_lock_blocks_approval(real):
    h, risk, _ = real
    aid = h.ingest(2)["approval_id"]
    risk.create_lock({"scope": "global", "reason": "Integration test emergency stop"}, "test", "c-lock")
    with pytest.raises(ApprovalError) as e:
        h.approve(aid)
    assert any("lock" in d.lower() or "never be approved" in d for d in e.value.details)


def test_real_module06_revalidation_and_reject_free_reservation(real):
    h, risk, _ = real
    aid = h.ingest(3)["approval_id"]
    old = h.view(aid)["risk_assessment_id"]
    new = h.svc.revalidate_risk(aid, h.users["tom"])
    assert new["risk_assessment_id"] != old and new["risk_decision"] in ("PASS", "REVIEW")
    assert risk.validate_assessment(old)["valid"] is False
    h.svc.reject(aid, h.users["tom"], {"client_request_id": "r", "request_version": new["version"],
                                        "reason_code": "trader_discretion"})
    assert risk.validate_assessment(new["risk_assessment_id"])["valid"] is False


def test_real_module06_block_on_bad_stop(real):
    h, _, data = real
    data.equity = data.balance = Decimal("100")      # tiny account: minimum lot exceeds the risk budget
    aid = h.ingest(4, stop_loss="1.05000")["approval_id"]
    v = h.view(aid)
    assert v["risk_decision"] in ("BLOCK", "ERROR") and v["can_approve"] is False
