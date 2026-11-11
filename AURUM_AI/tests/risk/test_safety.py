"""Safety tests required by the Module 06 specification, plus database integrity checks."""
from __future__ import annotations

import ast
import os
import re
from datetime import timedelta
from pathlib import Path

import pytest
from risk_helpers import EURUSD_BUY, NOW, Harness, ids
from sqlalchemy import create_engine, inspect, select, text

from aurum.risk.db_models import RiskAssessment, RiskAuditLog
from aurum.risk.money import D
from aurum.risk.rules import NOT_DISABLEABLE, RULES

ROOT = Path(__file__).resolve().parents[2]
RISK_SRC = ROOT / "aurum" / "risk"
EXECUTION_CALLS = ("order_send", "order_check", "order_calc_margin", "positions_get", "Buy(", "Sell(", "PositionClose",
                   "OrderSend", "TRADE_ACTION")


def test_no_order_placement_anywhere_in_module_06():
    for path in list(RISK_SRC.rglob("*.py")) + [ROOT / "aurum" / "api" / "routes" / "risk.py"]:
        src = path.read_text(encoding="utf-8")
        for call in EXECUTION_CALLS:
            assert call not in src, f"{call} found in {path}"
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in node.names] + [getattr(node, "module", "") or ""]
                assert not any("MetaTrader5" in n or "mt5_gateway" in n for n in names), f"MT5 import in {path}"


def test_no_trading_routes_in_risk_api():
    src = (ROOT / "aurum" / "api" / "routes" / "risk.py").read_text(encoding="utf-8")
    routes = re.findall(r'@r\.(?:get|post|put|delete)\("([^"]+)"', src)
    assert routes and not any(w in p for p in routes for w in ("order", "execute", "trade/", "close"))


def test_no_pass_with_missing_stop(h):
    for extra in ({}, {"ai_confidence": 1.0}, {"risk_pct": "0.1"}, {"requested_volume": "0.01"}):
        body = {k: v for k, v in EURUSD_BUY.items() if k != "stop_loss"}
        assert h.assess(body, key=str(extra), **extra)["decision"] == "BLOCK"


def test_module_05_confidence_cannot_override_hard_block(h):
    h.data.ideas["c-9"] = {"trade_idea_id": "c-9", "symbol": "EURUSD", "direction": "BUY", "entry_low": 1.1000, "entry_high": 1.1002,
                           "stop_loss": 1.1030, "take_profit": 1.1051, "strategy": "x", "timeframe": "H1", "confidence": 1.0}
    r = h.assess({"trade_idea_id": "c-9"})
    assert r["decision"] == "BLOCK" and "R011" in ids(r, "rule_violations")
    ai = next(x for x in r["information"] if x["rule_id"] == "R071")
    assert ai["evidence"]["ai_confidence"] == 1.0 and r["module_07"]["hard_block"] is True


def test_stale_account_never_passes(h):
    h.data.snapshot_at = NOW - timedelta(hours=2)
    r = h.assess()
    assert r["decision"] == "ERROR" and r["final_volume"] is None


def test_error_is_never_turned_into_pass_by_disabling_rules(h):
    h.data.has_snapshot = False
    v = h.svc.config()["version"]
    h.svc.update_config({"base_version": v, "reason": "try to disable", "acknowledge_weakening": True,
                         "changes": {"rules": {"R030": {"enabled": False}, "R034": {"enabled": False}}}}, "x", None)
    assert h.assess()["decision"] == "ERROR"


def test_safety_rules_cannot_be_disabled():
    assert {"R001", "R002", "R003", "R010", "R011", "R018", "R019", "R041", "R060"} <= NOT_DISABLEABLE
    assert {r.rule_id for r in RULES} >= NOT_DISABLEABLE


def test_rule_override_can_only_tighten(h):
    h.config(rules={"R015": {"severity": "HARD_BLOCK"}})
    r = h.assess(stop_loss="1.09990", take_profit="1.10100")   # 2-pip stop vs ~16-pip ATR: tight
    assert "R015" in ids(r, "rule_violations")
    h.config(rules={"R016": {"severity": "WARNING"}}, reason="try to soften")
    r = h.assess(take_profit="1.10100", key="soft")           # R:R < 1.5 stays a hard block
    assert "R016" in ids(r, "rule_violations")


def test_expired_assessment_frees_reservation(h):
    for i in range(2):     # two live ~1% reservations fill the 2% per-symbol budget
        h.assess({**EURUSD_BUY, "strategy": f"s{i}"}, key=f"r{i}")
    r = h.assess({**EURUSD_BUY, "strategy": "s2"}, key="third")
    assert r["decision"] == "BLOCK" and "R042" in ids(r, "rule_violations")
    h.clock.advance(minutes=16)                                 # past the 15-minute assessment TTL
    h.data.now = h.clock.t
    q = h.data.quotes["EURUSD"]
    h.data.set_quote("EURUSD", float(q.bid), float(q.ask - q.bid))  # fresh quote, same prices
    r = h.assess({**EURUSD_BUY, "strategy": "s2"}, key="after-expiry")
    assert r["decision"] == "PASS" and r["warnings"] == []


