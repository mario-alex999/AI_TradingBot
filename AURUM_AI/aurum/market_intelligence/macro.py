"""Macro intelligence: central-bank policy rates, decisions and macro indicators from OFFICIAL sources.

Rate sources (keyless, machine-readable, published by the bank itself):

| bank | source id          | series                                                              |
|------|--------------------|---------------------------------------------------------------------|
| Fed  | nyfed_effr_target  | NY Fed Markets API, EFFR release: FOMC target range (from / to)      |
| ECB  | ecb_data_portal    | FM.B.U2.EUR.4F.KR.DFR.LEV (deposit facility; MRO and MLF as detail)  |
| BoE  | boe_iadb           | IUDBEDR (Bank Rate)                                                  |
| RBA  | rba_a2             | Table A2 "New Cash Rate Target" (dates are EFFECTIVE dates)          |
| BoC  | boc_valet          | V39079 (target for the overnight rate)                               |
| SNB  | snb_data_portal    | cube snbgwdzid, D0 = LZ (SNB policy rate)                            |
| BoJ  | bis_cbpol          | BIS WS_CBPOL D.JP (official policy rate as compiled by the BIS)      |
| RBNZ | bis_cbpol          | BIS WS_CBPOL D.NZ (OCR; rbnz.govt.nz refuses automated clients)      |

A rate is never inferred from headlines. BIS data lag the bank's announcement by about 1-2 weeks; the observation is
still dated by its effective date, and its ``known_from`` follows the rules below.

Point-in-time rules (no look-ahead):
* A policy-rate observation is known from the bank's announcement time when it matches a configured meeting
  (effective within 3 days after it), otherwise from 00:00 of its effective date in the bank's zone.
* Banks without a fixed announcement time (``time_precision: date_only``, e.g. BoJ): known from 23:59 local time on
  the meeting day - conservative, so a decision is never visible before it was announced.
* Sources without vintages (Eurostat HICP, BLS, Treasury, ONS): an observation is known from the time AURUM FIRST RETRIEVED it.
* FRED series: every vintage is stored with its ``realtime_start``; an observation is known from the end of that day
  (New York) - conservative, because FRED gives no publication time of day.
"""
from __future__ import annotations

import csv
import io
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from aurum.market_intelligence.config import zone
from aurum.market_intelligence.http import HttpClient, ProviderError

log = logging.getLogger(__name__)
UTC = timezone.utc


@dataclass
class RateObservation:
    bank: str
    effective_date: date
    rate: float
    rate_lower: float | None = None
    source: str = ""
    source_url: str = ""
    detail: dict[str, Any] = field(default_factory=dict)


def _f(v: Any) -> float | None:
    try:
        s = str(v).strip().replace("+", "")
        return float(s) if s not in ("", "NaN", "nan") else None
    except (TypeError, ValueError):
        return None


