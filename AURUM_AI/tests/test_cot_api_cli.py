"""Backend API (FastAPI TestClient) and CLI for Module 02, on an isolated SQLite database."""
from __future__ import annotations

import time
from datetime import date

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from typer.testing import CliRunner

from aurum.api.app import camelize, create_app
from aurum.config import Settings
from aurum.cot.ingestion import CotService
from aurum.database.connection import engine_from_settings, init_database
from cot_helpers import FakeClient, cot_cfg, series

KEY = "test-admin-key-123"
START = date(2025, 1, 7)


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, database_url=f"sqlite:///{(tmp_path / 'api.db').as_posix()}",
                    data_dir=tmp_path / "data", log_dir=tmp_path / "logs", mt5_server_timezone="Etc/UTC",
                    aurum_admin_api_key=SecretStr(KEY))


@pytest.fixture
def data():
    n = [5_000 + (i % 5) * 700 + i * 40 for i in range(80)]
    return {"legacy": series("legacy", "099741", START, n), "tff": series("tff", "099741", START, n)}


@pytest.fixture
def api(settings, data):
    engine = engine_from_settings(settings)
    init_database(engine)
    fake = FakeClient(data)
    CotService(settings, cot_cfg(), engine, client=fake, today=lambda: date(2026, 11, 2)).backfill(
        start=START, end=date(2026, 11, 2), report_types=["legacy", "tff"])
    app = create_app(settings, cot_cfg(), engine, client=fake)
    with TestClient(app) as c:
        yield c


P = "/api/v1/cot"


def test_health_and_status(api):
    assert api.get("/api/v1/health").json()["status"] == "ok"
    st = api.get(f"{P}/status").json()
    assert st["engine"] == "ok" and st["contractsMonitored"] == 8
    assert {d["reportType"] for d in st["datasets"]} == {"legacy", "tff"}
    eur = [s for s in st["series"] if s["instrument"] == "EURUSD"]
    assert eur and all("freshness" in s for s in eur)
    assert st["cftc"]["checked"] is False
    assert api.get(f"{P}/status?probe=true").json()["cftc"]["checked"] is True


def test_datasets_and_instruments(api):
    ds = api.get(f"{P}/datasets").json()
    assert {d["reportType"] for d in ds} == {"legacy", "tff", "disaggregated"}
    tff = next(d for d in ds if d["reportType"] == "tff")
    assert any(c["key"] == "leveraged_funds" for c in tff["categories"])
    inst = {i["instrument"]: i for i in api.get(f"{P}/instruments").json()}
    assert set(inst) == {"EURUSD", "GBPUSD", "XAUUSD", "US30"}
    assert {m["cftcContractCode"] for m in inst["US30"]["mappings"]} == {"124603"}


def test_reports_filters_and_pagination(api):
    r = api.get(f"{P}/reports", params={"instrument": "EURUSD", "reportType": "tff", "pageSize": 10, "page": 2}).json()
    assert r["total"] == 80 and r["page"] == 2 and r["pageSize"] == 10 and len(r["items"]) == 10
    assert r["items"][0]["reportDate"] > r["items"][-1]["reportDate"]                    # newest first
    both = api.get(f"{P}/reports", params={"instrument": "EURUSD", "pageSize": 500}).json()
    assert both["total"] == 160
    ranged = api.get(f"{P}/reports", params={"reportType": "legacy", "start": "2025-03-01", "end": "2025-03-31"}).json()
    assert ranged["total"] == 4
    assert api.get(f"{P}/reports", params={"instrument": "XAUUSD", "reportType": "legacy"}).json()["total"] == 0  # empty


def test_report_detail_has_original_payload(api):
    rid = api.get(f"{P}/reports", params={"instrument": "EURUSD", "reportType": "legacy", "pageSize": 1}).json()["items"][0]["id"]
    d = api.get(f"{P}/reports/{rid}").json()
    assert d["sourcePayload"]["CFTC Contract Market Code"] == "099741"                 # original CFTC names
    assert len(d["positions"]) == 4 and len(d["metrics"]) == 4
    assert api.get(f"{P}/reports/999999").status_code == 404


