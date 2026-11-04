"""Errors reach the browser as errors: COT divergence on an empty database, and 500s that keep CORS headers."""
from __future__ import annotations

from cot_helpers import cot_cfg
from fastapi.testclient import TestClient

from aurum.api.app import create_app
from aurum.config import Settings
from aurum.database.connection import engine_from_settings, init_database
from aurum.database.repository import Repository

ORIGIN = "http://localhost:5173"


def _client(tmp_path):
    s = Settings(_env_file=None, database_url=f"sqlite:///{(tmp_path / 'e.db').as_posix()}", data_dir=tmp_path / "d",
                 log_dir=tmp_path / "l", api_cors_origins=ORIGIN)
    eng = engine_from_settings(s)
    init_database(eng)
    Repository(eng).upsert_instrument("EURUSD", "EURUSDm", "forex", "Euro")
    return TestClient(create_app(s, cot_cfg(), eng), raise_server_exceptions=False), eng


def test_divergence_without_cot_data_is_unavailable_not_a_crash(tmp_path):
    c, _ = _client(tmp_path)
    r = c.get("/api/v1/cot/divergence/EURUSD", params={"reportType": "legacy"})
    assert r.status_code == 200 and r.json()["available"] is False and "No COT metrics" in r.json()["reason"]


def test_unhandled_errors_keep_cors_headers(tmp_path, monkeypatch):
    c, _ = _client(tmp_path)
    from aurum.api.market_queries import MarketQueries

    def boom(self, *a, **k):
        raise RuntimeError("secret detail")

    monkeypatch.setattr(MarketQueries, "instruments", boom)
    r = c.get("/api/v1/market/instruments", headers={"Origin": ORIGIN})
    assert r.status_code == 500 and r.headers.get("access-control-allow-origin") == ORIGIN
    assert "secret" not in r.text