class RateSources:
    """Fetch functions per ``rate_source`` id. Each returns observations sorted by effective date."""

    def __init__(self, http: HttpClient | None = None):
        self.http = http or HttpClient(timeout=40)

    def fetch(self, source: str, bank: str, start: date) -> list[RateObservation]:
        fn = getattr(self, f"_{source}", None)
        if fn is None:
            raise ProviderError(f"Unknown rate source '{source}'", kind="config")
        obs = fn(bank, start)
        return sorted(obs, key=lambda o: o.effective_date)

    def _nyfed_effr_target(self, bank: str, start: date) -> list[RateObservation]:
        url = "https://markets.newyorkfed.org/api/rates/unsecured/effr/search.json"
        out: list[RateObservation] = []
        s = start
        today = datetime.now(UTC).date()
        while s <= today:
            e = min(today, s + timedelta(days=365))
            data = self.http.json(url, {"startDate": s.isoformat(), "endDate": e.isoformat()})
            for r in (data or {}).get("refRates", []):
                hi, lo = _f(r.get("targetRateTo")), _f(r.get("targetRateFrom"))
                if hi is None:
                    continue
                out.append(RateObservation(bank, date.fromisoformat(r["effectiveDate"]), hi, lo, "nyfed_effr_target",
                                           url, {"effr": _f(r.get("percentRate"))}))
            s = e + timedelta(days=1)
        return out

    def _ecb_data_portal(self, bank: str, start: date) -> list[RateObservation]:
        base = "https://data-api.ecb.europa.eu/service/data/FM/"
        series = {"dfr": "B.U2.EUR.4F.KR.DFR.LEV", "mro": "B.U2.EUR.4F.KR.MRR_FR.LEV", "mlf": "B.U2.EUR.4F.KR.MLFR.LEV"}
        rows: dict[str, list[tuple[date, float]]] = {}
        for k, key in series.items():
            resp = self.http.get(base + key, {"format": "csvdata", "detail": "dataonly",
                                              "startPeriod": (start - timedelta(days=800)).isoformat()})
            rows[k] = [(date.fromisoformat(r["TIME_PERIOD"]), float(r["OBS_VALUE"]))
                       for r in csv.DictReader(io.StringIO(resp.text)) if _f(r.get("OBS_VALUE")) is not None]
        def at(k: str, d: date) -> float | None:
            prior = [v for t, v in rows.get(k, []) if t <= d]
            return prior[-1] if prior else None
        return [RateObservation(bank, d, v, None, "ecb_data_portal", base + series["dfr"],
                                {"deposit_facility": v, "main_refinancing": at("mro", d), "marginal_lending": at("mlf", d)})
                for d, v in rows["dfr"]]

    def _boe_iadb(self, bank: str, start: date) -> list[RateObservation]:
        url = "https://www.bankofengland.co.uk/boeapps/database/_iadb-fromshowcolumns.asp"
        resp = self.http.get(url, {"csv.x": "yes", "Datefrom": (start - timedelta(days=800)).strftime("%d/%b/%Y"),
                                   "Dateto": "now", "SeriesCodes": "IUDBEDR", "CSVF": "TN", "UsingCodes": "Y",
                                   "VPD": "Y", "VFD": "N"})
        out = []
        for r in csv.DictReader(io.StringIO(resp.text)):
            v = _f(r.get("IUDBEDR"))
            if v is None:
                continue
            out.append(RateObservation(bank, datetime.strptime(r["DATE"], "%d %b %Y").date(), v, None, "boe_iadb", url))
        return out

    def _rba_a2(self, bank: str, start: date) -> list[RateObservation]:
        url = "https://www.rba.gov.au/statistics/tables/csv/a2-data.csv"
        text = self.http.get(url).text.lstrip("﻿")
        out = []
        for row in csv.reader(io.StringIO(text)):
            if len(row) < 3:
                continue
            try:
                d = datetime.strptime(row[0].strip(), "%d-%b-%Y").date()
            except ValueError:
                continue
            v = _f(row[2])
            if v is not None:
                out.append(RateObservation(bank, d, v, None, "rba_a2", url,
                                           {"note": "RBA A2 dates are effective dates (day after the announcement)."}))
        return out

    def _boc_valet(self, bank: str, start: date) -> list[RateObservation]:
        url = "https://www.bankofcanada.ca/valet/observations/V39079/json"
        data = self.http.json(url, {"start_date": (start - timedelta(days=800)).isoformat()})
        out = []
        for r in (data or {}).get("observations", []):
            v = _f((r.get("V39079") or {}).get("v"))
            if v is not None:
                out.append(RateObservation(bank, date.fromisoformat(r["d"]), v, None, "boc_valet", url))
        return out

    def _snb_data_portal(self, bank: str, start: date) -> list[RateObservation]:
        url = "https://data.snb.ch/api/cube/snbgwdzid/data/csv/en"
        text = self.http.get(url).text.lstrip("﻿")
        out = []
        for row in csv.reader(io.StringIO(text), delimiter=";"):
            if len(row) >= 3 and row[1] == "LZ" and row[2] != "":
                try:
                    d = date.fromisoformat(row[0])
                except ValueError:
                    continue
                if d >= start - timedelta(days=800):
                    out.append(RateObservation(bank, d, float(row[2]), None, "snb_data_portal", url))
        return out

    def _bis_cbpol(self, bank: str, start: date) -> list[RateObservation]:
        area = {"boj": "JP", "rbnz": "NZ"}.get(bank)
        if area is None:
            raise ProviderError(f"bis_cbpol has no reference area configured for '{bank}'", kind="config")
        url = f"https://stats.bis.org/api/v1/data/WS_CBPOL/D.{area}"
        text = self.http.get(url, {"startPeriod": (start - timedelta(days=800)).isoformat(), "format": "csv"}).text
        out = []
        for r in csv.DictReader(io.StringIO(text.lstrip("﻿"))):
            v = _f(r.get("OBS_VALUE"))
            if v is None or r.get("REF_AREA", area) != area:
                continue
            try:
                d = date.fromisoformat(str(r.get("TIME_PERIOD"))[:10])
            except ValueError:
                continue
            detail = {k: r[k] for k in ("COMPILATION",) if r.get(k)}
            out.append(RateObservation(bank, d, v, None, "bis_cbpol", url,
                                       {**detail, "note": "BIS central-bank policy rates (daily, forward-filled)."}))
        return out


