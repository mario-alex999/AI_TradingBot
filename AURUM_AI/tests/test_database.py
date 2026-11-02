"""Repository tests. Run on SQLite always; also on PostgreSQL when AURUM_TEST_DATABASE_URL is set
(e.g. postgresql+psycopg://aurum:pw@localhost:5432/aurum_test). The PostgreSQL run DROPS the test schema."""
from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import text

from aurum.database.connection import check_database, init_database, make_engine
from aurum.database.repository import Repository, fingerprint
from aurum.services.validation_service import QualityIssue
from conftest import TUESDAY, candle_frame

PG_URL = os.environ.get("AURUM_TEST_DATABASE_URL")
BACKENDS = ["sqlite"] + (["postgresql"] if PG_URL else [])
ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(params=BACKENDS)
def repo(request):
    if request.param == "sqlite":
        engine = make_engine("sqlite:///:memory:")
    else:
        engine = make_engine(PG_URL)
        with engine.begin() as c:
            c.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    init_database(engine)
    r = Repository(engine)
    yield r
    engine.dispose()


def _inst(repo):
    return repo.upsert_instrument("EURUSD", "EURUSD", "forex", "Euro")


def test_schema_ready(repo):
    assert check_database(repo.engine)["schema_ready"]
    init_database(repo.engine)          # idempotent: second run must not fail


def test_upsert_candles_is_idempotent(repo):
    iid = _inst(repo)
    df = candle_frame(TUESDAY, 50)
    assert repo.upsert_candles(iid, df, "run-1")[0] == 50
    repo.upsert_candles(iid, df, "run-2")
    out = repo.load_candles("EURUSD", "H1")
    assert len(out) == 50
    assert str(out["ts_utc"].dt.tz) == "UTC"
    assert out["ts_utc"].iloc[0] == df["ts_utc"].iloc[0]
    assert repo.latest_candle_ts(iid, "H1") == (TUESDAY + timedelta(hours=49))


def test_forming_candle_updated_in_place(repo):
    iid = _inst(repo)
    df = candle_frame(TUESDAY, 3)
    df.loc[2, "is_complete"] = False
    repo.upsert_candles(iid, df, "run-1")
    assert len(repo.load_candles("EURUSD", "H1")) == 2                         # incomplete excluded by default
    df.loc[2, ["close", "is_complete"]] = [1.2, True]
    _, revisions = repo.upsert_candles(iid, df, "run-2")
    assert revisions == []                                                      # was incomplete: not a revision
    out = repo.load_candles("EURUSD", "H1")
    assert len(out) == 3 and out["close"].iloc[-1] == 1.2


def test_revised_complete_candle_is_reported(repo):
    iid = _inst(repo)
    df = candle_frame(TUESDAY, 5)
    repo.upsert_candles(iid, df, "run-1")
    df.loc[1, "close"] += 0.0003
    _, revisions = repo.upsert_candles(iid, df, "run-2")
    assert len(revisions) == 1 and set(revisions[0]["changes"]) == {"close"}
    assert revisions[0]["old_run_id"] == "run-1"


def test_invalid_candles_are_stored_not_dropped(repo):
    iid = _inst(repo)
    df = candle_frame(TUESDAY, 5)
    df["quality_status"] = ["valid", "invalid", "valid", "suspect", "valid"]
    repo.upsert_candles(iid, df, "run-1")
    assert len(repo.load_candles("EURUSD", "H1")) == 4
    assert len(repo.load_candles("EURUSD", "H1", include_invalid=True)) == 5


def test_quality_issue_dedup(repo):
    iid = _inst(repo)
    issue = QualityIssue("missing_candles", "warning", TUESDAY, {"unexpected_candles": 3})
    repo.record_issues([issue], iid, "EURUSD", "H1", "run-1")
    repo.record_issues([issue], iid, "EURUSD", "H1", "run-2")
    assert repo.issue_count() == 1
    with repo.engine.connect() as c:
        occ, last = c.execute(text("SELECT occurrences, last_run_id FROM data_quality_issues")).one()
    assert occ == 2 and last == "run-2"


def test_spec_and_tick_upserts(repo):
    iid = _inst(repo)
    spec = {"broker_symbol": "EURUSD", "digits": 5, "point": 1e-5, "contract_size": 100000.0, "raw": {"a": 1}}
    repo.insert_spec(iid, spec, TUESDAY, "run-1")
    repo.insert_spec(iid, spec, TUESDAY, "run-1")
    assert len(repo.latest_specs()) == 1 and repo.latest_specs()[0]["digits"] == 5
    tick = {"broker_symbol": "EURUSD", "ts_utc": TUESDAY, "mt5_time_msc": 1, "bid": 1.1, "ask": 1.1002}
    repo.upsert_tick(iid, tick, "run-1")
    repo.upsert_tick(iid, tick, "run-1")
    with repo.engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM market_ticks")).scalar() == 1


def test_naive_datetime_rejected(repo):
    iid = _inst(repo)
    df = candle_frame(TUESDAY, 2)
    df["ts_utc"] = df["ts_utc"].dt.tz_localize(None)
    with pytest.raises(Exception, match="Naive datetime"):
        repo.upsert_candles(iid, df, "run-1")


def test_fingerprint_stable():
    a = fingerprint("EURUSD", "H1", "missing_candles", TUESDAY)
    assert a == fingerprint("EURUSD", "H1", "missing_candles", TUESDAY)
    assert a != fingerprint("EURUSD", "M5", "missing_candles", TUESDAY)


def test_schema_sql_defines_required_structures():
    sql = (ROOT / "sql" / "schema.sql").read_text()
    for t in ("instruments", "instrument_specs", "market_candles", "market_ticks", "data_collection_logs",
              "data_quality_issues"):
        assert f"CREATE TABLE IF NOT EXISTS {t}" in sql
    assert "PRIMARY KEY (instrument_id, timeframe, ts_utc)" in sql
    assert "create_hypertable('market_candles'" in sql
    assert "TIMESTAMPTZ" in sql and "REFERENCES instruments(id)" in sql
