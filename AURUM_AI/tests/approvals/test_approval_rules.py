"""Section 8 approval checks, one by one, plus rejection, cancellation, revalidation and idempotency."""
from __future__ import annotations

from datetime import timedelta

import pytest

from aurum.approvals.policy import Policy
from aurum.approvals.service import ApprovalError


def refused(h, aid, who="tom", code="APPROVAL_CHECKS_FAILED", text=None, **over):
    with pytest.raises(ApprovalError) as e:
        h.approve(aid, who, **over)
    assert e.value.code == code, (e.value.code, e.value.details)
    if text:
        assert any(text in d for d in e.value.details), e.value.details
    return e.value


def test_review_needs_every_warning_acknowledged(h):
    h.risk.decision = "REVIEW"
    h.risk.warnings = [{"rule_id": "R030", "name": "spread", "message": "Spread is wide"},
                       {"rule_id": "R041", "name": "news", "message": "News in 30 min"}]
    aid = h.ingest(1)["approval_id"]
    refused(h, aid, text="Acknowledge every risk warning", acknowledged_warnings=["R030"])
    assert h.approve(aid)["status"] == "EXECUTION_PENDING"


def test_review_blocked_by_policy(tmp_path, m07_template_root):
    from m07_harness import H
    p = Policy()
    p.review.risk_review_policy = "block"
    h = H(tmp_path, m07_template_root, policy=p)
    h.risk.decision = "REVIEW"
    aid = h.ingest(1)["approval_id"]
    refused(h, aid, text="does not allow approving")


def test_error_decision_never_approvable(h):
    h.risk.unavailable = True
    aid = h.ingest(1)["approval_id"]            # Module 06 down at intake: the request opens as ERROR
    h.risk.unavailable = False
    assert h.view(aid)["risk_decision"] == "ERROR"
    refused(h, aid, text="never be approved")


def test_confirmation_must_match(h):
    aid = h.ingest(1)["approval_id"]
    body = h.approve_body(aid)
    body["confirmation"]["stop_loss"] = "1.09000"
    with pytest.raises(ApprovalError) as e:
        h.svc.approve(aid, h.users["tom"], body)
    assert any("do not match" in d for d in e.value.details)
    body = h.approve_body(aid)
    body["confirmation"]["max_risk_amount"] = None
    with pytest.raises(ApprovalError):
        h.svc.approve(aid, h.users["tom"], body)


def test_evidence_hash_and_version_must_be_current(h):
    aid = h.ingest(1)["approval_id"]
    refused(h, aid, text="evidence changed", evidence_hash="0" * 64)
    refused(h, aid, code="STALE_VERSION", request_version=99)


def test_old_risk_assessment_needs_revalidation(h):
    aid = h.ingest(1)["approval_id"]
    h.clock.advance(minutes=6)                    # policy: 300 s
    refused(h, aid, text="older than")
    h.svc.revalidate_risk(aid, h.users["tom"])
    assert h.approve(aid)["status"] == "EXECUTION_PENDING"


def test_invalid_assessment_in_module06(h):
    aid = h.ingest(1)["approval_id"]
    h.risk.invalid.add(h.view(aid)["risk_assessment_id"])
    refused(h, aid, text="no longer valid")


def test_active_lock_blocks(h):
    aid = h.ingest(1)["approval_id"]
    h.risk.locks = [{"scope": "global", "reason": "Emergency stop"}]
    refused(h, aid, text="Emergency stop")


def test_module06_down_at_approval_refuses(h):
    aid = h.ingest(1)["approval_id"]
    h.risk.unavailable = True
    refused(h, aid, text="unavailable")
    assert h.view(aid)["status"] == "PENDING"


def test_permissions(h):
    aid = h.ingest(1)["approval_id"]
    with pytest.raises(ApprovalError) as e:
        h.approve(aid, "olly")                    # observer
    assert e.value.status == 403
    assert h.approve(aid, "rita")["status"] == "EXECUTION_PENDING"     # reviewers may decide


def test_service_token_can_never_approve(h):
    aid = h.ingest(1)["approval_id"]
    svc = h.service_principal(["approvals:view", "approvals:decide", "events:publish"])
    assert not svc.can("approvals:decide")        # human-only permissions are stripped from tokens
    with pytest.raises(ApprovalError) as e:
        h.svc.approve(aid, svc, h.approve_body(aid))
    assert e.value.status == 403


