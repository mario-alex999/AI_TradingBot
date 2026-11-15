"""WF08-02 Market Analysis Orchestrator: pending requests, data quality, the Module 05 run, coalescing, installation, API.

Docx test stage covered here: (4) a valid analysis request reaches Module 05 once, its result is stored and announced,
and bad data or a switched-off workflow stops before any analysis.
"""
from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from m08_harness import ALICE, N8N, NOW
from test_wf08_01 import store_candles

from aurum.auth.service import AuthService
from aurum.orchestration import market_analysis as ma
from aurum.orchestration.market_analysis import MarketAnalyst, install

P = "/api/v1/orchestration"


class FakeDecision:
    """Stands in for Module 05: records what it was asked to analyse."""

    def __init__(self, fail: bool = False):
        self.calls: list[tuple[str, bool]] = []
        self.fail = fail

    def symbols(self):
        return ["EURUSD", "GBPUSD", "XAUUSD", "US30"]

    def status(self):
        return {"schema_ready": True, "ai_reviewer": {"enabled": False}}

    def run(self, symbol, ai=True, **_):
        self.calls.append((symbol, ai))
        if self.fail:
            raise RuntimeError("module 05 exploded")
        return {"symbol": symbol, "status": "ok", "run_id": f"run-{len(self.calls)}", "signals": [{"id": "sig-1"}],
                "analysis": {"decision": {"decision": "NO_TRADE"}}}


@pytest.fixture
def world(h):
    fake = FakeDecision()
    return h, fake, MarketAnalyst(h.svc, decision=lambda: fake)


def request(h, symbol: str, timeframe: str = "H1", correlation: str | None = None):
    view, created = h.svc.publish(N8N, {
        "eventType": "ai.analysis.requested", "eventVersion": "1.0", "sourceModule": "n8n", "environment": "test",
        "idempotencyKey": f"wf08-01:{symbol}:{timeframe}:{uuid.uuid4().hex[:8]}", "correlationId": correlation or str(uuid.uuid4()),
        "payload": {"symbol": symbol, "timeframe": timeframe, "timeframes": [timeframe]}}, via_n8n=True)
    assert created
    return view


def set_cfg(h, **changes):
    cfg = h.svc.config().dump()
    cfg["market_analysis"].update(changes)
    h.svc.put_config(ALICE, {"reason": "test", "config": cfg})


# ---- pending requests ---------------------------------------------------------------------------------------------------------
def test_pending_groups_unanswered_requests_by_symbol(world):
    h, _, an = world
    assert an.pending()["count"] == 0
    request(h, "EURUSD", "M15")
    h.clock.advance(seconds=5)
    request(h, "EURUSD", "H1")
    request(h, "XAUUSD", "H4")
    p = an.pending()
    assert [(i["symbol"], i["requests"], i["timeframes"]) for i in p["items"]] == [("EURUSD", 2, ["H1", "M15"]) ,
                                                                                    ("XAUUSD", 1, ["H4"])] or \
           [(i["symbol"], i["requests"], sorted(i["timeframes"])) for i in p["items"]] == [("EURUSD", 2, ["H1", "M15"]),
                                                                                           ("XAUUSD", 1, ["H4"])]
    assert an.pending(["GBPUSD"])["count"] == 0
    forced = an.pending(["GBPUSD"], force=True)["items"]
    assert forced[0]["symbol"] == "GBPUSD" and forced[0]["forced"] is True


def test_run_answers_the_requests_once_and_announces_the_result(world):
    h, fake, an = world
    request(h, "EURUSD", "M15")
    h.clock.advance(seconds=5)
    request(h, "EURUSD", "H1")
    out = an.run("eurusd", execution_id="exec-1")
    assert out["status"] == "completed" and out["requests"] == 2 and out["candidates"] == 1 and out["decision"] == "NO_TRADE"
    assert fake.calls == [("EURUSD", True)]
    ev = h.svc.get_event(out["event_id"])
    assert ev["event_type"] == "ai.analysis.completed" and ev["source_module"] == "module_05"
    assert ev["payload"] == {"symbol": "EURUSD", "run_id": "run-1", "candidates": 1}
    # Answered: nothing pending, and a second run does not call Module 05 again.
    assert an.pending()["count"] == 0
    again = an.run("EURUSD")
    assert again["status"] == "skipped" and len(fake.calls) == 1
    # A newer request makes the symbol pending again.
    h.clock.advance(minutes=15)
    request(h, "EURUSD", "M15")
    assert an.pending()["items"][0]["requests"] == 1


def test_dry_run_force_off_switch_and_failure(h):
    fake = FakeDecision(fail=True)
    an = MarketAnalyst(h.svc, decision=lambda: fake)
    request(h, "XAUUSD", "H1")
    assert an.run("XAUUSD", dry_run=True)["status"] == "dry_run" and fake.calls == []
    out = an.run("XAUUSD")
    assert out["status"] == "failed" and "module 05 exploded" in out["reason"]
    assert h.svc.get_event(out["event_id"])["event_type"] == "ai.analysis.failed"
    assert an.pending()["count"] == 0                      # a failed attempt is answered too: no hot retry loop
    assert an.run("GBPUSD")["status"] == "skipped"         # no request and not forced
    assert an.run("GBPUSD", force=True)["status"] == "failed" and fake.calls[-1][0] == "GBPUSD"
    set_cfg(h, enabled=False)
    assert an.run("GBPUSD", force=True)["status"] == "skipped"
    assert an.preflight()["proceed"] is False and an.pending()["items"] == []