def test_positions_metrics_history(api):
    pos = api.get(f"{P}/positions", params={"instrument": "EURUSD", "reportType": "tff"}).json()
    assert len(pos["positions"]) == 6 and pos["releaseDate"]
    assert api.get(f"{P}/positions").status_code == 422
    m = api.get(f"{P}/metrics", params={"instrument": "EURUSD", "reportType": "tff", "category": "leveraged_funds"}).json()
    assert m["total"] == 80 and "cotIndex" in m["items"][0]
    h = api.get(f"{P}/history/EURUSD", params={"reportType": "tff"}).json()
    assert h["primaryCategory"] == "leveraged_funds" and len(h["series"]) == 80
    last = next(c for c in h["series"][-1]["categories"] if c["category"] == "leveraged_funds")
    assert {"long", "short", "spreading", "net", "historicalPercentile36m", "cotIndex"} <= set(last)


def test_analysis_and_divergence(api):
    a = api.get(f"{P}/analysis/EURUSD").json()
    assert a["instrument"] == "EURUSD" and len(a["families"]) == 2 and a["overall"]["stance"]
    assert set(a["families"][0]["statements"]) == {"facts", "metrics", "interpretation"}
    early = api.get(f"{P}/analysis/EURUSD", params={"asOf": "2025-03-07T12:00:00Z"}).json()
    assert {f["reportDate"] for f in early["families"]} == {"2025-02-25"}                 # 03-04 not public yet
    d = api.get(f"{P}/divergence/EURUSD", params={"reportType": "tff"}).json()
    assert d["category"] == "leveraged_funds" and d["available"] is False                # no Module 01 candles here
    assert api.get(f"{P}/divergence/EURUSD", params={"reportType": "tff", "category": "managed_money"}).status_code == 422


def test_invalid_parameters_and_missing_instruments(api):
    assert api.get(f"{P}/analysis/BTCUSD").status_code == 404
    assert api.get(f"{P}/history/NOPE").status_code == 404
    assert api.get(f"{P}/reports", params={"reportType": "weekly"}).status_code == 422
    assert api.get(f"{P}/reports", params={"pageSize": 0}).status_code == 422
    assert api.get(f"{P}/reports", params={"start": "not-a-date"}).status_code == 422
    assert api.get(f"{P}/quality", params={"severity": "fatal"}).status_code == 422
    assert api.get(f"{P}/export/everything").status_code == 422


def test_quality_logs_export(api):
    q = api.get(f"{P}/quality").json()
    assert {"items", "total", "page", "pageSize"} <= set(q)
    logs = api.get(f"{P}/logs", params={"jobType": "backfill"}).json()
    assert logs["total"] >= 2 and all(i["jobType"] == "backfill" for i in logs["items"])
    csv = api.get(f"{P}/export/metrics", params={"instrument": "EURUSD"})
    assert csv.status_code == 200 and csv.headers["content-type"].startswith("text/csv")
    assert "data_status" in csv.text.splitlines()[0]


def test_admin_operations_require_key(api):
    assert api.post(f"{P}/update").status_code == 401
    assert api.post(f"{P}/update", headers={"X-AURUM-Admin-Key": "wrong"}).status_code == 401
    assert api.post(f"{P}/backfill", json={"start": "2026-01-01", "end": "2025-01-01"},
                    headers={"X-AURUM-Admin-Key": KEY}).status_code == 422


def test_admin_disabled_without_configured_key(settings, data):
    settings = settings.model_copy(update={"aurum_admin_api_key": None})
    engine = engine_from_settings(settings)
    init_database(engine)
    with TestClient(create_app(settings, cot_cfg(), engine, client=FakeClient(data))) as c:
        assert c.post(f"{P}/update", headers={"X-AURUM-Admin-Key": KEY}).status_code == 503


