"""Module 04 - official keyless sources added to remove limitations: agency release calendars, "official" calendar
coverage, BIS policy rates, Eurostat/BLS/Treasury/ONS series, meeting-schedule checks, headline tone, caches.

All provider payloads are in-process fixtures shaped like the agencies' published formats (httpx.MockTransport);
no network is used."""
from datetime import date, datetime, timedelta, timezone

import httpx
import pandas as pd
import pytest

from aurum.database.connection import engine_from_settings, init_database
from aurum.market_intelligence import meetings as MT
from aurum.market_intelligence import repository as repo_mod
from aurum.market_intelligence.candles import RepositoryCandleSource, normalise, window
from aurum.market_intelligence.http import ProviderError
from aurum.market_intelligence.macro import RateSources, SeriesSources, announcement_at, known_from, next_meeting
from aurum.market_intelligence.module_adapters import Module02Adapter, Module03Adapter, NullModule03
from aurum.market_intelligence.official_calendars import (BlsIcsProvider, CensusCalendarProvider,
                                                         EurostatIcalProvider, OnsReleasesProvider, parse_ics)
from aurum.market_intelligence.sentiment import headline_tone, summarise
from aurum.market_intelligence.service import MarketIntelligenceService
from mi_helpers import cfg, mock_http, settings, source, walk

UTC = timezone.utc
C = cfg()
NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)

BLS_ICS = """BEGIN:VCALENDAR\r
X-WR-TIMEZONE:US-Eastern\r
BEGIN:VEVENT\r
UID:abc-1\r
DTSTART;TZID=US-Eastern:20261106T083000\r
SUMMARY:Employment Situation\r
LOCATION:Washington\\, DC\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:abc-2\r
DTSTART;TZID=US-Eastern:20261110T083000\r
SUMMARY:Consumer Price \r
 Index\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:abc-3\r
DTSTART;TZID=US-Eastern:20261112T100000\r
SUMMARY:Metropolitan Area Employment and Unemployment (Monthly)\r
END:VEVENT\r
END:VCALENDAR\r
"""
BEA_ICS = """BEGIN:VCALENDAR
BEGIN:VEVENT
SUMMARY:GDP (Second Estimate) and Corporate Profits\\, 3rd Quarter 2026
DTSTART;VALUE=DATE-TIME:20261125T133000Z
UID:bea-1
END:VEVENT
BEGIN:VEVENT
SUMMARY:Personal Income and Outlays\\, October 2026
DTSTART;VALUE=DATE-TIME:20261125T133000Z
UID:bea-2
END:VEVENT
END:VCALENDAR
"""
EUROSTAT_ICS = """BEGIN:VCALENDAR
BEGIN:VEVENT
DTSTART;VALUE=DATE:20261104
SUMMARY:Flash estimate inflation euro area
X-CATEGORY:Data release\\,Euro indicators release\\,Statistics Explained
UID:es-1
END:VEVENT
BEGIN:VEVENT
DTSTART;VALUE=DATE:20261105
SUMMARY:Unemployment
X-CATEGORY:News article
UID:es-2
END:VEVENT
END:VCALENDAR
"""
CENSUS_HTML = """<table><tr height="20">
<td height="20"><a href="/retail/index.html">Advance Monthly Sales for Retail and
  Food Services</a></td>
<td sorttable_customkey="202611170830">November 17, 2026</td>
<td>8:30 AM</td>
<td>October 2026</td>
<td class="hiden">A202611170830</td></tr>
<tr height="20"><td height="20"><a href="/x">Quarterly Services Survey</a></td>
<td sorttable_customkey="202611201000">November 20, 2026</td><td>10:00 AM</td><td>Q3 2026</td></tr></table>"""


def ons_payload(rows):
    return {"took": 1, "breakdown": {}, "releases": [
        {"uri": f"/releases/{i}", "date_changes": None,
         "description": {"title": t, "release_date": d, "published": False, "cancelled": c, "postponed": p,
                         "finalised": True}} for i, (t, d, c, p) in enumerate(rows)]}


ONS_ROWS = [("Consumer price inflation, UK: October 2026", "2026-11-18T07:00:00.000Z", False, False),
            ("Consumer price inflation, UK: October 2026 time series", "2026-11-18T07:00:00.000Z", False, False),
            ("UK Labour Market: November 2026", "2026-11-17T07:00:00.000Z", False, True),
            ("Retail sales, Great Britain: October 2026", "2026-11-20T07:00:00.000Z", True, False)]


