"""/api/v1/risk over HTTP (FastAPI TestClient). Inputs are MOCKED (MemoryRiskData); the database is temporary SQLite."""
from __future__ import annotations

import pytest
from risk_helpers import EURUSD_BUY, Harness
from cot_helpers import cot_cfg
from fastapi.testclient import TestClient

from aurum.api.app import create_app
from aurum.config import Settings

KEY = {"X-AURUM-Admin-Key": "secret-key", "X-AURUM-Actor": "Test Officer"}
BODY = {"symbol": "EURUSD", "direction": "BUY", "entry": 1.10010, "stopLoss": 1.09810, "takeProfit": 1.10510}


@pytest.fixture
def api(tmp_path):
    h = Harness(tmp_path)
    settings = Settings(_env_file=None, database_url=h.db_url, data_dir=tmp_path / "data", aurum_admin_api_key="secret-key")
    with TestClient(create_app(settings, cot_cfg(), h.engine, risk_service=h.svc)) as c:
        yield c, h


def test_assess_requires_admin_key(api):
    c, _ = api
    assert c.post("/api/v1/risk/assess", json=BODY).status_code == 401
    assert c.post("/api/v1/risk/assess", json=BODY, headers={"X-AURUM-Admin-Key": "wrong"}).status_code == 401


def test_admin_disabled_without_configured_key(tmp_path):
    h = Harness(tmp_path)
    settings = Settings(_env_file=None, database_url=h.db_url, data_dir=tmp_path / "d")
    with TestClient(create_app(settings, cot_cfg(), h.engine, risk_service=h.svc)) as c:
        assert c.put("/api/v1/risk/config", json={}, headers=KEY).status_code == 503


def test_assess_camelcase_response_and_correlation_id(api):
    c, _ = api
    r = c.post("/api/v1/risk/assess", json=BODY, headers={**KEY, "X-Correlation-ID": "corr-123"})
    assert r.status_code == 201, r.text
    j = r.json()
    assert j["decision"] == "PASS" and j["recommendedVolume"] == 0.46 and j["correlationId"] == "corr-123"
    assert r.headers["X-Correlation-ID"] == "corr-123"
    assert j["createdBy"] == "Test Officer"
    assert c.get(f"/api/v1/risk/assessments/{j['assessmentId']}").json()["decision"] == "PASS"


