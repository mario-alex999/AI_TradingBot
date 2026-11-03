"""Multi-timeframe alignment, the analysis service on Module 01's candle store, persistence and the API."""
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from aurum.analysis.config import AnalysisConfig
from aurum.analysis.multi_timeframe.timeframe_analysis import align_to_as_of
from aurum.analysis.services.analysis_service import TechnicalAnalysisService, UnknownInstrumentError
from aurum.config import Settings
from aurum.database.connection import engine_from_settings, init_database
from aurum.database.repository import Repository

M15_START = pd.Timestamp("2026-08-03 00:00", tz="UTC")        # a Monday
AS_OF = datetime(2026, 9, 10, 10, 7, tzinfo=timezone.utc)      # mid-candle for every timeframe above M5


def _m15(n=4000, seed=5):
    rng = np.random.default_rng(seed)
    drift = np.repeat(rng.normal(0, 0.0004, n // 96 + 1), 96)[:n]
    c = 1.10 * np.exp(np.cumsum(drift + rng.normal(0, 0.0007, n)))
    o = np.r_[1.10, c[:-1]]
    sp = np.abs(rng.normal(0, 0.0004, n)) * c
    df = pd.DataFrame({"ts_utc": pd.date_range(M15_START, periods=n, freq="15min"), "open": o,
                       "high": np.maximum(o, c) + sp, "low": np.minimum(o, c) - sp, "close": c})
    return df[df["ts_utc"].dt.weekday < 5].reset_index(drop=True)          # no weekend candles


def _resample(m15, rule):
    agg = m15.set_index("ts_utc").resample(rule, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna().reset_index()
    return agg


def _store(repo, symbol, tf, df):
    iid = repo.upsert_instrument(symbol, symbol, "forex")
    out = df.copy()
    out["symbol"], out["broker_symbol"], out["timeframe"] = symbol, symbol, tf
    out["mt5_time"] = out["ts_utc"].astype("int64") // 10**9
    out["tick_volume"], out["real_volume"], out["spread"] = 100, 0, 2
    out["is_complete"], out["quality_status"], out["quality_flags"] = True, "valid", None
    repo.upsert_candles(iid, out, "test-run")


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("ta")
    settings = Settings(_env_file=None, database_url=f"sqlite:///{(tmp / 'ta.db').as_posix()}",
                        data_dir=tmp / "data", log_dir=tmp / "logs", aurum_admin_api_key=SecretStr("k" * 24))
    engine = engine_from_settings(settings)
    init_database(engine)
    repo = Repository(engine)
    m15 = _m15()
    for tf, rule in (("M15", None), ("H1", "1h"), ("H4", "4h"), ("D1", "1D")):
        _store(repo, "EURUSD", tf, m15 if rule is None else _resample(m15, rule))
    svc = TechnicalAnalysisService(settings, engine, AnalysisConfig(min_candles=30))
    return settings, engine, svc


def test_align_to_as_of_hides_unclosed_higher_timeframe_candles():
    df = pd.DataFrame({"ts_utc": pd.date_range("2026-09-08", periods=4, freq="1D", tz="UTC"), "open": 1.0,
                       "high": 1.1, "low": 0.9, "close": 1.0, "is_complete": True})
    as_of = datetime(2026, 9, 10, 12, tzinfo=timezone.utc)
    closed = align_to_as_of(df, "D1", as_of, include_provisional=False)
    assert closed["ts_utc"].max() == pd.Timestamp("2026-09-09", tz="UTC")
    prov = align_to_as_of(df, "D1", as_of, include_provisional=True)
    assert len(prov) == 3 and prov["is_complete"].tolist() == [True, True, False]


def test_service_analyses_module01_candles_without_lookahead(env):
    _, _, svc = env
    a = svc.analyze("eurusd", "H1", AS_OF)
    assert a.status == "ok" and a.symbol == "EURUSD" and a.as_of <= AS_OF
    assert a.last_candle == datetime(2026, 9, 10, 9, tzinfo=timezone.utc)       # 10:00 candle not closed at 10:07
    p = svc.analyze("EURUSD", "H1", AS_OF, include_provisional=True)
    assert p.provisional and p.last_candle == datetime(2026, 9, 10, 10, tzinfo=timezone.utc)
    assert any("forming" in m for m in p.messages)


def test_unknown_symbol_and_timeframe(env):
    _, _, svc = env
    with pytest.raises(UnknownInstrumentError):
        svc.analyze("NOPE", "H1")
    with pytest.raises(ValueError):
        svc.analyze("EURUSD", "H2")


def test_confluence_report_and_cot_fallback(env):
    _, _, svc = env
    r = svc.analyze_confluence("EURUSD", ["D1", "H4", "H1", "M15"], AS_OF)
    assert r.timeframes == ["D1", "H4", "H1", "M15"]
    assert set(r.per_timeframe) == {"D1", "H4", "H1", "M15"}
    assert r.alignment["consensus"] in ("bullish", "bearish", "mixed")
    assert r.score.explanation and r.disclaimer
    assert r.cot["available"] is False and r.cot["reason"]           # Module 02 present but no stored reports
    for tf in r.timeframes:
        assert r.per_timeframe[tf]["as_of"] <= AS_OF


def test_persistence_is_idempotent(env):
    _, _, svc = env
    svc.analyze("EURUSD", "H4", AS_OF, persist=True)
    n1 = len(svc.store.zones("EURUSD", "H4"))
    svc.cache.clear()
    svc.analyze("EURUSD", "H4", AS_OF, persist=True)
    assert len(svc.store.zones("EURUSD", "H4")) == n1 > 0
    assert len(svc.store.runs("EURUSD", "H4")) == 2


def test_historical_recalculation_does_not_roll_back_zone_states(env):
    _, _, svc = env
    later = AS_OF
    svc.cache.clear()
    svc.analyze("EURUSD", "H1", later, persist=True)
    latest = {z["zone_uid"]: (z["state"], z["last_run_uid"]) for z in svc.store.zones("EURUSD", "H1")}

    def evolved(as_of):
        a = svc.analyze("EURUSD", "H1", as_of)
        zones = a.fair_value_gaps + a.order_blocks + a.balanced_price_ranges + a.liquidity_pools
        return [z.id for z in zones if z.id in latest and z.state != latest[z.id][0]]

    earlier = next((later - timedelta(hours=h) for h in range(6, 24 * 15, 6) if evolved(later - timedelta(hours=h))),
                   None)
    assert earlier is not None, "fixture must contain zones whose state evolves between two dates"
    svc.cache.clear()
    svc.analyze("EURUSD", "H1", earlier, persist=True)                 # recalculate an earlier point in time
    after = {z["zone_uid"]: (z["state"], z["last_run_uid"]) for z in svc.store.zones("EURUSD", "H1")}
    assert all(after[k] == latest[k] for k in latest)                  # later state and writer kept
    assert len(svc.store.runs("EURUSD", "H1")) >= 2                    # the historical run is still recorded
    svc.cache.clear()
    newer = svc.analyze("EURUSD", "H1", later + timedelta(hours=1), persist=True)   # a newer run still updates
    reported = {z.id: z.state for z in newer.fair_value_gaps + newer.order_blocks + newer.liquidity_pools}
    stored = {z["zone_uid"]: z for z in svc.store.zones("EURUSD", "H1")}
    assert reported and all(stored[k]["state"] == st and stored[k]["last_run_uid"] != latest.get(k, (None, None))[1]
                            for k, st in reported.items())


def test_api_routes(env):
    from aurum.api.app import create_app
    settings, engine, svc = env
    with TestClient(create_app(settings, engine=engine, analysis_service=svc)) as c:
        asof = AS_OF.isoformat()
        r = c.get("/api/v1/analysis/technical/EURUSD", params={"timeframe": "H1", "asOf": asof})
        assert r.status_code == 200 and r.json()["structure"]["external"]["bias"] in ("bullish", "bearish", "ranging")
        assert "orderBlocks" in r.json() and "score" in r.json()
        full = c.get("/api/v1/analysis/technical/EURUSD", params={"timeframe": "H1", "asOf": asof, "detail": "full"})
        assert "swings" in full.json()
        conf = c.get("/api/v1/analysis/confluence/EURUSD", params={"timeframes": "H4,H1", "asOf": asof})
        assert conf.status_code == 200 and conf.json()["alignment"]["consensus"]
        ms = c.get("/api/v1/analysis/market-structure", params={"instrument": "EURUSD"}).json()
        assert ms["instrument"] == "EURUSD" and {t["timeframe"] for t in ms["timeframes"]} >= {"H1", "D1"}
        assert all(t["bias"] in ("bullish", "bearish", "ranging", "awaiting") for t in ms["timeframes"])
        ov = c.get("/api/v1/analysis/market-structure",
                   params={"instrument": "EURUSD", "timeframe": "H1", "view": "overlays"}).json()
        assert isinstance(ov, list) and all({"id", "kind", "label", "price"} <= set(o) for o in ov)
        assert c.get("/api/v1/analysis/technical/NOPE").status_code == 404
        assert c.get("/api/v1/analysis/technical/EURUSD", params={"timeframe": "H2"}).status_code == 422
        assert c.get("/api/v1/analysis/config").json()["fingerprint"]
        assert c.post("/api/v1/analysis/run", json={"symbol": "EURUSD"}).status_code == 401
        ok = c.post("/api/v1/analysis/run", json={"symbol": "EURUSD", "timeframes": ["H4", "H1"]},
                    headers={"X-AURUM-Admin-Key": "k" * 24})
        assert ok.status_code == 201 and ok.json()["symbol"] == "EURUSD"
        assert c.get("/api/v1/analysis/runs", params={"symbol": "EURUSD"}).json()["items"]
