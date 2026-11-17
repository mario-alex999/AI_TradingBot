"""Cloud backup price feed (Twelve Data): used only while MT5 is offline, never replaces broker data.

Twelve Data is replaced by an httpx MockTransport; the database is a temporary SQLite file."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx
import pandas as pd
import pytest
from sqlalchemy import select

from aurum.api.market_queries import MarketQueries
from aurum.collectors.backup_prices import BackupPriceFeed, TwelveDataClient, bucket_start
from aurum.config import InstrumentConfig, Settings, TradingHours, load_instruments
from aurum.database.connection import engine_from_settings, init_database, session_scope
from aurum.database.models import BackupPriceStatus, MarketCandle, MarketTick
from aurum.database.repository import Repository

NOW = datetime(2026, 11, 17, 14, 2, 30, tzinfo=timezone.utc)          # a Tuesday: every market open
HOURS = TradingHours.from_dict({"timezone": "America/New_York", "weekly_close": "FRI 17:00", "weekly_open": "SUN 17:05"})
INSTRUMENTS = {"EURUSD": InstrumentConfig("EURUSD", "EURUSDm", "forex", trading_hours=HOURS, backup_symbol="EUR/USD"),
               "XAUUSD": InstrumentConfig("XAUUSD", "XAUUSDm", "metal", trading_hours=HOURS, backup_symbol="XAU/USD")}


def bars(start: datetime, n: int, price: float = 1.1000) -> list[dict]:
    return [{"datetime": (start + timedelta(minutes=5 * k)).strftime("%Y-%m-%d %H:%M:%S"),
             "open": f"{price + k * 0.0001:.5f}", "high": f"{price + k * 0.0001 + 0.0004:.5f}",
             "low": f"{price + k * 0.0001 - 0.0004:.5f}", "close": f"{price + k * 0.0001 + 0.0002:.5f}"}
            for k in range(n)]


class FakeTwelveData:
    """Answers /time_series like Twelve Data's batch format; ``errors`` maps a symbol to an error object."""

    def __init__(self, errors: dict | None = None, whole: dict | None = None):
        self.requests: list[httpx.Request] = []
        self.errors, self.whole = errors or {}, whole

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.whole is not None:
            return httpx.Response(200, json=self.whole)
        symbols = request.url.params["symbol"].split(",")
        start = NOW.replace(minute=0, second=0) - timedelta(minutes=30)     # 13:30 .. 14:00 (forming)
        out = {}
        for s in symbols:
            if s in self.errors:
                out[s] = {"code": self.errors[s], "message": f"{s} needs a higher plan", "status": "error"}
            else:
                out[s] = {"meta": {"symbol": s, "interval": "5min"}, "status": "ok",
                          "values": bars(start, 7, 2650.0 if s == "XAU/USD" else 1.1000)}
        return httpx.Response(200, json=out[symbols[0]] if len(symbols) == 1 else out)


@pytest.fixture
def env(tmp_path):
    settings = Settings(_env_file=None, database_url=f"sqlite:///{(tmp_path / 'b.db').as_posix()}",
                        data_dir=tmp_path / "data", log_dir=tmp_path / "logs", mt5_server_timezone="Etc/UTC",
                        enabled_instruments=["EURUSD", "XAUUSD"], twelve_data_api_key="td-secret-key")
    eng = engine_from_settings(settings)
    init_database(eng)
    repo = Repository(eng)
    eur = repo.upsert_instrument("EURUSD", "EURUSDm", "forex")
    xau = repo.upsert_instrument("XAUUSD", "XAUUSDm", "metal")
    repo.insert_spec(eur, {"broker_symbol": "EURUSDm", "digits": 5, "point": 0.00001, "contract_size": 100000.0,
                           "tick_size": 0.00001, "tick_value": 1.0}, NOW - timedelta(days=1), "run-1")
    # The MT5 PC went offline at 13:20: its last tick and candles are from then.
    for iid, sym, px in ((eur, "EURUSDm", 1.0990), (xau, "XAUUSDm", 2649.0)):
        repo.upsert_tick(iid, {"ts_utc": NOW - timedelta(minutes=42), "bid": px, "ask": px + 0.0001,
                               "broker_symbol": sym, "mt5_time_msc": 1}, "run-1")
    with session_scope(eng) as s:
        for k in range(4):                                                 # 13:00 .. 13:15 complete MT5 M5 candles
            ts = NOW.replace(minute=0, second=0) - timedelta(hours=1) + timedelta(minutes=5 * k)
            s.add(MarketCandle(instrument_id=eur, timeframe="M5", ts_utc=ts, open=1.0990, high=1.0995, low=1.0985,
                               close=1.0991, spread=8, is_complete=True, broker_symbol="EURUSDm",
                               mt5_time=int(ts.timestamp())))
    return eng, settings, repo, eur, xau


def feed(env, fake: FakeTwelveData, now: datetime = NOW, **over) -> BackupPriceFeed:
    eng, settings, *_ = env
    if over:
        settings = settings.model_copy(update=over)
    client = TwelveDataClient("td-secret-key", transport=httpx.MockTransport(fake))
    return BackupPriceFeed(eng, settings, INSTRUMENTS, client=client, clock=lambda: now)


