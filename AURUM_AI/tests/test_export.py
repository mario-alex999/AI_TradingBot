from __future__ import annotations

import pandas as pd
import pytest

from aurum.database.connection import init_database, make_engine
from aurum.database.repository import Repository
from aurum.exporters.market_data_exporter import MarketDataExporter
from module01_helpers import TUESDAY, candle_frame


@pytest.fixture
def exporter(tmp_path):
    engine = make_engine("sqlite:///:memory:")
    init_database(engine)
    repo = Repository(engine)
    iid = repo.upsert_instrument("EURUSD", "EURUSD", "forex")
    repo.upsert_candles(iid, candle_frame(TUESDAY, 24), "run-1")
    repo.upsert_candles(iid, candle_frame(TUESDAY, 10, 5, "M5"), "run-1")
    return MarketDataExporter(repo, tmp_path / "exports")


def test_export_layout_and_utc(exporter, tmp_path):
    res = exporter.export()
    paths = {r.path.relative_to(tmp_path / "exports").as_posix() for r in res}
    assert paths == {"EURUSD/H1.csv", "EURUSD/H1.parquet", "EURUSD/M5.csv", "EURUSD/M5.parquet"}
    csv = pd.read_csv(tmp_path / "exports/EURUSD/H1.csv")
    assert len(csv) == 24 and csv["ts_utc"].iloc[0] == "2026-03-03T00:00:00+00:00"
    pq = pd.read_parquet(tmp_path / "exports/EURUSD/H1.parquet")
    assert str(pq["ts_utc"].dt.tz) == "UTC" and len(pq) == 24


def test_export_does_not_overwrite_by_default(exporter, tmp_path):
    exporter.export(formats=("csv",))
    f = tmp_path / "exports/EURUSD/H1.csv"
    f.write_text("sentinel")
    res = exporter.export(formats=("csv",), timeframes=["H1"])
    assert res[0].action == "skipped_exists" and f.read_text() == "sentinel"
    res = exporter.export(formats=("csv",), timeframes=["H1"], on_exists="version")
    assert res[0].action == "versioned" and res[0].path.name.startswith("H1_") and f.read_text() == "sentinel"
    res = exporter.export(formats=("csv",), timeframes=["H1"], on_exists="overwrite")
    assert res[0].action == "overwritten" and f.read_text() != "sentinel"


def test_export_filters_and_bad_format(exporter):
    assert {r.timeframe for r in exporter.export(timeframes=["M5"], formats=("csv",))} == {"M5"}
    with pytest.raises(ValueError):
        exporter.export(formats=("xlsx",))