def test_idempotent_replay_and_conflict(h):
    aid = h.ingest(1)["approval_id"]
    body = h.approve_body(aid)
    first = h.svc.approve(aid, h.users["tom"], body)
    again = h.svc.approve(aid, h.users["tom"], body)
    assert again.get("replayed") is True and again["approval_id"] == first["approval_id"]
    with pytest.raises(ApprovalError) as e:
        h.svc.approve(aid, h.users["tom"], {**body, "comment": "different"})
    assert e.value.code == "IDEMPOTENCY_CONFLICT"
    with pytest.raises(ApprovalError) as e:
        h.svc.approve(aid, h.users["tom"], {**body, "client_request_id": None})
    assert e.value.code == "CLIENT_REQUEST_ID_REQUIRED"


def test_reject_requires_reason_and_frees_risk(h):
    aid = h.ingest(1)["approval_id"]
    v = h.view(aid)
    with pytest.raises(ApprovalError) as e:
        h.svc.reject(aid, h.users["tom"], {"client_request_id": "r1", "request_version": v["version"]})
    assert e.value.code == "REASON_REQUIRED"
    with pytest.raises(ApprovalError) as e:
        h.svc.reject(aid, h.users["tom"], {"client_request_id": "r2", "request_version": v["version"], "reason_code": "other"})
    assert e.value.code == "COMMENT_REQUIRED"
    out = h.svc.reject(aid, h.users["tom"], {"client_request_id": "r3", "request_version": v["version"],
                                              "reason_code": "trader_discretion"})
    assert out["status"] == "REJECTED"
    assert v["risk_assessment_id"] in h.risk.cancelled
    refused(h, aid, code="NOT_PENDING")


def test_cancel_and_open_review_never_approves(h):
    aid = h.ingest(1)["approval_id"]
    assert h.svc.start_review(aid, h.users["rita"])["status"] == "UNDER_REVIEW"
    assert h.svc.start_review(aid, h.users["tom"])["status"] == "UNDER_REVIEW"
    with pytest.raises(ApprovalError):
        h.svc.cancel(aid, h.users["tom"], "  ")
    assert h.svc.cancel(aid, h.users["tom"], "Changed my mind")["status"] == "CANCELLED"


def test_revalidation_to_block_stops_approval(h):
    aid = h.ingest(1)["approval_id"]
    h.risk.decision = "BLOCK"
    out = h.svc.revalidate_risk(aid, h.users["tom"])
    assert out["risk_decision"] == "BLOCK" and out["version"] == 2
    refused(h, aid, text="never be approved")


def test_multi_person_approval(tmp_path, m07_template_root):
    from m07_harness import H
    p = Policy()
    p.review.multi_person.enabled = True
    p.review.multi_person.required_approvals = 2
    h = H(tmp_path, m07_template_root, policy=p)
    aid = h.ingest(1)["approval_id"]
    first = h.approve(aid, "tom")
    assert first["status"] == "PENDING"
    with pytest.raises(ApprovalError) as e:
        h.approve(aid, "tom")
    assert e.value.code == "ALREADY_DECIDED"
    assert h.approve(aid, "rita")["status"] == "EXECUTION_PENDING"


def test_account_scope_hides_other_accounts(h):
    aid = h.ingest(1)["approval_id"]          # account 1001
    other = h.user("zed", "trader", account_scope=["2002"])
    with pytest.raises(ApprovalError) as e:
        h.svc.detail(aid, other)
    assert e.value.status == 404
    assert h.svc.list_requests(other)["total"] == 0


def test_handoff_failure_invalidates_and_publishes_nothing(h):
    aid = h.ingest(1)["approval_id"]
    h.risk.warnings = [{"rule_id": "R099", "message": "new warning"}]        # appears only at final validation
    out = h.approve(aid)
    assert out["status"] == "INVALIDATED" and "New risk warnings" in out["status_reason"]
    assert not [e for e in h.svc.repo.events(0, 500) if e["event_type"] == "trade.approved_for_execution"]


