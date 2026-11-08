"""The pipeline end to end on SQLite: idempotent runs, human decisions, the HTTP contract, the CLI and the no-trading scan."""
from __future__ import annotations

import os
import re
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace as NS

import pandas as pd
import pytest
from cot_helpers import cot_cfg
from decision_helpers import ACCOUNT_EMPTY, Builder, T, account, analysis, bos, context, intel, ob, ob_setup, pool, reply, service
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from typer.testing import CliRunner

from aurum.account.queries import AccountQueries
from aurum.api.app import create_app
from aurum.api.system_queries import SystemQueries
from aurum.decision import DISCLAIMER
from aurum.decision.lifecycle import IllegalTransition
from aurum.decision.recommendation import DECISIONS
from aurum.decision.service import ApprovalBlocked, CandidateNotFound, SchemaNotReady

ROOT = Path(__file__).resolve().parents[2]
ADMIN = {"X-AURUM-Admin-Key": "admin"}
DECISION = {"reviewer": "ann", "note": "Fits my plan."}


def first(svc):
    return svc.run("EURUSD")["signals"][0]


# ---- pipeline
def test_run_publishes_a_candidate_with_its_dossier(tmp_path):
    svc, _ = service(tmp_path)
    s = first(svc)
    d = svc.dossier(s["id"])
    assert s["status"] == "new" and s["instrument"] == "EURUSD" and s["evidence"]
    assert len(d["gates"]) == 9 and d["evidence_detail"] and d["status_history"][0]["to"] == "new"
    assert d["disclaimer"] == DISCLAIMER and d["ai_review"]["status"] == "unavailable"
    assert d["suggested_volume"] > 0 and d["availability"]


def test_no_setup_is_reported_with_reasons(tmp_path):
    svc, _ = service(tmp_path, context())
    r = svc.run("EURUSD")
    assert r["status"] == "no_setup" and r["signals"] == [] and r["skipped"]


def test_repeated_runs_update_the_same_candidate(tmp_path):
    svc, _ = service(tmp_path)
    ids = {first(svc)["id"] for _ in range(3)}
    assert len(ids) == 1 and len(svc.signals()) == 2      # the fixture also matches bos_continuation
    assert len(svc.dossier(ids.pop())["gates"]) == 9


def test_a_blocking_gate_rejects_and_clearing_it_revives(tmp_path):
    ctx = ob_setup(intelligence=intel(state="RELEASE_WINDOW"))
    svc, _ = service(tmp_path, ctx)
    s = first(svc)
    assert s["status"] == "rejected" and svc.dossier(s["id"])["rejected_by"] == "gate:event_risk"
    ctx.intelligence["event_risk"]["state"] = "NORMAL"
    assert first(svc)["status"] == "new" and svc.dossier(s["id"])["rejected_by"] is None


def test_human_and_final_decisions_are_frozen(tmp_path):
    svc, _ = service(tmp_path)
    uid = first(svc)["id"]
    svc.reject(uid, "ann", "No.")
    assert first(svc)["status"] == "rejected"
    assert svc.dossier(uid)["rejected_by"] == "human:ann"


def test_missing_tables_are_a_clear_error(tmp_path):
    svc, _ = service(tmp_path, init=False)
    with pytest.raises(SchemaNotReady):
        svc.run("EURUSD")
    assert svc.status()["schema_ready"] is False


def test_missing_equity_gives_no_size_and_says_why(tmp_path):
    svc, _ = service(tmp_path, ob_setup(account=ACCOUNT_EMPTY))
    d = svc.dossier(first(svc)["id"])
    assert d["suggested_volume"] is None and "equity" in d["volume_reason"]


def test_the_stronger_candidate_wins_the_duplication_gate(tmp_path):
    a = analysis(order_blocks=[ob(), ob(id_="ob2", top=1.1007, bottom=1.0999)], structure_events=[bos()], liquidity_pools=[pool()],
                 premium_discount=NS(equilibrium=1.105))
    a.order_blocks[1].state = "partially_mitigated"
    svc, _ = service(tmp_path, context(a))
    r = svc.run("EURUSD")
    assert len([s for s in r["signals"] if s["strategy"] == "order_block_continuation"]) == 1   # one zone per timeframe
    assert sorted(s["status"] for s in r["signals"]) == ["new", "rejected"]                     # the weaker long is the duplicate