def changes(obs: list[RateObservation]) -> list[RateObservation]:
    """Observations where the rate (or lower bound) differs from the previous one - the decision points."""
    out, prev = [], None
    for o in obs:
        if prev is None or o.rate != prev.rate or o.rate_lower != prev.rate_lower:
            out.append(o)
        prev = o
    return out


def announcement_at(bank_cfg: dict[str, Any], day: date, conservative: bool = False) -> tuple[datetime, str]:
    """Announcement instant of a meeting on ``day`` and its precision (exact | typical | date_only).

    Without a fixed time (date_only) the start of the local day is returned for scheduling, or - with
    ``conservative`` - 23:59 local, the latest moment the decision can have become public (used for ``known_from``)."""
    tz = zone(bank_cfg.get("timezone") or "UTC")
    hhmm = bank_cfg.get("announcement_time")
    precision = str(bank_cfg.get("time_precision") or ("typical" if hhmm else "date_only"))
    if not hhmm or precision == "date_only":
        return datetime.combine(day, time(23, 59) if conservative else time(0), tzinfo=tz).astimezone(UTC), "date_only"
    return datetime.combine(day, time.fromisoformat(str(hhmm)), tzinfo=tz).astimezone(UTC), precision


def known_from(bank_cfg: dict[str, Any], effective: date) -> tuple[datetime, date | None]:
    """When the information was public: announcement time of a matching meeting, else effective-date midnight."""
    tz = zone(bank_cfg.get("timezone") or "UTC")
    for m in bank_cfg.get("meetings") or []:
        md = date.fromisoformat(str(m))
        if md <= effective <= md + timedelta(days=3):
            return announcement_at(bank_cfg, md, conservative=True)[0], md
    return datetime.combine(effective, time(0), tzinfo=tz).astimezone(UTC), None


def decisions(bank: str, bank_cfg: dict[str, Any], obs: list[RateObservation]) -> list[dict[str, Any]]:
    """Rate changes (status 'changed') + configured past meetings without a change (status 'held')."""
    out = []
    chg = changes(obs)
    for i, o in enumerate(chg):
        if i == 0:
            continue                                      # first observation: start of history, not a decision
        prev = chg[i - 1]
        kf, meeting = known_from(bank_cfg, o.effective_date)
        out.append({"bank": bank, "decision_date": meeting or o.effective_date, "effective_date": o.effective_date,
                    "status": "changed", "previous_rate": prev.rate, "new_rate": o.rate,
                    "previous_rate_lower": prev.rate_lower, "new_rate_lower": o.rate_lower,
                    "change_bps": round((o.rate - prev.rate) * 100), "known_from": kf,
                    "matched_meeting": meeting is not None, "source": o.source, "source_url": o.source_url})
    if obs:
        first, last = obs[0].effective_date, obs[-1].effective_date
        changed_meetings = {d["decision_date"] for d in out}
        for m in bank_cfg.get("meetings") or []:
            md = date.fromisoformat(str(m))
            if md in changed_meetings or not (first < md <= last - timedelta(days=3)):
                continue
            r = [o for o in obs if o.effective_date <= md + timedelta(days=3)][-1]
            out.append({"bank": bank, "decision_date": md, "effective_date": None, "status": "held",
                        "previous_rate": r.rate, "new_rate": r.rate, "previous_rate_lower": r.rate_lower,
                        "new_rate_lower": r.rate_lower, "change_bps": 0,
                        "known_from": announcement_at(bank_cfg, md, conservative=True)[0], "matched_meeting": True,
                        "source": r.source, "source_url": r.source_url,
                        "note": "No change in the official rate series around this scheduled meeting."})
    return sorted(out, key=lambda d: d["decision_date"])


