"""The 30 risk scenarios from the Module 06 specification, run through the real engine, rules, locks and repository.
Inputs are MOCKED (MemoryRiskData); the database is a temporary SQLite file."""
from __future__ import annotations

import threading
from datetime import timedelta
from decimal import Decimal

import pytest
from risk_helpers import EURUSD_BUY, EURUSD_SELL, NOW, Harness, ids

from aurum.risk.account import ClosedTrade, EquityPoint
from aurum.risk.calculator import Quote
from aurum.risk.money import D
from aurum.risk.portfolio import Position
from aurum.risk.service import RiskError
from aurum.risk.simulation import calm_intelligence

D0 = Decimal


def position(symbol="EURUSD", side="BUY", volume="0.5", open_="1.10000", sl="1.09800", pid="p1", cls="forex"):
    return Position(pid, symbol, side, D(volume), D(open_), D(sl) if sl else None, None, NOW - timedelta(hours=1), None, cls)


# 1-2
def test_01_valid_buy_passes(h):
    r = h.assess()
    assert r["decision"] == "PASS", r["failed_checks"]
    assert r["recommended_volume"] == 0.46 and r["final_volume"] == 0.46
    assert r["monetary_risk"] <= 100 and r["risk_reward"] >= 1.5
    assert r["module_07"]["fully_risk_approved"] is True


def test_02_valid_sell_passes(h):
    r = h.assess(EURUSD_SELL)
    assert r["decision"] == "PASS", r["failed_checks"]


# 3-6
def test_03_buy_stop_above_entry_blocks(h):
    r = h.assess(stop_loss="1.10210")
    assert r["decision"] == "BLOCK" and "R011" in ids(r, "rule_violations")
    assert r["final_volume"] is None


def test_04_sell_stop_below_entry_blocks(h):
    r = h.assess(EURUSD_SELL, stop_loss="1.09810")
    assert r["decision"] == "BLOCK" and "R011" in ids(r, "rule_violations")


def test_05_missing_stop_blocks_and_is_never_added(h):
    body = {k: v for k, v in EURUSD_BUY.items() if k != "stop_loss"}
    r = h.assess(body)
    assert r["decision"] == "BLOCK" and "R010" in ids(r, "rule_violations")
    assert r["proposal"]["stop_loss"] is None and r["calculation"] is None


def test_06_invalid_take_profit_blocks(h):
    r = h.assess(take_profit="1.09900")
    assert r["decision"] == "BLOCK" and "R012" in ids(r, "rule_violations")


# 7-10
def test_07_excessive_risk_percentage_blocks(h):
    r = h.assess(risk_pct="5")
    assert r["decision"] == "BLOCK" and "R017" in ids(r, "rule_violations")


def test_08_size_below_minimum_lot_blocks(tmp_path):
    h = Harness(tmp_path, balance=D(1000), equity=D(1000))
    r = h.assess({"symbol": "US30", "direction": "BUY", "entry": "44000.0", "stop_loss": "41000.0", "take_profit": "50000.0"})
    assert r["decision"] == "BLOCK" and "R018" in ids(r, "rule_violations")


def test_09_requested_volume_above_maximum_blocks(h):
    r = h.assess(requested_volume="300")
    assert r["decision"] == "BLOCK" and "R019" in ids(r, "rule_violations")


def test_10_invalid_lot_step_blocks(h):
    r = h.assess(requested_volume="0.455")
    assert r["decision"] == "BLOCK" and "R019" in ids(r, "rule_violations")


def test_requested_volume_over_budget_blocks_but_smaller_is_used(h):
    assert "R019" in ids(h.assess(requested_volume="0.80"), "rule_violations")
    r = h.assess(requested_volume="0.20", key="small")
    assert r["decision"] == "PASS" and r["final_volume"] == 0.2


