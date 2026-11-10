"""Account mirror: positions rebuilt from MT5 deals, and the /account, /trades, /risk, /performance,
/agents/status and /notifications endpoints. All rows are small synthetic fixtures in an isolated SQLite DB."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from cot_helpers import cot_cfg
from fastapi.testclient import TestClient

from aurum.account.db_models import AccountSnapshot, TradeRecord
from aurum.account.deals import build_positions, cash_flows
from aurum.account.recorder import AccountRecorder
from aurum.api.app import create_app
from aurum.config import Settings
from aurum.database.connection import engine_from_settings, init_database, session_scope
from aurum.database.models import DataCollectionLog
from aurum.database.repository import Repository

T0 = datetime(2026, 3, 2, 8, 0, tzinfo=timezone.utc)  # a Monday, London session


def to_utc(sec: float) -> datetime:
    return datetime.fromtimestamp(sec, tz=timezone.utc)


def deal(ticket, pos, typ, entry, vol, price, at, profit=0.0, **kw):
    return {"ticket": ticket, "position_id": pos, "type": typ, "entry": entry, "volume": vol, "price": price,
            "time": int(at.timestamp()), "time_msc": int(at.timestamp() * 1000), "profit": profit, "symbol": kw.get("symbol", "EURUSDm"),
            "commission": kw.get("commission", 0.0), "swap": 0.0, "fee": 0.0, "comment": kw.get("comment", ""), "magic": 0,
            "sl": kw.get("sl", 0.0), "tp": kw.get("tp", 0.0)}


ACCOUNT = {"login": 123, "server": "Demo-1", "currency": "USD", "trade_mode": 0, "leverage": 100, "balance": 10_147.0,
           "equity": 10_120.0, "margin": 110.5, "margin_free": 10_009.5, "margin_level": 9158.4, "profit": -30.0}


def history(now: datetime) -> list[dict]:
    d = [deal(1, 0, 2, 0, 0, 0, T0 - timedelta(days=1), profit=10_000.0)]  # deposit (balance deal)
    d[0]["position_id"] = 0
    # closed winner (London) and closed loser (New York), both EURUSD
    d += [deal(2, 101, 0, 0, 1.0, 1.1000, T0, commission=-3.0, comment="breakout"),
          deal(3, 101, 1, 1, 1.0, 1.1020, T0 + timedelta(hours=2), profit=200.0)]
    d += [deal(4, 102, 1, 0, 0.5, 1.1050, T0 + timedelta(hours=6), comment="fade"),
          deal(5, 102, 0, 1, 0.5, 1.1060, T0 + timedelta(hours=7), profit=-50.0)]
    # still open: buy 1.0 EURUSD with SL 1.0950, opened shortly before "now"
    d += [deal(6, 103, 0, 0, 1.0, 1.1000, now - timedelta(minutes=30), commission=-3.0, sl=1.0950, tp=1.1100)]
    return d


@pytest.fixture
def env(tmp_path):
    settings = Settings(_env_file=None, database_url=f"sqlite:///{(tmp_path / 'a.db').as_posix()}", data_dir=tmp_path / "data",
                        log_dir=tmp_path / "logs", mt5_server_timezone="Etc/UTC", risk_max_daily_loss_pct=0.1,
                        risk_max_open_positions=5)
    eng = engine_from_settings(settings)
    init_database(eng)
    repo = Repository(eng)
    eur = repo.upsert_instrument("EURUSD", "EURUSDm", "forex", "Euro vs US Dollar")
    now = datetime.now(timezone.utc).replace(microsecond=0)
    repo.insert_spec(eur, {"broker_symbol": "EURUSDm", "digits": 5, "point": 0.00001, "contract_size": 100_000.0, "tick_size": 0.00001,
                           "tick_value": 1.0, "currency_base": "EUR", "currency_profit": "USD"}, now - timedelta(hours=1), "r")
    repo.upsert_tick(eur, {"ts_utc": now - timedelta(seconds=5), "bid": 1.1010, "ask": 1.1011, "spread_points": 10,
                           "broker_symbol": "EURUSDm", "mt5_time_msc": 1}, "r")
    return eng, settings, now


@pytest.fixture
def client(env):
    eng, settings, now = env
    rec = AccountRecorder(eng)
    rec.record_snapshot({**ACCOUNT, "equity": 10_200.0}, now - timedelta(days=1))
    rec.record_snapshot(ACCOUNT, now)
    rec.store_deals(history(now), to_utc)
    with TestClient(create_app(settings, cot_cfg(), eng)) as c:
        yield c


def test_positions_are_rebuilt_from_deals():
    now = T0 + timedelta(days=1)
    by = {p["id"]: p for p in build_positions(history(now), to_utc, {"EURUSDm": "EURUSD"})}
    assert by["101"]["status"] == "closed" and by["101"]["pnl"] == 197.0 and by["101"]["strategy"] == "breakout"
    assert by["102"]["side"] == "sell" and by["102"]["pnl"] == -50.0
    assert by["103"]["status"] == "open" and by["103"]["symbol"] == "EURUSD" and by["103"]["stop_loss"] == 1.0950
    assert cash_flows(history(now), to_utc) == [{"ticket": 1, "ts_utc": T0 - timedelta(days=1), "amount": 10_000.0, "kind": "balance"}]


def test_every_balance_deal_type_is_a_cash_flow():
    ds = [deal(1, 0, 12, 0, 0, 0, T0, profit=1.5), deal(2, 0, 8, 0, 0, 0, T0, commission=-2.0),
          deal(3, 0, 17, 0, 0, 0, T0, profit=-0.4), deal(4, 0, 13, 0, 0, 0, T0)]  # 13 = cancelled buy: not cash
    assert [(c["kind"], c["amount"]) for c in cash_flows(ds, to_utc)] == [("interest", 1.5), ("commission_daily", -2.0), ("tax", -0.4)]


def test_netting_reversal_opens_the_opposite_leg():
    ds = [deal(1, 300, 0, 0, 1.0, 1.1000, T0),
          deal(2, 300, 1, 2, 3.0, 1.1010, T0 + timedelta(hours=1), profit=100.0),   # INOUT: close 1.0, open 2.0 short
          deal(3, 300, 0, 1, 2.0, 1.1000, T0 + timedelta(hours=2), profit=200.0)]
    legs = build_positions(ds, to_utc)
    assert [(p["id"], p["side"], p["status"], p["volume"], p["pnl"]) for p in legs] == [
        ("300", "buy", "closed", 1.0, 100.0), ("300-2", "sell", "closed", 2.0, 200.0)]
    open_short = build_positions(ds[:2], to_utc)[1]
    assert (open_short["status"], open_short["volume"], open_short["open_price"]) == ("open", 2.0, 1.1010)


def test_overlapping_syncs_never_double_count_partial_closes(env):
    eng, _, _ = env
    rec = AccountRecorder(eng)
    entry, partial = deal(10, 200, 0, 0, 2.0, 1.1, T0), deal(11, 200, 1, 1, 0.5, 1.101, T0 + timedelta(days=20), profit=50.0)
    rec.store_deals([entry, partial], to_utc)
    with session_scope(eng) as s:
        r = s.get(TradeRecord, (0, "200"))
        assert (r.status, r.volume, r.pnl) == ("open", 1.5, 50.0)
    # A later window holds the partial exit again and the final exit, but not the entry deal.
    rec.store_deals([partial, deal(12, 200, 1, 1, 1.5, 1.102, T0 + timedelta(days=40), profit=300.0)], to_utc)
    with session_scope(eng) as s:
        r = s.get(TradeRecord, (0, "200"))
        assert (r.status, r.pnl, r.volume) == ("closed", 350.0, 2.0)
        assert r.close_price == pytest.approx(1.10175)


def test_sync_reads_back_to_the_last_stored_deal_after_a_long_pause(env):
    eng, _, now = env
    rec = AccountRecorder(eng)
    assert rec.sync_window_start(now, 30) == now - timedelta(days=30)
    rec.store_deals([deal(10, 200, 0, 0, 1.0, 1.1, now - timedelta(days=50))], to_utc)
    assert rec.sync_window_start(now, 30) == now - timedelta(days=51)


def test_old_snapshots_are_thinned_to_one_per_hour(env):
    eng, _, now = env
    rec = AccountRecorder(eng)
    old = (now - timedelta(days=10)).replace(minute=0, second=0)
    for i in range(12):  # every 10 s for two minutes, ten days ago
        rec.record_snapshot(ACCOUNT, old + timedelta(seconds=10 * i))
    for i in range(3):
        rec.record_snapshot(ACCOUNT, now - timedelta(seconds=10 * i))
    assert rec.compact(now) == 11
    with session_scope(eng) as s:
        assert s.query(AccountSnapshot).count() == 4


def test_non_demo_accounts_are_never_recorded(env):
    eng, _, _ = env
    with pytest.raises(ValueError, match="DEMO"):
        AccountRecorder(eng).record_snapshot({**ACCOUNT, "trade_mode": 2})


def test_sync_uses_only_account_info_and_deal_history(env):
    eng, _, now = env
    calls = []
    gw = SimpleNamespace(account_snapshot=lambda: calls.append("account") or ACCOUNT,
                         deals=lambda a, b: calls.append("deals") or history(now), clock=SimpleNamespace(to_utc=to_utc))
    res = AccountRecorder(eng).sync(gw, lookback_days=30, now=now)
    assert calls == ["account", "deals"] and res["open"] == 1 and res["cash_flows"] == 1


def test_two_linked_accounts_never_mix(env):
    """Same deal tickets and position ids on two broker accounts stay separate rows; each account rebuilds only its own."""
    eng, _, now = env
    a, b = AccountRecorder(eng, login=111), AccountRecorder(eng, login=222)
    a.store_deals([deal(10, 200, 0, 0, 1.0, 1.1, now - timedelta(days=5))], to_utc)
    b.store_deals([deal(10, 200, 1, 0, 3.0, 1.2, now - timedelta(days=2))], to_utc)
    b.store_deals([deal(11, 200, 0, 1, 3.0, 1.19, now - timedelta(days=1), profit=30.0)], to_utc)
    with session_scope(eng) as s:
        ra, rb = s.get(TradeRecord, (111, "200")), s.get(TradeRecord, (222, "200"))
        assert (ra.side, ra.status, ra.volume) == ("buy", "open", 1.0)
        assert (rb.side, rb.status, rb.pnl) == ("sell", "closed", 30.0)
    assert a.sync_window_start(now, 1) == now - timedelta(days=6)
    assert b.sync_window_start(now, 1) == now - timedelta(days=2)


def test_recorder_bound_to_a_login_refuses_another_accounts_snapshot(env):
    eng, _, _ = env
    with pytest.raises(ValueError, match="not the linked account"):
        AccountRecorder(eng, login=999).record_snapshot(ACCOUNT)


def test_thinning_is_per_account(env):
    eng, _, now = env
    old = (now - timedelta(days=10)).replace(minute=0, second=0)
    rec = AccountRecorder(eng)
    rec.record_snapshot(ACCOUNT, old)
    rec.record_snapshot({**ACCOUNT, "login": 456}, old + timedelta(seconds=10))
    assert rec.compact(now) == 0


def test_sync_stores_deals_under_the_reported_login(env):
    eng, _, now = env
    gw = SimpleNamespace(account_snapshot=lambda: ACCOUNT, deals=lambda a, b: history(now), clock=SimpleNamespace(to_utc=to_utc))
    assert AccountRecorder(eng).sync(gw, lookback_days=30, now=now)["login"] == 123
    with session_scope(eng) as s:
        assert {r.login for r in s.query(TradeRecord).all()} == {123}


def test_account_summary_null_until_first_snapshot(env):
    eng, settings, _ = env
    with TestClient(create_app(settings, cot_cfg(), eng)) as c:
        assert c.get("/api/v1/account/summary").json() is None
        assert c.get("/api/v1/performance/summary").json() is None
        risk = c.get("/api/v1/risk/overview").json()
        assert risk["dailyDrawdownPct"] is None and risk["limits"]["enforcedBy"] == "backend"


def test_account_summary_and_trades(client):
    s = client.get("/api/v1/account/summary").json()
    assert s["environment"] == "demo" and s["currency"] == "USD" and s["equity"] == 10_120.0
    assert s["openPositions"] == 1 and s["pendingApprovals"] == 0 and s["marginLevel"] == 9158.4
    (pos,) = client.get("/api/v1/trades/open").json()
    assert pos["instrument"] == "EURUSD" and pos["currentPrice"] == 1.1010 and pos["kind"] == "position"
    assert pos["floatingPnl"] == pytest.approx(97.0)  # 10 pips on 1 lot ($100) less $3 commission
    hist = client.get("/api/v1/trades/history").json()
    assert [h["ticket"] for h in hist] == ["102", "101"] and hist[1]["realisedPnl"] == 197.0


def test_performance_summary(client):
    p = client.get("/api/v1/performance/summary").json()
    m = p["metrics"]
    assert (m["totalTrades"], m["winningTrades"], m["losingTrades"], m["winRate"]) == (2, 1, 1, 50.0)
    assert m["profitFactor"] == 3.94 and m["expectancy"] == 73.5 and m["sharpe"] is None  # < 20 days of history
    assert p["equityCurve"][0]["equity"] == 10_000.0 and p["equityCurve"][-1]["equity"] == 10_147.0
    assert p["monthly"] == [{"month": "2026-03", "returnPct": 1.47}]
    assert {x["name"] for x in p["bySession"]} == {"London", "London-New York overlap"}  # 14:00 UTC: both open
    only = client.get("/api/v1/performance/summary", params={"session": "newyork"}).json()
    assert only["metrics"]["totalTrades"] == 1 and only["metrics"]["profitFactor"] == 0.0
    assert client.get("/api/v1/performance/summary", params={"strategy": "breakout", "from": "2026-03-01"}).json()["metrics"]["totalTrades"] == 1
    assert client.get("/api/v1/performance/summary", params={"to": "2026-01-01"}).json() is None


def test_risk_overview(client):
    r = client.get("/api/v1/risk/overview").json()
    assert r["openRisk"] == 500.0 and r["accountRiskPct"] == pytest.approx(4.94, abs=0.01)  # 50 pips to SL on 1 lot
    assert r["exposureByInstrument"][0]["name"] == "EURUSD" and r["exposureByInstrument"][0]["value"] == pytest.approx(1086.96, abs=0.01)
    assert r["exposureByAssetClass"][0]["name"] == "forex"
    assert r["dailyDrawdownPct"] == pytest.approx(0.78, abs=0.01) and r["limits"]["maxDailyLossPct"] == 0.1


def test_agents_status_and_notifications(client, env):
    eng, _, now = env
    with session_scope(eng) as s:
        s.add(DataCollectionLog(run_id="r", event="run", status="failed", started_at=now - timedelta(minutes=3),
                                finished_at=now - timedelta(minutes=2), message="terminal timeout"))
    agents = {a["id"]: a for a in client.get("/api/v1/agents/status").json()}
    assert agents["market-data"]["status"] == "failed" and agents["market-data"]["errorCount"] == 1
    assert agents["cot"]["status"] == "idle" and agents["decision"]["status"] == "idle"
    assert agents["risk"]["status"] == "warning" and "Daily drawdown" in agents["risk"]["lastResult"]
    assert agents["learning"]["status"] == "idle"
    notes = client.get("/api/v1/notifications").json()
    cats = {n["category"] for n in notes}
    assert {"collection_failure", "risk_limit"} <= cats and all(not n["read"] for n in notes)
    assert len({n["id"] for n in notes}) == len(notes)
    assert notes == client.get("/api/v1/notifications").json()  # ids are stable between calls


def test_routes_are_served(client):
    paths = client.get("/openapi.json").json()["paths"]
    for p in ("/account/summary", "/trades/open", "/trades/history", "/risk/overview", "/performance/summary",
              "/agents/status", "/notifications"):
        assert f"/api/v1{p}" in paths
    assert client.get("/api/v1/health").json()["modules"]["account"] is True