def candles(eng, iid, tf):
    with session_scope(eng) as s:
        rows = s.execute(select(MarketCandle).where(MarketCandle.instrument_id == iid, MarketCandle.timeframe == tf)
                         .order_by(MarketCandle.ts_utc)).scalars().all()
        s.expunge_all()
        return rows


def ticks(eng, iid, source):
    with session_scope(eng) as s:
        rows = s.execute(select(MarketTick).where(MarketTick.instrument_id == iid, MarketTick.source == source)
                         .order_by(MarketTick.ts_utc)).scalars().all()
        s.expunge_all()
        return rows


def test_off_without_a_key(env):
    eng, settings, *_ = env
    out = BackupPriceFeed(eng, settings.model_copy(update={"twelve_data_api_key": None}), INSTRUMENTS).run()
    assert out["outcome"] == "off" and "TWELVE_DATA_API_KEY" in out["reason"]


def test_nothing_is_fetched_while_mt5_is_live(env):
    eng, _, repo, eur, xau = env
    for iid in (eur, xau):
        repo.upsert_tick(iid, {"ts_utc": NOW - timedelta(seconds=20), "bid": 1.1, "ask": 1.1001, "broker_symbol": "x",
                               "mt5_time_msc": 2}, "run-2")
    fake = FakeTwelveData()
    out = feed(env, fake).run()
    assert out == {"outcome": "idle", "skipped": {"EURUSD": "mt5_live", "XAUUSD": "mt5_live"}}
    assert fake.requests == []


def test_nothing_is_fetched_while_the_market_is_closed(env):
    fake = FakeTwelveData()
    saturday = datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc)
    out = feed(env, fake, now=saturday).run()
    assert out["skipped"] == {"EURUSD": "market_closed", "XAUUSD": "market_closed"} and fake.requests == []


def test_mt5_offline_stores_backup_prices_in_one_request(env):
    eng, _, repo, eur, xau = env
    fake = FakeTwelveData()
    out = feed(env, fake).run()
    assert out["outcome"] == "updated", out
    assert len(fake.requests) == 1
    req = fake.requests[0]
    assert req.url.params["symbol"] == "EUR/USD,XAU/USD" and req.url.params["interval"] == "5min"
    assert req.url.params["timezone"] == "UTC"
    # the key travels in a header, never in the address (which ends up in logs)
    assert req.headers["Authorization"] == "apikey td-secret-key" and "td-secret-key" not in str(req.url)

    m5 = candles(eng, eur, "M5")
    backup = [c for c in m5 if c.source == "twelvedata"]
    assert len(backup) == 7 and all(c.source == "mt5" for c in m5 if c.ts_utc.minute < 20 and c.ts_utc.hour == 13)
    assert backup[-1].is_complete is False and all(c.is_complete for c in backup[:-1])   # 14:00 still forming
    h1 = candles(eng, eur, "H1")
    assert [c.ts_utc.hour for c in h1] == [13, 14] and h1[0].is_complete and not h1[1].is_complete
    assert h1[0].open == pytest.approx(1.0990)                     # the MT5 candles of 13:00-13:15 are part of it
    assert h1[0].high == pytest.approx(max(c.high for c in m5 if c.ts_utc.hour == 13))
    assert [c.ts_utc.day for c in candles(eng, eur, "D1")] == [17]

    tick = ticks(eng, eur, "twelvedata")[-1]
    assert tick.bid == pytest.approx(1.1008) and tick.ts_utc == NOW            # the forming bar's latest price
    assert tick.spread_points == 8 and tick.ask == pytest.approx(1.1008 + 8 * 0.00001)
    gold = ticks(eng, xau, "twelvedata")[-1]
    assert gold.ask == gold.bid                                              # no broker spread known: no estimate

    with session_scope(eng) as s:
        st = s.get(BackupPriceStatus, eur)
        assert st.last_outcome == "updated" and st.provider_symbol == "EUR/USD"


def test_each_instrument_is_fetched_at_most_once_per_interval(env):
    fake = FakeTwelveData()
    feed(env, fake).run()
    out = feed(env, fake, now=NOW + timedelta(minutes=3)).run()
    assert out["outcome"] == "idle" and set(out["skipped"].values()) == {"not_due"} and len(fake.requests) == 1
    assert feed(env, fake, now=NOW + timedelta(minutes=10)).run()["outcome"] == "updated"
    assert len(fake.requests) == 2


def test_complete_broker_candles_are_never_replaced(env):
    eng, _, repo, eur, _ = env
    ts = NOW.replace(minute=30, second=0) - timedelta(hours=1)                 # 13:30, also sent by the fake
    with session_scope(eng) as s:
        s.add(MarketCandle(instrument_id=eur, timeframe="M5", ts_utc=ts, open=1.2, high=1.3, low=1.1, close=1.25,
                           is_complete=True, broker_symbol="EURUSDm", mt5_time=int(ts.timestamp())))
        forming = ts + timedelta(minutes=5)
        s.add(MarketCandle(instrument_id=eur, timeframe="M5", ts_utc=forming, open=1.2, high=1.3, low=1.1, close=1.25,
                           is_complete=False, broker_symbol="EURUSDm", mt5_time=int(forming.timestamp())))
    feed(env, FakeTwelveData()).run()
    by_ts = {c.ts_utc: c for c in candles(eng, eur, "M5")}
    assert by_ts[ts].source == "mt5" and by_ts[ts].close == 1.25                    # broker candle kept
    assert by_ts[forming].source == "twelvedata" and by_ts[forming].close != 1.25    # unfinished broker candle filled