def agency_handler(fail: set[str] | None = None):
    fail = fail or set()

    def h(req: httpx.Request) -> httpx.Response:
        host, path = req.url.host, req.url.path
        for key in fail:
            if key in host:
                return httpx.Response(503)
        if "bls.gov" in host:
            return httpx.Response(200, text=BLS_ICS)
        if "bea.gov" in host:
            return httpx.Response(200, text=BEA_ICS)
        if "census.gov" in host:
            return httpx.Response(200, text=CENSUS_HTML)
        if "ons.gov.uk" in host:
            return httpx.Response(200, json=ons_payload(ONS_ROWS))
        if "europa.eu" in host:
            return httpx.Response(200, text=EUROSTAT_ICS)
        return httpx.Response(404)
    return h


def prov_cfg(name):
    return C.section("official_calendars")["providers"][name]


# ------------------------------------------------------------------------------------------- iCalendar / agencies
def test_ics_unfolds_lines_unescapes_and_converts_us_eastern():
    ev = parse_ics(BLS_ICS)
    assert ev[1]["SUMMARY"][1] == "Consumer Price Index"                 # folded line joined
    ev = BlsIcsProvider(prov_cfg("bls_ics"), mock_http(agency_handler())).fetch(date(2026, 11, 1), date(2026, 11, 30))
    by = {e.subcategory: e for e in ev}
    assert set(by) == {"nfp", "cpi"}                                     # unmatched release ignored, never guessed
    assert by["nfp"].scheduled_at == datetime(2026, 11, 6, 13, 30, tzinfo=UTC)   # 08:30 EST
    assert by["nfp"].time_precision == "exact" and by["nfp"].importance == "high"
    assert all(e.actual is None and e.consensus is None for e in ev)     # schedules only - nothing invented


def test_bls_time_respects_daylight_saving():
    ics = BLS_ICS.replace("20261106T083000", "20260710T083000")
    h = lambda r: httpx.Response(200, text=ics)  # noqa: E731
    ev = BlsIcsProvider(prov_cfg("bls_ics"), mock_http(h)).fetch(date(2026, 7, 1), date(2026, 7, 31))
    assert ev[0].scheduled_at == datetime(2026, 7, 10, 12, 30, tzinfo=UTC)      # 08:30 EDT


def test_bea_utc_times_and_escaped_titles():
    from aurum.market_intelligence.official_calendars import BeaIcsProvider
    ev = BeaIcsProvider(prov_cfg("bea_ics"), mock_http(agency_handler())).fetch(date(2026, 11, 1), date(2026, 11, 30))
    assert {e.subcategory for e in ev} == {"gdp", "pce"}
    assert all(e.scheduled_at == datetime(2026, 11, 25, 13, 30, tzinfo=UTC) for e in ev)
    assert any(e.name == "GDP (Second Estimate) and Corporate Profits, 3rd Quarter 2026" for e in ev)


def test_eurostat_all_day_dates_get_typical_11_00_luxembourg_and_category_filter():
    ev = EurostatIcalProvider(prov_cfg("eurostat_ical"), mock_http(agency_handler())).fetch(date(2026, 11, 1),
                                                                                           date(2026, 11, 30))
    assert len(ev) == 1                       # "Unemployment" here is a news article, not a euro-indicator release
    assert ev[0].scheduled_at == datetime(2026, 11, 4, 10, 0, tzinfo=UTC)       # 11:00 CET
    assert ev[0].time_precision == "typical" and ev[0].source_timezone == "Europe/Luxembourg"


def test_census_rows_and_year_page_fallback():
    seen = []

    def h(req):
        seen.append(req.url.path)
        if req.url.path.endswith("-2027.html"):
            return httpx.Response(404)
        return httpx.Response(200, text=CENSUS_HTML)
    ev = CensusCalendarProvider(prov_cfg("census_calendar"), mock_http(h)).fetch(date(2026, 11, 1), date(2027, 1, 31))
    assert [e.subcategory for e in ev] == ["retail_sales"]
    assert ev[0].scheduled_at == datetime(2026, 11, 17, 13, 30, tzinfo=UTC)
    assert ev[0].name == "Advance Monthly Sales for Retail and Food Services (October 2026)"
    assert "/economic-indicators/calendar-listview-2026.html" in seen and "/economic-indicators/calendar-listview.html" in seen


