"""Integration: DatabaseRiskData reads the REAL Module 01, account-mirror and Module 05 tables (seeded in a temporary
SQLite copy of the full schema), and the engine assesses from them end to end.

Only Module 04 is a stand-in (``calm_intelligence``), because its analysis needs years of stored candles and
calendars; that is stated in each test that uses it. Nothing here touches a broker or MT5."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from conftest import NOW
from sqlalchemy import insert

from aurum.account.db_models import AccountDeal, AccountSnapshot, TradeRecord
from aurum.database.models import DataQualityIssue, Instrument, InstrumentSpec, MarketCandle, MarketTick
from aurum.decision.db_models import TradeCandidate
from aurum.risk.calculator import CalcError
from aurum.risk.calendar import TradingCalendar
from aurum.risk.config import TradingDay
from aurum.risk.money import D
from aurum.risk.providers import DatabaseRiskData
from aurum.risk.service import RiskService
from aurum.risk.simulation import SAMPLE_SPECS, calm_intelligence

UTC = timezone.utc


class StandInModule04:
    """MOCKED Module 04: a calm, open market. The real service needs long candle and calendar history."""

    def analyze(self, symbol: str, as_of: datetime, include_module03: bool = False) -> dict:
        return calm_intelligence(symbol)


def naive(t: datetime) -> datetime:
    return t.astimezone(UTC).replace(tzinfo=None)


def seed(engine) -> None:
    cal = TradingCalendar(TradingDay())
    with engine.begin() as c:
        ids = {}
        for i, (sym, cls) in enumerate((("EURUSD", "forex"), ("XAUUSD", "metal"), ("GBPUSD", "forex")), start=1):
            c.execute(insert(Instrument).values(id=i, symbol=sym, broker_symbol=sym, asset_class=cls))
            ids[sym] = i
            sp = SAMPLE_SPECS[sym]
            c.execute(insert(InstrumentSpec).values(
                instrument_id=i, captured_at=NOW - timedelta(hours=1), broker_symbol=sym, digits=sp["digits"], point=sp["point"],
                contract_size=sp["contract_size"], tick_size=sp["tick_size"], tick_value=sp["tick_value"], volume_min=sp["volume_min"],
                volume_max=sp["volume_max"], volume_step=sp["volume_step"], currency_base=sp["currency_base"],
                currency_profit=sp["currency_profit"], raw=sp["raw"]))
        # Quotes: a tick for EURUSD and XAUUSD; GBPUSD only has M5 candles (tests the candle fallback).
        c.execute(insert(MarketTick).values(instrument_id=ids["EURUSD"], ts_utc=NOW - timedelta(seconds=30), bid=1.10000, ask=1.10012,
                                            broker_symbol="EURUSD", mt5_time_msc=1))
        c.execute(insert(MarketTick).values(instrument_id=ids["XAUUSD"], ts_utc=NOW - timedelta(seconds=30), bid=2650.00, ask=2650.30,
                                            broker_symbol="XAUUSD", mt5_time_msc=1))
        for sym, price, spread in (("EURUSD", 1.1, 12), ("GBPUSD", 1.3, 15), ("XAUUSD", 2650.0, 300)):
            for k in range(1, 21):
                c.execute(insert(MarketCandle).values(
                    instrument_id=ids[sym], timeframe="M5", ts_utc=NOW - timedelta(minutes=5 * k), open=price, high=price, low=price,
                    close=price, spread=spread + (k % 3), is_complete=True, quality_status="valid", broker_symbol=sym, mt5_time=k))
            for k in range(1, 301):     # H1 closes for correlation; one invalid bar that must be skipped
                px = price * (1 + 0.0005 * ((k * (7 if sym == "EURUSD" else 13)) % 11 - 5))
                c.execute(insert(MarketCandle).values(
                    instrument_id=ids[sym], timeframe="H1", ts_utc=NOW - timedelta(hours=k + 1), open=px, high=px, low=px, close=px,
                    spread=spread, is_complete=True, quality_status="invalid" if k == 7 else "valid", broker_symbol=sym, mt5_time=k))
        # Account mirror: DEMO, a snapshot at each trading-day start this month, and one now.
        d = cal.trading_date(cal.month(NOW).start) - timedelta(days=1)
        while d <= cal.trading_date(NOW):
            c.execute(insert(AccountSnapshot).values(captured_at=cal.day_start(d), login=1, currency="USD", trade_mode="DEMO",
                                                     leverage=100, balance=10000, equity=10000, margin=0, margin_free=10000))
            d += timedelta(days=1)
        c.execute(insert(AccountSnapshot).values(captured_at=NOW - timedelta(minutes=1), login=1, currency="USD", trade_mode="DEMO",
                                                 leverage=100, balance=10000, equity=10000, margin=265, margin_free=9735, profit=0))
        c.execute(insert(TradeRecord).values(id="901", position_id=901, symbol="XAUUSD", broker_symbol="XAUUSD", side="buy",
                                             status="open", volume=0.1, open_time=NOW - timedelta(hours=2), open_price=2650.0,
                                             stop_loss=2640.0, strategy="breakout"))
        c.execute(insert(TradeRecord).values(id="900", position_id=900, symbol="EURUSD", broker_symbol="EURUSD", side="sell",
                                             status="closed", volume=0.2, open_time=NOW - timedelta(hours=5), open_price=1.1,
                                             close_time=NOW - timedelta(hours=4), close_price=1.101, pnl=-20.0))
        c.execute(insert(AccountDeal).values(ticket=1, ts_utc=NOW - timedelta(hours=3), type=2, profit=0.0))          # zero deposit
        c.execute(insert(DataQualityIssue).values(fingerprint="f1", symbol="EURUSD", timeframe="M5", issue_type="missing_candles",
                                                  severity="warning", resolved=False, last_seen_at=NOW - timedelta(hours=1)))
        c.execute(insert(DataQualityIssue).values(fingerprint="f2", symbol="EURUSD", timeframe="M5", issue_type="candle_revision",
                                                  severity="warning", resolved=False, last_seen_at=NOW - timedelta(hours=1)))
        c.execute(insert(TradeCandidate).values(
            candidate_uid="idea-1", idempotent_key="k1", symbol="EURUSD", timeframe="H1", template="order_block_continuation",
            direction="long", as_of=NOW - timedelta(minutes=10), entry_low=1.10000, entry_high=1.10020, stop_loss=1.09810,
            take_profit=1.10510, risk_reward=2.5, deterministic_score=60, confidence=0.9, grade="B", status="new",
            expires_at=NOW + timedelta(hours=4), invalidation="close below stop", market_condition="TRENDING",
            first_seen_run=str(uuid.uuid4()), last_seen_run=str(uuid.uuid4()), input_hash="h"))


@pytest.fixture
def db(h):
    seed(h.engine)
    return DatabaseRiskData(h.engine, None, intelligence_service=StandInModule04())


# ---- reads (REAL tables) ---------------------------------------------------------------------------------------------------
def test_reads_module_01_specification_and_rejects_missing(db):
    s = db.spec("EURUSD")
    assert (s.contract_size, s.tick_size, s.volume_step, s.calc_mode, s.currency_profit) == (D(100000), D("0.00001"), D("0.01"), 0, "USD")
    assert db.symbols() == {"EURUSD": "forex", "XAUUSD": "metal", "GBPUSD": "forex"}
    with pytest.raises(CalcError, match="SPEC_MISSING|No stored"):
        db.spec("US30")


def test_quote_from_ticks_then_m5_fallback_and_spread_median(db):
    q = db.quote("EURUSD", NOW)
    assert q.source == "module_01.market_ticks" and q.spread == D("0.00012")
    g = db.quote("GBPUSD", NOW)
    assert g.source == "module_01.market_candles.M5" and g.at == NOW
    current, med = db.spread_stats("EURUSD", NOW)
    assert current == D(12) and med == D(13)


def test_closes_use_only_complete_valid_bars(db):
    s = db.closes("EURUSD", "H1", 500, NOW)
    assert len(s) == 299 and s.index.is_monotonic_increasing


def test_account_mirror_positions_trades_and_cash_flows(db):
    snap = db.account_snapshot()
    assert snap.trade_mode == "DEMO" and snap.equity == D(10000) and snap.margin == D(265)
    [pos] = db.open_positions()
    assert (pos.symbol, pos.side, pos.stop_loss, pos.asset_class) == ("XAUUSD", "BUY", D(2640), "metal")
    [closed] = db.closed_trades(NOW - timedelta(days=1))
    assert closed.pnl == D(-20)
    assert len(db.balance_operations(NOW - timedelta(days=1))) == 1
    assert db.opened_since(NOW - timedelta(days=1)) == 2


def test_quality_issues_exclude_candle_revisions(db):
    assert [i["issue_type"] for i in db.quality_issues("EURUSD", NOW)] == ["missing_candles"]


def test_reads_module_05_candidate(db):
    idea = db.trade_idea("idea-1")
    assert idea["direction"] == "BUY" and idea["strategy"] == "order_block_continuation" and idea["confidence"] == 0.9
    assert db.trade_idea("nope") is None


# ---- end to end through the real engine (Module 04 MOCKED, everything else from the tables) ------------------------------
def test_assessment_from_real_tables(h, db):
    svc = RiskService(h.engine, None, data=db, clock=h.clock)
    r = svc.assess({"symbol": "EURUSD", "direction": "BUY", "entry": "1.10010", "stop_loss": "1.09810", "take_profit": "1.10510"},
                   "tester", None, None)
    # 1% of 10,000 / (200 + 12 spread + 5 slippage per lot) = 0.4608 -> 0.46. Data-quality warning (R056) -> REVIEW.
    assert r["final_volume"] == 0.46 and r["decision"] == "REVIEW"
    assert [w["rule_id"] for w in r["warnings"]] == ["R056"]
    assert r["portfolio_status"]["summary"]["total_open_risk"] == 100.0    # the open XAUUSD position: 10 x 100 x 0.1 at its stop


def test_assessment_from_module_05_idea_in_real_table(h, db):
    svc = RiskService(h.engine, None, data=db, clock=h.clock)
    r = svc.assess({"trade_idea_id": "idea-1"}, "tester", None, None)
    assert r["trade_idea_id"] == "idea-1" and D(r["proposal"]["entry"]) == D("1.10010") and r["decision"] in ("PASS", "REVIEW")
    info = {i["rule_id"]: i for i in r["information"]}
    assert info["R071"]["evidence"]["ai_confidence"] == 0.9