def test_broker_candles_replace_backup_candles_without_a_revision(env):
    eng, _, repo, eur, _ = env
    feed(env, FakeTwelveData()).run()
    ts = NOW.replace(minute=30, second=0) - timedelta(hours=1)
    df = pd.DataFrame([{"timeframe": "M5", "ts_utc": ts, "open": 1.3, "high": 1.31, "low": 1.29, "close": 1.305,
                        "tick_volume": 5, "real_volume": 0, "spread": 9, "is_complete": True, "broker_symbol": "EURUSDm",
                        "mt5_time": int(ts.timestamp())}])
    written, revisions = repo.upsert_candles(eur, df, "run-3")
    assert written == 1 and revisions == []
    c = {c.ts_utc: c for c in candles(eng, eur, "M5")}[ts]
    assert c.source == "mt5" and c.close == 1.305


def test_the_mt5_collector_resumes_from_its_own_candles(env):
    eng, _, repo, eur, _ = env
    feed(env, FakeTwelveData()).run()
    assert repo.latest_candle_ts(eur, "M5", source="mt5").minute == 15          # backfills from 13:15, not 13:55
    assert repo.latest_candle_ts(eur, "M5").minute == 55


def test_a_symbol_outside_the_plan_is_paused(env):
    eng, *_ , eur, xau = env
    fake = FakeTwelveData(errors={"XAU/USD": 403})
    out = feed(env, fake).run()
    assert out["outcome"] == "partial"
    assert out["instruments"]["XAUUSD"] == {"outcome": "not_on_plan", "error": "XAU/USD needs a higher plan", "paused": True}
    later = feed(env, fake, now=NOW + timedelta(minutes=15)).run()
    assert later["skipped"]["XAUUSD"] == "paused" and later["instruments"]["EURUSD"]["outcome"] == "updated"
    assert fake.requests[-1].url.params["symbol"] == "EUR/USD"
    status = {i["symbol"]: i for i in feed(env, fake, now=NOW + timedelta(minutes=15)).status()["instruments"]}
    assert status["XAUUSD"]["pausedUntil"] is not None and status["XAUUSD"]["lastOutcome"] == "not_on_plan"


def test_a_rejected_key_is_reported_for_every_instrument(env):
    eng, *_, eur, xau = env
    fake = FakeTwelveData(whole={"code": 401, "message": "Invalid API key", "status": "error"})
    out = feed(env, fake).run()
    assert out["outcome"] == "failed" and out["code"] == 401
    with session_scope(eng) as s:
        assert {s.get(BackupPriceStatus, i).last_outcome for i in (eur, xau)} == {"failed"}
    assert ticks(eng, eur, "twelvedata") == []


def test_quotes_say_where_the_price_came_from(env):
    eng, *_, eur, _ = env
    feed(env, FakeTwelveData()).run()
    mq = MarketQueries(eng)
    q = {x["symbol"]: x for x in mq.quotes(now=NOW + timedelta(minutes=2))}
    assert q["EURUSD"]["source"] == "backup" and q["EURUSD"]["status"] == "open"
    # the MT5 feed still reads as disconnected: backup prices never hide that the PC is offline
    assert mq.latest_tick_time() == NOW - timedelta(minutes=42)
    assert mq.feed_state(60, NOW + timedelta(minutes=2))["mt5"] == "disconnected"


def test_status_never_shows_the_key(env):
    fake = FakeTwelveData()
    f = feed(env, fake)
    f.run()
    st = f.status()
    assert "td-secret-key" not in json.dumps(st)
    eur = st["instruments"][0]
    assert eur["priceSource"] == "backup" and eur["mt5Live"] is False and eur["backupSymbol"] == "EUR/USD"


def test_higher_timeframes_follow_the_broker_server_clock():
    t = datetime(2026, 11, 17, 21, 40, tzinfo=timezone.utc)
    from zoneinfo import ZoneInfo
    assert bucket_start(t, "H4", ZoneInfo("Etc/UTC")) == datetime(2026, 11, 17, 20, tzinfo=timezone.utc)
    assert bucket_start(t, "D1", ZoneInfo("Etc/GMT-2")) == datetime(2026, 11, 17, 22, tzinfo=timezone.utc) - timedelta(days=1)


def test_every_instrument_has_a_backup_symbol():
    inst = load_instruments(Settings(_env_file=None).resolve(Settings(_env_file=None).instruments_config))
    assert {s: i.backup_symbol for s, i in inst.items()} == {"EURUSD": "EUR/USD", "GBPUSD": "GBP/USD",
                                                               "XAUUSD": "XAU/USD", "US30": "DJI"}