# ---- human decisions
def test_approval_records_who_and_why(tmp_path):
    svc, _ = service(tmp_path)
    d = svc.approve(first(svc)["id"], "ann", "Fits my plan.")
    assert d["status"] == "approved"
    last = d["status_history"][-1]
    assert (last["actor"], last["note"], last["to"]) == ("human:ann", "Fits my plan.", "approved")


def test_approval_rechecks_gates(tmp_path):
    ctx = ob_setup()
    svc, _ = service(tmp_path, ctx)
    uid = first(svc)["id"]
    ctx.intelligence["event_risk"]["state"] = "RELEASE_WINDOW"
    with pytest.raises(ApprovalBlocked, match="event_risk"):
        svc.approve(uid, "ann", "x")
    assert svc.dossier(uid)["status"] == "new"
    assert any(g["gate"] == "event_risk" and g["result"] == "fail" for g in svc.dossier(uid)["gates"])


def test_approval_rechecks_expiry(tmp_path):
    svc, _ = service(tmp_path)
    uid = first(svc)["id"]
    svc.clock["t"] = T + timedelta(days=3)
    with pytest.raises(ApprovalBlocked, match="expired"):
        svc.approve(uid, "ann", "x")
    assert svc.dossier(uid)["status"] == "expired"


@pytest.mark.parametrize("fn", ["approve", "reject"])
def test_final_states_cannot_change(tmp_path, fn):
    svc, _ = service(tmp_path)
    uid = first(svc)["id"]
    svc.approve(uid, "ann", "x")
    with pytest.raises(IllegalTransition):
        getattr(svc, fn)(uid, "ann", "again")


def test_unknown_candidate(tmp_path):
    svc, _ = service(tmp_path)
    with pytest.raises(CandidateNotFound):
        svc.dossier("nope")


def test_only_an_ai_rejection_can_be_reopened(tmp_path):
    svc, _ = service(tmp_path)
    uid = first(svc)["id"]
    svc.reject(uid, "ann", "No.")
    with pytest.raises(ApprovalBlocked):
        svc.reopen(uid, "ann", "Changed my mind.")


# ---- expiry and outcomes
def frame(rows):
    return pd.DataFrame({"ts_utc": [T + timedelta(minutes=5 * i) for i in range(len(rows))], "high": [r[0] for r in rows], "low": [r[1] for r in rows],
                         "close": [r[2] for r in rows]})


def test_stale_candidates_expire_by_time(tmp_path):
    svc, _ = service(tmp_path)
    uid = first(svc)["id"]
    assert svc.expire_stale(T + timedelta(days=3)) == 1 and svc.dossier(uid)["status"] == "expired"
    assert svc.expire_stale(T + timedelta(days=3)) == 0


def test_a_broken_stop_expires_a_pending_candidate(tmp_path):
    svc, _ = service(tmp_path)
    uid = first(svc)["id"]
    svc.technical.candles.frames[("EURUSD", "M5")] = frame([(1.10, 1.08, 1.085)])
    assert svc.expire_stale(T + timedelta(minutes=10)) == 1
    assert "beyond the stop" in svc.dossier(uid)["status_history"][-1]["note"]


def test_outcomes_are_labelled_from_stored_candles(tmp_path):
    svc, _ = service(tmp_path)
    uid = first(svc)["id"]
    svc.technical.candles.frames[("EURUSD", "M5")] = frame([(1.1010, 1.0999, 1.1003), (1.1050, 1.1000, 1.1045)])
    assert svc.update_outcomes() == {"tp": 2}
    o = svc.dossier(uid)["outcome"]
    assert o["filled"] and o["result"] == "tp" and o["r_multiple"] > 2
    assert svc.update_outcomes() == {}