def test_census_layout_change_is_an_error_not_empty_success():
    with pytest.raises(ProviderError):
        CensusCalendarProvider(prov_cfg("census_calendar"),
                               mock_http(lambda r: httpx.Response(200, text="<html></html>"))).fetch(
            date(2026, 11, 1), date(2026, 11, 30))


def test_ons_statuses_rules_and_null_results():
    ev = OnsReleasesProvider(prov_cfg("ons_releases"), mock_http(agency_handler())).fetch(date(2026, 11, 1),
                                                                                         date(2026, 11, 30))
    st = {e.subcategory: e.status for e in ev}
    assert st == {"cpi": "scheduled", "labour_market": "delayed", "retail_sales": "cancelled"}
    assert len([e for e in ev if e.subcategory == "cpi"]) == 1           # "time series" companion excluded
    empty = OnsReleasesProvider(prov_cfg("ons_releases"),
                                mock_http(lambda r: httpx.Response(200, json={"releases": None})))
    assert empty.fetch(date(2026, 11, 1), date(2026, 11, 30)) == []
    with pytest.raises(ProviderError):
        OnsReleasesProvider(prov_cfg("ons_releases"), mock_http(lambda r: httpx.Response(200, json={}))).fetch(
            date(2026, 11, 1), date(2026, 11, 30))


# ------------------------------------------------------------------------------------------- official coverage
@pytest.fixture
def official_svc(tmp_path, monkeypatch):
    clock = {"t": NOW}
    monkeypatch.setattr(repo_mod, "_now", lambda: clock["t"])
    st = settings(tmp_path)
    engine = engine_from_settings(st)
    init_database(engine)

    def make(fail=None):
        return MarketIntelligenceService(st, engine, C, source(), NullModule03(), Module02Adapter(None),
                                         http_factory=lambda s: mock_http(agency_handler(fail), s),
                                         now=lambda: clock["t"])
    return make, clock


def test_official_coverage_allows_normal_with_explicit_gaps(official_svc):
    make, clock = official_svc
    svc = make()
    svc.refresh_calendar(2, 30)
    cov = svc.calendar_coverage(NOW)
    assert cov["level"] == "official" and all(v["covered"] for v in cov["currencies"].values())
    clock["t"] = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)              # no release within the windows
    r = svc.event_risk("EURUSD", clock["t"])
    assert r["state"] == "NORMAL" and r["coverage"]["complete"] is True
    assert r["coverage"]["symbol_currencies"] == ["EUR", "USD"] and r["coverage"]["gaps"]["USD"]
    assert "official schedules only" in r["reason"]


def test_official_coverage_detects_an_agency_release(official_svc):
    make, clock = official_svc
    svc = make()
    svc.refresh_calendar(2, 30)
    t = datetime(2026, 11, 6, 13, 20, tzinfo=UTC)                     # 10 min before US payrolls
    clock["t"] = t
    r = svc.event_risk("EURUSD", t)
    assert r["state"] in ("HIGH_EVENT_RISK", "RELEASE_WINDOW") and "Employment Situation" in r["reason"]


def test_failed_agency_leaves_its_currency_uncovered_and_risk_unknown(official_svc):
    make, clock = official_svc
    svc = make(fail={"bea.gov"})
    res = svc.refresh_calendar(2, 30)
    assert next(p for p in res["providers"] if p["provider"] == "bea_ics")["status"] == "failed"
    cov = svc.calendar_coverage(NOW)
    assert cov["currencies"]["USD"]["covered"] is False and "bea_ics" in cov["currencies"]["USD"]["missing"]
    clock["t"] = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
    r = svc.event_risk("EURUSD", clock["t"])
    assert r["state"] == "UNKNOWN" and r["coverage"]["uncovered_currencies"] == ["USD"]


def test_official_coverage_is_point_in_time(official_svc):
    make, clock = official_svc
    svc = make()
    svc.refresh_calendar(2, 30)
    assert svc.calendar_coverage(NOW - timedelta(days=1))["level"] in ("none", "partial")   # before the download
    assert svc.calendar_coverage(NOW + timedelta(days=8))["level"] in ("none", "partial")   # stale after 168 h