def test_ai_review_follows_the_configuration(world):
    h, fake, an = world
    set_cfg(h, ai_review=False)
    request(h, "US30", "H1")
    an.run("US30")
    assert fake.calls == [("US30", False)]


# ---- data quality -------------------------------------------------------------------------------------------------------------
def test_data_quality_needs_closed_candles_and_reports_stale_data(world):
    h, _, an = world
    q = an.data_quality("EURUSD")
    assert q["ok"] is False and "no instrument" in q["reasons"][0]
    assert an.data_quality("BTCUSD")["ok"] is False
    store_candles(h.engine, "EURUSD", "H1", NOW - timedelta(hours=2))
    q = an.data_quality("eurusd")
    assert q["ok"] is True and q["stale"] is False and q["timeframes"]["H1"]["latest_candle_open"] == "2026-11-09T10:00:00Z"
    h.clock.advance(days=10)
    q = an.data_quality("EURUSD")
    assert q["ok"] is True and q["stale"] is True          # stale data is reported, not blocked, by default
    set_cfg(h, block_when_stale=True)
    assert an.data_quality("EURUSD")["ok"] is False
    set_cfg(h, block_when_stale=False, required_timeframes=["H1", "D1"])
    assert "D1" in " ".join(an.data_quality("EURUSD")["reasons"])


# ---- installation and coalescing ----------------------------------------------------------------------------------------------
def test_install_and_one_execution_per_monitoring_run(h):
    h.n8n.workflows["n8nWF0802"] = {"id": "n8nWF0802", "active": True, "webhook_paths": [ma.WEBHOOK_PATH]}
    out = install(h.svc, ALICE, "n8nWF0802")
    assert out["state"] == "ACTIVE" and out["placeholder"] is False and out["subscription"]["event_pattern"] == "ai.analysis.requested"
    again = install(h.svc, ALICE, "n8nWF0802")
    assert any("already present" in s for s in again["steps"])
    assert len([s for s in h.svc.list_subscriptions()["items"] if s["workflow_id"] == ma.WORKFLOW_ID]) == 1
    # One WF08-01 run requests three candles (same correlation id): one WF08-02 execution, not three.
    for sym, tf in (("EURUSD", "M15"), ("EURUSD", "H1"), ("XAUUSD", "H1")):
        request(h, sym, tf, correlation="wf0801-run-A")
    h.svc.deliver_due()
    runs = [e for e in h.svc.list_executions(workflow_id=ma.WORKFLOW_ID)["items"]]
    assert len(runs) == 1 and runs[0]["trigger_type"] == "event"
    # The next WF08-01 run gets its own execution.
    request(h, "EURUSD", "M15", correlation="wf0801-run-B")
    h.svc.deliver_due()
    assert len(h.svc.list_executions(workflow_id=ma.WORKFLOW_ID)["items"]) == 2
    # With coalescing off, every request triggers its own execution.
    set_cfg(h, coalesce=False)
    request(h, "GBPUSD", "H1", correlation="wf0801-run-C")
    request(h, "US30", "H1", correlation="wf0801-run-C")
    h.svc.deliver_due()
    assert len(h.svc.list_executions(workflow_id=ma.WORKFLOW_ID)["items"]) == 4


# ---- HTTP API -----------------------------------------------------------------------------------------------------------------
def test_api_routes_and_who_may_run_an_analysis(h, monkeypatch):
    from aurum.api.app import create_app
    from aurum.config import Settings
    fake = FakeDecision()
    monkeypatch.setattr(MarketAnalyst, "decision", property(lambda self: fake))
    auth = AuthService(h.engine, iterations=1000)
    api = TestClient(create_app(settings=Settings(_env_file=None, database_url="sqlite://"), engine=h.engine,
                                auth_service=auth, orchestration_service=h.svc))
    store_candles(h.engine, "EURUSD", "H1", NOW - timedelta(hours=2))
    request(h, "EURUSD", "H1")
    assert api.get(f"{P}/market-analysis/pending").status_code == 401
    token, _ = auth.create_service_token("n8n-wf0802", "test", ["orchestration:view", "orchestration:callback"])
    hdr = {"Authorization": f"Bearer {token}"}
    assert api.get(f"{P}/market-analysis/config", headers=hdr).json()["requestEvent"] == "ai.analysis.requested"
    assert api.get(f"{P}/market-analysis/preflight", headers=hdr).json()["proceed"] is True
    assert api.get(f"{P}/market-analysis/pending", headers=hdr).json()["items"][0]["symbol"] == "EURUSD"
    assert api.get(f"{P}/market-analysis/data-quality", params={"symbol": "EURUSD"}, headers=hdr).json()["ok"] is True
    # A token that may only read cannot start an analysis; neither can a request without a symbol.
    ro, _ = auth.create_service_token("read-only", "test", ["orchestration:view"])
    assert api.post(f"{P}/market-analysis/run", json={"symbol": "EURUSD"}, headers={"Authorization": f"Bearer {ro}"}).status_code == 403
    assert api.post(f"{P}/market-analysis/run", json={}, headers=hdr).status_code == 422
    r = api.post(f"{P}/market-analysis/run", json={"symbol": "EURUSD", "executionId": "exec-9"}, headers=hdr)
    assert r.status_code == 200 and r.json()["status"] == "completed" and r.json()["runId"] == "run-1", r.text
    assert api.get(f"{P}/market-analysis/pending", headers=hdr).json()["count"] == 0
