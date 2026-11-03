"""Module 01 market-data API: shapes the frontend expects, computed only from stored rows.

The rows below are small synthetic fixtures written to an isolated SQLite database.
"""
from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from aurum.api.app import create_app
from aurum.api.market_queries import MarketQueries
from aurum.config import Settings
from aurum.database.connection import engine_from_settings, init_database, session_scope
from aurum.database.models import DataCollectionLog, DataQualityIssue, MarketCandle
from aurum.database.repository import Repository
from cot_helpers import cot_cfg

NOW = datetime.now(timezone.utc).replace(microsecond=0)
DAY = NOW.replace(hour=0, minute=0, second=0)


def _candle(inst_id: int, tf: str, ts: datetime, o: float, h: float, lo: float, c: float, **kw) -> MarketCandle:
    return MarketCandle(instrument_id=inst_id, timeframe=tf, ts_utc=ts, open=o, high=h, low=lo, close=c, tick_volume=kw.get("vol", 10),
                        is_complete=kw.get("complete", True), quality_status=kw.get("q", "valid"), broker_symbol="EURUSD",
                        mt5_time=int(ts.timestamp()))


@pytest.fixture
def engine(tmp_path):
    settings = Settings(_env_file=None, database_url=f"sqlite:///{(tmp_path / 'm.db').as_posix()}", data_dir=tmp_path / "data",
                        log_dir=tmp_path / "logs", mt5_server_timezone="Etc/UTC")
    eng = engine_from_settings(settings)
    init_database(eng)
    repo = Repository(eng)
    eur = repo.upsert_instrument("EURUSD", "EURUSDm", "forex", "Euro vs US Dollar")
    xau = repo.upsert_instrument("XAUUSD", "XAUUSDm", "metal", "Gold spot vs US Dollar")
    repo.insert_spec(eur, {"broker_symbol": "EURUSDm", "digits": 5, "point": 0.00001, "contract_size": 100000.0,
                           "tick_size": 0.00001, "tick_value": 1.0}, NOW - timedelta(hours=1), "run-1")
    repo.upsert_tick(eur, {"ts_utc": NOW - timedelta(seconds=5), "bid": 1.10500, "ask": 1.10508, "spread_points": 8,
                           "broker_symbol": "EURUSDm", "mt5_time_msc": 1}, "run-1")
    repo.upsert_tick(xau, {"ts_utc": NOW - timedelta(hours=3), "bid": 2650.0, "ask": 2650.3, "spread_points": 30,
                           "broker_symbol": "XAUUSDm", "mt5_time_msc": 2}, "run-1")
    with session_scope(eng) as s:
        s.add(_candle(eur, "D1", DAY - timedelta(days=1), 1.1, 1.102, 1.098, 1.10000))
        s.add(_candle(eur, "M5", DAY + timedelta(minutes=5), 1.1, 1.10700, 1.09900, 1.104, vol=40))
        s.add(_candle(eur, "M5", DAY + timedelta(minutes=10), 1.104, 1.10600, 1.10300, 1.105, vol=60))
        s.add(_candle(eur, "M5", DAY + timedelta(minutes=15), 1.105, 1.2, 1.0, 1.105, q="invalid"))  # excluded everywhere
        for i in range(3):
            s.add(_candle(eur, "H1", DAY - timedelta(hours=3 - i), 1.1, 1.101, 1.099, 1.1 + i / 1000))
        s.add(DataCollectionLog(run_id="run-1", event="run", status="success", started_at=NOW - timedelta(minutes=2),
                                finished_at=NOW - timedelta(minutes=2) + timedelta(milliseconds=850)))
        for i, st in enumerate(["success", "partial", "failed"]):
            s.add(DataCollectionLog(run_id="run-1", event="candles", status=st, symbol="EURUSD", timeframe="H1",
                                    started_at=NOW - timedelta(minutes=10 - i), finished_at=NOW - timedelta(minutes=10 - i, seconds=-1),
                                    rows_received=500, rows_stored=500 if st == "success" else 480,
                                    message=None if st == "success" else f"{st}: terminal timeout"))
        s.add(DataQualityIssue(fingerprint="a", issue_type="invalid_ohlc", severity="error", symbol="EURUSD", timeframe="M5"))
        s.add(DataQualityIssue(fingerprint="b", issue_type="missing_candles", severity="warning", symbol="EURUSD", timeframe="H1"))
        s.add(DataQualityIssue(fingerprint="c", issue_type="duplicate_candle", severity="warning", symbol="EURUSD", timeframe="H1",
                               resolved=True))  # resolved issues are not counted
    return eng, settings


@pytest.fixture
def client(engine):
    eng, settings = engine
    with TestClient(create_app(settings, cot_cfg(), eng)) as c:
        yield c


def test_instruments_map_asset_classes(client):
    assert client.get("/api/v1/market/instruments").json() == [
        {"symbol": "EURUSD", "name": "Euro vs US Dollar", "assetClass": "forex", "enabled": True},
        {"symbol": "XAUUSD", "name": "Gold spot vs US Dollar", "assetClass": "commodities", "enabled": True},
    ]