# ------------------------------------------------------------------------------------------- rates & meetings
def test_bis_policy_rate_parser():
    csv_text = ("REF_AREA,TIME_PERIOD,OBS_VALUE,COMPILATION\n"
                "JP,2026-09-23,0.75,x\nJP,2026-09-24,1.25,From 24 Sep 2026: call rate around 1.25 percent\n"
                "JP,2026-09-25,,\n")
    obs = RateSources(mock_http(lambda r: httpx.Response(200, text=csv_text))).fetch("bis_cbpol", "boj",
                                                                                    date(2026, 1, 1))
    assert [(o.effective_date.isoformat(), o.rate) for o in obs] == [("2026-09-23", 0.75), ("2026-09-24", 1.25)]
    assert "1.25 percent" in obs[-1].detail["COMPILATION"]
    with pytest.raises(ProviderError):
        RateSources(mock_http(lambda r: httpx.Response(200, text=csv_text))).fetch("bis_cbpol", "fed", date(2026, 1, 1))


def test_date_only_meetings_are_known_only_at_end_of_day():
    boj = C.banks["banks"]["boj"]
    at, precision = announcement_at(boj, date(2026, 9, 18))
    assert precision == "date_only" and at == datetime(2026, 9, 17, 15, 0, tzinfo=UTC)          # 00:00 Tokyo
    kf, meeting = known_from(boj, date(2026, 9, 18))
    assert meeting == date(2026, 9, 18) and kf == datetime(2026, 9, 18, 14, 59, tzinfo=UTC)     # 23:59 Tokyo
    nm = next_meeting(boj, datetime(2026, 12, 18, 3, 0, tzinfo=UTC))   # meeting day itself, decision may be pending
    assert nm["date"] == "2026-12-18" and nm["time_precision"] == "date_only"


FED_HTML = """<h4>2027 FOMC Meetings</h4><div>January 26-27</div><div>March 16-17*</div>
<div>Apr/May 30-1</div><p>* Meeting associated with a Summary of Economic Projections.</p>
<p>Note: A two-day meeting is scheduled for January 25-26, 2028.</p>
<h4>2023 FOMC Meetings</h4><div>Jan/Feb 31-1</div><div>Statement: PDF</div><div>Back to Top</div>"""
BOJ_HTML = """2026 Table : 2026 Date of MPM Release Schedule Jan. 22 (Thurs.), 23 (Fri.) Jan. 23 (Fri.)
June 15 (Mon.), 16 (Tues.) - June 24 (Wed.) Note: x"""
RBA_HTML = """Board meeting schedules 2026 Month Monetary Policy Board Payments System Board February 2–3 February
March 16–17 March 5 March March 31 March–1 April"""


def test_meeting_page_parsers():
    assert MT.parse_fed(FED_HTML) == [date(2023, 2, 1), date(2027, 1, 27), date(2027, 3, 17), date(2027, 5, 1)]
    assert MT.parse_boj(BOJ_HTML) == [date(2026, 1, 23), date(2026, 6, 16)]
    assert MT.parse_rba(RBA_HTML) == [date(2026, 2, 3), date(2026, 3, 17), date(2026, 4, 1)]


def test_compare_overlay_and_staleness(tmp_path):
    c = MT.compare(["2026-12-09", "2026-12-10"], [date(2026, 12, 9), date(2027, 1, 27)])
    assert c["missing_from_config"] == ["2027-01-27"] and c["not_on_official_page"] == ["2026-12-10"]
    banks = {"banks": {"x": {"meetings": ["2026-12-09"]}}}
    merged = MT.merge_overlay(banks, {"banks": {"x": {"meetings": ["2027-01-27"], "source": "u"}}})
    assert merged["banks"]["x"]["meetings"] == ["2026-12-09", "2027-01-27"]          # union, nothing removed
    assert MT.staleness({"meetings": ["2026-12-09"]}, date(2026, 11, 3))["status"] == "running_out"
    assert MT.staleness({"meetings": []}, date(2026, 11, 3))["status"] == "none"
    assert MT.staleness({"meetings": ["2026-01-01"]}, date(2026, 11, 3))["status"] == "expired"
    path = MT.write_overlay({"fed": {"status": "ok", "official": ["2027-01-27"], "source": "u"},
                             "rbnz": {"status": "unavailable"}}, tmp_path / "o.yaml", NOW)
    assert "fed" in path.read_text() and "rbnz" not in path.read_text()