# 11 and 15
def test_11_risk_budget_already_reserved_blocks(h):
    """Three PASS assessments reserve about 1% each; the fourth would exceed the 3% aggregate."""
    decisions = [h.assess({"symbol": s, **b})["decision"] for s, b in (
        ("EURUSD", {k: v for k, v in EURUSD_BUY.items() if k != "symbol"}),
        ("XAUUSD", {"direction": "SELL", "entry": "2650.000", "stop_loss": "2656.000", "take_profit": "2638.000"}),
        ("US30", {"direction": "BUY", "entry": "44000.0", "stop_loss": "43900.0", "take_profit": "44250.0"}))]
    assert decisions == ["PASS", "PASS", "PASS"]
    r = h.assess(EURUSD_SELL)
    assert r["decision"] == "BLOCK" and "R041" in ids(r, "rule_violations")


def test_15_excessive_open_risk_from_positions_blocks(h):
    h.data.positions = [position(volume="1.25")]           # 0.00200 x 100000 x 1.25 = 250 = 2.5%
    r = h.assess({"symbol": "XAUUSD", "direction": "SELL", "entry": "2650.000", "stop_loss": "2656.000", "take_profit": "2638.000"})
    assert r["decision"] == "BLOCK" and "R041" in ids(r, "rule_violations")


def test_open_position_without_stop_makes_risk_unbounded(h):
    h.data.positions = [position(sl=None)]
    r = h.assess({"symbol": "US30", "direction": "BUY", "entry": "44000.0", "stop_loss": "43900.0", "take_profit": "44250.0"})
    v = next(x for x in r["rule_violations"] if x["rule_id"] == "R041")
    assert v["reason_code"] == "OPEN_RISK_UNBOUNDED"


# 12-14
def test_12_daily_loss_limit_blocks_and_locks(h):
    h.data.equity = D(9650)                                # 3.5% below the day's starting equity
    r = h.assess()
    assert r["decision"] == "BLOCK" and {"R030", "R060"} <= set(ids(r, "rule_violations"))
    locks = h.svc.locks(True)["active"]
    assert len(locks) == 1 and locks[0]["rule_id"] == "R030" and locks[0]["expires_at"]       # resets next trading day
    h.assess(key="again")
    assert len(h.svc.locks(True)["active"]) == 1          # no duplicate lock
    events = [e["event_type"] for e in h.svc.events(0, 100, None)]
    assert "risk.limit.reached" in events and "risk.lock.activated" in events


def test_daily_lock_expires_at_next_trading_day(h):
    h.data.equity = D(9650)
    h.assess()
    h.clock.advance(hours=11)                             # past 17:00 New York
    h.data.now = h.clock.t
    h.data.history.append(EquityPoint(h.clock.t - timedelta(minutes=1), D(9650), D(10000)))
    assert h.svc.locks(True)["active"] == []
    r = h.assess(key="next-day")
    assert "R030" not in ids(r, "rule_violations")


def test_13_max_drawdown_blocks(h):
    h.data.history.insert(0, EquityPoint(NOW - timedelta(days=200), D(12000), D(12000)))
    r = h.assess()
    assert r["decision"] == "BLOCK" and "R033" in ids(r, "rule_violations")


def test_14_too_many_open_positions_blocks(h):
    h.data.positions = [position(volume="0.01", pid=f"p{i}", sl="1.09990") for i in range(5)]
    r = h.assess({"symbol": "XAUUSD", "direction": "SELL", "entry": "2650.000", "stop_loss": "2656.000", "take_profit": "2638.000"})
    assert r["decision"] == "BLOCK" and "R040" in ids(r, "rule_violations")


# 16
def test_16_correlated_exposure_blocks_same_way_only(h):
    h.data.correlated_series("EURUSD", "GBPUSD", 0.9)
    h.data.positions = [position(volume="0.6")]           # 120 USD = 1.2%; with the new ~0.98% that exceeds 2%
    buy = {"symbol": "GBPUSD", "direction": "BUY", "entry": "1.30000", "stop_loss": "1.29700", "take_profit": "1.30600"}
    r = h.assess(buy)
    assert r["decision"] == "BLOCK" and "R044" in ids(r, "rule_violations")
    assert r["correlation_status"]["affected_positions"][0]["symbol"] == "EURUSD"
    sell = {**buy, "direction": "SELL", "stop_loss": "1.30300", "take_profit": "1.29400"}
    assert "R044" not in ids(h.assess(sell), "rule_violations")


