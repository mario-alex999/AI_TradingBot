"""Database + ingestion: insertion, upsert, duplicates, revisions, rollback, FKs, resume, Module 01 coexistence."""
from __future__ import annotations

from datetime import date, datetime, timezone

import pandas as pd
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from aurum.config import Settings
from aurum.cot.ingestion import CotService
from aurum.cot.models import CotMetric, CotPosition, CotReport, CotReportRevision
from aurum.database.connection import engine_from_settings, init_database
from cot_helpers import FakeClient, cot_cfg, make_record, series

TODAY = date(2026, 11, 2)


@pytest.fixture
def env(tmp_path):
    s = Settings(_env_file=None, database_url=f"sqlite:///{(tmp_path / 't.db').as_posix()}", data_dir=tmp_path / "data",
                 log_dir=tmp_path / "logs", mt5_server_timezone="Etc/UTC")
    engine = engine_from_settings(s)
    init_database(engine)
    return s, engine


def data(weeks: int = 60, start: date = date(2025, 9, 2)):
    nets = [1000 + i * 100 for i in range(weeks)]
    return {"legacy": series("legacy", "099741", start, nets) + series("legacy", "088691", start, nets),
            "tff": series("tff", "099741", start, nets),
            "disaggregated": series("disaggregated", "088691", start, nets)}


def svc(env, client, today=TODAY):
    s, engine = env
    return CotService(s, cot_cfg(), engine, client=client, today=lambda: today)


def count(engine, model):
    with engine.connect() as c:
        return c.execute(select(func.count()).select_from(model)).scalar_one()


def test_backfill_inserts_reports_positions_and_metrics(env):
    client = FakeClient(data())
    job = svc(env, client).backfill(years=4)
    engine = env[1]
    assert job.status == "success" and job.totals()["inserted"] == 240 and job.totals()["rejected"] == 0
    assert count(engine, CotReport) == 240
    assert count(engine, CotPosition) == 60 * (4 + 4 + 6 + 6)
    assert job.metrics_written == count(engine, CotMetric) == count(engine, CotPosition)
    with engine.connect() as c:
        r = c.execute(select(CotReport).where(CotReport.report_type == "tff").order_by(CotReport.report_date)).first()
    assert r.report_date == date(2025, 9, 2) and r.release_date == date(2025, 9, 5)       # separate dates stored
    assert r.release_timestamp == datetime(2025, 9, 5, 19, 30, tzinfo=timezone.utc)
    assert r.source_payload["CFTC_Contract_Market_Code"] == "099741"                       # verbatim payload
    assert r.contract_mapping_id is not None


def test_rerun_is_idempotent_and_no_duplicates(env):
    service = svc(env, FakeClient(data()))
    service.backfill(years=4)
    job = service.backfill(years=4, resume=False)
    assert job.totals()["inserted"] == 0 and job.totals()["unchanged"] == 240
    assert count(env[1], CotReport) == 240


def test_unique_constraint_enforced_at_database_level(env):
    service = svc(env, FakeClient(data(weeks=2)))
    service.backfill(start=date(2025, 9, 1))
    with env[1].connect() as c:
        row = dict(c.execute(select(CotReport)).first()._mapping)
    row.pop("id")
    with pytest.raises(IntegrityError), env[1].begin() as c:
        c.execute(CotReport.__table__.insert().values(**row))


def test_cftc_revision_updates_values_and_preserves_previous_payload(env):
    d = data(weeks=3)
    service = svc(env, FakeClient(d))
    service.backfill(start=date(2025, 9, 1))
    revised = make_record("tff", "099741", date(2025, 9, 9), 555)
    d["tff"] = [revised if r["Report_Date_as_YYYY-MM-DD"] == "2025-09-09" else r for r in d["tff"]]
    job = svc(env, FakeClient(d)).update()
    assert job.totals()["updated"] == 1
    engine = env[1]
    with engine.connect() as c:
        rev = c.execute(select(CotReportRevision)).one()
        cur = c.execute(select(CotReport).where(CotReport.id == rev.cot_report_id)).one()
        lf = c.execute(select(CotPosition.net_positions).where(CotPosition.cot_report_id == cur.id,
                                                               CotPosition.trader_category == "leveraged_funds")).scalar()
    assert lf == 555 and rev.previous_hash != rev.new_hash
    assert rev.previous_payload["Lev_Money_Positions_Long_All"] != cur.source_payload["Lev_Money_Positions_Long_All"]


