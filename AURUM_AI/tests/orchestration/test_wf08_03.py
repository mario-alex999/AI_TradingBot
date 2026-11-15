"""WF08-03 Trade Opportunity Processor: pending ideas, status and expiry checks, the Module 06 hand-off, coalescing, API.

Docx test stage covered here: (5) a new Module 05 idea reaches Module 06 once, Module 06's decision is reported
unchanged, and closed, expired or already assessed ideas never reach it.
"""
from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from m08_harness import ALICE, NOW

from aurum.auth.service import AuthService
from aurum.database.connection import session_scope
from aurum.decision.db_models import TradeCandidate
from aurum.orchestration import trade_opportunities as to
from aurum.orchestration.catalogue import M05, Envelope
from aurum.orchestration.trade_opportunities import OpportunityProcessor, assessment_key, install

P = "/api/v1/orchestration"


class FakeRisk:
    """Stands in for Module 06: records what it was asked to assess and answers with a fixed decision."""

    def __init__(self, decision: str = "PASS", fail: bool = False):
        self.decision, self.fail = decision, fail
        self.calls: list[tuple[str, str]] = []
        self.stored: dict[str, dict] = {}

    def health(self):
        return {"status": "ok"}

    def assess(self, body, actor, correlation_id, idempotency_key):
        if self.fail:
            raise RuntimeError("module 06 exploded")
        if idempotency_key in self.stored:
            return {**self.stored[idempotency_key], "replayed": True}
        self.calls.append((body["trade_idea_id"], idempotency_key))
        out = {"assessment_id": f"ra-{len(self.calls)}", "decision": self.decision, "final_volume": 0.1, "risk_pct": 0.5,
               "risk_reward": 2.0, "violations": [{"rule_id": "max_daily_loss", "message": "Daily loss limit reached."}]
               if self.decision == "BLOCK" else []}
        self.stored[idempotency_key] = out
        return out


class Processor(OpportunityProcessor):
    """Reads 'already assessed' from the fake instead of Module 06's table."""

    def _assessed(self, keys):
        return {k: {"assessment_id": v["assessment_id"], "decision": v["decision"]}
                for k, v in self.risk.stored.items() if k in keys}


def idea(h, symbol="EURUSD", status="new", expires_in=timedelta(hours=4), entry=1.10) -> str:
    uid = str(uuid.uuid4())
    with session_scope(h.engine) as s:
        s.add(TradeCandidate(candidate_uid=uid, idempotent_key=uuid.uuid4().hex, symbol=symbol, timeframe="H1",
                             template="fair_value_gap_retracement", direction="long", as_of=NOW, entry_low=entry,
                             entry_high=entry + 0.001, stop_loss=entry - 0.005, take_profit=entry + 0.01, risk_reward=2.0,
                             deterministic_score=70.0, confidence=70.0, grade="B", status=status, expires_at=NOW + expires_in,
                             invalidation="below the gap", market_condition="trend", first_seen_run="r1", last_seen_run="r1",
                             input_hash="h"))
    return uid


@pytest.fixture
def world(h):
    risk = FakeRisk()
    return h, risk, Processor(h.svc, risk=lambda: risk)


def set_cfg(h, **changes):
    cfg = h.svc.config().dump()
    cfg["trade_opportunities"].update(changes)
    h.svc.put_config(ALICE, {"reason": "test", "config": cfg})


def analysis_completed(h, symbol: str, correlation: str):
    env = Envelope(event_id=str(uuid.uuid4()), event_type="ai.analysis.completed", event_version="1.0", occurred_at=h.clock(),
                   source_module=M05, correlation_id=correlation, idempotency_key=f"t:{uuid.uuid4().hex}", environment=h.svc.env,
                   payload={"symbol": symbol, "run_id": "run-1", "candidates": 1}, published_at=h.clock())
    with h.svc.repo.write() as s:
        assert h.svc._insert_event(s, env, "internal") is not None


# ---- pending ideas and the checks the workflow branches on -------------------------------------------------------------------
def test_pending_lists_only_open_unexpired_unassessed_ideas(world):
    h, risk, proc = world
    open_id = idea(h)
    idea(h, status="rejected")
    expired_id = idea(h, expires_in=timedelta(hours=-1))
    p = proc.pending()
    assert [i["idea_id"] for i in p["items"]] == [open_id] and p["items"][0]["symbol"] == "EURUSD"
    st = proc.idea_status(expired_id)
    assert st["ok"] is False and st["expired"] is True and "expired" in st["reasons"][0]
    assert proc.idea_status("nope")["exists"] is False
    # Asked for by id, every idea comes back with the reason it would be skipped.
    named = proc.pending([open_id, expired_id, "nope"])["items"]
    assert [i["ok"] for i in named] == [True, False, False]


def test_assess_hands_the_idea_to_module_06_once_and_reports_its_decision(world):
    h, risk, proc = world
    uid = idea(h)
    out = proc.assess(uid, execution_id="exec-1")
    assert out["status"] == "completed" and out["decision"] == "PASS" and out["outcome"] == "approved"
    assert out["assessment_id"] == "ra-1" and risk.calls == [(uid, proc.idea_status(uid)["assessment_key"])]
    assert proc.pending()["count"] == 0                                   # assessed: no longer pending
    again = proc.assess(uid)
    assert again["status"] == "skipped" and again["decision"] == "PASS" and len(risk.calls) == 1


