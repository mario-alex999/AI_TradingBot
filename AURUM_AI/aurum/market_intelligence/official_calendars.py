"""Official statistical-agency release calendars (keyless; published by the agencies for subscription/automated use).

| provider          | publisher                     | format / endpoint                                                                | time      |
|-------------------|-------------------------------|----------------------------------------------------------------------------------|-----------|
| bls_ics           | US Bureau of Labor Statistics | iCalendar  https://www.bls.gov/schedule/news_release/bls.ics                     | exact, ET |
| bea_ics           | US Bureau of Economic Analysis| iCalendar  https://www.bea.gov/news/schedule/ics/online-calendar-subscription.ics | exact, UTC|
| census_calendar   | US Census Bureau              | HTML table https://www.census.gov/economic-indicators/calendar-listview.html      | exact, ET |
| ons_releases      | UK Office for National Stats  | JSON       https://api.beta.ons.gov.uk/v1/search/releases                        | exact, UTC|
| eurostat_ical     | Eurostat                      | iCalendar  https://ec.europa.eu/eurostat/o/calendars/eventsIcal                   | date only |

These calendars carry the SCHEDULE only: they have no consensus forecasts and AURUM does not invent any. Each provider
keeps only the releases matched by an explicit rule in ``config/market_intelligence.yaml`` (``official_calendars``);
the rule supplies category, subcategory and importance (AURUM's own, documented rating - the agencies rate nothing).
Unmatched releases are ignored, never guessed.

Eurostat's calendar lists dates only. Euro-indicator releases are published at 11:00 Luxembourg time by Eurostat's
release policy, so those events carry ``time_precision = "typical"``; other Eurostat items are not used.
"""
from __future__ import annotations

import hashlib
import html as htmllib
import re
from datetime import date, datetime, time, timezone
from typing import Any

from aurum.market_intelligence.config import zone
from aurum.market_intelligence.economic_calendar import EconomicEvent
from aurum.market_intelligence.http import HttpClient, ProviderError

UTC = timezone.utc
TZ_ALIASES = {"US-Eastern": "America/New_York", "US/Eastern": "America/New_York"}


# ----------------------------------------------------------------------------------------------- iCalendar parsing
def _unescape(v: str) -> str:
    return (v.replace("\\n", " ").replace("\\N", " ").replace("\\,", ",").replace("\\;", ";")
            .replace("\\\\", "\\")).strip()