def test_idempotency_key_header(api):
    c, _ = api
    a = c.post("/api/v1/risk/assess", json=BODY, headers={**KEY, "Idempotency-Key": "n8n-1"}).json()
    b = c.post("/api/v1/risk/assess", json=BODY, headers={**KEY, "Idempotency-Key": "n8n-1"}).json()
    assert a["assessmentId"] == b["assessmentId"] and b["replayed"] is True
    r = c.post("/api/v1/risk/assess", json={**BODY, "entry": 1.10020}, headers={**KEY, "Idempotency-Key": "n8n-1"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


@pytest.mark.parametrize("body,code", [({"direction": "BUY", "entry": 1.1}, "INVALID_PROPOSAL"),
                                       ({**BODY, "direction": "UP"}, "INVALID_PROPOSAL"),
                                       ({**BODY, "symbol": "BTCUSD"}, "INVALID_PROPOSAL"),
                                       ({**BODY, "entry": -1}, "INVALID_PROPOSAL"),
                                       ({**BODY, "entry": "abc"}, "INVALID_PROPOSAL")])
def test_invalid_payloads_are_422_with_structured_error(api, body, code):
    c, _ = api
    r = c.post("/api/v1/risk/assess", json=body, headers=KEY)
    assert r.status_code == 422 and r.json()["error"]["code"] == code and r.json()["error"]["correlationId"]


def test_position_size_uses_backend_and_stores_nothing(api):
    c, h = api
    r = c.post("/api/v1/risk/position-size", json={**BODY, "equity": 50000, "riskPct": 0.5})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["valid"] and j["equitySource"] == "request" and j["calculation"]["recommendedVolume"] == 1.15
    assert c.get("/api/v1/risk/history").json()["total"] == 0


def test_history_pagination_and_filters(api):
    c, _ = api
    for i in range(3):
        c.post("/api/v1/risk/assess", json={**BODY, "stopLoss": 1.10210}, headers={**KEY, "Idempotency-Key": f"b{i}"})
    c.post("/api/v1/risk/assess", json=BODY, headers=KEY)
    page = c.get("/api/v1/risk/history", params={"limit": 2, "offset": 0}).json()
    assert page["total"] == 4 and len(page["items"]) == 2
    assert c.get("/api/v1/risk/history", params={"decision": "BLOCK"}).json()["total"] == 3
    assert c.get("/api/v1/risk/history", params={"rule": "R011"}).json()["total"] == 3
    assert c.get("/api/v1/risk/history", params={"decision": "MAYBE"}).status_code == 422
    counts = {(x["ruleId"], x["outcome"]): x["count"] for x in c.get("/api/v1/risk/history/rules").json()}
    assert counts[("R011", "fail")] == 3


def test_config_get_put_versioning_and_audit(api):
    c, _ = api
    cfg = c.get("/api/v1/risk/config").json()
    assert cfg["version"] == 1 and cfg["config"]["riskPerTradePct"] == 1.0
    assert c.put("/api/v1/risk/config", json={"baseVersion": 1, "reason": "tighten", "changes": {"riskPerTradePct": 0.5}}).status_code == 401
    r = c.put("/api/v1/risk/config", json={"baseVersion": 1, "reason": "tighten per-trade risk", "changes": {"riskPerTradePct": 0.5}},
              headers=KEY)
    assert r.status_code == 200, r.text
    assert r.json()["version"] == 2 and r.json()["weakened"] == []
    stale = c.put("/api/v1/risk/config", json={"baseVersion": 1, "reason": "stale edit", "changes": {"riskPerTradePct": 0.4}}, headers=KEY)
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "VERSION_CONFLICT"
    loosen = c.put("/api/v1/risk/config", json={"baseVersion": 2, "reason": "loosen", "changes": {"maxDailyLossPct": 8}}, headers=KEY)
    assert loosen.status_code == 409 and loosen.json()["error"]["code"] == "WEAKENING_NOT_ACKNOWLEDGED"
    assert "max_daily_loss_pct: 3.0 -> 8.0" in loosen.json()["error"]["message"]
    bad = c.put("/api/v1/risk/config", json={"baseVersion": 2, "reason": "typo", "changes": {"riskPerTrade": 0.5}}, headers=KEY)
    assert bad.status_code == 422
    per_symbol = c.put("/api/v1/risk/config", json={"baseVersion": 2, "reason": "tighter gold spread",
                                                    "changes": {"maxSpreadPoints": {"XAUUSD": 400}, "resetPolicy": {"dailyLoss": "manual"}}},
                       headers=KEY)
    assert per_symbol.status_code == 200, per_symbol.text
    assert per_symbol.json()["config"]["maxSpreadPoints"]["XAUUSD"] == 400
    audit = c.get("/api/v1/risk/audit", params={"entityType": "risk_config"}).json()
    assert [a["action"] for a in audit][:2] == ["config.updated", "config.updated"] and audit[0]["actor"] == "Test Officer"


def test_profile_switch(api):
    c, _ = api
    r = c.put("/api/v1/risk/config", json={"baseVersion": 1, "reason": "go conservative", "profile": "conservative"}, headers=KEY)
    assert r.status_code == 200 and r.json()["config"]["profile"] == "conservative" and r.json()["config"]["riskPerTradePct"] == 0.5


def test_locks_api(api):
    c, _ = api
    assert c.post("/api/v1/risk/locks", json={"scope": "global", "reason": "manual stop"}).status_code == 401
    assert c.post("/api/v1/risk/locks", json={"scope": "instrument", "reason": "no value"}, headers=KEY).status_code == 422
    lock = c.post("/api/v1/risk/locks", json={"scope": "global", "reason": "manual stop"}, headers=KEY)
    assert lock.status_code == 201
    lid = lock.json()["lockId"]
    assert c.post("/api/v1/risk/assess", json=BODY, headers=KEY).json()["decision"] == "BLOCK"
    assert c.post(f"/api/v1/risk/locks/{lid}/release", json={"reason": "ok"}, headers=KEY).status_code == 422
    assert c.post(f"/api/v1/risk/locks/{lid}/release", json={"reason": "all clear"}).status_code == 401
    rel = c.post(f"/api/v1/risk/locks/{lid}/release", json={"reason": "all clear now"}, headers=KEY)
    assert rel.status_code == 200
    assert c.post(f"/api/v1/risk/locks/{lid}/release", json={"reason": "all clear now"}, headers=KEY).status_code == 409
    locks = c.get("/api/v1/risk/locks").json()
    assert locks["active"] == [] and locks["history"][0]["history"][-1]["action"] == "released"


def test_validate_consume_cancel(api):
    c, _ = api
    a = c.post("/api/v1/risk/assess", json=BODY, headers=KEY).json()
    assert c.post(f"/api/v1/risk/assessments/{a['assessmentId']}/validate").json()["valid"] is True
    assert c.post(f"/api/v1/risk/assessments/{a['assessmentId']}/consume", json={}).status_code == 401
    assert c.post(f"/api/v1/risk/assessments/{a['assessmentId']}/consume", json={}, headers=KEY).json()["state"] == "consumed"
    assert c.post(f"/api/v1/risk/assessments/{a['assessmentId']}/cancel", json={}, headers=KEY).status_code == 409
    assert c.get("/api/v1/risk/assessments/nope").status_code == 404


def test_read_endpoints(api):
    c, _ = api
    for path in ("account", "portfolio", "rules", "health", "events", "config"):
        assert c.get(f"/api/v1/risk/{path}").status_code == 200, path
    acct = c.get("/api/v1/risk/account").json()
    assert acct["overallStatus"] == "WITHIN_LIMITS" and acct["equity"] == 10000.0
    assert len(c.get("/api/v1/risk/rules").json()) >= 30
    assert c.get("/api/v1/health").json()["modules"]["risk"] is True


def test_events_outbox_and_inbound_dedupe(api):
    c, h = api
    c.post("/api/v1/risk/assess", json={**BODY, "stopLoss": 1.10210}, headers=KEY)
    ev = c.get("/api/v1/risk/events").json()
    types = [e["eventType"] for e in ev]
    assert types == ["risk.assessment.completed", "risk.assessment.blocked"]
    e = ev[0]
    for field in ("eventId", "eventType", "schemaVersion", "timestamp", "correlationId", "idempotencyKey", "entityId", "sourceModule", "payload"):
        assert e[field] is not None, field
    assert c.get("/api/v1/risk/events", params={"after": ev[-1]["sequence"]}).json() == []
    inbound = {"eventId": "x-1", "eventType": "account.snapshot.updated", "payload": {}}
    assert c.post("/api/v1/risk/events/inbound", json=inbound).status_code == 401
    assert c.post("/api/v1/risk/events/inbound", json=inbound, headers=KEY).json()["duplicate"] is False
    assert c.post("/api/v1/risk/events/inbound", json=inbound, headers=KEY).json()["duplicate"] is True


def test_rate_limit(tmp_path):
    from fastapi import FastAPI

    from aurum.api.app import AppState
    from aurum.api.routes.risk import build_router
    h = Harness(tmp_path)
    settings = Settings(_env_file=None, database_url=h.db_url, data_dir=tmp_path / "d", aurum_admin_api_key="secret-key")
    app = FastAPI()
    app.state.aurum = AppState(settings, None, h.engine, None, None)
    app.include_router(build_router(app.state.aurum, lambda: h.svc, rate_limit_per_minute=2), prefix="/r")
    with TestClient(app) as c:
        codes = [c.post("/r/position-size", json=BODY).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def test_cors_allows_put_for_settings(api):
    c, _ = api
    r = c.options("/api/v1/risk/config", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "PUT"})
    assert r.status_code == 200 and "PUT" in r.headers["access-control-allow-methods"]


def test_position_size_without_account_currency_explains_why(tmp_path):
    h = Harness(tmp_path, has_snapshot=False)
    settings = Settings(_env_file=None, database_url=h.db_url, data_dir=tmp_path / "d")
    with TestClient(create_app(settings, cot_cfg(), h.engine, risk_service=h.svc)) as c:
        r = c.post("/api/v1/risk/position-size", json={**BODY, "equity": 10000}).json()
        assert r["valid"] is False and [f["reasonCode"] for f in r["failedChecks"]] == ["ACCOUNT_CURRENCY_UNKNOWN"]
        ok = c.post("/api/v1/risk/position-size", json={**BODY, "equity": 10000, "accountCurrency": "USD"}).json()
        assert ok["valid"] is True and ok["calculation"]["recommendedVolume"] == 0.46
