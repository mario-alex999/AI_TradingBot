"""Volatility regimes, statistics, conditions, scoring, macro point-in-time rules and official news parsing."""
from datetime import date, datetime, timedelta, timezone

import httpx
import numpy as np
import pandas as pd
import pytest

from aurum.market_intelligence.candles import FrameCandleSource
from aurum.market_intelligence.conditions import efficiency_ratio
from aurum.market_intelligence.macro import (RateObservation, RateSources, changes, decisions, fred_known_from,
                                             known_from, next_meeting)
from aurum.market_intelligence.news import OfficialFeedProvider, parse_rss
from aurum.market_intelligence.scoring import event_risk_score, session_activity_score, volatility_score
from aurum.market_intelligence.statistics import session_statistics
from aurum.market_intelligence.volatility import classify_regime, series_volatility, volatility_report
from mi_helpers import cfg, mock_http, source, walk

UTC = timezone.utc
C = cfg()
AS_OF = datetime(2026, 3, 11, 11, 7, tzinfo=UTC)


# ------------------------------------------------------------------------------------------ volatility
@pytest.mark.parametrize("pct,regime", [(5, "VERY_LOW"), (20, "LOW"), (50, "NORMAL"), (80, "HIGH"), (95, "VERY_HIGH"),
                                        (None, "UNKNOWN")])
def test_regime_cutoffs(pct, regime):
    assert classify_regime(pct, {"very_low": 10, "low": 30, "high": 70, "very_high": 90})[0] == regime


def test_volatility_expansion_detected_when_recent_bars_widen():
    df = walk(datetime(2026, 1, 5, tzinfo=UTC), datetime(2026, 3, 1, tzinfo=UTC), 60, step=0.0003)
    tail = df.index[-8:]
    df.loc[tail, "high"] += 0.004
    df.loc[tail, "low"] -= 0.004
    src = FrameCandleSource({("EURUSD", "H1"): df})
    v = series_volatility(src.frame("EURUSD", "H1", datetime(2026, 3, 2, tzinfo=UTC)), "EURUSD", "H1", C)
    assert v["status"] == "ok" and v["expansion_state"] == "expansion" and v["regime"] in ("HIGH", "VERY_HIGH")
    assert v["atr_percentile"] >= 90


def test_insufficient_history_is_unknown_not_guessed():
    df = walk(datetime(2026, 3, 2, tzinfo=UTC), datetime(2026, 3, 3, 12, tzinfo=UTC), 60)
    v = series_volatility(FrameCandleSource({("EURUSD", "H1"): df}).frame("EURUSD", "H1", datetime(2026, 3, 4, tzinfo=UTC)),
                          "EURUSD", "H1", C)
    assert v["status"] == "insufficient_data" and v["regime"] == "UNKNOWN"
    assert v["atr"] is None and v["atr_percentile"] is None and v["abnormal_movement"] is None


def test_volatility_percentile_is_causal():
    full = walk(datetime(2026, 1, 5, tzinfo=UTC), datetime(2026, 3, 14, tzinfo=UTC), 60, step=0.0003)
    later = full.ts_utc >= pd.Timestamp(AS_OF)
    full.loc[later, "high"] += 0.02                              # violent future moves must not change today's rank
    trunc = full[~later]
    a = volatility_report(C, FrameCandleSource({("EURUSD", "H1"): full}), "EURUSD", AS_OF)["timeframes"]["H1"]
    b = volatility_report(C, FrameCandleSource({("EURUSD", "H1"): trunc}), "EURUSD", AS_OF)["timeframes"]["H1"]
    assert a["atr"] == pytest.approx(b["atr"]) and a["atr_percentile"] == pytest.approx(b["atr_percentile"])


def test_efficiency_ratio_trending_vs_ranging():
    assert efficiency_ratio(np.linspace(1.0, 1.1, 30), 20) == pytest.approx(1.0)
    assert efficiency_ratio(np.array([1.0, 1.1] * 15), 20) < 0.1
    assert efficiency_ratio(np.array([1.0, 1.1]), 20) is None


def test_session_statistics_windows_and_sample_flags():
    st = session_statistics(C, source(), "EURUSD", AS_OF)
    lon = st["sessions"]["london"]
    assert set(lon["windows"]) == {"5", "10", "20", "30", "60"}
    assert lon["windows"]["5"]["sufficient"] is True
    assert lon["windows"]["60"]["sample_size"] < 60 and lon["windows"]["60"]["sufficient"] is False  # ~45 sessions stored
    assert all(lon["latest"]["end"] <= AS_OF for _ in [0])


# ------------------------------------------------------------------------------------------ scoring
def test_event_risk_score_unknown_has_no_number():
    assert event_risk_score(C, {"state": "UNKNOWN", "reason": "partial"}, None, None)["score"] is None