def test_module_06_decisions_are_reported_unchanged(h):
    for decision, outcome in (("PASS", "approved"), ("REVIEW", "review"), ("BLOCK", "rejected"), ("ERROR", "error")):
        risk = FakeRisk(decision)
        out = Processor(h.svc, risk=lambda r=risk: r).assess(idea(h))
        assert (out["decision"], out["outcome"]) == (decision, outcome)
        if decision == "BLOCK":
            assert "Daily loss limit reached." in out["reason"]


def test_closed_expired_dry_run_off_switch_and_module_06_failure(h):
    risk = FakeRisk(fail=True)
    proc = Processor(h.svc, risk=lambda: risk)
    assert proc.assess(idea(h, status="approved"))["status"] == "skipped"
    assert proc.assess(idea(h, expires_in=timedelta(minutes=-5)))["status"] == "skipped"
    uid = idea(h)
    assert proc.assess(uid, dry_run=True)["status"] == "dry_run"
    out = proc.assess(uid)
    assert out["status"] == "failed" and out["outcome"] == "error" and "module 06 exploded" in out["reason"]
    set_cfg(h, enabled=False)
    assert proc.assess(uid)["status"] == "skipped" and proc.pending()["items"] == []
    assert proc.preflight()["proceed"] is False


def test_a_repriced_idea_gets_a_new_assessment_key(h):
    a = {"candidate_uid": "x", "entry_low": 1.1, "entry_high": 1.101, "stop_loss": 1.09, "take_profit": 1.12, "direction": "long"}
    assert assessment_key(a) == assessment_key(dict(a)) and assessment_key(a).startswith("wf08-03:x:")
    assert assessment_key({**a, "stop_loss": 1.08}) != assessment_key(a)


# ---- installation and coalescing ----------------------------------------------------------------------------------------------
def test_install_and_one_execution_per_analysis_run(h):
    h.n8n.workflows["n8nWF0803"] = {"id": "n8nWF0803", "active": True, "webhook_paths": [to.WEBHOOK_PATH]}
    out = install(h.svc, ALICE, "n8nWF0803")
    assert out["state"] == "ACTIVE" and out["placeholder"] is False and out["subscription"]["event_pattern"] == "ai.analysis.completed"
    assert any("already present" in s for s in install(h.svc, ALICE, "n8nWF0803")["steps"])
    for sym in ("EURUSD", "XAUUSD", "US30"):                   # one WF08-02 run analysed three symbols
        analysis_completed(h, sym, "wf0802-run-A")
    h.svc.deliver_due()
    assert len(h.svc.list_executions(workflow_id=to.WORKFLOW_ID)["items"]) == 1
    analysis_completed(h, "EURUSD", "wf0802-run-B")
    h.svc.deliver_due()
    assert len(h.svc.list_executions(workflow_id=to.WORKFLOW_ID)["items"]) == 2


# ---- HTTP API -----------------------------------------------------------------------------------------------------------------
def test_api_routes_and_who_may_request_an_assessment(h, monkeypatch):
    from aurum.api.app import create_app
    from aurum.config import Settings
    risk = FakeRisk("REVIEW")
    monkeypatch.setattr(OpportunityProcessor, "risk", property(lambda self: risk))
    monkeypatch.setattr(OpportunityProcessor, "_assessed", Processor._assessed)
    auth = AuthService(h.engine, iterations=1000)
    api = TestClient(create_app(settings=Settings(_env_file=None, database_url="sqlite://"), engine=h.engine,
                                auth_service=auth, orchestration_service=h.svc))
    uid = idea(h)
    assert api.get(f"{P}/trade-opportunities/pending").status_code == 401
    token, _ = auth.create_service_token("n8n-wf0803", "test", ["orchestration:view", "orchestration:callback"])
    hdr = {"Authorization": f"Bearer {token}"}
    assert api.get(f"{P}/trade-opportunities/config", headers=hdr).json()["triggerEvent"] == "ai.analysis.completed"
    assert api.get(f"{P}/trade-opportunities/preflight", headers=hdr).json()["proceed"] is True
    assert api.get(f"{P}/trade-opportunities/pending", headers=hdr).json()["items"][0]["ideaId"] == uid
    assert api.get(f"{P}/trade-opportunities/idea-status", params={"ideaId": uid}, headers=hdr).json()["ok"] is True
    ro, _ = auth.create_service_token("read-only", "test", ["orchestration:view"])
    assert api.post(f"{P}/trade-opportunities/assess", json={"ideaId": uid}, headers={"Authorization": f"Bearer {ro}"}).status_code == 403
    assert api.post(f"{P}/trade-opportunities/assess", json={}, headers=hdr).status_code == 422
    r = api.post(f"{P}/trade-opportunities/assess", json={"ideaId": uid, "executionId": "exec-3"}, headers=hdr)
    assert r.status_code == 200 and r.json()["decision"] == "REVIEW" and r.json()["outcome"] == "review", r.text
    assert api.get(f"{P}/trade-opportunities/pending", headers=hdr).json()["count"] == 0


def test_real_module_06_wiring_on_an_empty_database(h):
    """No stand-ins: the real Module 06 service and its assessment table."""
    proc = OpportunityProcessor(h.svc)
    assert proc._assessed(["wf08-03:none:0"]) == {}
    pre = proc.preflight()
    assert pre["checks"]["module_05_schema"] is True and pre["checks"]["database"] == "ok", pre
    uid = idea(h)
    assert proc.pending()["items"][0]["idea_id"] == uid
    out = proc.assess(uid)                       # Module 06 really decides (no account data here, so it does not pass)
    assert out["status"] in ("completed", "failed") and out.get("decision") != "PASS", out
    if out["status"] == "completed":
        assert proc.pending()["count"] == 0 and proc.idea_status(uid)["already_assessed"] is True