def test_handoff_deferred_while_module06_down_then_retried(h):
    aid = h.ingest(1)["approval_id"]
    body = h.approve_body(aid)
    orig = h.risk.validate
    state = {"n": 0}

    def flaky(a):
        state["n"] += 1
        if state["n"] >= 2:          # first call is the approval check; later calls (handoff) fail
            h.risk.unavailable = True
        try:
            return orig(a)
        finally:
            pass
    h.risk.validate = flaky
    out = h.svc.approve(aid, h.users["tom"], body)
    assert out["status"] == "APPROVED"           # approval stands; nothing published yet
    h.risk.unavailable = False
    h.risk.validate = orig
    assert h.svc.handoff_due() == 1
    assert h.view(aid)["status"] == "EXECUTION_PENDING"


def test_handoff_past_deadline_invalidates(h):
    aid = h.ingest(1)["approval_id"]
    body = h.approve_body(aid)
    h.risk.unavailable = False
    orig = h.risk.validate
    calls = {"n": 0}

    def down_after_first(a):
        calls["n"] += 1
        if calls["n"] > 1:
            from aurum.approvals.gateways import GatewayUnavailable
            raise GatewayUnavailable("down")
        return orig(a)
    h.risk.validate = down_after_first
    assert h.svc.approve(aid, h.users["tom"], body)["status"] == "APPROVED"
    h.clock.advance(minutes=90)
    h.svc.handoff_due()
    assert h.view(aid)["status"] == "INVALIDATED"


def test_execution_results_are_deduplicated_and_forward_only(h):
    from m07_harness import FakeExecution
    aid = h.ingest(1)["approval_id"]
    h.approve(aid)
    m09 = FakeExecution(h)
    job = m09.take()[0]
    r1 = h.svc.report_execution(m09.p, {"event_id": "e1", "execution_request_id": job["execution_request_id"], "status": "filled",
                                        "position_id": "77"})
    r2 = h.svc.report_execution(m09.p, {"event_id": "e1", "execution_request_id": job["execution_request_id"], "status": "filled"})
    assert r1["duplicate"] is False and r2["duplicate"] is True
    m09.report(job["execution_request_id"], "submitted")       # late, out of order: ignored
    assert h.view(aid)["status"] == "EXECUTED"
    with pytest.raises(ApprovalError):
        h.svc.report_execution(m09.p, {"event_id": "e9", "execution_request_id": "nope", "status": "filled"})


def test_expiry_job(h):
    a = h.ingest(1, timeframe="M15")["approval_id"]
    b = h.ingest(2, timeframe="D1")["approval_id"]
    h.clock.advance(minutes=14)
    out = h.svc.expire_due()
    assert out["expired"] == 0 and out["expiring_soon"] == 1
    assert h.svc.expire_due()["expiring_soon"] == 0           # notified once
    h.clock.advance(minutes=2)
    assert h.svc.expire_due()["expired"] == 1
    assert h.view(a)["status"] == "EXPIRED" and h.view(b)["status"] == "PENDING"
    assert h.svc.expire_due()["expired"] == 0                 # idempotent


def test_approved_request_is_never_expired(h):
    aid = h.ingest(1, timeframe="M15")["approval_id"]
    h.risk.unavailable = False
    orig = h.risk.validate
    calls = {"n": 0}

    def down_after_first(a):
        calls["n"] += 1
        if calls["n"] > 1:
            from aurum.approvals.gateways import GatewayUnavailable
            raise GatewayUnavailable("down")
        return orig(a)
    h.risk.validate = down_after_first
    assert h.approve(aid)["status"] == "APPROVED"
    h.clock.advance(minutes=16)
    h.svc.expire_due()
    assert h.view(aid)["status"] == "APPROVED"


def test_idea_expiry_caps_request_expiry(h):
    aid = h.ingest(1, timeframe="D1", expires_at=(h.clock() + timedelta(minutes=30)).isoformat())["approval_id"]
    v = h.view(aid)
    assert v["expires_at"] == h.clock() + timedelta(minutes=30)


def test_audit_chain_detects_tampering(h):
    aid = h.ingest(1)["approval_id"]
    h.approve(aid)
    assert h.svc.repo.verify_audit()[0]
    from sqlalchemy import text
    with h.engine.begin() as c:
        c.execute(text("UPDATE approval_audit_logs SET reason = 'edited' WHERE seq = 2"))
    ok, _, err = h.svc.repo.verify_audit()
    assert not ok and "2" in err