def test_quotes_come_from_the_latest_tick_and_todays_candles(client):
    q = {x["symbol"]: x for x in client.get("/api/v1/market/quotes").json()}
    eur = q["EURUSD"]
    assert (eur["bid"], eur["ask"], eur["spread"], eur["digits"]) == (1.105, 1.10508, 8.0, 5)
    assert eur["dailyChangePct"] == pytest.approx(0.4545, abs=1e-4)  # vs yesterday's D1 close 1.10000
    assert (eur["dailyHigh"], eur["dailyLow"], eur["volume"]) == (1.107, 1.099, 100)  # invalid candle ignored
    assert eur["status"] == "open"  # tick 5 s old
    assert q["XAUUSD"]["status"] == "closed" and q["XAUUSD"]["dailyChangePct"] == 0.0  # stale tick, no prior close
    only = client.get("/api/v1/market/quotes", params={"symbols": "eurusd"}).json()
    assert [x["symbol"] for x in only] == ["EURUSD"]


def test_candles_are_ascending_valid_and_limited(client):
    rows = client.get("/api/v1/market/candles", params={"symbol": "EURUSD", "timeframe": "M5"}).json()
    assert [r["close"] for r in rows] == [1.104, 1.105] and rows[0]["time"] < rows[1]["time"]
    assert len(client.get("/api/v1/market/candles", params={"symbol": "EURUSD", "timeframe": "H1", "limit": 2}).json()) == 2
    assert client.get("/api/v1/market/candles", params={"symbol": "NOPE", "timeframe": "H1"}).status_code == 404
    assert client.get("/api/v1/market/candles", params={"symbol": "EURUSD", "timeframe": "W1"}).status_code == 422


def test_specifications_and_data_quality(client):
    specs = {s["symbol"]: s for s in client.get("/api/v1/market/specifications").json()}
    assert specs["EURUSD"]["brokerSymbol"] == "EURUSDm" and specs["EURUSD"]["tickValue"] == 1.0
    assert specs["XAUUSD"]["digits"] is None  # no snapshot yet: missing, not guessed
    dq = client.get("/api/v1/market/data-quality").json()
    assert dq["instrumentsMonitored"] == ["EURUSD", "XAUUSD"]
    assert dq["candlesCollected"] == 7 and dq["validationErrors"] == 1
    assert (dq["invalidOhlc"], dq["missingCandles"], dq["duplicateCandles"]) == (1, 1, 0)
    assert dq["collectionErrors"] == 1 and dq["latencyMs"] == 850 and dq["lastValidationResult"] == "passed"
    tf = {t["timeframe"]: t for t in dq["byTimeframe"]}
    assert tf["M5"]["storedCandles"] == 3 and tf["M15"]["availability"] == "missing" and tf["M15"]["quality"] == "unknown"


def test_collection_logs_page_and_filter(client):
    page = client.get("/api/v1/market/collection-logs", params={"page_size": 2}).json()
    assert page["total"] == 3 and len(page["items"]) == 2 and page["items"][0]["validationStatus"] == "failed"
    failed = client.get("/api/v1/market/collection-logs", params={"status": "failed"}).json()["items"]
    assert len(failed) == 1 and failed[0]["errorMessage"] == "failed: terminal timeout" and failed[0]["durationMs"] == 1000
    assert client.get("/api/v1/market/collection-logs", params={"search": "timeout"}).json()["total"] == 2


def test_export_csv_and_parquet(client):
    r = client.get("/api/v1/market/export", params={"format": "csv", "symbol": "EURUSD", "timeframe": "H1"})
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    assert len(pd.read_csv(io.StringIO(r.text))) == 3
    r = client.get("/api/v1/market/export", params={"format": "parquet"})
    df = pd.read_parquet(io.BytesIO(r.content))
    assert set(df["timeframe"]) == {"D1", "H1", "M5"} and "invalid" not in set(df["quality_status"])
    assert client.get("/api/v1/market/export", params={"symbol": "BTCUSD"}).status_code == 404


def test_health_reports_feed_state_and_capabilities(client):
    h = client.get("/api/v1/health").json()
    assert h["environment"] == "demo" and h["mt5"] == "connected" and h["collection"] == "connected"
    assert h["capabilities"] == ["market.export.csv", "market.export.parquet"]


def test_feed_state_flags_a_stale_feed(engine):
    eng, _ = engine
    later = NOW + timedelta(hours=2)
    assert MarketQueries(eng).feed_state(300, now=later) == {"mt5": "disconnected", "collection": "warning"}


def test_quote_stream_emits_server_sent_events(engine, monkeypatch):
    import asyncio

    import aurum.api.routes.market as market_routes

    monkeypatch.setattr(market_routes, "STREAM_POLL_SECONDS", 0.01)
    eng, _ = engine
    calls = {"n": 0}

    async def disconnected() -> bool:
        calls["n"] += 1
        return calls["n"] > 1  # one polling round, then the client leaves

    async def collect() -> str:
        return "".join([c async for c in market_routes.quote_events(MarketQueries(eng), disconnected, ["EURUSD"])])

    body = asyncio.run(collect())
    assert body.startswith("retry: 3000")
    assert body.count("event: quote") == 1 and '"symbol": "EURUSD"' in body and "XAUUSD" not in body


def test_quote_stream_route_is_served(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/v1/market/stream/quotes" in paths
