"""WF08-01 Market Monitoring Scheduler: backend checks, duplicate prevention, installation, API and the n8n workflow file.

Docx test stages covered here: (2) scheduler selects the configured symbols/timeframes, (3) incomplete or unsettled
candles are rejected, duplicates are prevented, the off switch stops new triggers.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import timedelta
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from m08_harness import ALICE, N8N, NOW

from aurum.auth.service import AuthService
from aurum.database.repository import Repository
from aurum.orchestration import market_monitoring as mm
from aurum.orchestration.market_monitoring import MarketMonitor, idempotency_key, install

WF_FILE = Path(__file__).resolve().parents[2] / "n8n" / "workflows" / "aurum_wf08_01_market_monitoring.json"
P = "/api/v1/orchestration"


def store_candles(engine, symbol: str, timeframe: str, last_open, n: int = 5, *, forming_after: bool = False):
    """Closed candles ending with one that opened at ``last_open`` (Module 01's own repository does the writing)."""
    repo = Repository(engine)
    iid = repo.upsert_instrument(symbol, symbol, "forex")
    step = {"M5": "5min", "M15": "15min", "H1": "1h", "H4": "4h", "D1": "1D"}[timeframe]
    ts = pd.date_range(end=pd.Timestamp(last_open), periods=n, freq=step)
    df = pd.DataFrame({"ts_utc": ts, "open": 1.1, "high": 1.2, "low": 1.0, "close": 1.15})
    df["is_complete"] = True
    if forming_after:   # plus the candle that is still forming now
        df = pd.concat([df, pd.DataFrame({"ts_utc": [ts[-1] + pd.Timedelta(step)], "open": [1.1], "high": [1.2],
                                          "low": [1.0], "close": [1.15], "is_complete": [False]})], ignore_index=True)
    df["symbol"], df["broker_symbol"], df["timeframe"] = symbol, symbol, timeframe
    df["mt5_time"] = df["ts_utc"].astype("int64") // 10**9
    df["tick_volume"], df["real_volume"], df["spread"] = 100, 0, 2
    df["quality_status"], df["quality_flags"] = "valid", None
    repo.upsert_candles(iid, df, "test")


@pytest.fixture
def mon(h):
    return MarketMonitor(h.engine, h.svc.config, clock=h.clock)


def set_cfg(h, **changes):
    cfg = h.svc.config().dump()
    cfg["market_monitoring"].update(changes)
    h.svc.put_config(ALICE, {"reason": "test", "config": cfg})


# ---- configuration and preflight ---------------------------------------------------------------------------------------------
def test_config_defaults_match_the_document(mon):
    c = mon.config_view()
    assert c["symbols"] == ["EURUSD", "GBPUSD", "XAUUSD", "US30"]
    assert c["timeframes"] == ["D1", "H4", "H1", "M15", "M5"]
    assert c["enabled"] is True and c["kill_switch"] is False and c["request_event"] == "ai.analysis.requested"


def test_preflight_needs_module01_data_and_respects_the_off_switch(h, mon):
    p = mon.preflight()
    assert p["proceed"] is False and "collector" in " ".join(p["reasons"])
    store_candles(h.engine, "EURUSD", "M15", NOW - timedelta(minutes=30))
    assert mon.preflight()["proceed"] is True
    set_cfg(h, enabled=False)
    p = mon.preflight()
    assert p["proceed"] is False and "switched off" in p["reasons"][0]


# ---- eligible pairs ------------------------------------------------------------------------------------------------------------
def test_eligible_pairs_and_filters(h, mon):
    e = mon.eligible()
    assert e["count"] == 20 and {"symbol": "XAUUSD", "timeframe": "M5"} in e["items"]
    f = mon.eligible(["eurusd", "BTCUSD"], ["H1", "W1"])
    assert f["items"] == [{"symbol": "EURUSD", "timeframe": "H1"}]
    assert {x.get("symbol") or x.get("timeframe") for x in f["excluded"]} == {"BTCUSD", "W1"}
    set_cfg(h, enabled=False)
    assert mon.eligible()["items"] == []


# ---- candle completion -------------------------------------------------------------------------------------------------------
def test_candle_must_be_closed_and_settled(h, mon):
    # latest closed M15 opened 11:45 -> closed 12:00 = NOW; a forming 12:00 candle is stored too and must be ignored
    store_candles(h.engine, "EURUSD", "M15", NOW - timedelta(minutes=15), forming_after=True)
    s = mon.candle_status("EURUSD", "M15")
    assert s["latest_candle_open"] == "2026-11-09T11:45:00Z" and s["latest_candle_close"] == "2026-11-09T12:00:00Z"
    assert s["ready"] is False and "settle" in s["reason"]               # closed 0 s ago, settle is 60 s
    h.clock.advance(seconds=61)
    s = mon.candle_status("EURUSD", "M15")
    assert s["ready"] is True and s["stale"] is False


def test_stale_unknown_and_missing_data(h, mon):
    store_candles(h.engine, "EURUSD", "H1", NOW - timedelta(hours=6))
    s = mon.candle_status("EURUSD", "H1")
    assert s["ready"] is True and s["stale"] is True and "behind" in s["reason"]
    assert mon.candle_status("NOPE", "H1")["reason"].startswith("Module 01 has no instrument")
    assert "no closed M5 candle" in mon.candle_status("EURUSD", "M5")["reason"]
    assert mon.candle_status("EURUSD", "M7")["ready"] is False


# ---- duplicate prevention ----------------------------------------------------------------------------------------------------
def request_event(h, key, symbol="EURUSD", tf="M15"):
    return h.svc.publish(N8N, {"eventType": "ai.analysis.requested", "eventVersion": "1.0", "sourceModule": "n8n",
                               "environment": "test", "idempotencyKey": key, "correlationId": uuid.uuid4().hex,
                               "payload": {"symbol": symbol, "timeframe": tf, "timeframes": [tf]}}, via_n8n=True)


def test_duplicate_prevention_two_layers(h, mon):
    opened = NOW - timedelta(minutes=15)
    d = mon.dispatch_status("EURUSD", "M15", opened)
    assert d["already_requested"] is False and d["idempotency_key"] == "wf08-01:EURUSD:M15:2026-11-09T11:45Z"
    view, created = request_event(h, d["idempotency_key"])
    assert created is True
    again = mon.dispatch_status("eurusd", "m15", opened)
    assert again["already_requested"] is True and again["event_id"] == view["event_id"]
    _, created_twice = request_event(h, d["idempotency_key"])     # overlapping run: the event store refuses a second one
    assert created_twice is False
    assert mon.dispatch_status("EURUSD", "M15", opened + timedelta(minutes=15))["already_requested"] is False


def test_n8n_may_not_publish_decisions(h):
    from aurum.orchestration.common import OrchError
    with pytest.raises(OrchError) as exc:
        h.svc.publish(N8N, {"eventType": "risk.assessment.completed", "sourceModule": "n8n", "environment": "test",
                            "payload": {"assessment_id": "a", "decision": "ALLOW"}}, via_n8n=True)
    assert exc.value.code == "EVENT_NOT_ALLOWED_FROM_N8N"


# ---- installation --------------------------------------------------------------------------------------------------------------
def test_install_turns_the_placeholder_into_an_active_scheduled_workflow(h):
    h.n8n.workflows["n8nWF0801"] = {"id": "n8nWF0801", "active": True, "webhook_paths": [mm.WEBHOOK_PATH]}
    before = h.svc.get_workflow(mm.WORKFLOW_ID)
    assert before["placeholder"] is True
    out = install(h.svc, ALICE, "n8nWF0801")
    assert out["state"] == "ACTIVE" and out["placeholder"] is False
    assert out["schedule"]["expression"] == mm.DEFAULT_SCHEDULE and out["schedule"]["timezone"] == "UTC"
    nxt = pd.Timestamp(out["schedule"]["next_run_at"])
    assert nxt.minute % 5 == 1                                          # one minute after an M5 close
    again = install(h.svc, ALICE, "n8nWF0801")                          # idempotent
    assert any("already present" in s for s in again["steps"]) and not any("added" in s for s in again["steps"])
    assert len([x for x in h.svc.list_schedules()["items"] if x["workflow_id"] == mm.WORKFLOW_ID]) == 1
    ex, created = h.trigger(wid=mm.WORKFLOW_ID, inp={"dryRun": True, "symbols": ["EURUSD"]}, trigger_type="manual", who=ALICE)
    assert created and ex["workflow_id"] == mm.WORKFLOW_ID


def test_trigger_input_is_checked_against_the_contract(h):
    from aurum.orchestration.common import OrchError
    h.n8n.workflows["n8nWF0801"] = {"id": "n8nWF0801", "active": True, "webhook_paths": [mm.WEBHOOK_PATH]}
    install(h.svc, ALICE, "n8nWF0801", schedule=None)
    with pytest.raises(OrchError):
        h.trigger(wid=mm.WORKFLOW_ID, inp={"timeframes": ["W1"]}, trigger_type="manual", who=ALICE)


# ---- HTTP API ------------------------------------------------------------------------------------------------------------------
def test_api_routes_for_the_n8n_service_token(h):
    from aurum.api.app import create_app
    from aurum.config import Settings
    auth = AuthService(h.engine, iterations=1000)
    app = create_app(settings=Settings(_env_file=None, database_url="sqlite://"), engine=h.engine, auth_service=auth,
                     orchestration_service=h.svc)
    api = TestClient(app)
    store_candles(h.engine, "XAUUSD", "H1", NOW - timedelta(hours=2))
    assert api.get(f"{P}/market-monitoring/config").status_code == 401           # no anonymous access
    token, _ = auth.create_service_token("n8n-wf0801", "test", None)
    hdr = {"Authorization": f"Bearer {token}"}
    assert api.get(f"{P}/market-monitoring/config", headers=hdr).json()["killSwitch"] is False
    assert api.get(f"{P}/market-monitoring/preflight", headers=hdr).json()["proceed"] is True
    e = api.get(f"{P}/market-monitoring/eligible", params={"symbols": "XAUUSD", "timeframes": "H1,M5"}, headers=hdr).json()
    assert e["count"] == 2
    c = api.get(f"{P}/market-monitoring/candle-status", params={"symbol": "XAUUSD", "timeframe": "H1"}, headers=hdr).json()
    assert c["ready"] is True and c["latestCandleOpen"] == "2026-11-09T10:00:00Z"
    d = api.get(f"{P}/market-monitoring/dispatch-status", headers=hdr,
                params={"symbol": "XAUUSD", "timeframe": "H1", "candleOpen": c["latestCandleOpen"]}).json()
    assert d["alreadyRequested"] is False and d["idempotencyKey"] == idempotency_key("XAUUSD", "H1", NOW - timedelta(hours=2))


# ---- the n8n workflow file -----------------------------------------------------------------------------------------------------
def wf() -> dict:
    return json.loads(WF_FILE.read_text(encoding="utf-8"))


def test_workflow_file_is_well_formed_and_inactive():
    w = wf()
    assert w["active"] is False and w["settings"]["executionOrder"] == "v1"
    names = [n["name"] for n in w["nodes"]]
    assert len(names) == len(set(names)) and len({n["id"] for n in w["nodes"]}) == len(names)
    for src, conn in w["connections"].items():
        assert src in names
        for branch in conn["main"]:
            for t in branch:
                assert t["node"] in names
    hook = next(n for n in w["nodes"] if n["type"] == "n8n-nodes-base.webhook")
    assert hook["parameters"]["path"] == mm.WEBHOOK_PATH and hook["parameters"]["authentication"] == "headerAuth"


def test_workflow_only_calls_aurum_and_only_safe_endpoints():
    allowed_post = {f"http://host.docker.internal:8000{P}/webhooks/n8n/callback",
                    f"http://host.docker.internal:8000{P}/webhooks/n8n/events"}
    for n in wf()["nodes"]:
        if n["type"] != "n8n-nodes-base.httpRequest":
            continue
        p = n["parameters"]
        assert p["url"].startswith(f"http://host.docker.internal:8000{P}/"), n["name"]
        if p.get("method", "GET") != "GET":
            assert p["url"] in allowed_post, n["name"]
        assert "credentials" in n and set(n["credentials"]) == {"httpHeaderAuth"}
    text = WF_FILE.read_text(encoding="utf-8")
    assert not re.search(r"(?i)/(approve|reject|execute|orders?|trade-management)\b", text)
    assert not re.search(r"(?i)(password|bearer\s+[a-z0-9]{8}|api[_-]?key\s*[:=])", text)


def test_workflow_maps_every_step_of_the_document():
    names = {n["name"] for n in wf()["nodes"]}
    for step in ("AURUM trigger (Module 08 schedule)", "Load automation configuration", "Check system health",
                 "Essential services available?", "Get eligible symbols and timeframes", "Split out instruments",
                 "Loop over instruments", "Check candle completion", "Candle closed and settled?",
                 "Check duplicate analysis", "Already requested?", "Request analysis (publish event)",
                 "Callback: completed (run recorded)"):
        assert step in names, step
    w = wf()
    loop_back = [s for s, c in w["connections"].items() if any(t["node"] == "Loop over instruments"
                                                               for b in c["main"] for t in b)]
    assert len(loop_back) == 8          # split + seven outcome records return to the loop (continue to next instrument)
    req = next(n for n in w["nodes"] if n["name"] == "Request analysis (publish event)")
    body = req["parameters"]["jsonBody"]
    assert "sourceModule: 'n8n'" in body and "idempotencyKey: $json.idempotencyKey" in body
    for n in w["nodes"]:
        if n["type"] == "n8n-nodes-base.httpRequest" and n["name"].startswith(("Load", "Check", "Get", "Request")):
            assert n.get("onError") == "continueErrorOutput", n["name"]
