"""Module 04 API, CLI, persistence, migration safety and end-to-end no-look-ahead."""
import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from aurum.api.app import create_app
from aurum.database.connection import engine_from_settings, init_database
from aurum.market_intelligence import cli as mi_cli
from aurum.market_intelligence.candles import FrameCandleSource
from aurum.market_intelligence.module_adapters import Module02Adapter, NullModule03
from aurum.market_intelligence.repository import TABLES
from aurum.market_intelligence.service import MarketIntelligenceService
from mi_helpers import cfg, mock_http, resample, settings, source, walk

UTC = timezone.utc
C = cfg()
NOW = datetime(2026, 3, 11, 11, 7, tzinfo=UTC)
ADMIN = "admin-test-key"
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def svc(tmp_path):
    st = settings(tmp_path, aurum_admin_api_key=ADMIN)
    engine = engine_from_settings(st)
    init_database(engine)
    return MarketIntelligenceService(st, engine, C, source(), NullModule03(), Module02Adapter(None),
                                     http_factory=lambda s: mock_http(lambda r: httpx.Response(503), s), now=lambda: NOW)


@pytest.fixture
def client(svc):
    with TestClient(create_app(svc.settings, engine=svc.engine, intelligence_service=svc)) as c:
        yield c


P = "/api/v1/intelligence"


def test_read_endpoints_return_camelcase_and_provenance(client):
    st = client.get(f"{P}/status").json()
    assert st["schemaReady"] is True and st["tradeExecution"] is False
    s = client.get(f"{P}/sessions/current", params={"asOf": NOW.isoformat()}).json()
    assert s["activeSessions"] == ["london"] and "configFingerprint" in s
    lv = client.get(f"{P}/sessions/levels/EURUSD", params={"asOf": NOW.isoformat()}).json()
    assert {x["levelType"] for x in lv["levels"]} >= {"previous_london_high", "previous_day_low", "previous_week_high"}
    v = client.get(f"{P}/volatility/EURUSD", params={"asOf": NOW.isoformat()}).json()
    assert v["regime"] in ("VERY_LOW", "LOW", "NORMAL", "HIGH", "VERY_HIGH")
    hist = client.get(f"{P}/sessions/history", params={"end": NOW.isoformat(), "names": "london"}).json()
    assert hist["total"] == 6                                   # 7-day default window: 5 completed + the open session


def test_calendar_without_provider_is_unavailable_not_fabricated(client):
    cal = client.get(f"{P}/calendar", params={"range": "this_week"}).json()
    assert cal["items"] == [] and cal["coverage"]["complete"] is False and cal["coverage"]["level"] == "none"
    m = client.get(f"{P}/market/EURUSD", params={"asOf": NOW.isoformat()}).json()
    assert m["eventRisk"]["state"] == "UNKNOWN" and m["scores"]["eventRisk"]["score"] is None
    assert m["integrations"]["economicCalendar"]["available"] is False
    assert "not a trade signal" in m["disclaimer"]


def test_errors_map_to_http_status(client):
    assert client.get(f"{P}/market/NOPE").status_code == 404
    assert client.get(f"{P}/calendar", params={"importance": "extreme"}).status_code == 422
    assert client.get(f"{P}/calendar/123456").status_code == 404


def test_admin_operations_need_the_key_and_run_as_jobs(client):
    assert client.post(f"{P}/calendar/refresh", json={}).status_code == 401
    assert client.post(f"{P}/analyze/EURUSD", json={}).status_code == 401
    r = client.post(f"{P}/calendar/refresh", json={"daysBack": 1, "daysAhead": 30}, headers={"X-AURUM-Admin-Key": ADMIN})
    assert r.status_code == 202
    job = r.json()["jobId"]
    for _ in range(100):
        j = client.get(f"{P}/jobs/{job}").json()
        if j["status"] in ("succeeded", "failed"):
            break
        time.sleep(0.05)
    assert j["status"] == "succeeded"


def test_no_trading_routes_exist(client):
    paths = [p for p in client.app.openapi()["paths"] if p.startswith(P)]
    assert len(paths) > 25 and not [p for p in paths if re.search(r"order|trade|execut|position", p, re.I)]


def test_schema_not_ready_is_503(tmp_path):
    st = settings(tmp_path)
    engine = engine_from_settings(st)                          # tables never created
    s = MarketIntelligenceService(st, engine, C, source(), NullModule03(), Module02Adapter(None), now=lambda: NOW)
    with TestClient(create_app(st, engine=engine, intelligence_service=s)) as c:
        assert c.get(f"{P}/calendar").status_code == 503
        assert c.get(f"{P}/sessions/current").status_code == 200     # sessions need no database


def test_persisted_analysis_is_idempotent(svc):
    a = svc.analyze("EURUSD", NOW, persist=True)
    b = svc.analyze("EURUSD", NOW, persist=True)
    assert a["run_uid"] and b["run_uid"] and a["run_uid"] != b["run_uid"]
    counts = svc.repo.table_counts()
    assert counts["market_session_levels"] == len(a["levels"]["levels"])          # upserted, not duplicated
    assert counts["market_intelligence_runs"] == 2


