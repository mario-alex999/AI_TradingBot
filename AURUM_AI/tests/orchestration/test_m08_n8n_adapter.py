"""The REAL N8nEngine against an httpx MockTransport that imitates n8n 2.x responses (status codes and shapes taken
from the n8n public API and webhook behaviour). This checks our HTTP handling; it does not prove a live n8n works -
test_n8n_live.py does that, opt-in."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx
import pytest

from aurum.orchestration.engines import EngineError, EngineNotConfigured, N8nEngine, check_webhook_path
from aurum.orchestration.security import verify

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)
SECRET = "adapter-secret-0123456789abcdef"


def engine(handler, *, api_key="k-123", env="test", token="hdr-token"):
    return N8nEngine(base_url="http://n8n.test:5678", webhook_base_url=None, api_key=api_key, signing_secret=SECRET,
                     webhook_token=token, environment=env, timeout=2, transport=httpx.MockTransport(handler))


REQ = {"executionId": "e-1", "attempt": 1, "environment": "test", "correlationId": "c-1", "input": {"message": "hi"}}


def test_dispatch_posts_signed_request_to_webhook_path():
    seen = {}

    def h(r: httpx.Request):
        seen["r"] = r
        return httpx.Response(200, json={"accepted": True, "executionId": "8812"})
    res = engine(h).dispatch("aurum-core-test-echo", REQ, NOW)
    r = seen["r"]
    assert str(r.url) == "http://n8n.test:5678/webhook/aurum-core-test-echo" and r.method == "POST"
    assert r.headers["Idempotency-Key"] == "e-1" and r.headers["X-AURUM-Webhook-Token"] == "hdr-token"
    verify(r.content, r.headers["X-Aurum-Timestamp"], r.headers["X-Aurum-Signature"], [SECRET], NOW, 300)
    assert json.loads(r.content)["executionId"] == "e-1"
    assert "X-N8N-API-KEY" not in r.headers                          # the API key never goes to the webhook
    assert (res.outcome, res.engine_execution_id) == ("accepted", "8812")


@pytest.mark.parametrize("status,outcome,category", [
    (404, "permanent", "WORKFLOW_NOT_FOUND"),     # n8n: webhook not registered / workflow inactive
    (401, "permanent", "AUTH_FAILED"),            # header auth rejected
    (429, "transient", "RATE_LIMITED"),
    (503, "transient", "SERVER_ERROR"),
    (500, "unknown", "OUTCOME_UNKNOWN"),          # the workflow may have run
])
def test_dispatch_http_classification(status, outcome, category):
    res = engine(lambda r: httpx.Response(status, json={"message": "x"})).dispatch("p", REQ, NOW)
    assert (res.outcome, res.error_category, res.http_status) == (outcome, category, status)


def test_dispatch_network_errors_distinguish_not_sent_from_unknown():
    def refuse(r):
        raise httpx.ConnectError("refused", request=r)

    def slow(r):
        raise httpx.ReadTimeout("slow", request=r)
    assert engine(refuse).dispatch("p", REQ, NOW).outcome == "transient"
    assert engine(slow).dispatch("p", REQ, NOW).outcome == "unknown"


def test_dispatch_refuses_other_environment_and_bad_paths():
    called = []
    e = engine(lambda r: called.append(r) or httpx.Response(200))
    assert e.dispatch("p", {**REQ, "environment": "production"}, NOW).error_category == "ENVIRONMENT_MISMATCH"
    assert not called
    for bad in ("../admin", "a b", "", "x?y=1"):
        with pytest.raises(ValueError):
            check_webhook_path(bad)


def test_accepted_without_json_body_is_still_accepted():
    res = engine(lambda r: httpx.Response(200, text="Workflow was started")).dispatch("p", REQ, NOW)
    assert res.outcome == "accepted" and res.engine_execution_id is None


def test_health_states():
    def ok(r):
        if r.url.path.startswith("/api/v1"):
            assert r.headers["X-N8N-API-KEY"] == "k-123"
            return httpx.Response(200, json={"data": []})
        return httpx.Response(200, json={"status": "ok"})
    assert engine(ok).health().status == "HEALTHY"
    assert engine(ok, api_key=None).health().status == "DEGRADED"
    assert engine(lambda r: httpx.Response(401) if r.url.path.startswith("/api") else httpx.Response(200)).health().status \
        == "DEGRADED"
    assert engine(lambda r: httpx.Response(503) if r.url.path.endswith("readiness") else httpx.Response(200)).health().status \
        == "DEGRADED"

    def down(r):
        raise httpx.ConnectError("x", request=r)
    h = engine(down).health()
    assert h.status == "UNAVAILABLE" and "k-123" not in h.detail


def test_public_api_calls():
    calls = []

    def h(r: httpx.Request):
        calls.append((r.method, r.url.path, dict(r.url.params)))
        if r.url.path == "/api/v1/workflows/wf1":
            return httpx.Response(200, json={"id": "wf1", "name": "Echo", "active": True, "versionId": "v9", "nodes": [
                {"type": "n8n-nodes-base.webhook", "parameters": {"path": "aurum-core-test-echo"}},
                {"type": "n8n-nodes-base.httpRequest", "parameters": {}}]})
        if r.url.path == "/api/v1/executions/77":
            return httpx.Response(200, json={"id": 77, "status": "success", "finished": True, "workflowId": "wf1"})
        if r.url.path == "/api/v1/executions":
            return httpx.Response(200, json={"data": [{"id": 1, "status": "error"}]})
        if r.url.path.endswith("/stop"):
            return httpx.Response(200, json={"id": "78", "status": "canceled"})
        if r.url.path.endswith("/activate"):
            return httpx.Response(200, json={"id": "wf1", "active": True})
        return httpx.Response(404, json={"message": "not found"})
    e = engine(h)
    wf = e.get_workflow("wf1")
    assert wf["webhook_paths"] == ["aurum-core-test-echo"] and wf["node_count"] == 2
    assert e.get_execution("77") == {"id": "77", "status": "success", "finished": True, "mode": None, "workflow_id": "wf1",
                                     "started_at": None, "stopped_at": None}
    assert e.list_executions("wf1")[0]["status"] == "error"
    assert e.stop_execution("78")["status"] == "canceled"
    assert e.set_active("wf1", True)["active"] is True
    with pytest.raises(EngineError) as ei:
        e.get_workflow("missing")
    assert ei.value.classified.category == "WORKFLOW_NOT_FOUND"
    assert ("GET", "/api/v1/executions/77", {"includeData": "false"}) in calls


def test_public_api_needs_key():
    e = engine(lambda r: httpx.Response(200), api_key=None)
    assert not e.api_configured()
    with pytest.raises(EngineNotConfigured):
        e.get_execution("1")
    assert "needs N8N_API_KEY" in e.capabilities()["execution_status"]