def test_unreliable_correlation_is_flagged_and_treated_conservatively(h):
    h.data.series.pop("XAUUSD")
    h.data.positions = [position(symbol="XAUUSD", side="SELL", volume="0.15", open_="2650.000", sl="2656.000", cls="metal")]
    r = h.assess()
    assert "R045" in ids(r, "warnings")
    assert r["correlation_status"]["unreliable_symbols"] == ["XAUUSD"]


# 17-20
def test_17_high_impact_event_blocks(h):
    h.data.intel["EURUSD"] = calm_intelligence("EURUSD", event_state="HIGH_EVENT_RISK")
    r = h.assess()
    assert r["decision"] == "BLOCK" and "R053" in ids(r, "rule_violations")


def test_18_abnormal_spread_blocks(h):
    h.data.quotes["EURUSD"] = Quote(D("1.10000"), D("1.10050"), NOW, "test")   # 50 points vs 12 median, cap 40
    r = h.assess()
    assert r["decision"] == "BLOCK" and "R052" in ids(r, "rule_violations")


def test_19_extreme_volatility_blocks(h):
    h.data.intel["EURUSD"] = calm_intelligence("EURUSD", regime="VERY_HIGH")
    r = h.assess()
    assert r["decision"] == "BLOCK" and "R054" in ids(r, "rule_violations")


def test_20_stale_market_data_blocks(h):
    h.data.quotes["EURUSD"] = Quote(D("1.10000"), D("1.10012"), NOW - timedelta(minutes=30), "test")
    r = h.assess()
    assert r["decision"] == "BLOCK" and "R051" in ids(r, "rule_violations")


def test_market_closed_blocks(h):
    h.data.intel["EURUSD"] = calm_intelligence("EURUSD", market_open=False)
    assert "R050" in ids(h.assess(), "rule_violations")


def test_module_04_unavailable_is_never_reported_as_checked(h):
    h.data.intel["EURUSD"] = None
    r = h.assess()
    assert r["decision"] == "REVIEW"
    codes = {v["reason_code"] for v in r["warnings"]}
    assert {"NEWS_FILTER_UNAVAILABLE", "SESSIONS_UNAVAILABLE", "VOLATILITY_UNKNOWN"} <= codes
    assert r["market_status"]["status"] == "DATA_UNAVAILABLE"
    h.config(news={"unknown_policy": "block"})
    assert h.assess(key="strict")["decision"] == "BLOCK"


# 21-23
def test_21_missing_account_snapshot_is_error(h):
    h.data.has_snapshot = False
    r = h.assess()
    assert r["decision"] == "ERROR" and "R001" in ids(r, "errors")
    assert r["account_status"]["status"] == "DATA_UNAVAILABLE"


def test_22_missing_instrument_specification_is_error(h):
    h.data.specs["EURUSD"]["tick_size"] = None
    r = h.assess()
    assert r["decision"] == "ERROR" and "R002" in ids(r, "errors")


def test_stale_instrument_specification_is_error(h):
    h.data.spec_time = NOW - timedelta(days=5)
    assert "R002" in ids(h.assess(), "errors")


def test_23_currency_conversion_unavailable_is_error(tmp_path):
    h = Harness(tmp_path, currency="JPY")
    h.data.spec_time = NOW - timedelta(hours=2)            # broker tick value too old to carry a conversion
    r = h.assess()
    assert r["decision"] == "ERROR"
    err = next(e for e in r["errors"] if e["rule_id"] == "R003")
    assert err["reason_code"] == "CONVERSION_UNAVAILABLE"


# 24-26
def test_24_duplicate_trade_idea_blocks_second_live_assessment(h):
    assert h.assess(trade_idea_id="idea-1")["decision"] == "PASS"
    r = h.assess(trade_idea_id="idea-1", entry="1.10020")
    assert r["decision"] == "BLOCK" and "R047" in ids(r, "rule_violations")