def test_resolved_candidates_feed_calibration(tmp_path):
    svc, _ = service(tmp_path)
    first(svc)
    svc.technical.candles.frames[("EURUSD", "M5")] = frame([(1.1010, 1.0999, 1.1003), (1.1050, 1.1000, 1.1045)])
    svc.update_outcomes()
    assert [v["status"] for v in svc.status()["calibration"].values()] == ["not_enough_outcomes"]     # the duplicate is excluded


# ---- other modules
def test_pending_approvals_is_real_and_safe_without_tables(tmp_path):
    svc, _ = service(tmp_path)
    q = AccountQueries(svc.engine, svc.settings)
    assert q._pending_approvals() == 0
    first(svc)
    assert q._pending_approvals() == 1 == svc.pending_approvals()
    bare, _ = service(tmp_path / "b" if not (tmp_path / "b").mkdir() else None, init=False)
    assert AccountQueries(bare.engine, bare.settings)._pending_approvals() == 0


def test_agents_and_notifications(tmp_path):
    svc, _ = service(tmp_path)
    q = SystemQueries(svc.engine, svc.settings)
    by_id = {a["id"]: a for a in q.agents(T)}
    assert by_id["decision"]["status"] == "idle" and by_id["strategy"]["status"] == "idle"
    first(svc)
    by_id = {a["id"]: a for a in q.agents(T)}
    assert by_id["decision"]["status"] == "completed" and "waiting" in by_id["decision"]["lastResult"]
    grade = svc.dossier(svc.signals()[0]["id"])["grade"]
    seen = [n for n in q.notifications(T) if n["category"] == "approval_required"]
    assert bool(seen) == (grade in ("A", "B"))


def test_expired_candidates_are_notified(tmp_path):
    svc, _ = service(tmp_path)
    first(svc)
    svc.expire_stale(T + timedelta(days=1))
    assert any("expired" in n["title"] for n in SystemQueries(svc.engine, svc.settings).notifications(T + timedelta(days=1)))


# ---- HTTP
@pytest.fixture
def api(tmp_path):
    ctx = ob_setup()
    svc, _ = service(tmp_path, ctx, aurum_admin_api_key="admin")
    with TestClient(create_app(settings=svc.settings, cfg=cot_cfg(), engine=svc.engine, decision_service=svc)) as c:
        yield c, svc, ctx


def test_signals_match_the_trade_signal_contract_exactly(api):
    client, svc, _ = api
    first(svc)
    rows = client.get("/api/v1/analysis/signals").json()
    assert set(rows[0]) == {"id", "instrument", "direction", "entryZone", "stopLoss", "takeProfit", "riskReward", "strategy", "timeframe",
                            "marketCondition", "confidence", "evidence", "timestamp", "status"}
    assert len(rows[0]["entryZone"]) == 2 and isinstance(rows[0]["evidence"][0], str)


def test_signal_filters(api):
    client, svc, _ = api
    first(svc)
    assert len(client.get("/api/v1/analysis/signals?status=new&instrument=eurusd&limit=5").json()) == 1
    assert client.get("/api/v1/analysis/signals?status=approved").json() == []
    assert client.get("/api/v1/analysis/signals?status=bogus").status_code == 422
    assert client.get("/api/v1/analysis/signals?limit=0").status_code == 422


def test_dossier_is_camel_case_and_carries_the_disclaimer(api):
    client, svc, _ = api
    uid = first(svc)["id"]
    d = client.get(f"/api/v1/analysis/signals/{uid}").json()
    assert {"evidenceDetail", "gates", "aiReview", "statusHistory", "outcome", "suggestedVolume", "availability"} <= set(d)
    assert d["disclaimer"] == DISCLAIMER
    assert client.get("/api/v1/analysis/signals/nope").status_code == 404