def test_chunk_failure_rolls_back_and_is_logged(env, monkeypatch):
    service = svc(env, FakeClient(data(weeks=3)))
    real = service.repo.upsert_report
    calls = {"n": 0}

    def boom(*a, **k):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("disk full")
        return real(*a, **k)
    monkeypatch.setattr(service.repo, "upsert_report", boom)
    job = service.backfill(start=date(2025, 9, 1), report_types=["tff"])
    failed = [c for c in job.chunks if c.status == "failed"]
    assert job.status == "partial" and len(failed) == 1 and failed[0].period_start.year == 2025
    assert count(env[1], CotReport) == 0                          # first insert of the chunk rolled back too
    logs, _ = service.repo.logs(job_type="backfill")
    assert any(lg["status"] == "failed" and "disk full" in (lg["error_message"] or "") for lg in logs)
    issues, _ = service.repo.issues(severity="error")
    assert any(i["issue_type"] == "ingestion_failed" for i in issues)


def test_source_outage_isolated_per_family(env):
    job = svc(env, FakeClient(data(), fail={"tff"})).backfill(years=4)
    assert job.status == "partial"
    assert {c.report_type for c in job.chunks if c.status == "failed"} == {"tff"}
    assert count(env[1], CotReport) == 180


def test_foreign_keys_and_cascade(env):
    svc(env, FakeClient(data(weeks=2))).backfill(start=date(2025, 9, 1))
    engine = env[1]
    with engine.begin() as c:
        with pytest.raises(IntegrityError):
            with c.begin_nested():
                c.execute(CotPosition.__table__.insert().values(cot_report_id=999999, trader_category="x",
                                                                is_reportable=True))
        rid = c.execute(select(CotReport.id)).first()[0]
        c.execute(CotReport.__table__.delete().where(CotReport.id == rid))
        assert c.execute(select(func.count()).select_from(CotPosition).where(CotPosition.cot_report_id == rid)).scalar() == 0


def test_backfill_resumes_after_interruption(env):
    d = data(weeks=80, start=date(2024, 6, 4))
    first = svc(env, FakeClient(d, fail={"tff"}))
    first.backfill(start=date(2024, 6, 1), end=TODAY)
    client = FakeClient(d)
    job = svc(env, client).backfill(start=date(2024, 6, 1), end=TODAY)
    fetched = {(k, s.year) for k, s, e in client.calls}
    assert ("legacy", 2024) not in fetched and ("legacy", 2025) not in fetched     # completed years skipped
    assert ("tff", 2024) in fetched and ("tff", 2025) in fetched                   # failed family retried
    assert ("legacy", 2026) in fetched                                             # open year refreshed
    assert any(c.skipped for c in job.chunks)
    assert count(env[1], CotReport) == 80 * 4


def test_update_without_data_runs_backfill_and_twice_is_idempotent(env):
    service = svc(env, FakeClient(data()))
    j1 = service.update()
    assert j1.totals()["inserted"] == 240
    j2 = service.update()
    assert j2.totals()["inserted"] == 0 and j2.totals()["updated"] == 0 and count(env[1], CotReport) == 240


def test_module01_tables_untouched(env):
    from aurum.database.repository import Repository
    engine = env[1]
    repo = Repository(engine)
    iid = repo.upsert_instrument("EURUSD", "EURUSDm", "forex")
    candles = pd.DataFrame([{"ts_utc": pd.Timestamp("2026-01-06", tz="UTC"), "open": 1.1, "high": 1.2, "low": 1.0,
                             "close": 1.15, "tick_volume": 1, "real_volume": 0, "spread": 1, "is_complete": True,
                             "quality_status": "valid", "quality_flags": None, "broker_symbol": "EURUSDm",
                             "mt5_time": 1767657600, "timeframe": "D1"}])
    repo.upsert_candles(iid, candles, "run-1")
    before = repo.load_candles("EURUSD", "D1")
    svc(env, FakeClient(data())).backfill(years=4)
    after = repo.load_candles("EURUSD", "D1")
    assert len(before) == len(after) == 1 and after.iloc[0]["close"] == 1.15
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM market_candles")).scalar() == 1


def test_sync_config_deactivates_removed_mappings(env):
    import dataclasses
    s, engine = env
    cfg = cot_cfg()
    service = CotService(s, cfg, engine, client=FakeClient({}))
    service.sync()
    smaller = dataclasses.replace(cfg, mappings=tuple(m for m in cfg.mappings if m.aurum_symbol != "US30"))
    CotService(s, smaller, engine, client=FakeClient({})).sync()
    active = {(m["aurum_symbol"], m["report_type"]) for m in service.repo.mappings()}
    allm = {(m["aurum_symbol"], m["report_type"]) for m in service.repo.mappings(active_only=False)}
    assert ("US30", "tff") not in active and ("US30", "tff") in allm         # deactivated, never deleted