def test_idempotent_repeat_returns_the_same_assessment(h):
    a = h.assess(key="k1")
    b = h.assess(key="k1")
    assert b["replayed"] and a["assessment_id"] == b["assessment_id"]
    with pytest.raises(RiskError) as exc:
        h.assess(key="k1", entry="1.10020")
    assert exc.value.status == 409


def test_25_duplicate_event_is_processed_once(h):
    h.data.ideas["c-1"] = {"trade_idea_id": "c-1", "symbol": "EURUSD", "direction": "BUY", "entry_low": 1.1000, "entry_high": 1.1002,
                           "stop_loss": 1.0981, "take_profit": 1.1051, "strategy": "bos_continuation", "timeframe": "H1",
                           "confidence": 0.99}
    ev = {"event_id": "evt-1", "event_type": "trade_idea.created", "payload": {"trade_idea_id": "c-1"}}
    first = h.svc.receive_event(ev, "n8n")
    second = h.svc.receive_event(ev, "n8n")
    assert first["status"] == "processed" and second["duplicate"] is True
    assert h.svc.history(limit=50, offset=0, decision=None, symbol=None, rule_id=None, strategy=None, date_from=None,
                         date_to=None)["total"] == 1


def test_failed_event_is_dead_lettered_after_retries(h):
    ev = {"event_id": "evt-bad", "event_type": "trade_idea.created", "payload": {"trade_idea_id": "missing"}}
    for _ in range(4):
        with pytest.raises(RiskError):
            h.svc.receive_event(ev, "n8n")
    with pytest.raises(RiskError) as exc:
        h.svc.receive_event(ev, "n8n")
    assert exc.value.code == "EVENT_DEAD_LETTERED"
    assert h.svc.repo.inbox_rows("dead")[0]["event_id"] == "evt-bad"


def test_26_expired_assessment_cannot_be_used(h):
    a = h.assess()
    assert h.svc.validate_assessment(a["assessment_id"])["valid"]
    h.clock.advance(minutes=16)
    check = h.svc.validate_assessment(a["assessment_id"])
    assert not check["valid"] and any("expired" in r for r in check["reasons"])
    with pytest.raises(RiskError):
        h.svc.set_assessment_state(a["assessment_id"], "consumed", "m07", None, None)


def test_consumed_assessment_cannot_be_reused(h):
    a = h.assess()
    h.svc.set_assessment_state(a["assessment_id"], "consumed", "m07", "approved", None)
    assert not h.svc.validate_assessment(a["assessment_id"])["valid"]
    with pytest.raises(RiskError):
        h.svc.set_assessment_state(a["assessment_id"], "consumed", "m07", None, None)


def test_config_change_invalidates_earlier_assessments(h):
    a = h.assess()
    h.config(risk_per_trade_pct=0.5)
    assert not h.svc.validate_assessment(a["assessment_id"])["valid"]


# 27-29
def test_27_emergency_lock_blocks(h):
    h.svc.create_lock({"scope": "global", "reason": "manual emergency stop"}, "ops", None)
    r = h.assess()
    assert r["decision"] == "BLOCK" and "R060" in ids(r, "rule_violations")


def test_instrument_and_strategy_locks_are_scoped(h):
    h.svc.create_lock({"scope": "instrument", "scope_value": "XAUUSD", "reason": "gold feed suspect"}, "ops", None)
    h.svc.create_lock({"scope": "strategy", "scope_value": "fade", "reason": "strategy under review"}, "ops", None)
    assert h.assess()["decision"] == "PASS"
    assert "R060" in ids(h.assess(strategy="fade", key="s"), "rule_violations")


def test_28_lock_survives_restart(h):
    lock = h.svc.create_lock({"scope": "global", "reason": "before restart"}, "ops", None)
    h.restart()
    assert [x["lock_id"] for x in h.svc.locks(False)["active"]] == [lock["lock_id"]]
    assert h.assess()["decision"] == "BLOCK"
    h.svc.release_lock(lock["lock_id"], {"reason": "checked, resume"}, "ops", None)
    h.restart()
    assert h.assess(key="after")["decision"] == "PASS"