@pytest.mark.parametrize("path", ["approve", "reject", "reopen"])
def test_decisions_need_the_admin_key(api, path):
    client, svc, _ = api
    uid = first(svc)["id"]
    assert client.post(f"/api/v1/analysis/signals/{uid}/{path}", json=DECISION).status_code == 401
    assert client.post(f"/api/v1/analysis/signals/{uid}/{path}", json=DECISION, headers={"X-AURUM-Admin-Key": "bad"}).status_code in (401, 403)


def test_admin_runs_need_the_admin_key(api):
    client, _, _ = api
    assert client.post("/api/v1/decision/run", json={}).status_code == 401
    assert client.post("/api/v1/backtests/run", json={"from": "2026-01-01", "to": "2026-02-01"}).status_code == 401


def test_approve_and_reject_over_http(api):
    client, svc, _ = api
    uid = first(svc)["id"]
    r = client.post(f"/api/v1/analysis/signals/{uid}/approve", json=DECISION, headers=ADMIN)
    assert r.status_code == 200 and r.json()["status"] == "approved"
    assert client.post(f"/api/v1/analysis/signals/{uid}/reject", json=DECISION, headers=ADMIN).status_code == 409
    assert client.post("/api/v1/analysis/signals/nope/approve", json=DECISION, headers=ADMIN).status_code == 404


def test_a_note_and_reviewer_are_required(api):
    client, svc, _ = api
    uid = first(svc)["id"]
    assert client.post(f"/api/v1/analysis/signals/{uid}/approve", json={"reviewer": "ann", "note": ""}, headers=ADMIN).status_code == 422
    assert client.post(f"/api/v1/analysis/signals/{uid}/approve", json={"note": "x"}, headers=ADMIN).status_code == 422


def test_blocked_approval_is_a_conflict(api):
    client, svc, ctx = api
    uid = first(svc)["id"]
    ctx.intelligence["event_risk"]["state"] = "RELEASE_WINDOW"
    r = client.post(f"/api/v1/analysis/signals/{uid}/approve", json=DECISION, headers=ADMIN)
    assert r.status_code == 409 and "event_risk" in r.json()["detail"]


def test_context_and_status(api):
    client, svc, _ = api
    assert client.get("/api/v1/decision/context/EURUSD").json()["disclaimer"] == DISCLAIMER
    st = client.get("/api/v1/decision/status").json()
    assert st["schemaReady"] is True and st["disclaimer"] == DISCLAIMER and "sk-ant" not in str(st)


def test_decision_run_is_a_background_job(api):
    client, _, _ = api
    r = client.post("/api/v1/decision/run", json={"symbols": ["EURUSD"], "aiReview": False}, headers=ADMIN)
    assert r.status_code == 202 and r.json()["type"] == "decision_run"
    assert client.get(f"/api/v1/decision/jobs/{r.json()['jobId']}").status_code == 200
    assert client.get("/api/v1/decision/jobs/nope").status_code == 404


def test_no_backtest_yet_is_a_404_not_fake_numbers(api):
    client, _, _ = api
    assert client.get("/api/v1/backtests/latest").status_code == 404


def test_latest_backtest_is_a_performance_report(api):
    from aurum.decision.backtest.metrics import report
    client, svc, _ = api
    rep = report([], 0.5, 10_000, None, 30)
    svc.repo.save_backtest("fp", ["EURUSD"], T, T + timedelta(days=1), None, rep, [])
    body = client.get("/api/v1/backtests/latest").json()
    assert body["kind"] == "backtest" and "totalTrades" in body["metrics"] and "equityCurve" in body and body["disclaimer"] == DISCLAIMER


def test_uninitialised_tables_are_a_503(tmp_path):
    svc, _ = service(tmp_path, init=False)
    with TestClient(create_app(settings=svc.settings, cfg=cot_cfg(), engine=svc.engine, decision_service=svc)) as c:
        assert c.get("/api/v1/analysis/signals").status_code == 503


def test_no_order_endpoint_exists(api):
    client, _, _ = api
    paths = list(client.app.openapi()["paths"])
    assert len(paths) > 30
    assert not [p for p in paths if re.search(r"order|execute|place|position_open", p)]


