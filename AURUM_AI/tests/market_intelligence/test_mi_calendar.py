"""Economic calendar: parsing, classification, impact mapping, provider failures, ingestion revisions, point-in-time
views and event-risk states. Provider responses are in-process fixtures (httpx.MockTransport) - no network."""
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest

from aurum.database.connection import engine_from_settings, init_database
from aurum.market_intelligence import repository as repo_mod
from aurum.market_intelligence.economic_calendar import (EconomicEvent, EventClassifier, ImpactMapper, event_state,
                                                         instrument_event_risk, parse_value, visible_event)
from aurum.market_intelligence.http import ProviderError
from aurum.market_intelligence.module_adapters import Module02Adapter, NullModule03
from aurum.market_intelligence.providers import FinnhubProvider, FmpProvider, OfficialScheduleProvider
from aurum.market_intelligence.service import MarketIntelligenceService
from mi_helpers import cfg, mock_http, settings, source

UTC = timezone.utc
C = cfg()
NOW = datetime(2026, 3, 11, 12, 0, tzinfo=UTC)
KEY = "TEST-KEY-9f8e7d"

FMP_ROWS = [
    {"date": "2026-03-11 12:30:00", "country": "US", "event": "CPI (MoM) (Feb)", "currency": "USD", "previous": "0.2%",
     "estimate": "0.3%", "actual": None, "impact": "High", "unit": "%"},
    {"date": "2026-03-11 10:00:00", "country": "EU", "event": "Industrial Production (MoM)", "currency": "EUR",
     "previous": "-0.4", "estimate": "", "actual": "0.8", "impact": "Medium"},
    {"date": "2026-03-12 13:30:00", "country": "US", "event": "Initial Jobless Claims", "currency": "USD",
     "previous": "221K", "estimate": "225K", "actual": None, "impact": "Medium"},
]


# ---------------------------------------------------------------------------------------------- parsing
@pytest.mark.parametrize("raw,expected", [("0.3%", 0.3), ("215K", 215000.0), ("-0.1", -0.1), ("1,234.5", 1234.5),
                                          (2, 2.0), ("", None), (None, None), ("-", None), ("n/a", None)])
def test_parse_value_never_turns_missing_into_zero(raw, expected):
    assert parse_value(raw) == expected


def _ev(name, currency="USD", importance=None, at=NOW + timedelta(hours=1), country=None, **kw):
    return EconomicEvent(provider="t", provider_event_id=name, name=name, country=country, currency=currency,
                         scheduled_at=at, importance=importance, **kw)


def test_classifier_needs_pattern_and_currency_and_keeps_provider_importance():
    cl = EventClassifier(C)
    cpi = cl.classify(_ev("CPI (YoY)"))
    assert (cpi.category, cpi.subcategory, cpi.importance) == ("inflation", "cpi", "high")
    assert cpi.importance_source.startswith("rule")
    jp = cl.classify(_ev("Nonfarm Payrolls", currency="JPY"))      # NFP rule is USD-only
    assert jp.subcategory != "nonfarm_payrolls"
    own = cl.classify(_ev("CPI (YoY)", importance="low"))
    assert own.importance == "low" and own.importance_source == "provider"


def test_country_is_mapped_to_currency():
    assert EventClassifier(C).currency_for("US", None) == "USD"
    assert EventClassifier(C).currency_for("Atlantis", None) is None          # unknown country: no guess
    assert EventClassifier(C).classify(_ev("CPI (YoY)", currency=None, country="US")).currency == "USD"


def test_impact_mapping_relevance_only_never_direction():
    m = ImpactMapper(C)
    usd = m.affected(EventClassifier(C).classify(_ev("CPI (YoY)")))
    names = {a["instrument"] for a in usd}
    assert {"EURUSD", "GBPUSD", "XAUUSD", "US30"} <= names
    assert all(a["direction"] is None for a in usd)
    gbp = {a["instrument"] for a in m.affected(EventClassifier(C).classify(_ev("CPI (YoY)", currency="GBP")))}
    assert "GBPUSD" in gbp and "US30" not in gbp and "EURUSD" not in gbp


# ---------------------------------------------------------------------------------------------- providers
def test_fmp_parses_values_and_keeps_missing_as_none():
    seen = {}

    def h(req: httpx.Request):
        seen.update(dict(req.url.params))
        assert req.url.path == "/stable/economic-calendar"
        assert "AURUM-AI" in req.headers["User-Agent"]
        return httpx.Response(200, json=FMP_ROWS)

    ev = FmpProvider(KEY, http=mock_http(h, [KEY])).fetch(date(2026, 3, 10), date(2026, 3, 13))
    cpi = next(e for e in ev if e.name.startswith("CPI"))
    assert cpi.consensus == 0.3 and cpi.previous == 0.2 and cpi.actual is None and cpi.status == "scheduled"
    assert cpi.scheduled_at == datetime(2026, 3, 11, 12, 30, tzinfo=UTC) and cpi.importance == "high"
    ip = next(e for e in ev if e.name.startswith("Industrial"))
    assert ip.consensus is None and ip.actual == 0.8 and ip.status == "released"
    assert seen["apikey"] == KEY and seen["from"] == "2026-03-10"