def test_background_job_submission_and_completion(api):
    r = api.post(f"{P}/calculate", json={"instruments": ["eurusd"]}, headers={"X-AURUM-Admin-Key": KEY})
    assert r.status_code == 202 and r.json()["status"] in ("queued", "running")
    jid = r.json()["jobId"]
    for _ in range(100):
        j = api.get(f"{P}/jobs/{jid}").json()
        if j["status"] in ("succeeded", "failed"):
            break
        time.sleep(0.1)
    assert j["status"] == "succeeded" and j["result"]["metricsWritten"] > 0
    assert any(x["jobId"] == jid for x in api.get(f"{P}/jobs").json())
    assert api.get(f"{P}/jobs/unknown").status_code == 404
    up = api.post(f"{P}/update", headers={"X-AURUM-Admin-Key": KEY})
    assert up.status_code == 202
    assert api.post(f"{P}/calculate", json={"instruments": ["BTC"]}, headers={"X-AURUM-Admin-Key": KEY}).status_code == 404


def test_secrets_never_exposed(api):
    for url in (f"{P}/status", f"{P}/datasets", "/api/v1/health"):
        body = api.get(url).text
        assert KEY not in body and "password" not in body.lower()


def test_camelize():
    assert camelize({"a_b": [{"c_d_e": date(2026, 1, 2)}], "x": float("nan")}) == {"aB": [{"cDE": "2026-01-02"}], "x": None}


# ------------------------------------------------------------------ CLI
@pytest.fixture
def cli(settings, data, monkeypatch):
    import aurum.cli as main_cli
    import aurum.cot.ingestion as ing
    monkeypatch.setattr(main_cli, "_settings", lambda: settings)
    real_init = ing.CotService.__init__

    def init(self, s, cfg, engine, client=None, today=date.today):
        real_init(self, s, cfg, engine, client=FakeClient(data), today=lambda: date(2026, 11, 2))
    monkeypatch.setattr(ing.CotService, "__init__", init)
    return CliRunner(), main_cli.app


def test_cli_full_flow(cli):
    runner, app = cli
    assert runner.invoke(app, ["init-db"]).exit_code == 0                                # Module 01 command still works
    r = runner.invoke(app, ["cot", "backfill", "--start", "2025-01-01", "-r", "legacy", "-r", "tff"])
    assert r.exit_code == 0, r.output
    assert "Success" in r.output
    r = runner.invoke(app, ["cot", "update"])
    assert r.exit_code == 0 and "'inserted': 0" in r.output                               # idempotent
    for cmd in (["cot", "status"], ["cot", "datasets"], ["cot", "calculate"], ["cot", "sync-config"],
                ["cot", "report", "--instrument", "EURUSD"], ["cot", "report", "-i", "eurusd", "--json"],
                ["cot", "export", "--kind", "metrics"]):
        res = runner.invoke(app, cmd)
        assert res.exit_code == 0, (cmd, res.output)
    assert runner.invoke(app, ["cot", "report", "--instrument", "BTCUSD"]).exit_code == 1
    assert runner.invoke(app, ["cot", "quality"]).exit_code in (0, 2)


def test_cli_requires_schema(cli):
    runner, app = cli
    r = runner.invoke(app, ["cot", "status"])
    assert r.exit_code == 1 and "init-db" in r.output


def test_cli_help_lists_module01_and_cot_commands(cli):
    runner, app = cli
    out = runner.invoke(app, ["--help"]).output
    for c in ("check", "init-db", "collect", "export", "specs", "cot"):
        assert c in out
    cot_help = runner.invoke(app, ["cot", "--help"]).output
    for c in ("check", "datasets", "backfill", "update", "calculate", "report", "status", "quality", "export"):
        assert c in cot_help
