"""HTTP layer: sign-in and roles, CSRF, service tokens vs people, n8n webhooks, Idempotency-Key, camelCase, metrics."""
from __future__ import annotations

import json
import uuid

import pytest
from fastapi.testclient import TestClient
from m08_harness import SECRET

from aurum.auth.service import AuthService
from aurum.orchestration.security import sign
from aurum.orchestration.service import TEST_WORKFLOW_ID

PASSWORD = "correct-horse-battery-9"
P = "/api/v1/orchestration"


@pytest.fixture
def world(ha):
    from aurum.api.app import create_app
    from aurum.config import Settings
    auth = AuthService(ha.engine, iterations=1000)
    for name, role in (("alice", "admin"), ("tom", "trader"), ("olly", "observer")):
        auth.create_user(name, PASSWORD, role, name.title(), f"{name}@example.test")
    settings = Settings(_env_file=None, database_url="sqlite://", api_cors_origins=["http://localhost:5173"])
    app = create_app(settings=settings, engine=ha.engine, auth_service=auth, orchestration_service=ha.svc)
    ha.auth = auth
    return ha, TestClient(app)


def login(api, user):
    r = api.post("/api/v1/auth/login", json={"username": user, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return {"X-CSRF-Token": r.json()["csrfToken"]}


def service(h, scopes=None):
    token, _ = h.auth.create_service_token(f"n8n-{uuid.uuid4().hex[:6]}", "test", scopes)
    return {"Authorization": f"Bearer {token}"}


def test_sign_in_required_and_camel_case(world):
    h, api = world
    assert api.get(f"{P}/workflows").status_code == 401
    login(api, "olly")
    r = api.get(f"{P}/workflows")
    assert r.status_code == 200
    item = r.json()["items"][0]
    assert "workflowId" in item and "workflow_id" not in item
    assert api.get(f"{P}/overview").json()["environment"] == "test"
    m = api.get(f"{P}/metrics")
    assert m.status_code == 200 and m.headers["content-type"].startswith("text/plain") and SECRET not in m.text


def test_observer_cannot_change_anything(world):
    h, api = world
    csrf = login(api, "olly")
    r = api.post(f"{P}/executions/trigger", json={"workflowId": TEST_WORKFLOW_ID, "input": {}}, headers=csrf)
    assert r.status_code == 403
    r = api.post(f"{P}/workflows/{TEST_WORKFLOW_ID}/state", json={"state": "PAUSED", "reason": "x"}, headers=csrf)
    assert r.status_code == 403


def test_csrf_required_for_people(world):
    h, api = world
    csrf = login(api, "tom")
    body = {"workflowId": TEST_WORKFLOW_ID, "triggerType": "manual", "input": {"message": "m"}}
    r = api.post(f"{P}/executions/trigger", json=body)
    assert r.status_code == 403 and r.json()["error"]["code"] == "CSRF_FAILED"
    r = api.post(f"{P}/executions/trigger", json=body, headers=csrf)
    assert r.status_code in (200, 201), r.text
    assert r.json()["status"] in ("QUEUED", "WAITING_CALLBACK") and r.json()["triggerType"] == "manual"


def test_trader_cannot_manage_admin_can(world):
    h, api = world
    csrf = login(api, "tom")
    r = api.post(f"{P}/workflows/{TEST_WORKFLOW_ID}/state", json={"state": "PAUSED", "reason": "x"}, headers=csrf)
    assert r.status_code == 403
    api.cookies.clear()
    csrf = login(api, "alice")
    r = api.post(f"{P}/workflows/{TEST_WORKFLOW_ID}/state", json={"state": "PAUSED", "reason": "maintenance"}, headers=csrf)
    assert r.status_code == 200, r.text and r.json()["state"] == "PAUSED"
    r = api.get(f"{P}/config")
    assert r.status_code == 200 and SECRET not in r.text


def test_service_token_triggers_with_idempotency_header(world):
    h, api = world
    auth = service(h)
    body = {"workflowId": TEST_WORKFLOW_ID, "input": {"message": "svc"}}
    r1 = api.post(f"{P}/executions/trigger", json=body, headers={**auth, "Idempotency-Key": "abc-1"})
    r2 = api.post(f"{P}/executions/trigger", json=body, headers={**auth, "Idempotency-Key": "abc-1"})
    assert r1.status_code in (200, 201) and r1.json()["executionId"] == r2.json()["executionId"]
    r3 = api.post(f"{P}/executions/trigger", json={**body, "input": {"message": "other"}},
                  headers={**auth, "Idempotency-Key": "abc-1"})
    assert r3.status_code == 409
    # Human-only actions refuse a service token even though it holds the service permissions.
    eid = r1.json()["executionId"]
    r = api.post(f"{P}/executions/{eid}/cancel", json={"reason": "x"}, headers=auth)
    assert r.status_code == 403
    r = api.put(f"{P}/config", json={"config": {}, "reason": "x"}, headers=auth)
    assert r.status_code == 403
    assert api.get(f"{P}/executions", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_n8n_callback_webhook(world):
    h, api = world
    auth = service(h)
    x, _ = h.trigger("api-cb")
    h.svc.dispatch_due()
    body = json.dumps({"callbackId": "cb-1", "executionId": x["execution_id"], "workflowId": TEST_WORKFLOW_ID,
                       "status": "completed", "environment": "test", "attempt": 1, "output": {"echo": "hi"}}).encode()
    ts = int(h.clock().timestamp())
    sig = {"X-Aurum-Timestamp": str(ts), "X-Aurum-Signature": sign(SECRET, ts, body), "Content-Type": "application/json"}
    # People cannot post callbacks, even admins.
    csrf = login(api, "alice")
    assert api.post(f"{P}/webhooks/n8n/callback", content=body, headers={**sig, **csrf}).status_code == 403
    api.cookies.clear()
    assert api.post(f"{P}/webhooks/n8n/callback", content=body, headers=sig).status_code == 401
    bad = {**sig, "X-Aurum-Signature": "v1=" + "0" * 64}
    r = api.post(f"{P}/webhooks/n8n/callback", content=body, headers={**auth, **bad})
    assert r.status_code == 401 and r.json()["error"]["code"] == "SIGNATURE_INVALID"
    r = api.post(f"{P}/webhooks/n8n/callback", content=body, headers={**auth, **sig})
    assert r.status_code == 200 and r.json()["status"] == "SUCCEEDED", r.text
    r = api.post(f"{P}/webhooks/n8n/callback", content=body, headers={**auth, **sig})
    assert r.status_code == 200 and r.json()["duplicate"] is True


def test_n8n_events_webhook_only_requests(world):
    h, api = world
    auth = service(h)
    ok = {"eventType": "ai.analysis.requested", "sourceModule": "n8n", "environment": "test", "payload": {"symbol": "XAUUSD"}}
    r = api.post(f"{P}/webhooks/n8n/events", json=ok, headers=auth)
    assert r.status_code in (200, 201, 202), r.text
    bad = {"eventType": "approval.approved", "sourceModule": "module_07", "environment": "test", "payload": {"approval_id": "a"}}
    r = api.post(f"{P}/webhooks/n8n/events", json=bad, headers=auth)
    assert r.status_code == 409 and r.json()["error"]["code"] == "EVENT_NOT_ALLOWED_FROM_N8N"


def test_errors_are_structured_and_leak_nothing(world):
    h, api = world
    login(api, "olly")
    r = api.get(f"{P}/executions/{uuid.uuid4()}")
    assert r.status_code == 404
    e = r.json()["error"]
    assert e["code"] == "EXECUTION_NOT_FOUND" and e["correlationId"]


def test_config_document_round_trips_with_snake_case_keys(world):
    h, api = world
    csrf = login(api, "alice")
    got = api.get(f"{P}/config").json()
    assert "createdBy" in got and "max_attempts" in got["config"]["retry"]      # envelope camelCase, document as stored
    doc = got["config"]
    doc["modules"] = {"module_09": {"base_url": "http://module09:8100"}}
    r = api.put(f"{P}/config", json={"config": doc, "reason": "point at Module 09", "version": got["version"]}, headers=csrf)
    assert r.status_code == 200, r.text
    assert r.json()["version"] == got["version"] + 1 and r.json()["config"]["modules"]["module_09"]["base_url"].endswith(":8100")
    r = api.put(f"{P}/config", json={"config": doc, "reason": "again", "version": got["version"]}, headers=csrf)
    assert r.status_code == 409 and r.json()["error"]["code"] == "STALE_VERSION"