def test_auth_failure_is_reported_without_leaking_the_key():
    def h(req):
        return httpx.Response(403, text=f"forbidden for key {KEY}")
    with pytest.raises(ProviderError) as ei:
        FmpProvider(KEY, http=mock_http(h, [KEY])).fetch(date(2026, 3, 10), date(2026, 3, 13))
    assert ei.value.kind == "auth" and KEY not in str(ei.value) and "apikey" not in str(ei.value)


def test_rate_limit_retries_honour_retry_after_then_fail():
    calls = []

    def h(req):
        calls.append(1)
        return httpx.Response(429, headers={"Retry-After": "7"})
    http = mock_http(h, [KEY])
    with pytest.raises(ProviderError) as ei:
        FmpProvider(KEY, http=http).fetch(date(2026, 3, 10), date(2026, 3, 13))
    assert ei.value.kind == "rate_limited" and len(calls) == 3 and http.slept == [7.0, 7.0]


def test_network_timeout_becomes_provider_error():
    def h(req):
        raise httpx.ConnectTimeout("timed out", request=req)
    with pytest.raises(ProviderError) as ei:
        FinnhubProvider(KEY, http=mock_http(h, [KEY])).fetch(date(2026, 3, 10), date(2026, 3, 13))
    assert ei.value.kind == "timeout"


def test_finnhub_without_premium_list_is_a_parse_error_not_empty_success():
    with pytest.raises(ProviderError):
        FinnhubProvider(KEY, http=mock_http(lambda r: httpx.Response(200, json={"error": "premium"}), [KEY])).fetch(
            date(2026, 3, 10), date(2026, 3, 13))


def test_official_schedule_uses_configured_meetings_only():
    ev = OfficialScheduleProvider(C.banks).fetch(date(2026, 12, 1), date(2026, 12, 31))
    assert {e.provider_event_id for e in ev} >= {"fed-2026-12-09", "ecb-2026-12-17", "boe-2026-12-17"}
    assert all(e.actual is None and e.consensus is None for e in ev)
    prec = {e.provider_event_id.split("-")[0]: e.time_precision for e in ev}
    # Fed/ECB/BoE/RBA/BoC: usual time (typical); SNB: exact time from its iCal; BoJ: no fixed time (date_only)
    assert prec["fed"] == prec["ecb"] == prec["boe"] == "typical"
    assert prec["snb"] == "exact" and prec["boj"] == "date_only"
    boj = next(e for e in ev if e.provider_event_id == "boj-2026-12-18")
    assert boj.scheduled_at == datetime(2026, 12, 17, 15, 0, tzinfo=UTC)        # 00:00 Asia/Tokyo


# ---------------------------------------------------------------------------------------------- ingestion
@pytest.fixture
def svc_factory(tmp_path, monkeypatch):
    clock = {"t": NOW}
    monkeypatch.setattr(repo_mod, "_now", lambda: clock["t"])
    st = settings(tmp_path, economic_calendar_provider="fmp", economic_calendar_api_key=KEY)
    engine = engine_from_settings(st)
    init_database(engine)

    def make(rows_fn):
        def h(req):
            return httpx.Response(200, json=rows_fn())
        return MarketIntelligenceService(st, engine, C, source(), NullModule03(), Module02Adapter(None),
                                         http_factory=lambda secrets: mock_http(h, secrets), now=lambda: clock["t"])
    return make, clock


def test_ingestion_is_idempotent_and_records_revisions(svc_factory):
    make, clock = svc_factory
    rows = [dict(r) for r in FMP_ROWS]
    svc = make(lambda: rows)
    first = next(p for p in svc.refresh_calendar(2, 3)["providers"] if p["provider"] == "fmp")
    assert first["status"] == "success" and first["inserted"] == 3
    again = next(p for p in svc.refresh_calendar(2, 3)["providers"] if p["provider"] == "fmp")
    assert again["inserted"] == 0 and again["updated"] == 0 and again["unchanged"] == 3
    # CPI is released 40 minutes later
    clock["t"] = NOW + timedelta(minutes=40)
    rows[0] = {**rows[0], "actual": "0.4%"}
    upd = next(p for p in svc.refresh_calendar(2, 3)["providers"] if p["provider"] == "fmp")
    assert upd["updated"] == 1
    cpi = next(e for e in svc.calendar(NOW - timedelta(days=1), NOW + timedelta(days=2))["items"] if e["name"].startswith("CPI"))
    assert cpi["actual"] == 0.4 and cpi["actual_first_seen_at"] is not None
    detail = svc.repo.event(cpi["id"])
    assert [r["change_type"] for r in detail["revisions"]] == ["created", "values"]


