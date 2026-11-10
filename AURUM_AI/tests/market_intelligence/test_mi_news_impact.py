"""High-impact news: conditional scenarios, measured past reactions, outcome after release, headline effects."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
from cot_helpers import cot_cfg
from fastapi.testclient import TestClient

from aurum.api.app import create_app
from aurum.database.connection import engine_from_settings, init_database
from aurum.market_intelligence.candles import FrameCandleSource
from aurum.market_intelligence.economic_calendar import EconomicEvent, EventClassifier, ImpactMapper
from aurum.market_intelligence.module_adapters import Module02Adapter, NullModule03
from aurum.market_intelligence.news_impact import NewsImpact, classify_surprise, instrument_direction
from aurum.market_intelligence.service import MarketIntelligenceService
from mi_helpers import cfg, settings

UTC = timezone.utc
NOW = datetime(2026, 3, 11, 12, 0, tzinfo=UTC)
C = cfg()
# Past US CPI releases: (time, actual, consensus). Two beats, one miss.
PAST = [(datetime(2026, 1, 13, 13, 30, tzinfo=UTC), 3.3, 3.1), (datetime(2026, 2, 11, 13, 30, tzinfo=UTC), 3.2, 3.0),
        (datetime(2026, 2, 25, 13, 30, tzinfo=UTC), 2.8, 3.0)]


def candles() -> FrameCandleSource:
    """Flat EURUSD at 1.1000; after a beat it drops 0.2% within the hour, after a miss it rises 0.1%."""
    ts = pd.date_range(datetime(2026, 1, 12, tzinfo=UTC), NOW + timedelta(hours=1), freq="5min", tz="UTC", inclusive="left")
    close = pd.Series(1.1, index=ts)
    for t, actual, cons in PAST + [(NOW - timedelta(minutes=45), 3.4, 3.1)]:
        after = (ts >= t) & (ts < t + timedelta(hours=1))
        close[after] = 1.1 * (0.998 if actual > cons else 1.001)
    df = pd.DataFrame({"ts_utc": ts, "open": close.values, "high": close.values + 1e-5, "low": close.values - 1e-5,
                       "close": close.values, "tick_volume": 100, "is_complete": True})
    return FrameCandleSource({("EURUSD", "M5"): df})


def ev(i: int, at: datetime, actual=None, consensus=3.1, previous=3.0, name="CPI (YoY)", currency="USD") -> dict:
    e = EventClassifier(C).classify(EconomicEvent(provider="t", provider_event_id=str(i), name=name, country="US",
                                                  currency=currency, scheduled_at=at, actual=actual, consensus=consensus,
                                                  previous=previous))
    affected = ImpactMapper(C).affected(e)
    return {"id": i, "name": name, "currency": currency, "country": "US", "category": e.category, "subcategory": e.subcategory,
            "importance": e.importance, "scheduled_at": at, "actual": actual, "consensus": consensus, "previous": previous,
            "unit": "%", "status": "released" if actual is not None else "scheduled", "time_precision": "exact",
            "affected_instruments": affected, "source_url": None}


class Repo:
    def __init__(self, events, news=()):
        self._events, self._news = events, list(news)

    def events(self, start=None, end=None, *, currencies=None, importance=None, **_):
        out = [e for e in self._events if (start is None or e["scheduled_at"] >= start) and (end is None or e["scheduled_at"] < end)
               and (not currencies or e["currency"] in currencies) and (not importance or e["importance"] in importance)]
        return out, len(out)

    def news(self, as_of, currencies, limit, offset, since=None):
        out = [n for n in self._news if (not currencies or set(currencies) & set(n["currencies"]))
               and (since is None or n["published_at"] >= since)]
        return out, len(out)


def impact(events, news=()) -> NewsImpact:
    return NewsImpact(Repo(events, news), candles(), ImpactMapper(C), lambda: ["EURUSD", "XAUUSD", "US30"], lambda: NOW)


def test_direction_rules():
    assert instrument_direction("EURUSD", "USD", 1)[0] == "down"
    assert instrument_direction("EURUSD", "EUR", 1)[0] == "up"
    assert instrument_direction("XAUUSD", "USD", -1)[0] == "up"
    assert instrument_direction("US30", "USD", 1)[0] == "mixed"
    assert instrument_direction("GBPUSD", "EUR", 1)[0] is None
    assert [classify_surprise(3.2, 3.1), classify_surprise(3.1, 3.1), classify_surprise(3.0, 3.1)] == ["above", "inline", "below"]


def test_upcoming_event_gets_expectation_scenarios_and_measured_history():
    past = [ev(i, t, a, c) for i, (t, a, c) in enumerate(PAST, start=1)]
    nxt = ev(10, NOW + timedelta(hours=3), consensus=3.2, previous=3.0)
    rep = impact(past + [nxt]).report()
    e = next(x for x in rep["events"] if x["id"] == 10)
    assert rep["next_event"]["id"] == 10 and not e["released"] and e["outcome"] is None
    assert e["expectation"]["vs_previous"] == "higher" and e["polarity"] == 1
    above = next(s for s in e["scenarios"] if s["key"] == "above")
    eur = next(r for r in above["instruments"] if r["instrument"] == "EURUSD")
    assert above["currency_effect"] == "bullish" and eur["direction"] == "down"
    # two past beats, EURUSD fell 0.2% after both: the rule held 100% of the time
    assert eur["history"] == {"samples": 2, "avg_move_pct": -0.2, "up_pct": 0, "followed_rule_pct": 100, "minutes": 60}
    below = next(s for s in e["scenarios"] if s["key"] == "below")
    assert next(r for r in below["instruments"] if r["instrument"] == "EURUSD")["history"]["samples"] == 1
    us30 = next(r for r in above["instruments"] if r["instrument"] == "US30")
    assert us30["direction"] == "mixed" and us30["history"] is None   # no stored US30 candles: no history, not a guess
    assert e["history_samples"] == 2


def test_released_event_reports_the_surprise_and_measured_move():
    rep = impact([ev(20, NOW - timedelta(minutes=45), actual=3.4, consensus=3.1)], ).report()
    o = rep["events"][0]["outcome"]
    assert o["scenario"] == "above" and o["surprise"] == 0.3 and o["currency_effect"] == "bullish"
    eur = next(r for r in o["instruments"] if r["instrument"] == "EURUSD")
    assert eur["expected"] == "down" and eur["move_pct"] == -0.2 and eur["followed_rule"] is True and eur["minutes_measured"] == 45


def test_events_without_a_numeric_rule_say_so():
    speech = ev(30, NOW + timedelta(hours=5), consensus=None, previous=None, name="Fed Chair Powell Speaks")
    speech["importance"] = "high"
    e = impact([speech]).report()["events"][0]
    assert e["polarity"] is None and e["scenarios"] == [] and "depends on what is said" in e["rule"]


def test_hawkish_central_bank_headline_maps_to_instruments():
    news = [{"id": 1, "headline": "ECB raises interest rates by 25 basis points", "source": "ECB", "url": "https://x",
             "published_at": NOW - timedelta(hours=2), "category": "central_bank", "currencies": ["EUR"], "description": None},
            {"id": 2, "headline": "Quarterly bulletin published", "source": "BoE", "url": "https://y",
             "published_at": NOW - timedelta(hours=3), "category": "statistics", "currencies": ["GBP"], "description": None}]
    hl = impact([], news).report()["headlines"]
    assert [h["id"] for h in hl] == [1]                         # low-impact wording filtered out
    assert hl[0]["tone"]["label"] == "hawkish" and hl[0]["effect"] == {"currencies": ["EUR"], "bias": "bullish"}
    assert [(r["instrument"], r["direction"]) for r in hl[0]["instruments"]] == [("EURUSD", "up")]  # not US30 / XAUUSD


def test_api_serves_news_impact(tmp_path):
    st = settings(tmp_path)
    engine = engine_from_settings(st)
    init_database(engine)
    svc = MarketIntelligenceService(st, engine, C, candles(), NullModule03(), Module02Adapter(None), now=lambda: NOW)
    run = svc.repo.create_run(C, "calendar", None, NOW)
    e = EventClassifier(C).classify(EconomicEvent(provider="t", provider_event_id="n1", name="CPI (YoY)", country="US",
                                                  currency="USD", scheduled_at=NOW + timedelta(hours=2), consensus=3.2, previous=3.0))
    e.affected_instruments = ImpactMapper(C).affected(e)
    svc.repo.upsert_events([e], run)
    with TestClient(create_app(st, cot_cfg(), engine, intelligence_service=svc)) as c:
        r = c.get("/api/v1/intelligence/news-impact")
        assert r.status_code == 200
        body = r.json()
        assert body["events"][0]["name"] == "CPI (YoY)" and body["nextEvent"]["consensus"] == 3.2
        assert {s["key"] for s in body["events"][0]["scenarios"]} == {"above", "inline", "below"}
        assert c.get("/api/v1/intelligence/news-impact", params={"importance": "huge"}).status_code == 422


def test_older_releases_fall_back_to_m15_candles():
    """M5 covers only the latest release; the two earlier ones exist on M15 and must still be measured."""
    m5 = candles()._frames[("EURUSD", "M5")]
    recent = m5.loc[m5["ts"] >= pd.Timestamp(datetime(2026, 2, 20, tzinfo=UTC))]
    raw = m5.rename(columns={"ts": "ts_utc"})[["ts_utc", "open", "high", "low", "close", "tick_volume"]].assign(is_complete=True)
    src = FrameCandleSource({("EURUSD", "M5"): recent.rename(columns={"ts": "ts_utc"})[["ts_utc", "open", "high", "low", "close", "tick_volume"]]
                             .assign(is_complete=True), ("EURUSD", "M15"): raw.iloc[::3]})
    past = [ev(i, t, a, c) for i, (t, a, c) in enumerate(PAST, start=1)]
    nxt = ev(10, NOW + timedelta(hours=3), consensus=3.2, previous=3.0)
    ni = NewsImpact(Repo(past + [nxt]), src, ImpactMapper(C), lambda: ["EURUSD"], lambda: NOW)
    e = next(x for x in ni.report()["events"] if x["id"] == 10)
    above = next(s for s in e["scenarios"] if s["key"] == "above")
    assert above["instruments"][0]["history"]["samples"] == 2      # both beats measured, one only on M15


def test_a_past_speech_counts_as_released_with_a_note():
    speech = ev(31, NOW - timedelta(hours=2), consensus=None, previous=None, name="Fed Chair Powell Speaks")
    speech["importance"] = "high"
    e = impact([speech]).report()["events"][0]
    assert e["released"] is True and "publishes no figure" in e["outcome"]["note"]
    pending = impact([ev(32, NOW - timedelta(minutes=5), actual=None, consensus=3.1)]).report()["events"][0]
    assert pending["released"] is False                            # a data release waits for its figure


def test_headlines_follow_the_currency_filter():
    news = [{"id": 1, "headline": "ECB raises interest rates by 25 basis points", "source": "ECB", "url": "https://x",
             "published_at": NOW - timedelta(hours=2), "category": "central_bank", "currencies": ["EUR"], "description": None},
            {"id": 2, "headline": "Fed holds rates unchanged", "source": "Fed", "url": "https://y",
             "published_at": NOW - timedelta(hours=1), "category": "central_bank", "currencies": ["USD"], "description": None}]
    assert [h["id"] for h in impact([], news).report(currencies=["USD"])["headlines"]] == [2]