def test_event_risk_score_formula_and_bounds():
    ev = {"name": "CPI", "importance": "high", "minutes_until_release": 30.0, "state": "HIGH_EVENT_RISK"}
    base = event_risk_score(C, {"state": "HIGH_EVENT_RISK", "active_events": [ev]}, None, None)
    hot = event_risk_score(C, {"state": "HIGH_EVENT_RISK", "active_events": [ev]}, {"atr_percentile": 100},
                           {"mean_ratio": 2.0, "samples": 5})
    cold = event_risk_score(C, {"state": "HIGH_EVENT_RISK", "active_events": [ev]}, {"atr_percentile": 0},
                            {"mean_ratio": 0.0, "samples": 5})
    assert base["modifier"] == 1.0 and cold["score"] < base["score"] <= hot["score"] <= 100
    assert hot["modifier"] == pytest.approx(1.3) and cold["modifier"] == pytest.approx(0.7)
    assert "probability" in hot["explanation"].lower()


def test_session_activity_excludes_missing_components():
    s = session_activity_score(C, None, {"atr_percentile": 80}, [], [])
    assert s["score"] == pytest.approx(80.0)                     # only the volatility component is available
    assert [c["available"] for c in s["components"]] == [False, True, False]
    assert session_activity_score(C, None, None, [], [])["score"] is None


def test_volatility_score_is_the_percentile():
    assert volatility_score({"atr_percentile": 67.24, "regime": "NORMAL"})["score"] == 67.2
    assert volatility_score(None)["score"] is None


# ------------------------------------------------------------------------------------------ macro
FED = C.banks["banks"]["fed"]


def test_known_from_uses_announcement_time_for_meeting_changes():
    kf, meeting = known_from(FED, date(2026, 9, 17))             # effective the day after the 09-16 FOMC
    assert meeting == date(2026, 9, 16) and kf == datetime(2026, 9, 16, 18, 0, tzinfo=UTC)   # 14:00 EDT


def test_known_from_without_meeting_is_effective_midnight_local():
    kf, meeting = known_from({"timezone": "Australia/Sydney"}, date(2026, 11, 4))
    assert meeting is None and kf == datetime(2026, 11, 3, 13, 0, tzinfo=UTC)


def test_decisions_changed_and_held_from_official_series():
    obs = [RateObservation("fed", date(2026, 9, 1) + timedelta(days=i), 4.0 if i < 16 else 4.25, 3.75 if i < 16 else 4.0,
                           "nyfed_effr_target") for i in range(70)]
    d = decisions("fed", FED, obs)
    changed = [x for x in d if x["status"] == "changed"]
    held = [x for x in d if x["status"] == "held"]
    assert len(changes(obs)) == 2 and len(changed) == 1 and changed[0]["change_bps"] == 25
    assert changed[0]["decision_date"] == date(2026, 9, 16)
    assert [h["decision_date"] for h in held] == [date(2026, 10, 28)]


def test_next_meeting_is_typical_time_and_strictly_future():
    nm = next_meeting(FED, datetime(2026, 12, 9, 19, 0, tzinfo=UTC))
    assert nm["date"] != "2026-12-09" and nm["time_precision"] == "typical"


def test_fred_vintage_known_from_is_end_of_new_york_day():
    assert fred_known_from("2026-03-11") == datetime(2026, 3, 12, 3, 59, tzinfo=UTC)


def test_boc_valet_parser_skips_missing_values():
    body = {"observations": [{"d": "2026-10-28", "V39079": {"v": "2.25"}}, {"d": "2026-10-29", "V39079": {"v": "2.50"}},
                             {"d": "2026-10-30", "V39079": {}}]}
    obs = RateSources(mock_http(lambda r: httpx.Response(200, json=body))).fetch("boc_valet", "boc", date(2026, 1, 1))
    assert [(o.effective_date.day, o.rate) for o in obs] == [(28, 2.25), (29, 2.5)]


# ------------------------------------------------------------------------------------------ news
RSS = """<?xml version="1.0"?><rss><channel>
<item><title>Federal Reserve issues FOMC statement</title><link>https://www.federalreserve.gov/a.htm</link>
<pubDate>Wed, 28 Oct 2026 18:00:00 GMT</pubDate></item>
<item><title></title><link>https://x</link></item>
</channel></rss>"""


def test_rss_headlines_are_kept_verbatim_and_untitled_items_dropped():
    items = parse_rss(RSS)
    assert [i["headline"] for i in items] == ["Federal Reserve issues FOMC statement"]
    assert items[0]["published_at"] == datetime(2026, 10, 28, 18, 0, tzinfo=UTC)


def test_feed_failure_is_reported_not_filled():
    def h(req):
        return httpx.Response(200, text=RSS) if "ok" in str(req.url) else httpx.Response(404)
    feeds = [{"id": "ok_feed", "url": "https://ok.example/rss", "bank": "fed"},
             {"id": "bad_feed", "url": "https://bad.example/rss", "bank": "ecb"}]
    items, errors = OfficialFeedProvider(feeds, mock_http(h)).fetch()
    assert len(items) == 1 and items[0]["source_type"] == "official_announcement" and items[0]["currencies"] == ["USD"]
    assert [e["feed"] for e in errors] == ["bad_feed"]