def test_point_in_time_view_hides_later_actuals_and_later_events(svc_factory):
    make, clock = svc_factory
    rows = [dict(r) for r in FMP_ROWS]
    svc = make(lambda: rows)
    svc.refresh_calendar(2, 3)
    clock["t"] = NOW + timedelta(minutes=40)
    rows[0] = {**rows[0], "actual": "0.4%"}
    rows.append({"date": "2026-03-12 15:00:00", "country": "US", "event": "Crude Oil Inventories", "currency": "USD",
                 "impact": "Low"})
    svc.refresh_calendar(2, 3)
    view = svc.calendar(NOW - timedelta(days=1), NOW + timedelta(days=2), as_of=NOW + timedelta(minutes=10))
    names = [e["name"] for e in view["items"]]
    assert view["point_in_time"] is True and "Crude Oil Inventories" not in names     # first seen later
    cpi = next(e for e in view["items"] if e["name"].startswith("CPI"))
    assert cpi["actual"] is None                                                      # not yet released at as_of


def test_rescheduled_event_is_marked_delayed(svc_factory):
    make, clock = svc_factory
    rows = [dict(r) for r in FMP_ROWS]
    svc = make(lambda: rows)
    svc.refresh_calendar(2, 3)
    rows[2] = {**rows[2], "date": "2026-03-12 15:00:00"}                               # same day -> same derived id
    svc.refresh_calendar(2, 3)
    claims = next(e for e in svc.calendar(NOW, NOW + timedelta(days=3))["items"] if "Claims" in e["name"])
    assert claims["status"] == "delayed" and claims["original_scheduled_at"].hour == 13


def test_failed_provider_is_logged_and_coverage_stays_partial(tmp_path):
    st = settings(tmp_path, economic_calendar_provider="fmp", economic_calendar_api_key=KEY)
    engine = engine_from_settings(st)
    init_database(engine)
    svc = MarketIntelligenceService(st, engine, C, source(), NullModule03(), Module02Adapter(None),
                                    http_factory=lambda s: mock_http(lambda r: httpx.Response(401), s), now=lambda: NOW)
    res = svc.refresh_calendar(1, 1)
    fmp = next(p for p in res["providers"] if p["provider"] == "fmp")
    assert fmp["status"] == "failed" and fmp["kind"] == "auth" and KEY not in fmp["error"]
    assert res["coverage"]["complete"] is False
    log = svc.repo.ingestion_logs(50)
    assert any(x["provider"] == "fmp" and x["status"] == "failed" for x in log)
    assert all(KEY not in str(x) for x in log)
    assert svc.event_risk("EURUSD", NOW)["state"] == "UNKNOWN"


def test_full_coverage_allows_normal_and_detects_approaching(svc_factory):
    make, clock = svc_factory
    svc = make(lambda: FMP_ROWS)
    svc.refresh_calendar(2, 3)
    assert svc.calendar_coverage(NOW)["level"] == "full"
    r = svc.event_risk("EURUSD", NOW)
    assert r["state"] == "HIGH_EVENT_RISK" and r["next_event"]["name"].startswith("CPI")   # 30 min to US CPI
    quiet = svc.event_risk("EURUSD", NOW + timedelta(days=6))
    assert quiet["state"] in ("NORMAL", "UNKNOWN")      # stale after 36 h -> UNKNOWN, never silently NORMAL
    assert svc.calendar_coverage(NOW + timedelta(days=6))["complete"] is False


# ---------------------------------------------------------------------------------------------- states
def _d(minutes, importance="high", **kw):
    return {"name": "X", "importance": importance, "scheduled_at": NOW + timedelta(minutes=minutes),
            "status": "scheduled", "time_precision": "exact", **kw}


@pytest.mark.parametrize("mins,state", [(2000, "NORMAL"), (600, "EVENT_APPROACHING"), (45, "HIGH_EVENT_RISK"),
                                        (0, "RELEASE_WINDOW"), (-3, "RELEASE_WINDOW"), (-30, "POST_EVENT_VOLATILITY"),
                                        (-90, "NORMAL")])
def test_event_state_windows_for_high_importance(mins, state):
    assert event_state(C, _d(mins), NOW)["state"] == state


def test_cancelled_event_carries_no_risk():
    assert event_state(C, _d(10, status="cancelled"), NOW)["state"] == "NORMAL"


def test_partial_coverage_without_events_is_unknown_not_normal():
    cov = {"complete": False, "level": "partial", "reason": "partial"}
    assert instrument_event_risk(C, "EURUSD", [], NOW, cov)["state"] == "UNKNOWN"
    assert instrument_event_risk(C, "EURUSD", [], NOW, {"complete": True})["state"] == "NORMAL"


def test_visible_event_hides_actual_before_release():
    ev = {**_d(30), "actual": 0.4, "status": "released"}
    v = visible_event(ev, NOW)
    assert v["actual"] is None and v["status"] == "scheduled"
    assert visible_event(ev, NOW + timedelta(hours=1))["actual"] == 0.4
