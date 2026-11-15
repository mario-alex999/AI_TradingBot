"""WF08-04 Human Approval Notification: which ideas need a person, validity checks, the Module 07 hand-off, API.

Docx test stage covered here: (6) an idea Module 06 passed produces one Module 07 approval request and one notification
with everything a reviewer needs; closed, expired, blocked or already notified ideas produce none.

Module 07 is real here (its service, tables and notification rows). Module 06 is a stand-in.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from m08_harness import ALICE, NOW
from test_wf08_03 import idea

from aurum.approvals.db_models import ApprovalRequest
from aurum.approvals.gateways import Module05Source
from aurum.approvals.service import ApprovalService
from aurum.auth.service import AuthService
from aurum.database.connection import session_scope
from aurum.decision.db_models import CandidateEvidence
from aurum.decision.repository import DecisionRepository
from aurum.orchestration import approval_notifications as an
from aurum.orchestration.approval_notifications import ApprovalNotifier, install
from sqlalchemy import select

P = "/api/v1/orchestration"


class RiskGateway:
    """Module 06 as Module 07 sees it (its intake assessment)."""

    def __init__(self, clock, decision="PASS"):
        self.clock, self.decision, self.n = clock, decision, 0

    def assess(self, proposal, actor, correlation_id, idempotency_key):
        self.n += 1
        return {"assessment_id": f"m07-{self.n}", "decision": self.decision, "final_volume": "0.10", "risk_pct": "0.50",
                "monetary_risk": "20.00", "potential_reward": "50.00", "risk_reward": "2.50", "warnings": [],
                "rule_violations": [], "errors": [], "config_version": 1, "timestamp": self.clock(),
                "expires_at": self.clock() + timedelta(minutes=5), "market_status": {"volatility_regime": "NORMAL"}}

    def validate(self, assessment_id): return {"valid": True, "reasons": []}
    def cancel(self, *a, **k): return None
    def normalised_entry(self, uid): return None
    def quote_time(self, symbol, now): return self.clock()
    def symbols(self): return {"EURUSD": "forex", "XAUUSD": "metal", "GBPUSD": "forex", "US30": "index"}
    def account_login(self): return "1001"
    def active_locks(self, symbol=None, strategy=None): return []


class Prescreen:
    """Module 06 as WF08-04 sees it: the assessments WF08-03 requested."""

    def __init__(self):
        self.rows: dict[str, dict] = {}
        self.cancelled: list[str] = []
        self.invalid: set[str] = set()

    def passed(self, idea_id, decision="PASS", state="active"):
        self.rows[idea_id] = {"assessment_id": f"pre-{idea_id[:8]}", "decision": decision, "state": state}

    def validate_assessment(self, assessment_id):
        bad = assessment_id in self.invalid
        return {"valid": not bad, "reasons": ["Assessment has expired; request a new one."] if bad else []}

    def set_assessment_state(self, assessment_id, state, actor, reason, correlation_id):
        self.cancelled.append(assessment_id)


class Notifier(ApprovalNotifier):
    def _prescreened(self, idea_ids=None):
        return {k: v for k, v in self.risk.rows.items() if not idea_ids or k in idea_ids}


class _Settings:
    enabled_instruments: list[str] = []
    approvals_config = None
    approvals_events_webhook_url = None
    approvals_worker_interval_seconds = 15
    notify_email_enabled = False
    smtp_host = smtp_from = smtp_username = smtp_password = None
    smtp_port = 587
    smtp_use_tls = True
    telegram_bot_token = telegram_chat_id = None
    aurum_dashboard_url = "https://aurum.example.test/"


@pytest.fixture
def world(h):
    auth = AuthService(h.engine, iterations=1000)
    for name, role in (("alice", "admin"), ("tom", "trader")):
        auth.create_user(name, "correct-horse-battery-9", role, name.title(), f"{name}@example.test")
    decision = SimpleNamespace(repo=DecisionRepository(h.engine), ai=SimpleNamespace(audit=SimpleNamespace(latest=lambda uid: None)))
    approvals = ApprovalService(h.engine, _Settings(), RiskGateway(h.clock), Module05Source(lambda: decision), None, None, clock=h.clock)
    pre = Prescreen()
    h.svc.settings = _Settings() if False else h.svc.settings
    notifier = Notifier(h.svc, approvals=lambda: approvals, risk=lambda: pre)
    notifier._dashboard = lambda: "https://aurum.example.test"
    return h, pre, approvals, notifier


def evidence(h, uid):
    with session_scope(h.engine) as s:
        s.add(CandidateEvidence(candidate_uid=uid, component="trend", points=18.0, max_points=20.0,
                                sentence="H4 and D1 trend agree with the trade.", sources=[]))
        s.add(CandidateEvidence(candidate_uid=uid, component="cot", points=1.0, max_points=15.0,
                                sentence="COT positioning does not support the direction.", sources=[]))


def set_cfg(h, **changes):
    cfg = h.svc.config().dump()
    cfg["approval_notifications"].update(changes)
    h.svc.put_config(ALICE, {"reason": "test", "config": cfg})


# ---- which ideas need a person --------------------------------------------------------------------------------------------------
def test_pending_is_only_ideas_module_06_passed_and_module_07_has_not_seen(world):
    h, pre, _, notifier = world
    passed, blocked, unassessed, review = idea(h), idea(h, symbol="XAUUSD"), idea(h, symbol="GBPUSD"), idea(h, symbol="US30")
    pre.passed(passed)
    pre.passed(blocked, "BLOCK")
    pre.passed(review, "REVIEW")
    assert sorted(i["idea_id"] for i in notifier.pending()["items"]) == sorted([passed, review])
    assert notifier.status(blocked)["reasons"] == ["Module 06 did not pass this idea (BLOCK)."]
    assert "has not assessed" in notifier.status(unassessed)["reasons"][0]
    assert notifier.status("nope")["exists"] is False


def test_check_still_valid_before_notifying(world):
    h, pre, approvals, notifier = world
    expired = idea(h, expires_in=timedelta(minutes=-1))
    pre.passed(expired)
    assert "expired" in notifier.status(expired)["reasons"][0] and notifier.notify(expired)["status"] == "skipped"
    stale = idea(h)
    pre.passed(stale)
    pre.invalid.add(pre.rows[stale]["assessment_id"])
    st = notifier.status(stale)
    assert st["ok"] is False and "expired" in st["reasons"][0]
    cancelled = idea(h, symbol="GBPUSD")
    pre.passed(cancelled, state="cancelled")
    assert notifier.status(cancelled)["reasons"] == ["The risk assessment is cancelled."]
    with approvals.repo.read() as s:
        assert s.execute(select(ApprovalRequest)).first() is None            # nobody was asked


# ---- the hand-off to Module 07 ---------------------------------------------------------------------------------------------------
def test_notify_opens_one_request_and_reports_a_complete_notification(world):
    h, pre, approvals, notifier = world
    uid = idea(h)
    evidence(h, uid)
    pre.passed(uid)
    out = notifier.notify(uid, execution_id="exec-1")
    assert out["status"] == "notified" and out["waiting_for"] == "Module 07 approval decision", out
    n = out["notification"]
    for field in ("symbol", "direction", "strategy", "entry_zone", "stop_loss", "take_profit", "risk_reward", "evidence_score",
                  "supporting_reasons", "conflicting_evidence", "risk_assessment", "expires_at", "review_url"):
        assert n.get(field) not in (None, ""), field
    assert n["symbol"] == "EURUSD" and n["risk_assessment"]["decision"] == "PASS" and n["entry_zone"] == [1.10, 1.101]
    assert n["supporting_reasons"] == ["H4 and D1 trend agree with the trade."]
    assert n["conflicting_evidence"] == ["COT positioning does not support the direction."]
    assert n["review_url"] == f"https://aurum.example.test/approvals/{out['approval_id']}"
    assert "cannot approve" in n["note"]
    # Module 07 really holds the request, and the reviewers have in-app notifications.
    with approvals.repo.read() as s:
        req = s.get(ApprovalRequest, out["approval_id"])
        assert req.status == "PENDING" and req.idea_id == uid and req.risk_decision == "PASS"
    assert out["deliveries"]["in_app"] and sum(out["deliveries"]["in_app"].values()) >= 2
    # WF08-03's assessment is released: Module 07's own assessment now carries the reservation.
    assert pre.cancelled == [pre.rows[uid]["assessment_id"]]
    # Asked once: a second run does not open another request.
    again = notifier.notify(uid)
    assert again["status"] == "skipped" and again["approval_id"] == out["approval_id"]
    assert notifier.pending()["count"] == 0
    with approvals.repo.read() as s:
        assert len(s.execute(select(ApprovalRequest)).all()) == 1


def test_dry_run_off_switch_and_module_07_refusal(world):
    h, pre, approvals, notifier = world
    uid = idea(h)
    pre.passed(uid)
    assert notifier.notify(uid, dry_run=True)["status"] == "dry_run"
    unknown_symbol = idea(h, symbol="BTCUSD")            # Module 07 does not accept this instrument
    pre.passed(unknown_symbol)
    out = notifier.notify(unknown_symbol)
    assert out["status"] == "failed" and "Module 07 could not open" in out["reason"] and pre.cancelled == []
    set_cfg(h, enabled=False)
    assert notifier.notify(uid)["status"] == "skipped" and notifier.pending()["items"] == []
    assert notifier.preflight()["proceed"] is False
    with approvals.repo.read() as s:
        assert s.execute(select(ApprovalRequest)).first() is None


# ---- installation and coalescing --------------------------------------------------------------------------------------------------
def test_install_registers_the_workflow_and_shares_one_run_per_processing_run(h):
    from test_wf08_03 import analysis_completed  # noqa: F401 - same harness helpers
    from aurum.orchestration.catalogue import M06, Envelope
    import uuid
    h.n8n.workflows["n8nWF0804"] = {"id": "n8nWF0804", "active": True, "webhook_paths": [an.WEBHOOK_PATH]}
    out = install(h.svc, ALICE, "n8nWF0804")
    assert out["state"] == "ACTIVE" and "registered" in out["steps"]
    assert sorted(s["event_pattern"] for s in out["subscriptions"]) == sorted(an.TRIGGER_EVENTS)
    again = install(h.svc, ALICE, "n8nWF0804")
    assert "registered" not in again["steps"] and len([s for s in h.svc.list_subscriptions()["items"]
                                                        if s["workflow_id"] == an.WORKFLOW_ID]) == 2

    def risk_event(etype, corr):
        payload = {"assessment_id": str(uuid.uuid4()), "decision": "PASS"} if etype.endswith("completed") else {"assessment_id": str(uuid.uuid4())}
        env = Envelope(event_id=str(uuid.uuid4()), event_type=etype, event_version="1.0", occurred_at=h.clock(), source_module=M06,
                       correlation_id=corr, idempotency_key=f"t:{uuid.uuid4().hex}", environment=h.svc.env, payload=payload,
                       published_at=h.clock())
        with h.svc.repo.write() as s:
            assert h.svc._insert_event(s, env, "internal") is not None
    # One WF08-03 run passed two ideas, one PASS and one REVIEW (two event types, two subscriptions): one WF08-04 run.
    risk_event("risk.assessment.completed", "wf0803-run-A")
    risk_event("risk.assessment.requires_review", "wf0803-run-A")
    h.svc.deliver_due()
    assert len(h.svc.list_executions(workflow_id=an.WORKFLOW_ID)["items"]) == 1
    risk_event("risk.assessment.completed", "wf0803-run-B")
    h.svc.deliver_due()
    assert len(h.svc.list_executions(workflow_id=an.WORKFLOW_ID)["items"]) == 2


# ---- HTTP API -----------------------------------------------------------------------------------------------------------------
def test_api_routes_and_who_may_notify(world, monkeypatch):
    from aurum.api.app import create_app
    from aurum.config import Settings
    h, pre, approvals, _ = world
    monkeypatch.setattr(ApprovalNotifier, "risk", property(lambda self: pre))
    monkeypatch.setattr(ApprovalNotifier, "approvals", property(lambda self: approvals))
    monkeypatch.setattr(ApprovalNotifier, "_prescreened", Notifier._prescreened)
    auth = AuthService(h.engine, iterations=1000)
    api = TestClient(create_app(settings=Settings(_env_file=None, database_url="sqlite://", aurum_dashboard_url="https://aurum.example.test"),
                                engine=h.engine, auth_service=auth, orchestration_service=h.svc))
    uid = idea(h)
    pre.passed(uid)
    assert api.get(f"{P}/approval-notifications/pending").status_code == 401
    token, _ = auth.create_service_token("n8n-wf0804", "test", ["orchestration:view", "orchestration:callback"])
    hdr = {"Authorization": f"Bearer {token}"}
    assert api.get(f"{P}/approval-notifications/config", headers=hdr).json()["triggerEvents"] == list(an.TRIGGER_EVENTS)
    assert api.get(f"{P}/approval-notifications/preflight", headers=hdr).json()["proceed"] is True
    assert api.get(f"{P}/approval-notifications/pending", headers=hdr).json()["items"][0]["ideaId"] == uid
    assert api.get(f"{P}/approval-notifications/status", params={"ideaId": uid}, headers=hdr).json()["ok"] is True
    ro, _ = auth.create_service_token("read-only", "test", ["orchestration:view"])
    assert api.post(f"{P}/approval-notifications/notify", json={"ideaId": uid}, headers={"Authorization": f"Bearer {ro}"}).status_code == 403
    assert api.post(f"{P}/approval-notifications/notify", json={}, headers=hdr).status_code == 422
    r = api.post(f"{P}/approval-notifications/notify", json={"ideaId": uid, "executionId": "exec-4"}, headers=hdr)
    assert r.status_code == 200 and r.json()["status"] == "notified" and r.json()["notification"]["symbol"] == "EURUSD", r.text
    assert api.get(f"{P}/approval-notifications/pending", headers=hdr).json()["count"] == 0


def test_real_queries_on_an_empty_database(h):
    """No stand-ins for the database reads: Module 06's assessment table and Module 07's request table."""
    notifier = ApprovalNotifier(h.svc)
    assert notifier._prescreened() == {} and notifier._prescreened(["x"]) == {}
    assert notifier._requests(["x"]) == {} and notifier._deliveries("x") == {}
    pre = notifier.preflight()
    assert pre["proceed"] is True and pre["checks"]["module_06_schema"] and pre["checks"]["module_07_schema"], pre
    assert notifier.pending() == {"enabled": True, "items": [], "count": 0, "deferred": 0}
    uid = idea(h)
    assert notifier.notify(uid)["status"] == "skipped"           # Module 06 has not passed it: nobody is asked
