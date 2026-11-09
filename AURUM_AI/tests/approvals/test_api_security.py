"""HTTP-level security: sign-in, CSRF, roles, service tokens, structured errors, camelCase responses."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from aurum.api.app import create_app
from m07_harness import PASSWORD


@pytest.fixture
def api(h):
    from aurum.config import Settings
    settings = Settings(_env_file=None, database_url="sqlite://", api_cors_origins=["http://localhost:5173"])
    app = create_app(settings=settings, engine=h.engine, auth_service=h.auth, approval_service=h.svc)
    return TestClient(app)


def login(api, user="tom"):
    r = api.post("/api/v1/auth/login", json={"username": user, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return r.json()["csrfToken"]


def body_for(h, aid):
    b = h.approve_body(aid)
    return {"clientRequestId": b["client_request_id"], "requestVersion": b["request_version"], "evidenceHash": b["evidence_hash"],
            "acknowledgedWarnings": b["acknowledged_warnings"],
            "confirmation": {"symbol": b["confirmation"]["symbol"], "direction": b["confirmation"]["direction"],
                             "entry": b["confirmation"]["entry"], "stopLoss": b["confirmation"]["stop_loss"],
                             "takeProfit": b["confirmation"]["take_profit"], "positionSize": b["confirmation"]["position_size"],
                             "maxRiskAmount": b["confirmation"]["max_risk_amount"]}}


def test_requires_sign_in(api):
    r = api.get("/api/v1/approvals")
    assert r.status_code == 401 and r.json()["error"]["code"] == "NOT_AUTHENTICATED"


def test_wrong_password_and_lockout(api, h):
    for _ in range(5):
        r = api.post("/api/v1/auth/login", json={"username": "rita", "password": "wrong-password-123"})
        assert r.status_code == 401 and r.json()["error"]["code"] == "INVALID_CREDENTIALS"
    r = api.post("/api/v1/auth/login", json={"username": "rita", "password": PASSWORD})
    assert r.status_code in (401, 423)          # locked: the right password no longer works for a while
    assert "password" not in r.text.lower() or "incorrect" in r.text.lower() or "locked" in r.text.lower()


def test_cookie_is_httponly_and_csrf_enforced(api, h):
    r = api.post("/api/v1/auth/login", json={"username": "tom", "password": PASSWORD})
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie
    aid = h.ingest(1)["approval_id"]
    no_csrf = api.post(f"/api/v1/approvals/{aid}/approve", json=body_for(h, aid))
    assert no_csrf.status_code == 403 and no_csrf.json()["error"]["code"] == "CSRF_FAILED"
    bad = api.post(f"/api/v1/approvals/{aid}/approve", json=body_for(h, aid), headers={"X-CSRF-Token": "forged"})
    assert bad.status_code == 403
    ok = api.post(f"/api/v1/approvals/{aid}/approve", json=body_for(h, aid), headers={"X-CSRF-Token": r.json()["csrfToken"]})
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "EXECUTION_PENDING" and "approvalId" in ok.json()


def test_get_detail_is_camelcase_and_read_only(api, h):
    login(api)
    aid = h.ingest(1)["approval_id"]
    r = api.get(f"/api/v1/approvals/{aid}")
    assert r.status_code == 200
    j = r.json()
    assert j["request"]["status"] == "PENDING" and "riskDecision" in j["request"] and "disclaimer" in j
    assert h.view(aid)["status"] == "PENDING"        # viewing never changes anything


def test_observer_cannot_decide(api, h):
    csrf = login(api, "olly")
    aid = h.ingest(1)["approval_id"]
    r = api.post(f"/api/v1/approvals/{aid}/approve", json=body_for(h, aid), headers={"X-CSRF-Token": csrf})
    assert r.status_code == 403 and r.json()["error"]["code"] in ("FORBIDDEN", "HUMAN_REQUIRED")


def test_service_token_cannot_approve_even_with_scope(api, h):
    token, _ = h.auth.create_service_token("n8n-test", "test", ["approvals:view", "approvals:decide", "events:publish"])
    aid = h.ingest(1)["approval_id"]
    auth = {"Authorization": f"Bearer {token}"}
    assert api.get("/api/v1/approvals", headers=auth).status_code == 200
    r = api.post(f"/api/v1/approvals/{aid}/approve", json=body_for(h, aid), headers=auth)
    assert r.status_code == 403
    assert h.view(aid)["status"] == "PENDING"
    bad = api.get("/api/v1/approvals", headers={"Authorization": "Bearer not-a-token"})
    assert bad.status_code == 401


def test_structured_error_on_block(api, h):
    h.risk.decision = "BLOCK"
    csrf = login(api)
    aid = h.ingest(1)["approval_id"]
    r = api.post(f"/api/v1/approvals/{aid}/approve", json=body_for(h, aid), headers={"X-CSRF-Token": csrf})
    assert r.status_code == 409
    e = r.json()["error"]
    assert e["code"] == "APPROVAL_CHECKS_FAILED" and e["correlationId"] and isinstance(e["details"], list)


def test_logout_ends_session(api):
    csrf = login(api)
    assert api.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf}).status_code == 200
    assert api.get("/api/v1/approvals").status_code == 401


def test_session_idle_timeout(api, h):
    login(api)
    assert api.get("/api/v1/approvals").status_code == 200
    h.clock.advance(minutes=31)
    assert api.get("/api/v1/approvals").status_code == 401


def test_module09_contract_endpoints(api, h):
    token, _ = h.auth.create_service_token("module09", "test", ["execution:read", "execution:report"])
    auth = {"Authorization": f"Bearer {token}"}
    aid = h.ingest(1)["approval_id"]
    h.approve(aid)
    q = api.get("/api/v1/approvals/execution/queue", headers=auth).json()["items"]
    assert len(q) == 1
    r = api.post("/api/v1/approvals/execution/results", headers=auth,
                 json={"eventId": "e-1", "executionRequestId": q[0]["executionRequestId"], "status": "filled", "positionId": "1"})
    assert r.status_code == 200 and r.json()["duplicate"] is False
    assert h.view(aid)["status"] == "EXECUTED"


def test_management_and_dashboard_endpoints(api, h):
    login(api)
    h.ingest(1)
    d = api.get("/api/v1/approvals/dashboard").json()
    assert d["counts"]["PENDING"] == 1 and len(d["pending"]) == 1
    p = api.get("/api/v1/trade-management/positions").json()
    assert p["source"] == "account_mirror" and "Module 09 is not built" in p["note"]
    assert api.get("/api/v1/approvals/health").json()["dependencies"]["module09"]["ok"] is False


def test_notifications_feed_merges_stored_for_signed_in_person(api, h):
    h.ingest(1)
    anon = api.get("/api/v1/notifications").json()
    assert not [n for n in anon if n.get("source") == "approvals"]
    login(api, "tom")
    mine = [n for n in api.get("/api/v1/notifications").json() if n.get("source") == "approvals"]
    assert mine and mine[0]["category"] == "approval_required" and mine[0]["read"] is False


def test_decisions_are_rate_limited_per_person(api, h):
    csrf = login(api)
    codes = [api.post("/api/v1/approvals/nope/reject", json={"reasonCode": "other"}, headers={"X-CSRF-Token": csrf}).status_code
             for _ in range(31)]
    assert codes[:30] == [404] * 30                     # each call counts, even refused ones
    r = api.post("/api/v1/approvals/nope/approve", json={}, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 429 and r.headers["Retry-After"] == "60" and r.json()["error"]["code"] == "RATE_LIMITED"
    assert codes[30] == 429


def test_event_stream_sends_outbox_events(api, h):
    aid = h.ingest(1)["approval_id"]
    login(api)
    with api.stream("GET", "/api/v1/approvals/events/stream?after=0") as r:
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        seen = ""
        for chunk in r.iter_text():
            seen += chunk
            if "approval.requested" in seen:
                break
    assert "retry: 3000" in seen and "event: approval.requested" in seen and aid in seen
    assert '"eventType"' in seen                         # camelCase envelope