def parse_ics(text: str) -> list[dict[str, Any]]:
    """Minimal RFC 5545 reader: unfolds lines and returns each VEVENT as {NAME: (params, value)}."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n[ \t]", "", text)                     # line folding
    events, cur = [], None
    for line in text.split("\n"):
        if line == "BEGIN:VEVENT":
            cur = {}
        elif line == "END:VEVENT":
            if cur is not None:
                events.append(cur)
            cur = None
        elif cur is not None and ":" in line:
            head, value = line.split(":", 1)
            name, *params = head.split(";")
            cur[name.upper()] = ({k.upper(): v for k, _, v in (p.partition("=") for p in params)}, value)
    return events


def ics_start(prop: tuple[dict[str, str], str] | None) -> tuple[datetime | date, str | None] | None:
    """DTSTART -> (UTC datetime, source zone) or (date, None) for all-day values."""
    if not prop:
        return None
    params, v = prop
    v = v.strip()
    try:
        if params.get("VALUE") == "DATE" or re.fullmatch(r"\d{8}", v):
            return datetime.strptime(v[:8], "%Y%m%d").date(), None
        if v.endswith("Z"):
            return datetime.strptime(v, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC), "UTC"
        tzid = TZ_ALIASES.get(params.get("TZID", ""), params.get("TZID") or "UTC")
        return datetime.strptime(v, "%Y%m%dT%H%M%S").replace(tzinfo=zone(tzid)).astimezone(UTC), tzid
    except (ValueError, KeyError):
        return None


# ----------------------------------------------------------------------------------------------- release rules
class ReleaseRules:
    """Explicit name rules: ``{pattern, category, subcategory, importance}``; first match wins, no match = ignored."""

    def __init__(self, rules: list[dict[str, Any]]):
        self.rules = [{**r, "_re": re.compile(r["pattern"], re.IGNORECASE)} for r in rules or []]

    def match(self, title: str) -> dict[str, Any] | None:
        return next((r for r in self.rules if r["_re"].search(title)), None)


def stable_id(uid: str, limit: int = 96) -> str:
    """Provider ids longer than the column (96) keep a readable prefix plus a hash of the full id (deterministic)."""
    if len(uid) <= limit:
        return uid
    return f"{uid[:limit - 17]}~{hashlib.sha1(uid.encode()).hexdigest()[:16]}"


def _event(provider: str, uid: str, title: str, rule: dict[str, Any], at: datetime, tz: str | None, precision: str,
           country: str, currency: str, url: str, raw: dict[str, Any], status: str = "scheduled") -> EconomicEvent:
    return EconomicEvent(
        provider=provider, provider_event_id=stable_id(uid), name=title, country=country, currency=currency, scheduled_at=at,
        source_timezone=tz, time_precision=precision, importance=rule.get("importance"),
        importance_source=f"agency_rule:{rule.get('subcategory') or rule.get('category')}",
        category=rule.get("category") or "other", subcategory=rule.get("subcategory"), status=status, source_url=url,
        retrieved_at=datetime.now(UTC), raw=raw)


class _Agency:
    name = ""
    full_calendar = False
    official = True
    publisher = ""
    country = ""
    currency = ""
    DEFAULT_URL = ""

    def __init__(self, cfg: dict[str, Any] | None = None, http: HttpClient | None = None):
        cfg = cfg or {}
        self.url = cfg.get("url") or self.DEFAULT_URL
        self.rules = ReleaseRules(cfg.get("releases") or [])
        self.cfg = cfg
        self.http = http or HttpClient(timeout=40)

    def status(self) -> dict[str, Any]:
        return {"provider": self.name, "configured": True, "requires_key": False, "official": True,
                "publisher": self.publisher, "currency": self.currency, "url": self.url,
                "coverage": f"{len(self.rules.rules)} release rules ({self.publisher} schedule; no consensus values)"}


class IcsAgencyProvider(_Agency):
    """iCalendar agency feed. All-day entries get ``all_day_time`` (precision typical) or are date_only."""

    def fetch(self, start: date, end: date) -> list[EconomicEvent]:
        text = self.http.get(self.url).text
        if "BEGIN:VCALENDAR" not in text:
            raise ProviderError(f"{self.name}: response is not an iCalendar document", kind="parse")
        need_cat = self.cfg.get("require_category")
        all_day = self.cfg.get("all_day_time")
        all_day_tz = self.cfg.get("all_day_timezone") or "UTC"
        out = []
        for ev in parse_ics(text):
            title = _unescape(ev.get("SUMMARY", ({}, ""))[1])
            if not title:
                continue
            cats = _unescape(ev.get("X-CATEGORY", ({}, ""))[1] + "," + ev.get("CATEGORIES", ({}, ""))[1])
            if need_cat and need_cat.lower() not in cats.lower():
                continue
            rule = self.rules.match(title)
            st = ics_start(ev.get("DTSTART"))
            if rule is None or st is None:
                continue
            when, tz = st
            if isinstance(when, datetime):
                at, precision = when, "exact"
            else:
                if not (start <= when <= end):
                    continue
                if all_day:
                    tz = all_day_tz
                    at = datetime.combine(when, time.fromisoformat(all_day), tzinfo=zone(tz)).astimezone(UTC)
                    precision = "typical"
                else:
                    tz, at, precision = all_day_tz, datetime.combine(when, time(0), tzinfo=zone(all_day_tz)), "date_only"
                    at = at.astimezone(UTC)
            if not (start <= at.date() <= end):
                continue
            # Eurostat regenerates its UIDs on every download, so its events are keyed by title + date instead
            uid = ("" if self.cfg.get("uid_unstable") else _unescape(ev.get("UID", ({}, ""))[1])) or title
            status = "cancelled" if ev.get("STATUS", ({}, ""))[1].upper() == "CANCELLED" else "scheduled"
            out.append(_event(self.name, f"{uid}|{at.date().isoformat()}", title, rule, at, tz, precision,
                              self.country, self.currency, self.cfg.get("source_page") or self.url,
                              {"summary": title, "dtstart": ev.get("DTSTART", ({}, ""))[1], "uid": uid,
                               "categories": cats.strip(","), "rule": rule["pattern"],
                               "time_note": self.cfg.get("time_note")}, status))
        return out


class BlsIcsProvider(IcsAgencyProvider):
    name, publisher, country, currency = "bls_ics", "US Bureau of Labor Statistics", "US", "USD"
    DEFAULT_URL = "https://www.bls.gov/schedule/news_release/bls.ics"


class BeaIcsProvider(IcsAgencyProvider):
    name, publisher, country, currency = "bea_ics", "US Bureau of Economic Analysis", "US", "USD"
    DEFAULT_URL = "https://www.bea.gov/news/schedule/ics/online-calendar-subscription.ics"


class EurostatIcalProvider(IcsAgencyProvider):
    name, publisher, country, currency = "eurostat_ical", "Eurostat", "EA", "EUR"
    DEFAULT_URL = "https://ec.europa.eu/eurostat/o/calendars/eventsIcal?theme=0&category=0"


class CensusCalendarProvider(_Agency):
    """Census economic-indicator calendar (list view). Each row: title link, ``sorttable_customkey=YYYYMMDDHHMM``
    (Eastern Time), time, reference period."""
    name, publisher, country, currency = "census_calendar", "US Census Bureau", "US", "USD"
    DEFAULT_URL = "https://www.census.gov/economic-indicators/calendar-listview.html"
    ROW = re.compile(r'<a href="([^"]*)">(.*?)</a>\s*</td>\s*<td[^>]*sorttable_customkey="(\d{12})"[^>]*>.*?</td>'
                     r'\s*<td[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>', re.S)

    YEAR_URL = "https://www.census.gov/economic-indicators/calendar-listview-{year}.html"

    def _pages(self, start: date, end: date) -> list[tuple[str, str]]:
        """The list view shows one calendar year; earlier/other years have their own page. A missing year page
        (HTTP 404) falls back to the default list view."""
        pages, seen = [], set()
        for year in range(start.year, end.year + 1):
            url = (self.cfg.get("year_url") or self.YEAR_URL).format(year=year)
            try:
                text = self.http.get(url).text
            except ProviderError as exc:
                if exc.status != 404:
                    raise
                url, text = self.url, self.http.get(self.url).text
            if url not in seen:
                seen.add(url)
                pages.append((url, text))
        return pages

    def fetch(self, start: date, end: date) -> list[EconomicEvent]:
        rows = []
        for _, text in self._pages(start, end):
            rows += self.ROW.findall(text)
        if not rows:
            raise ProviderError("census_calendar: no release rows found (page layout changed?)", kind="parse")
        out, seen = [], set()
        for href, title, key, tm, period in rows:
            if (title, key) in seen:
                continue
            seen.add((title, key))
            title = re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", "", title))).strip()
            period = re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", "", period))).strip()
            rule = self.rules.match(title)
            if rule is None:
                continue
            at = datetime.strptime(key, "%Y%m%d%H%M").replace(tzinfo=zone("America/New_York")).astimezone(UTC)
            if not (start <= at.date() <= end):
                continue
            name = f"{title} ({period})" if period else title
            url = href if href.startswith("http") else "https://www.census.gov" + href
            out.append(_event(self.name, f"{title}|{key}", name, rule, at, "America/New_York", "exact", self.country,
                              self.currency, url, {"title": title, "key": key, "time": tm.strip(), "period": period,
                                                   "rule": rule["pattern"]}))
        return out


class OnsReleasesProvider(_Agency):
    """ONS release calendar API: one search per configured query, upcoming and published releases."""
    name, publisher, country, currency = "ons_releases", "UK Office for National Statistics", "GB", "GBP"
    DEFAULT_URL = "https://api.beta.ons.gov.uk/v1/search/releases"

    def fetch(self, start: date, end: date) -> list[EconomicEvent]:
        out, seen = [], set()
        queries = self.cfg.get("queries") or []
        for q in queries:
            for rtype in ("type-upcoming", "type-published"):
                data = self.http.json(self.url, {"query": q, "release-type": rtype, "limit": 50,
                                                 "fromDate": start.isoformat(), "toDate": end.isoformat()})
                if not isinstance(data, dict) or "releases" not in data:
                    raise ProviderError("ons_releases: response has no 'releases' list", kind="parse")
                for r in data.get("releases") or []:                 # null = no match for this query
                    d = r.get("description") or {}
                    title = str(d.get("title") or "").strip()
                    uri = str(r.get("uri") or "")
                    rule = self.rules.match(title)
                    if rule is None or not d.get("release_date") or uri in seen:
                        continue
                    try:
                        at = datetime.fromisoformat(str(d["release_date"]).replace("Z", "+00:00")).astimezone(UTC)
                    except ValueError:
                        continue
                    if not (start <= at.date() <= end):
                        continue
                    seen.add(uri)
                    status = ("cancelled" if d.get("cancelled") else "delayed" if d.get("postponed") else
                              "scheduled")
                    precision = "exact" if d.get("finalised") or d.get("published") else "typical"
                    out.append(_event(self.name, uri or f"{title}|{at.isoformat()}", title, rule, at, "UTC",
                                      precision, self.country, self.currency, "https://www.ons.gov.uk" + uri,
                                      {"uri": uri, "release_date": d.get("release_date"),
                                       "published": d.get("published"), "finalised": d.get("finalised"),
                                       "cancelled": d.get("cancelled"), "postponed": d.get("postponed"),
                                       "date_changes": r.get("date_changes"), "rule": rule["pattern"]}, status))
        return out


AGENCY_PROVIDERS = {c.name: c for c in (BlsIcsProvider, BeaIcsProvider, CensusCalendarProvider, OnsReleasesProvider,
                                        EurostatIcalProvider)}


def build_agency_providers(cfg_section: dict[str, Any], http: HttpClient | None = None) -> list[Any]:
    """Enabled agency providers (``official_calendars.providers.<name>.enabled``, default on when configured)."""
    out = []
    for name, pcfg in ((cfg_section or {}).get("providers") or {}).items():
        cls = AGENCY_PROVIDERS.get(name)
        if cls is not None and (pcfg or {}).get("enabled", True):
            out.append(cls(pcfg, http))
    return out