def test_rbnz_schedule_is_reported_unavailable_not_guessed():
    res = MT.check({"banks": {"rbnz": {"meetings": []}}}, mock_http(lambda r: httpx.Response(403)), date(2026, 11, 3))
    assert res["rbnz"]["status"] == "unavailable" and res["rbnz"]["staleness"]["status"] == "none"


# ------------------------------------------------------------------------------------------- indicator series
def test_eurostat_json_stat_and_single_series_guard():
    js = {"id": ["freq", "unit", "geo", "time"], "size": [1, 1, 1, 3],
          "dimension": {"time": {"category": {"index": {"2026-07": 0, "2026-08": 1, "2026-09": 2}}}},
          "value": {"0": 2.9, "2": 3.8}}
    rows = SeriesSources(mock_http(lambda r: httpx.Response(200, json=js))).fetch(
        {"source": "eurostat", "dataset": "prc_hicp_minr", "params": {"geo": "EA"}}, date(2026, 1, 1))
    assert [(r["period"], r["value"]) for r in rows] == [("2026-07", 2.9), ("2026-09", 3.8)]   # gap stays a gap
    bad = {**js, "size": [1, 2, 1, 3]}
    with pytest.raises(ProviderError):
        SeriesSources(mock_http(lambda r: httpx.Response(200, json=bad))).eurostat("d", {}, date(2026, 1, 1))


def test_bls_treasury_and_ons_series():
    bls = {"status": "REQUEST_SUCCEEDED", "message": [], "Results": {"series": [{"data": [
        {"year": "2026", "period": "M09", "value": "336.298"}, {"year": "2026", "period": "M13", "value": "1"},
        {"year": "2026", "period": "M08", "value": "-"}]}]}}
    rows = SeriesSources(mock_http(lambda r: httpx.Response(200, json=bls))).bls("CUSR0000SA0", date(2026, 1, 1))
    assert [(r["period"], r["value"]) for r in rows] == [("2026-09", 336.298)]
    limited = {"status": "REQUEST_NOT_PROCESSED", "message": ["daily threshold reached"]}
    with pytest.raises(ProviderError) as ei:
        SeriesSources(mock_http(lambda r: httpx.Response(200, json=limited))).bls("X", date(2026, 1, 1))
    assert ei.value.kind == "rate_limited"
    csv_text = 'Date,"2 Yr","10 Yr"\n11/02/2026,4.59,4.91\n10/30/2026,4.57,\n'
    t = SeriesSources(mock_http(lambda r: httpx.Response(200, text=csv_text))).treasury("10 Yr", date(2026, 10, 1))
    assert [(r["period"], r["value"]) for r in t] == [("2026-11-02", 4.91)]
    ons = {"months": [{"date": "2026 SEP", "value": "3.5"}, {"date": "2026 AUG", "value": ""}]}
    o = SeriesSources(mock_http(lambda r: httpx.Response(200, json=ons))).ons("/x/timeseries/d7g7/mm23",
                                                                            date(2026, 1, 1))
    assert [(r["period"], r["value"]) for r in o] == [("2026-09", 3.5)]


# ------------------------------------------------------------------------------------------- headline tone
@pytest.mark.parametrize("headline,label", [
    ("Bank Rate maintained at 3.75% - September 2026", "neutral"),
    ("ECB cuts key interest rates", "dovish"),
    ("Fed raises the policy rate by 25 basis points", "hawkish"),
    ("Fed will not raise rates this year", "unclear"),               # simple negation is not counted as hawkish
    ("Federal Reserve issues FOMC statement", "unclear"),
])
def test_policy_tone_is_rule_based_and_lists_terms(headline, label):
    r = headline_tone(headline, "central_bank")
    assert r["label"] == label and r["kind"] == "policy_tone" and r["method"] == "lexicon_v1"
    assert bool(r["terms"]) == (label not in ("unclear",))


def test_risk_tone_and_summary():
    assert headline_tone("Stocks rally as payrolls beat forecasts")["label"] == "positive"
    assert headline_tone("Gold slumps on recession fears")["label"] == "negative"
    assert headline_tone("Treasury publishes auction schedule")["label"] == "unclear"
    s = summarise([{"currencies": ["USD"], "tone": {"kind": "policy_tone", "label": "hawkish"}},
                   {"currencies": ["USD"], "tone": {"kind": "policy_tone", "label": "hawkish"}}])
    assert s["by_currency"]["USD"]["policy_tone:hawkish"] == 2