def next_meeting(bank_cfg: dict[str, Any], t: datetime) -> dict[str, Any] | None:
    for m in sorted(bank_cfg.get("meetings") or []):
        at, precision = announcement_at(bank_cfg, date.fromisoformat(str(m)))
        if announcement_at(bank_cfg, date.fromisoformat(str(m)), conservative=True)[0] > t:
            return {"date": str(m), "announcement_utc": at, "time_precision": precision,
                    "source": bank_cfg.get("official_url"), "verified_on": bank_cfg.get("verified_on")}
    return None


# ------------------------------------------------------------------------------------- macro indicator series
class SeriesSources:
    def __init__(self, http: HttpClient | None = None, fred_key: str | None = None):
        self.http = http or HttpClient(timeout=40, secrets=[fred_key] if fred_key else None)
        self.fred_key = fred_key

    def ecb(self, flow: str, key: str, start: date) -> list[dict[str, Any]]:
        url = f"https://data-api.ecb.europa.eu/service/data/{flow}/{key}"
        resp = self.http.get(url, {"format": "csvdata", "detail": "dataonly", "startPeriod": start.strftime("%Y-%m")})
        out = []
        for r in csv.DictReader(io.StringIO(resp.text)):
            v = _f(r.get("OBS_VALUE"))
            if v is not None:
                out.append({"period": r["TIME_PERIOD"], "value": v, "vintage_date": None, "source_url": url})
        return out

    def eurostat(self, dataset: str, params: dict[str, Any], start: date) -> list[dict[str, Any]]:
        """Eurostat dissemination API (JSON-stat 2.0). ``params`` must select exactly one series (every dimension
        except time fixed)."""
        url = f"https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/{dataset}"
        data = self.http.json(url, {**params, "format": "JSON", "lang": "EN",
                                    "sinceTimePeriod": start.strftime("%Y-%m")})
        try:
            sizes = dict(zip(data["id"], data["size"]))
            index = data["dimension"]["time"]["category"]["index"]
            values = data.get("value") or {}
        except (KeyError, TypeError) as exc:
            raise ProviderError(f"Eurostat {dataset}: unexpected JSON-stat layout", kind="parse") from exc
        if any(n != 1 for d, n in sizes.items() if d != "time"):
            raise ProviderError(f"Eurostat {dataset}: parameters select more than one series", kind="config")
        out = []
        for period, pos in sorted(index.items(), key=lambda kv: kv[1]):
            raw = values.get(str(pos)) if isinstance(values, dict) else (values[pos] if pos < len(values) else None)
            v = _f(raw)
            if v is not None:
                out.append({"period": period, "value": v, "vintage_date": None, "source_url": url})
        return out

    def bls(self, series_id: str, start: date) -> list[dict[str, Any]]:
        """BLS Public Data API v1 (keyless; BLS allows 25 requests per day per client)."""
        url = f"https://api.bls.gov/publicAPI/v1/timeseries/data/{series_id}"
        data = self.http.json(url)
        if not isinstance(data, dict) or data.get("status") != "REQUEST_SUCCEEDED":
            msg = "; ".join(data.get("message") or []) if isinstance(data, dict) else "invalid response"
            raise ProviderError(f"BLS {series_id}: {msg or 'request not successful'}",
                                kind="rate_limited" if "threshold" in msg.lower() else "http")
        out = []
        for sr in (data.get("Results") or {}).get("series") or []:
            for r in sr.get("data") or []:
                per = str(r.get("period", ""))
                if not per.startswith("M") or per == "M13":       # monthly values only (M13 = annual average)
                    continue
                period = f"{r['year']}-{per[1:]}"
                v = _f(r.get("value"))
                if v is not None and period >= start.strftime("%Y-%m"):
                    out.append({"period": period, "value": v, "vintage_date": None,
                                "source_url": f"https://data.bls.gov/timeseries/{series_id}"})
        return sorted(out, key=lambda r: r["period"])

    def treasury(self, column: str, start: date) -> list[dict[str, Any]]:
        """US Treasury daily par yield curve CSV (one file per calendar year)."""
        base = "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv"
        out = []
        for year in range(start.year, datetime.now(UTC).year + 1):
            text = self.http.get(f"{base}/{year}/all", {"type": "daily_treasury_yield_curve",
                                                        "field_tdr_date_value": year, "page": "",
                                                        "_format": "csv"}).text.lstrip("\ufeff")
            rows = list(csv.DictReader(io.StringIO(text)))
            if rows and column not in rows[0]:
                raise ProviderError(f"Treasury CSV has no column '{column}'", kind="parse")
            for r in rows:
                try:
                    d = datetime.strptime(r["Date"], "%m/%d/%Y").date()
                except (KeyError, ValueError):
                    continue
                v = _f(r.get(column))
                if v is not None and d >= start:
                    out.append({"period": d.isoformat(), "value": v, "vintage_date": None,
                                "source_url": "https://home.treasury.gov/resource-center/data-chart-center/"
                                              "interest-rates/TextView?type=daily_treasury_yield_curve"})
        return sorted(out, key=lambda r: r["period"])

    def ons(self, path: str, start: date) -> list[dict[str, Any]]:
        """ONS time-series JSON (``<topic>/timeseries/<cdid>/<dataset>/data``), monthly values."""
        url = f"https://www.ons.gov.uk{path.rstrip('/')}/data"
        data = self.http.json(url)
        names = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
        months = {m: i for i, m in enumerate(names, 1)}
        out = []
        for r in (data or {}).get("months") or []:
            try:
                y, m = str(r["date"]).split()
                period = f"{int(y):04d}-{months[m.upper()]:02d}"
            except (KeyError, ValueError):
                continue
            v = _f(r.get("value"))
            if v is not None and period >= start.strftime("%Y-%m"):
                out.append({"period": period, "value": v, "vintage_date": None,
                            "source_url": f"https://www.ons.gov.uk{path}"})
        return out

    def fetch(self, meta: dict[str, Any], start: date) -> list[dict[str, Any]]:
        """Dispatch on ``meta['source']`` (official_series entries in central_banks.yaml)."""
        src = meta.get("source")
        if src == "ecb_data_portal":
            return self.ecb(meta["flow"], meta["key"], start)
        if src == "eurostat":
            return self.eurostat(meta["dataset"], meta.get("params") or {}, start)
        if src == "bls":
            return self.bls(meta["series"], start)
        if src == "treasury":
            return self.treasury(meta["column"], start)
        if src == "ons":
            return self.ons(meta["path"], start)
        raise ProviderError(f"Unknown series source '{src}'", kind="config")

    def fred(self, series_id: str, start: date) -> list[dict[str, Any]]:
        """All vintages (ALFRED) of ``series_id`` since ``start``; each row carries its realtime_start."""
        if not self.fred_key:
            raise ProviderError("FRED_API_KEY is not set", kind="auth")
        url = "https://api.stlouisfed.org/fred/series/observations"
        data = self.http.json(url, {"series_id": series_id, "api_key": self.fred_key, "file_type": "json",
                                    "observation_start": start.isoformat(), "realtime_start": start.isoformat(),
                                    "realtime_end": "9999-12-31", "limit": 100000})
        out = []
        for r in (data or {}).get("observations", []):
            v = _f(r.get("value"))
            if v is None:                                  # FRED marks missing values with "."
                continue
            out.append({"period": r["date"], "value": v, "vintage_date": r.get("realtime_start"),
                        "source_url": f"https://fred.stlouisfed.org/series/{series_id}"})
        return out


def fred_known_from(vintage_date: str) -> datetime:
    """End of the vintage day in New York (FRED gives no time of day)."""
    d = date.fromisoformat(vintage_date)
    return datetime.combine(d, time(23, 59), tzinfo=zone("America/New_York")).astimezone(UTC)