# ---- the /ai surface
def test_analyze_needs_the_admin_key_and_returns_a_decision(api):
    client, _, _ = api
    assert client.post("/api/v1/ai/analyze", json={"symbol": "EURUSD"}).status_code == 401
    r = client.post("/api/v1/ai/analyze", json={"symbol": "EURUSD", "aiReview": False}, headers=ADMIN)
    a = r.json()["analysis"]
    assert r.status_code == 200 and a["decision"]["decision"] in DECISIONS and a["humanReview"] == "PENDING_HUMAN_REVIEW"
    assert {"regime", "bias", "conflicts", "explanation", "evidence", "cotPositioning"} <= set(a)


def test_analysis_history_and_details(api):
    client, svc, _ = api
    uid = svc.run("EURUSD")["run_id"]
    rows = client.get("/api/v1/ai/analysis?symbol=eurusd").json()
    assert rows[0]["id"] == uid and rows[0]["decision"]
    assert client.get(f"/api/v1/ai/analysis/{uid}").json()["summary"]["decision"]
    assert client.get(f"/api/v1/ai/analysis/{uid}/evidence").json()["explanation"]["source"] == "deterministic"
    assert client.get(f"/api/v1/ai/analysis/{uid}/decision").json()["humanReview"] == "PENDING_HUMAN_REVIEW"
    assert client.get("/api/v1/ai/analysis/nope").status_code == 404
    assert client.get("/api/v1/ai/analysis?limit=0").status_code == 422


@pytest.mark.parametrize("path,key", [("bias", "overall"), ("regime", "regime"), ("setups", "setups")])
def test_read_only_views(api, path, key):
    client, _, _ = api
    body = client.get(f"/api/v1/ai/{path}/EURUSD").json()
    assert key in body and body["disclaimer"] == DISCLAIMER


def test_setups_save_nothing(api):
    client, svc, _ = api
    assert client.get("/api/v1/ai/setups/EURUSD").json()["setups"]
    assert svc.signals() == []


def test_trade_ideas_are_marked_for_human_review(api):
    client, svc, _ = api
    uid = first(svc)["id"]
    ideas = lambda: {i["id"]: i for i in client.get("/api/v1/ai/trade-ideas").json()}
    idea = ideas()[uid]
    assert idea["humanReview"] == "PENDING_HUMAN_REVIEW" and idea["reviewStatus"] == "GENERATED" and idea["volumeIsEstimate"] is True
    assert client.get("/api/v1/ai/trade-ideas?status=bogus").status_code == 422
    svc.approve(uid, "ann", "x")
    assert ideas()[uid]["reviewStatus"] == "APPROVED_FOR_FURTHER_PROCESSING"


def test_a_broken_stop_reads_as_invalidated(tmp_path):
    svc, _ = service(tmp_path)
    uid = first(svc)["id"]
    svc.technical.candles.frames[("EURUSD", "M5")] = frame([(1.10, 1.08, 1.085)])
    svc.expire_stale(T + timedelta(minutes=10))
    assert svc.dossier(uid)["review_status"] == "INVALIDATED"


def test_strategies_providers_health(api):
    client, _, _ = api
    assert len(client.get("/api/v1/ai/strategies").json()) == 8
    assert client.get("/api/v1/ai/providers").status_code == 200
    assert client.get("/api/v1/ai/health").json()["schemaReady"] is True


def test_config_read_and_validated_write(api):
    client, svc, _ = api
    before = client.get("/api/v1/ai/config").json()["fingerprint"]
    change = {"reviewer": "ann", "overrides": {"gates": {"min_rr": 3.0}}}
    assert client.put("/api/v1/ai/config", json=change).status_code == 401
    r = client.put("/api/v1/ai/config", json=change, headers=ADMIN)
    assert r.status_code == 200 and r.json()["fingerprint"] != before and svc.cfg.gates["min_rr"] == 3.0
    bad = client.put("/api/v1/ai/config", json={"reviewer": "ann", "overrides": {"gates": {"nope": 1}}}, headers=ADMIN)
    assert bad.status_code == 422 and svc.cfg.gates["min_rr"] == 3.0