# ------------------------------------------------------------------------------------------- performance / caches
def test_window_slicing_matches_mask():
    df = normalise(walk(datetime(2026, 3, 2, tzinfo=UTC), datetime(2026, 3, 9, tzinfo=UTC), 60), "H1")
    a, b = datetime(2026, 3, 3, 7, 30, tzinfo=UTC), datetime(2026, 3, 4, 16, 0, tzinfo=UTC)
    m = df.loc[(df["ts"] >= pd.Timestamp(a)) & (df["ts"] < pd.Timestamp(b))]
    assert window(df, a, b).equals(m)


def test_repository_source_cuts_at_as_of_and_reloads_incrementally(monkeypatch):
    full = walk(datetime(2026, 3, 2, tzinfo=UTC), datetime(2026, 3, 5, tzinfo=UTC), 60)
    calls = []

    class Repo:                         # Module 01 Repository stand-in without an engine (slow path)
        def load_candles(self, s, tf):
            calls.append(tf)
            return full
    src = RepositoryCandleSource(Repo(), ttl_seconds=-1.0)
    t = datetime(2026, 3, 3, 12, 0, tzinfo=UTC)
    f = src.frame("EURUSD", "H1", t)
    assert f["close_t"].max() <= pd.Timestamp(t) and len(f) > 0
    src.frame("EURUSD", "H1", t)
    assert len(calls) == 2              # expired ttl -> refreshed (incremental path falls back to load_candles)


def test_module03_adapter_caches_per_minute():
    n = {"calls": 0}

    class Fake:
        def analyze(self, *a, **k):
            n["calls"] += 1
            raise RuntimeError("boom")   # failures are never cached
    ad = Module03Adapter(factory=lambda: Fake())
    t = datetime(2026, 3, 3, 12, 0, 5, tzinfo=UTC)
    assert ad.context("EURUSD", "M15", t)["available"] is False
    ad.context("EURUSD", "M15", t + timedelta(seconds=20))
    assert n["calls"] == 2
    ad._cached(("ctx", "EURUSD", "M15", "k"), lambda: {"available": True, "v": 1})
    assert ad._cached(("ctx", "EURUSD", "M15", "k"), lambda: {"available": True, "v": 2})["v"] == 1


def test_analysis_cache_hit_and_invalidation(official_svc):
    make, clock = official_svc
    svc = make()
    a = svc.analyze("EURUSD", datetime(2026, 3, 10, 12, 0, 10, tzinfo=UTC))
    b = svc.analyze("EURUSD", datetime(2026, 3, 10, 12, 0, 40, tzinfo=UTC))
    assert a["cache"]["hit"] is False and b["cache"]["hit"] is True
    assert b["levels"] == a["levels"] and b["volatility"] == a["volatility"]
    svc.refresh_news()
    assert svc.analyze("EURUSD", datetime(2026, 3, 10, 12, 0, 50, tzinfo=UTC))["cache"]["hit"] is False
    assert svc.analyze("EURUSD", datetime(2026, 3, 10, 12, 0, 50, tzinfo=UTC), persist=True)["cache"]["hit"] is False


def test_eurostat_events_keep_their_id_when_the_feed_regenerates_uids():
    n = {"i": 0}

    def h(req):
        n["i"] += 1
        return httpx.Response(200, text=EUROSTAT_ICS.replace("UID:es-1", f"UID:regenerated-{n['i']}"))
    p = EurostatIcalProvider(prov_cfg("eurostat_ical"), mock_http(h))
    a = [e.provider_event_id for e in p.fetch(date(2026, 11, 1), date(2026, 11, 30))]
    b = [e.provider_event_id for e in p.fetch(date(2026, 11, 1), date(2026, 11, 30))]
    assert a == b == ["Flash estimate inflation euro area|2026-11-04"]


def test_long_provider_ids_fit_the_column_deterministically():
    from aurum.market_intelligence.official_calendars import stable_id
    long = "x" * 200
    assert len(stable_id(long)) == 96 and stable_id(long) == stable_id(long) and stable_id("short") == "short"