def test_position_size_never_rounded_up(h):
    r = h.svc.position_size({**EURUSD_BUY, "equity": "12345.67", "risk_pct": "0.73"})
    c = r["calculation"]
    assert c["estimatedLoss" if "estimatedLoss" in c else "estimated_loss"] <= c["max_risk_amount"]


# ---- database integrity --------------------------------------------------------------------------------------------------
def test_financial_values_round_trip_as_decimal(h):
    r = h.assess()
    with h.engine.connect() as c:
        row = c.execute(select(RiskAssessment.monetary_risk, RiskAssessment.recommended_volume)
                        .where(RiskAssessment.assessment_id == r["assessment_id"])).one()
    assert row.recommended_volume == D("0.46")
    assert str(row.monetary_risk).startswith("99.82")


def test_audit_chain_detects_tampering(h):
    h.assess()
    h.svc.create_lock({"scope": "global", "reason": "chain test"}, "ops", None)
    ok, n, _ = h.svc.repo.verify_audit()
    assert ok and n >= 3
    with h.engine.begin() as c:
        c.execute(text("UPDATE risk_audit_log SET actor='someone-else' WHERE id = 2"))
    ok, _, problem = h.svc.repo.verify_audit()
    assert not ok and "row 2" in problem


def test_assessment_and_rule_rows_written_atomically(h):
    r = h.assess()
    with h.engine.connect() as c:
        n_rules = c.execute(text("select count(*) from risk_rule_evaluations where assessment_id=:a"), {"a": r["assessment_id"]}).scalar()
        n_events = c.execute(text("select count(*) from risk_events where entity_id=:a"), {"a": r["assessment_id"]}).scalar()
        n_audit = c.execute(select(RiskAuditLog).where(RiskAuditLog.entity_id == r["assessment_id"])).all()
    assert n_rules == len(RULES) and n_events == 1 and len(n_audit) == 1


def test_config_history_is_append_only(h):
    h.config(risk_per_trade_pct=0.5)
    h.config(risk_per_trade_pct=0.75, reason="back up a little")
    versions = h.svc.repo.config_versions()
    assert [v["version"] for v in versions] == [3, 2, 1] and versions[0]["weakened"] == ["risk_per_trade_pct: 0.5 -> 0.75"]


def test_sqlite_schema_has_every_module_06_table(h):
    tables = set(inspect(h.engine).get_table_names())
    expected = {"risk_config_versions", "risk_assessments", "risk_rule_evaluations", "risk_account_snapshots",
                "risk_portfolio_snapshots", "risk_correlation_snapshots", "risk_period_records", "risk_locks", "risk_lock_history",
                "risk_loss_streaks", "risk_audit_log", "risk_events", "risk_inbox"}
    assert expected <= tables


def test_migration_matches_models():
    sql = (ROOT / "sql" / "migrations" / "0008_risk_engine.sql").read_text(encoding="utf-8")
    from aurum.database.models import Base
    import aurum.risk.db_models  # noqa: F401
    for name, table in Base.metadata.tables.items():
        if not name.startswith("risk_"):
            continue
        assert f"CREATE TABLE IF NOT EXISTS {name} (" in sql, name
        block = sql.split(f"CREATE TABLE IF NOT EXISTS {name} (")[1].split(");")[0]
        for col in table.columns:
            assert re.search(rf"\b{col.name}\b", block), f"{name}.{col.name} missing from migration"


@pytest.mark.skipif(not os.environ.get("AURUM_TEST_DATABASE_URL"), reason="set AURUM_TEST_DATABASE_URL to run")
def test_migration_is_idempotent_and_append_only_on_postgres():
    engine = create_engine(os.environ["AURUM_TEST_DATABASE_URL"])
    sql = (ROOT / "sql" / "migrations" / "0008_risk_engine.sql").read_text(encoding="utf-8")
    with engine.begin() as c:
        # Raw driver call (as init_database does): psycopg must not parse the '%' in RAISE messages as placeholders.
        c.connection.driver_connection.execute(sql)
        c.connection.driver_connection.execute(sql)
        assert c.execute(text("select to_regclass('risk_assessments')")).scalar() is not None
        c.exec_driver_sql("INSERT INTO risk_audit_log (actor, action, entity_type, prev_hash, hash) VALUES ('t','t','t','0','0')")
    with pytest.raises(Exception, match="append-only"):
        with engine.begin() as c:
            c.exec_driver_sql("UPDATE risk_audit_log SET actor='x'")