def test_config_changes_survive_a_restart(tmp_path):
    from aurum.decision.service import DecisionService
    svc, _ = service(tmp_path)
    svc.config_update({"gates": {"min_rr": 3.0}}, "ann")
    assert DecisionService(svc.settings, svc.engine, technical=svc.technical, intelligence=object()).cfg.gates["min_rr"] == 3.0


def test_evaluation_endpoints(api):
    from aurum.decision.backtest.metrics import report
    client, svc, _ = api
    assert client.get("/api/v1/ai/evaluation/runs").json() == []
    assert client.post("/api/v1/ai/evaluation/run", json={"from": "2026-01-01", "to": "2026-02-01"}).status_code == 401
    uid = svc.repo.save_backtest("fp", ["EURUSD"], T, T + timedelta(days=1), None, report([], 0.5, 10_000, None, 30), [])
    assert client.get("/api/v1/ai/evaluation/runs").json()[0]["id"] == uid
    assert client.get(f"/api/v1/ai/evaluation/{uid}").json()["kind"] == "backtest"
    assert client.get("/api/v1/ai/evaluation/nope").status_code == 404


def test_batch_analyze_is_a_background_job(api):
    client, _, _ = api
    r = client.post("/api/v1/ai/analyze/batch", json={"symbols": ["EURUSD"], "aiReview": False}, headers=ADMIN)
    assert r.status_code == 202 and r.json()["type"] == "ai_analyze_batch"


# ---- CLI
def cli(svc, monkeypatch, *args):
    from aurum.cli import app
    monkeypatch.setattr("aurum.decision.cli._service", lambda: svc)
    return CliRunner().invoke(app, ["decide", *args])


def test_cli_run_list_show(tmp_path, monkeypatch):
    svc, _ = service(tmp_path)
    r = cli(svc, monkeypatch, "run", "--symbol", "EURUSD", "--no-ai")
    assert r.exit_code == 0 and "Candidates" in r.output and "not a probability" in " ".join(r.output.split())
    assert cli(svc, monkeypatch, "list", "--status", "new").exit_code == 0
    uid = svc.signals()[0]["id"]
    r = cli(svc, monkeypatch, "show", uid)
    assert r.exit_code == 0 and "Gates" in r.output and "not a probability" in " ".join(r.output.split())


def test_cli_explains_no_setup(tmp_path, monkeypatch):
    svc, _ = service(tmp_path, context())
    r = cli(svc, monkeypatch, "run", "--symbol", "EURUSD", "--no-ai")
    assert r.exit_code == 0 and "No setup" in r.output


def test_cli_approve_says_no_trade_is_placed(tmp_path, monkeypatch):
    svc, _ = service(tmp_path)
    uid = first(svc)["id"]
    r = cli(svc, monkeypatch, "approve", uid, "--note", "ok")
    assert r.exit_code == 0 and "does not place trades" in " ".join(r.output.split())
    assert cli(svc, monkeypatch, "approve", uid, "--note", "again").exit_code == 1


def test_cli_approve_needs_a_note(tmp_path, monkeypatch):
    svc, _ = service(tmp_path)
    assert cli(svc, monkeypatch, "approve", first(svc)["id"]).exit_code == 2


def test_cli_reject_expire_outcomes_status(tmp_path, monkeypatch):
    svc, _ = service(tmp_path)
    uid = first(svc)["id"]
    assert cli(svc, monkeypatch, "reject", uid, "--note", "no").exit_code == 0
    for command in ("expire-stale", "outcomes-update", "status"):
        assert cli(svc, monkeypatch, command).exit_code == 0


def test_cli_check_prints_no_secret(tmp_path, monkeypatch):
    svc, _ = service(tmp_path, ai=True)
    r = cli(svc, monkeypatch, "check")
    assert r.exit_code == 0 and "key present True" in r.output and "sk-ant" not in r.output


