"""Economic-calendar providers behind one interface (``fetch(start, end) -> list[EconomicEvent]``).

| provider            | access                           | endpoint (documented)                                          |
|---------------------|----------------------------------|----------------------------------------------------------------|
| official_schedule   | none (config/central_banks.yaml) | dates copied from the banks' official calendar pages            |
| fmp                 | API key; paid plan for calendar  | GET https://financialmodelingprep.com/stable/economic-calendar  |
| finnhub             | API key; premium endpoint        | GET https://finnhub.io/api/v1/calendar/economic                 |
| tradingeconomics    | API key; paid subscription       | GET https://api.tradingeconomics.com/calendar/country/All/{d1}/{d2} |
| fred_release_dates  | free FRED API key                | GET https://api.stlouisfed.org/fred/releases/dates (dates only) |
| bls_ics, bea_ics, census_calendar, ons_releases, eurostat_ical | none | official agency calendars (official_calendars.py) |

A provider without its key is reported as ``not_configured``; nothing is ever fabricated. Field mappings follow each
provider's published response format; every original payload is kept in ``raw`` for audit.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Protocol

from aurum.market_intelligence.config import MIConfig, zone
from aurum.market_intelligence.economic_calendar import (EconomicEvent, derive_event_id, normalise_importance,
                                                          parse_value)
from aurum.market_intelligence.http import HttpClient, ProviderError

log = logging.getLogger(__name__)
UTC = timezone.utc
FULL_CALENDAR_PROVIDERS = ("fmp", "finnhub", "tradingeconomics")


class CalendarProvider(Protocol):
    name: str
    full_calendar: bool        # True = all countries / categories; False = a partial, specialised schedule

    def status(self) -> dict[str, Any]: ...
    def fetch(self, start: date, end: date) -> list[EconomicEvent]: ...


def _parse_utc(s: Any, tz: str | None = None) -> datetime | None:
    if s in (None, ""):
        return None
    txt = str(s).strip().replace("T", " ").replace("Z", "")
    txt = re.sub(r"\.\d+$", "", txt)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            d = datetime.strptime(txt[:19] if fmt.endswith("%S") else txt, fmt)
            return d.replace(tzinfo=zone(tz) if tz else UTC).astimezone(UTC)
        except ValueError:
            continue
    try:
        d = datetime.fromisoformat(str(s))
        return (d if d.tzinfo else d.replace(tzinfo=UTC)).astimezone(UTC)
    except ValueError:
        return None


def _chunks(start: date, end: date, days: int) -> list[tuple[date, date]]:
    out, s = [], start
    while s <= end:
        e = min(end, s + timedelta(days=days - 1))
        out.append((s, e))
        s = e + timedelta(days=1)
    return out


# ----------------------------------------------------------------------------- official central-bank schedule
class OfficialScheduleProvider:
    name = "official_schedule"
    full_calendar = False

    def __init__(self, banks_cfg: dict[str, Any]):
        self.banks = (banks_cfg or {}).get("banks") or {}

    def status(self) -> dict[str, Any]:
        configured = {k: len(v.get("meetings") or []) for k, v in self.banks.items() if v.get("meetings")}
        return {"provider": self.name, "configured": bool(configured), "requires_key": False,
                "coverage": "scheduled monetary-policy decisions of " + ", ".join(sorted(configured)) if configured
                else "none", "meetings": configured,
                "verified_on": {k: v.get("verified_on") for k, v in self.banks.items() if v.get("verified_on")}}

    def fetch(self, start: date, end: date) -> list[EconomicEvent]:
        from aurum.market_intelligence.macro import announcement_at
        out = []
        now = datetime.now(UTC)
        for bank, b in self.banks.items():
            for d in b.get("meetings") or []:
                day = date.fromisoformat(str(d))
                if not (start <= day <= end):
                    continue
                at, precision = announcement_at(b, day)
                note = {"exact": "Time from the bank's official calendar.",
                        "typical": "Official page lists the date only; the time is the bank's usual announcement time.",
                        "date_only": "The bank announces after the meeting ends, at no fixed time; the whole local "
                                     "day is treated as the release window."}[precision]
                out.append(EconomicEvent(
                    provider=self.name, provider_event_id=f"{bank}-{day.isoformat()}",
                    name=f"{b['name']} monetary policy decision", country=None, currency=b["currency"],
                    scheduled_at=at, source_timezone=b["timezone"], time_precision=precision,
                    importance="high", importance_source="official_schedule", category="monetary_policy",
                    subcategory="rate_decision", status="scheduled", source_url=b.get("official_url"),
                    retrieved_at=now,
                    raw={"bank": bank, "date": day.isoformat(), "verified_on": b.get("verified_on"),
                         "time_note": note, "meetings_note": b.get("meetings_note")}))
        return out


# ----------------------------------------------------------------------------- Financial Modeling Prep
class FmpProvider:
    name = "fmp"
    full_calendar = True
    DEFAULT_BASE = "https://financialmodelingprep.com"

    def __init__(self, api_key: str, base_url: str | None = None, http: HttpClient | None = None):
        self.key, self.base = api_key, (base_url or self.DEFAULT_BASE).rstrip("/")
        self.http = http or HttpClient(secrets=[api_key])

    def status(self) -> dict[str, Any]:
        return {"provider": self.name, "configured": bool(self.key), "requires_key": True,
                "access": "API key; the economic calendar needs an FMP plan that includes it"}

    def fetch(self, start: date, end: date) -> list[EconomicEvent]:
        out, now = [], datetime.now(UTC)
        for s, e in _chunks(start, end, 30):
            data = self.http.json(f"{self.base}/stable/economic-calendar",
                                  {"from": s.isoformat(), "to": e.isoformat(), "apikey": self.key})
            if isinstance(data, dict) and ("Error Message" in data or "error" in data):
                raise ProviderError(f"FMP error: {str(data)[:200]}", kind="auth")
            if not isinstance(data, list):
                raise ProviderError("FMP response is not a list of events", kind="parse")
            for r in data:
                at = _parse_utc(r.get("date"))
                if at is None or not r.get("event"):
                    continue
                out.append(EconomicEvent(
                    provider=self.name, provider_event_id=derive_event_id(self.name, r.get("country"), r["event"], at),
                    name=str(r["event"]), country=r.get("country"), currency=r.get("currency"), scheduled_at=at,
                    importance=normalise_importance(r.get("impact")), actual=parse_value(r.get("actual")),
                    consensus=parse_value(r.get("estimate")), previous=parse_value(r.get("previous")),
                    unit=r.get("unit"), status="released" if r.get("actual") not in (None, "") else "scheduled",
                    retrieved_at=now, raw=r))
        return out


# ----------------------------------------------------------------------------- Finnhub
class FinnhubProvider:
    name = "finnhub"
    full_calendar = True
    DEFAULT_BASE = "https://finnhub.io/api/v1"

    def __init__(self, api_key: str, base_url: str | None = None, http: HttpClient | None = None):
        self.key, self.base = api_key, (base_url or self.DEFAULT_BASE).rstrip("/")
        self.http = http or HttpClient(secrets=[api_key])

    def status(self) -> dict[str, Any]:
        return {"provider": self.name, "configured": bool(self.key), "requires_key": True,
                "access": "API key; /calendar/economic is a premium Finnhub endpoint"}

    def fetch(self, start: date, end: date) -> list[EconomicEvent]:
        data = self.http.json(f"{self.base}/calendar/economic", {"from": start.isoformat(), "to": end.isoformat(),
                                                                  "token": self.key})
        rows = data.get("economicCalendar") if isinstance(data, dict) else None
        if rows is None:
            raise ProviderError("Finnhub response has no 'economicCalendar' list (premium access required?)",
                                kind="parse")
        out, now = [], datetime.now(UTC)
        for r in rows:
            at = _parse_utc(r.get("time"))
            if at is None or not r.get("event"):
                continue
            out.append(EconomicEvent(
                provider=self.name, provider_event_id=derive_event_id(self.name, r.get("country"), r["event"], at),
                name=str(r["event"]), country=r.get("country"), currency=None, scheduled_at=at,
                importance=normalise_importance(r.get("impact")), actual=parse_value(r.get("actual")),
                consensus=parse_value(r.get("estimate")), previous=parse_value(r.get("prev")), unit=r.get("unit"),
                status="released" if r.get("actual") is not None else "scheduled", retrieved_at=now, raw=r))
        return out


# ----------------------------------------------------------------------------- Trading Economics
class TradingEconomicsProvider:
    name = "tradingeconomics"
    full_calendar = True
    DEFAULT_BASE = "https://api.tradingeconomics.com"

    def __init__(self, api_key: str, base_url: str | None = None, http: HttpClient | None = None):
        self.key, self.base = api_key, (base_url or self.DEFAULT_BASE).rstrip("/")
        self.http = http or HttpClient(secrets=[api_key])

    def status(self) -> dict[str, Any]:
        return {"provider": self.name, "configured": bool(self.key), "requires_key": True,
                "access": "API key; Trading Economics API subscription"}

    def fetch(self, start: date, end: date) -> list[EconomicEvent]:
        data = self.http.json(f"{self.base}/calendar/country/All/{start.isoformat()}/{end.isoformat()}",
                              {"c": self.key, "f": "json"})
        if not isinstance(data, list):
            raise ProviderError("Trading Economics response is not a list", kind="parse")
        out, now = [], datetime.now(UTC)
        for r in data:
            at = _parse_utc(r.get("Date"))
            name = r.get("Event") or r.get("Category")
            if at is None or not name:
                continue
            revised = parse_value(r.get("Revised"))
            out.append(EconomicEvent(
                provider=self.name, provider_event_id=str(r.get("CalendarId") or
                                                          derive_event_id(self.name, r.get("Country"), name, at)),
                name=str(name), country=r.get("Country"), currency=r.get("Currency") or None, scheduled_at=at,
                importance=normalise_importance(r.get("Importance")), actual=parse_value(r.get("Actual")),
                consensus=parse_value(r.get("Forecast")), previous=parse_value(r.get("Previous")),
                revised_previous=revised, unit=r.get("Unit") or None,
                status="released" if parse_value(r.get("Actual")) is not None else "scheduled",
                source_url=r.get("SourceURL") or None, provider_updated_at=_parse_utc(r.get("LastUpdate")),
                retrieved_at=now, raw=r))
        return out


# ----------------------------------------------------------------------------- FRED release dates (US, dates only)
class FredReleaseDatesProvider:
    name = "fred_release_dates"
    full_calendar = False
    BASE = "https://api.stlouisfed.org/fred"

    def __init__(self, api_key: str, releases: list[dict[str, Any]], http: HttpClient | None = None):
        self.key = api_key
        self.releases = [{**r, "_re": re.compile(r["pattern"], re.IGNORECASE)} for r in releases]
        self.http = http or HttpClient(secrets=[api_key])

    def status(self) -> dict[str, Any]:
        return {"provider": self.name, "configured": bool(self.key), "requires_key": True,
                "access": "free FRED API key", "coverage": f"{len(self.releases)} configured US releases (dates; "
                                                           "times from configuration, precision 'typical')"}

    def fetch(self, start: date, end: date) -> list[EconomicEvent]:
        data = self.http.json(f"{self.BASE}/releases/dates", {
            "api_key": self.key, "file_type": "json", "realtime_start": start.isoformat(),
            "realtime_end": end.isoformat(), "include_release_dates_with_no_data": "true", "limit": 1000,
            "sort_order": "asc"})
        rows = data.get("release_dates") if isinstance(data, dict) else None
        if rows is None:
            raise ProviderError("FRED response has no 'release_dates'", kind="parse")
        out, now = [], datetime.now(UTC)
        for r in rows:
            m = next((x for x in self.releases if x["_re"].search(str(r.get("release_name", "")))), None)
            if m is None:
                continue
            day = date.fromisoformat(str(r["date"]))
            if not (start <= day <= end):
                continue
            tm = m.get("time")
            precision = "typical" if tm else "date_only"
            at = datetime.combine(day, time.fromisoformat(tm) if tm else time(0),
                                  tzinfo=zone(m.get("timezone", "America/New_York"))).astimezone(UTC)
            out.append(EconomicEvent(
                provider=self.name, provider_event_id=f"{r.get('release_id')}-{day.isoformat()}",
                name=str(m.get("name") or r.get("release_name")), country="US", currency="USD", scheduled_at=at,
                source_timezone=m.get("timezone", "America/New_York"), time_precision=precision,
                importance=None, status="scheduled",
                source_url=f"https://fred.stlouisfed.org/release?rid={r.get('release_id')}", retrieved_at=now,
                raw={**r, "time_note": "FRED gives the release DATE; the time is the configured usual release time."
                     if tm else "FRED gives the release DATE only."}))
        return out


def build_providers(settings: Any, cfg: MIConfig, http: HttpClient | None = None) -> list[Any]:
    """Configured providers, in priority order. Keys come from the environment (Settings), never from source."""
    from aurum.market_intelligence.official_calendars import build_agency_providers
    providers: list[Any] = [OfficialScheduleProvider(cfg.banks)]
    if getattr(settings, "official_data_enabled", True):
        providers += build_agency_providers(cfg.section("official_calendars"), http)
    if not getattr(settings, "economic_calendar_enabled", True):
        return providers
    name = (getattr(settings, "economic_calendar_provider", None) or "").strip().lower()
    key = getattr(settings, "economic_calendar_api_key", None)
    key = key.get_secret_value() if key is not None else None
    base = getattr(settings, "economic_calendar_base_url", None)
    cls = {"fmp": FmpProvider, "finnhub": FinnhubProvider, "tradingeconomics": TradingEconomicsProvider}.get(name)
    if cls is not None and key:
        providers.append(cls(key, base, http))
    fred = getattr(settings, "fred_api_key", None)
    fred = fred.get_secret_value() if fred is not None else None
    if fred:
        providers.append(FredReleaseDatesProvider(fred, cfg.section("fred_releases").get("releases") or [], http))
    return providers


def provider_registry(settings: Any, cfg: MIConfig) -> list[dict[str, Any]]:
    """Status of every supported provider (configured or not) for /calendar/status and the CLI."""
    name = (getattr(settings, "economic_calendar_provider", None) or "").strip().lower()
    key_set = getattr(settings, "economic_calendar_api_key", None) is not None
    fred_set = getattr(settings, "fred_api_key", None) is not None
    from aurum.market_intelligence.official_calendars import AGENCY_PROVIDERS
    rows = [OfficialScheduleProvider(cfg.banks).status()]
    official_on = getattr(settings, "official_data_enabled", True)
    for pname, pcfg in (cfg.section("official_calendars").get("providers") or {}).items():
        if pname in AGENCY_PROVIDERS:
            st = AGENCY_PROVIDERS[pname](pcfg).status()
            rows.append({**st, "configured": bool(official_on and (pcfg or {}).get("enabled", True))})
    for p, access in (("fmp", "API key; plan must include the economic calendar"),
                      ("finnhub", "API key; premium endpoint"),
                      ("tradingeconomics", "API key; paid subscription")):
        rows.append({"provider": p, "configured": name == p and key_set, "requires_key": True, "access": access,
                     "selected": name == p})
    rows.append({"provider": "fred_release_dates", "configured": fred_set, "requires_key": True,
                 "access": "free FRED API key (FRED_API_KEY)"})
    return rows