def test_29_consecutive_losses_lock_and_release(h):
    for i in range(3):
        t = NOW - timedelta(hours=3 - i)
        h.data.closed.append(ClosedTrade(f"L{i}", "EURUSD", t - timedelta(minutes=30), t, D(-100)))
    r = h.assess()
    assert r["decision"] == "BLOCK" and "R035" in ids(r, "rule_violations")
    lock = h.svc.locks(False)["active"][0]
    assert lock["rule_id"] == "R035" and lock["expires_at"] is None       # manual release by default
    h.svc.release_lock(lock["lock_id"], {"reason": "reviewed the three losses"}, "risk-officer", None)
    assert "R035" not in ids(h.assess(key="after-release"), "rule_violations")
    h.data.closed.append(ClosedTrade("L3", "EURUSD", NOW - timedelta(minutes=20), NOW - timedelta(minutes=5), D(-50)))
    assert "R035" in ids(h.assess(key="fourth-loss"), "rule_violations")


def test_30_unauthorised_settings_change_needs_reason_and_acknowledgement(h):
    v = h.svc.config()["version"]
    with pytest.raises(RiskError) as exc:
        h.svc.update_config({"base_version": v, "changes": {"max_daily_loss_pct": 5}}, "x", None)
    assert exc.value.code == "REASON_REQUIRED"
    with pytest.raises(RiskError) as exc:
        h.svc.update_config({"base_version": v, "reason": "loosen it", "changes": {"max_daily_loss_pct": 5}}, "x", None)
    assert exc.value.code == "WEAKENING_NOT_ACKNOWLEDGED" and "max_daily_loss_pct: 3.0 -> 5.0" in exc.value.details[0]
    with pytest.raises(RiskError) as exc:
        h.svc.update_config({"base_version": v, "reason": "disable", "changes": {"rules": {"R010": {"enabled": False}}},
                             "acknowledge_weakening": True}, "x", None)
    assert exc.value.code == "RULE_NOT_DISABLEABLE"
    # HTTP-level authorisation (missing/invalid admin key) is covered in test_api.py.


def test_config_change_never_releases_active_lock(h):
    h.svc.create_lock({"scope": "global", "reason": "hold everything"}, "ops", None)
    h.config(max_daily_loss_pct=5)
    assert len(h.svc.locks(False)["active"]) == 1


# ---- concurrency ---------------------------------------------------------------------------------------------------------
def test_concurrent_assessments_cannot_exceed_aggregate_budget(h):
    bodies = [{"symbol": "EURUSD", "direction": d, "entry": "1.10010", "stop_loss": s, "take_profit": t}
              for d, s, t in (("BUY", "1.09810", "1.10510"), ("SELL", "1.10210", "1.09510"))] * 3
    out: list[str] = []

    def worker(i: int, b: dict) -> None:
        try:
            out.append(h.svc.assess({**b, "strategy": f"s{i}"}, "t", None, f"c{i}")["decision"])
        except Exception as exc:  # noqa: BLE001
            out.append(type(exc).__name__)

    h.config(max_symbol_risk_pct=10, max_asset_class_risk_pct=10, max_correlated_risk_pct=10)
    threads = [threading.Thread(target=worker, args=(i, b)) for i, b in enumerate(bodies)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(out) == 6
    assert out.count("PASS") + out.count("REVIEW") <= 3      # 3% aggregate / ~1% each
    assert out.count("BLOCK") >= 3


def test_decision_is_deterministic_for_identical_inputs(h):
    a = h.assess(stop_loss="1.10210")                      # BLOCK reserves nothing, so the inputs stay identical
    b = h.assess(stop_loss="1.10210", key="second")
    strip = lambda r: [(x["rule_id"], x["outcome"], x["reason_code"]) for x in r["account_status"]["checks"] + r["failed_checks"]]  # noqa: E731
    assert a["decision"] == b["decision"] and strip(a) == strip(b)