@pytest.mark.parametrize("args,needle", [
    (("ai", "status"), "Tables ready"), (("ai", "analyze", "--symbol", "EURUSD", "--no-ai"), "regime"), (("ai", "bias", "--symbol", "EURUSD"), "bias"),
    (("ai", "analyze", "--symbol", "EURUSD", "--timeframe", "H1", "--no-ai"), "regime"), (("ai", "analyze", "--symbol", "EURUSD", "--timeframes", "H1,M15", "--no-ai"), "regime"),
    (("ai", "regime", "--symbol", "EURUSD"), "TREND"), (("ai", "setups", "--symbol", "EURUSD"), "order_block"), (("ai", "strategies", "list"), "session_breakout"),
    (("ai", "providers"), "deterministic"), (("ai", "config", "show"), "Fingerprint"), (("ai", "check"), "Tables ready"),
])
def test_ai_cli(tmp_path, monkeypatch, args, needle):
    from aurum.cli import app
    svc, _ = service(tmp_path)
    monkeypatch.setattr("aurum.decision.cli._service", lambda: svc)
    r = CliRunner().invoke(app, list(args))
    assert r.exit_code == 0 and needle in r.output


def test_ai_cli_reports_an_unknown_instrument(tmp_path, monkeypatch):
    from aurum.analysis.services.analysis_service import UnknownInstrumentError
    from aurum.cli import app
    svc, _ = service(tmp_path)

    def boom(*a, **k):
        raise UnknownInstrumentError("Unknown instrument 'X'.")
    svc.builder.build = boom
    monkeypatch.setattr("aurum.decision.cli._service", lambda: svc)
    assert CliRunner().invoke(app, ["ai", "bias", "--symbol", "X"]).exit_code == 1


# ---- no trading capability
def test_decision_package_has_no_trading_calls():
    forbidden = re.compile(r"\b(order_send|order_check|order_calc_\w+|positions_get|orders_get|TRADE_ACTION_\w+)\b")
    files = [*(ROOT / "aurum" / "decision").rglob("*.py"), ROOT / "aurum" / "api" / "routes" / "decision.py", ROOT / "aurum" / "api" / "routes" / "ai_engine.py"]
    assert len(files) > 15
    assert [str(p) for p in files if forbidden.search(p.read_text(encoding="utf-8"))] == []


def test_decision_package_never_imports_the_mt5_gateway():
    files = (ROOT / "aurum" / "decision").rglob("*.py")
    assert not [str(p) for p in files if re.search(r"MetaTrader5|mt5_gateway|ReadOnlyMT5", p.read_text(encoding="utf-8"))]


def test_nothing_sets_executed():
    for p in (ROOT / "aurum" / "decision").rglob("*.py"):
        if p.name != "lifecycle.py":
            assert '"executed"' not in p.read_text(encoding="utf-8"), p


# ---- PostgreSQL migration, when a test database is provided
@pytest.mark.skipif(not os.environ.get("AURUM_TEST_DATABASE_URL"), reason="set AURUM_TEST_DATABASE_URL to run")
def test_migration_is_idempotent_on_postgres():
    engine = create_engine(os.environ["AURUM_TEST_DATABASE_URL"])
    sql = (ROOT / "sql" / "migrations" / "0007_decision_engine.sql").read_text(encoding="utf-8")
    with engine.begin() as c:
        c.exec_driver_sql(sql)
        c.exec_driver_sql(sql)
        assert c.execute(text("select to_regclass('trade_candidates')")).scalar() is not None
    assert inspect(engine).has_table("backtest_trades")


# ---- one real call, on request only
@pytest.mark.skipif(os.environ.get("AURUM_LIVE_AI") != "1", reason="set AURUM_LIVE_AI=1 to spend real money on one call")
def test_live_review_returns_a_valid_answer(tmp_path):
    from aurum.config import get_settings
    key = get_settings().anthropic_api_key
    svc, _ = service(tmp_path, ai=True, anthropic_api_key=key.get_secret_value())
    svc.ai._client = None
    d = svc.dossier(first(svc)["id"])
    assert d["ai_review"]["status"] == "reviewed", d["ai_review"]
