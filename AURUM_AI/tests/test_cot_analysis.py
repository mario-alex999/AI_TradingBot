"""Look-ahead protection, price-position divergence, intelligence reports and exports."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pandas as pd
import pytest

from aurum.config import Settings
from aurum.cot import divergence as dv
from aurum.cot.config import DivergenceConfig
from aurum.cot.exports import KINDS, export
from aurum.cot.ingestion import CotService
from aurum.cot.intelligence import instrument_report
from aurum.database.connection import engine_from_settings, init_database
from cot_helpers import FakeClient, cot_cfg, series, tuesdays

START = date(2023, 1, 3)
WEEKS = 190


def nets(n=WEEKS):
    return [20_000 + (i % 7) * 900 + i * 50 for i in range(n)]


@pytest.fixture
def seeded(tmp_path):
    s = Settings(_env_file=None, database_url=f"sqlite:///{(tmp_path / 'a.db').as_posix()}", data_dir=tmp_path / "data",
                 log_dir=tmp_path / "logs", mt5_server_timezone="Etc/UTC")
    engine = engine_from_settings(s)
    init_database(engine)
    n = nets()
    data = {"legacy": series("legacy", "099741", START, n), "tff": series("tff", "099741", START, n)}
    svc = CotService(s, cot_cfg(), engine, client=FakeClient(data), today=lambda: date(2026, 11, 2))
    svc.backfill(start=START, end=date(2026, 11, 2), report_types=["legacy", "tff"])
    return svc


def candles(days: list[date], closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"ts_utc": pd.to_datetime(days).tz_localize("UTC"), "close": closes})


# ------------------------------------------------------------------ look-ahead
def test_report_not_visible_before_release(seeded):
    repo = seeded.repo
    mid = seeded._map_ids[("EURUSD", "tff")]
    last = tuesdays(START, WEEKS)[-1]
    rel = datetime.combine(last + timedelta(days=3), datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=19, minutes=30)
    before = repo.report_frame(mapping_id=mid, as_of=rel - timedelta(minutes=1))
    after = repo.report_frame(mapping_id=mid, as_of=rel)
    assert before["report_date"].max() == last - timedelta(weeks=1)
    assert after["report_date"].max() == last
    # On the Tuesday itself (and Wednesday/Thursday) the Tuesday report is not yet usable
    tue = datetime.combine(last, datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=23)
    assert repo.report_frame(mapping_id=mid, as_of=tue)["report_date"].max() < last
    assert repo.metrics_frame(mapping_id=mid, as_of=tue)["report_date"].max() < last


def test_historical_metrics_do_not_change_when_future_reports_arrive(seeded, tmp_path):
    """Metrics stored for an old report are identical whether or not later reports exist (no future data used)."""
    mid = seeded._map_ids[("EURUSD", "tff")]
    full = seeded.repo.metrics_frame(mapping_id=mid, category="leveraged_funds").set_index("report_date")
    s = Settings(_env_file=None, database_url=f"sqlite:///{(tmp_path / 'b.db').as_posix()}", data_dir=tmp_path / "d2",
                 log_dir=tmp_path / "l2", mt5_server_timezone="Etc/UTC")
    e2 = engine_from_settings(s)
    init_database(e2)
    cut = 150                                       # > ceil(0.8*156) so the 36-month stats have values
    svc2 = CotService(s, cot_cfg(), e2, client=FakeClient({"tff": series("tff", "099741", START, nets()[:cut])}),
                      today=lambda: date(2026, 11, 2))
    svc2.backfill(start=START, end=date(2026, 11, 2), report_types=["tff"])
    part = svc2.repo.metrics_frame(mapping_id=svc2._map_ids[("EURUSD", "tff")], category="leveraged_funds").set_index("report_date")
    cols = ["historical_percentile_12m", "historical_percentile_36m", "cot_index", "z_score", "positioning_strength"]
    assert part["historical_percentile_36m"].notna().sum() > 20
    pd.testing.assert_frame_equal(part[cols], full.loc[part.index, cols], check_dtype=False)


def test_intelligence_as_of_uses_only_public_data(seeded):
    days = tuesdays(START, WEEKS)
    as_of = datetime.combine(days[100] + timedelta(days=2), datetime.min.time(), tzinfo=timezone.utc)  # Thursday
    rep = instrument_report(seeded.repo, seeded.cfg, "EURUSD", seeded._map_ids, None, as_of=as_of)
    assert all(f["report_date"] == days[99] for f in rep["families"])


# ------------------------------------------------------------------ divergence
CFG = DivergenceConfig(window_reports=4, extreme_lookback_reports=10, min_price_change_pct=0.5)


def metric_frame(dates, net):
    rel = [pd.Timestamp(d, tz="UTC") + pd.Timedelta(days=3, hours=19, minutes=30) for d in dates]
    return pd.DataFrame({"report_date": dates, "net_position": net, "release_timestamp": rel})


def test_bearish_divergence_price_up_positioning_down():
    d = tuesdays(date(2026, 1, 6), 12)
    res = dv.analyse(metric_frame(d, [5000 - i * 200 for i in range(12)]),
                     candles(d, [1.10 + i * 0.01 for i in range(12)]), CFG)
    assert res.available and "bearish_divergence" in res.latest["signals"]
    assert res.latest["window_start_report_date"] == d[-5] and res.latest["price_date"] == d[-1]


def test_bullish_divergence_and_unconfirmed_low():
    d = tuesdays(date(2026, 1, 6), 14)
    res = dv.analyse(metric_frame(d, [-9000 + i * 300 for i in range(14)]),
                     candles(d, [2.0 - i * 0.02 for i in range(14)]), CFG)
    assert {"bullish_divergence", "unconfirmed_low"} <= set(res.latest["signals"])


def test_unconfirmed_high():
    d = tuesdays(date(2026, 1, 6), 14)
    net = [1000 * i for i in range(10)] + [8000, 7500, 7000, 8500]     # price new high, net below its 9000 high
    res = dv.analyse(metric_frame(d, net), candles(d, [100 + i for i in range(14)]), CFG)
    assert "unconfirmed_high" in res.latest["signals"]


def test_divergence_never_uses_future_candles():
    d = tuesdays(date(2026, 1, 6), 8)
    # candles on the Monday before (value i) and the Wednesday after (value 100+i) each report Tuesday
    mon, wed = [x - timedelta(days=1) for x in d], [x + timedelta(days=1) for x in d]
    c = candles(mon + wed, [float(i) for i in range(8)] + [100.0 + i for i in range(8)])
    aligned = dv.align_prices(d, c)
    assert all(p == (float(i), mon[i]) for i, p in enumerate(aligned))  # never the later Wednesday candle
    # only later candles available -> no price (stale earlier candle beyond the 4-day limit is not used either)
    assert dv.align_prices(d, candles(wed, [1.0] * 8))[0] == (None, None)
    # daily candles; at 12:00 UTC on report day d[3] its D1 candle is still forming -> previous day's close used
    days = [d[0] + timedelta(days=i) for i in range(60)]
    daily = candles(days, [float(i) for i in range(60)])
    as_of = (pd.Timestamp(d[3], tz="UTC") + pd.Timedelta(hours=12)).to_pydatetime()
    price, pdate = dv.align_prices(d, daily, as_of=as_of)[3]
    assert pdate == d[3] - timedelta(days=1) and price == float((d[3] - d[0]).days - 1)
    assert dv.align_prices(d, daily)[3] == (float((d[3] - d[0]).days), d[3])   # once closed, the Tuesday close


def test_divergence_reports_insufficient_overlap():
    d = tuesdays(date(2026, 1, 6), 10)
    res = dv.analyse(metric_frame(d, list(range(10))), candles(d[-3:], [1.0, 1.1, 1.2]), CFG)
    assert not res.available and "overlap" in res.reason
    assert not dv.analyse(metric_frame(d, list(range(10))), pd.DataFrame(), CFG).available


# ------------------------------------------------------------------ intelligence + exports
def test_instrument_report_structure_and_rules(seeded):
    days = tuesdays(START, WEEKS)
    prices = candles(days, [1.0 + 0.001 * i for i in range(WEEKS)])
    rep = instrument_report(seeded.repo, seeded.cfg, "EURUSD", seeded._map_ids, lambda s, tf: prices)
    assert rep["instrument"] == "EURUSD" and len(rep["families"]) == 2 and rep["disclaimer"]
    tff = next(f for f in rep["families"] if f["report_type"] == "tff")
    assert tff["contract"]["code"] == "099741" and tff["report_date"] == days[-1]
    assert tff["release_date"] == days[-1] + timedelta(days=3)
    assert {"facts", "metrics", "interpretation"} == set(tff["statements"])
    assert any("Non-reportable traders" in s and "not an exact proxy for retail" in s for s in tff["statements"]["interpretation"])
    lf = next(c for c in tff["categories"] if c["category"] == "leveraged_funds")
    assert lf["net_position"] == nets()[-1] and lf["positioning_strength"] is not None
    assert tff["divergence"]["available"] and tff["confidence"]["level"] in ("high", "medium", "low")
    assert tff["freshness"]["status"] in ("current", "update_due", "stale")
    assert rep["overall"]["stance"] in ("bullish", "bearish", "neutral", "mixed")
    text = " ".join(" ".join(f["statements"]["interpretation"]) for f in rep["families"]).lower()
    for banned in ("guarantee", "will rise", "will fall", "profit"):
        assert banned not in text
    with pytest.raises(KeyError):
        instrument_report(seeded.repo, seeded.cfg, "BTCUSD", seeded._map_ids)


def test_missing_data_is_reported_not_fabricated(seeded):
    rep = instrument_report(seeded.repo, seeded.cfg, "XAUUSD", seeded._map_ids)
    assert rep["overall"]["stance"] == "unavailable"
    assert all(not f["available"] for f in rep["families"])


def test_exports_write_csv_with_data_status(seeded, tmp_path):
    out = tmp_path / "exp"
    builder = lambda: [instrument_report(seeded.repo, seeded.cfg, "EURUSD", seeded._map_ids)]  # noqa: E731
    for kind in KINDS:
        path, n = export(kind, seeded.repo, seeded.cfg, out, None, analysis_builder=builder)
        df = pd.read_csv(path)
        assert n > 0 and "data_status" in df.columns
    raw = pd.read_csv(out / "cot_raw.csv", dtype=str)
    assert set(raw["aurum_symbol"]) == {"EURUSD"} and raw["source_payload_json"].str.contains("099741").all()
    metrics = pd.read_csv(out / "cot_metrics.csv")
    assert {"real", "incomplete"} <= set(metrics["data_status"])        # early weeks lack history
    path2, _ = export("raw", seeded.repo, seeded.cfg, out, None)
    assert path2.name != "cot_raw.csv"                                   # never silently overwritten
    empty, n = export("raw", seeded.repo, seeded.cfg, out, "XAUUSD")
    assert pd.read_csv(empty)["data_status"].tolist() == ["unavailable"]