def test_analysis_has_no_look_ahead():
    """The full report at T is identical whether or not candles after T are stored."""
    m5 = walk(datetime(2026, 1, 5, tzinfo=UTC), datetime(2026, 3, 14, tzinfo=UTC), 5, step=0.00015, tf="M5")
    later = m5.ts_utc >= pd.Timestamp("2026-03-11T11:05Z")
    m5.loc[later, "high"] += 0.03
    m5.loc[later, "low"] -= 0.03

    def src(df):
        return FrameCandleSource({("EURUSD", "M5"): df, ("EURUSD", "M15"): resample(df, "15min", "M15"),
                                  ("EURUSD", "H1"): resample(df, "1h", "H1"), ("EURUSD", "D1"): resample(df, "1D", "D1")})

    def run(s):
        svc = MarketIntelligenceService(settings(Path("."), database_url="sqlite:///:memory:"), None, C, s,
                                        NullModule03(), Module02Adapter(None), now=lambda: NOW)
        return svc.analyze("EURUSD", NOW)

    # resampled H1/D1 bars that are still open at NOW are excluded by close_t <= as_of, so truncating M5 is exact
    a, b = run(src(m5)), run(src(m5[~later]))
    strip = lambda r: json.dumps({k: r[k] for k in ("levels", "liquidity", "volatility", "conditions")}, default=str, sort_keys=True)
    assert strip(a) == strip(b)


# ------------------------------------------------------------------------------------------ CLI
def test_cli_commands(svc, monkeypatch, tmp_path):
    monkeypatch.setattr(mi_cli, "_service", lambda: svc)
    run = CliRunner().invoke
    for args in (["check"], ["sessions", "--as-of", NOW.isoformat()], ["session-levels", "-s", "EURUSD", "--as-of", NOW.isoformat()],
                 ["calendar"], ["calendar-upcoming"], ["volatility", "-s", "EURUSD", "--as-of", NOW.isoformat()],
                 ["conditions", "-s", "EURUSD", "--as-of", NOW.isoformat()], ["status"], ["macro"]):
        r = run(mi_cli.intelligence_app, args)
        assert r.exit_code == 0, (args, r.output)
    r = run(mi_cli.intelligence_app, ["analyze", "-s", "EURUSD", "--as-of", NOW.isoformat(), "--json"])
    assert r.exit_code == 0 and json.loads(r.output)["event_risk"]["state"] == "UNKNOWN"
    r = run(mi_cli.intelligence_app, ["export", "-s", "EURUSD", "--as-of", NOW.isoformat(), "--out", str(tmp_path / "x")])
    assert r.exit_code == 0 and list((tmp_path / "x").glob("EURUSD_*_intelligence.json"))
    r = run(mi_cli.intelligence_app, ["session-levels", "-s", "NOPE"])
    assert r.exit_code == 1 and "Unknown instrument" in r.output


def test_cli_calendar_update_reports_failure_with_nonzero_exit(tmp_path, monkeypatch):
    st = settings(tmp_path, economic_calendar_provider="fmp", economic_calendar_api_key="SECRET-XYZ")
    engine = engine_from_settings(st)
    init_database(engine)
    s = MarketIntelligenceService(st, engine, C, source(), NullModule03(), Module02Adapter(None),
                                  http_factory=lambda sec: mock_http(lambda r: httpx.Response(403), sec), now=lambda: NOW)
    monkeypatch.setattr(mi_cli, "_service", lambda: s)
    r = CliRunner().invoke(mi_cli.intelligence_app, ["calendar-update", "--days-back", "1", "--days-ahead", "1"])
    assert r.exit_code == 1 and "FAILED" in r.output and "SECRET-XYZ" not in r.output


# ------------------------------------------------------------------------------------------ migration
MIGRATION = ROOT / "sql" / "migrations" / "0004_market_intelligence.sql"


def test_migration_is_additive_only():
    sql = re.sub(r"--[^\n]*", "", MIGRATION.read_text(encoding="utf-8"))
    assert not re.search(r"\b(DROP|TRUNCATE|DELETE\s+FROM|ALTER\s+TABLE\s+\w+\s+DROP|UPDATE\s+\w+\s+SET)\b", sql, re.I)
    creates = re.findall(r"CREATE\s+(?:UNIQUE\s+)?(?:TABLE|INDEX)\s+(IF\s+NOT\s+EXISTS\s+)?", sql, re.I)
    assert creates and all(c.strip() for c in creates)          # every CREATE is IF NOT EXISTS
    altered = re.findall(r"ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?(\w+)", sql, re.I)
    assert set(altered) <= set(TABLES)                           # never alters a Module 01-03 table


def test_migration_tables_match_orm_models():
    sql = MIGRATION.read_text(encoding="utf-8")
    in_sql = set(re.findall(r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+(\w+)", sql, re.I))
    assert len(in_sql) == 18 and in_sql == set(TABLES)
