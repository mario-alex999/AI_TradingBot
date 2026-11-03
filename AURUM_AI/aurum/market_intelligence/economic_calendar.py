"""Economic-event model, classification, instrument impact and pre/post-release event risk.

* Values are never invented: a missing forecast / actual / previous stays ``None`` (never 0).
* An ``actual`` is only visible to an analysis whose ``as_of`` is at or after the release time, and only the revision
  of an event that AURUM had retrieved by ``as_of`` is used (see ``EventStore.as_of``) - no look-ahead.
* Classification uses explicit rules from ``classification.rules``: a rule needs a name pattern AND (when listed) a
  matching currency. Importance comes from the provider when it gives one; a rule's ``default_importance`` is only
  used when the provider gives none. The original event name is always kept.
* Impact maps currencies/categories to instruments with a stated reason; it never predicts a direction.
* Event-risk states are analytical context, not trade restrictions, and never claim volatility WILL follow.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from aurum.market_intelligence.config import MIConfig

UTC = timezone.utc
IMPORTANCE = ("low", "medium", "high")
STATUSES = ("scheduled", "upcoming", "released", "delayed", "cancelled", "revised", "unknown")
RISK_STATES = ("NORMAL", "EVENT_APPROACHING", "HIGH_EVENT_RISK", "RELEASE_WINDOW", "POST_EVENT_VOLATILITY", "UNKNOWN")
VALUE_FIELDS = ("actual", "consensus", "previous", "revised_previous")


@dataclass
class EconomicEvent:
    provider: str
    provider_event_id: str
    name: str
    country: str | None
    currency: str | None
    scheduled_at: datetime                       # UTC
    source_timezone: str | None = None           # zone the provider reported in (None = UTC)
    time_precision: str = "exact"                # exact | typical | date_only
    importance: str | None = None                # low | medium | high | None (provider gave none, no rule)
    importance_source: str | None = None         # provider | rule:<subcategory> | None
    category: str = "other"
    subcategory: str | None = None
    actual: float | str | None = None
    consensus: float | str | None = None
    previous: float | str | None = None
    revised_previous: float | str | None = None
    unit: str | None = None
    status: str = "scheduled"
    source_url: str | None = None
    provider_updated_at: datetime | None = None
    retrieved_at: datetime | None = None
    affected_instruments: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] | None = None

    def key(self) -> str:
        return f"{self.provider}:{self.provider_event_id}"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("raw", None)
        return d


def derive_event_id(provider: str, country: str | None, name: str, scheduled_at: datetime) -> str:
    """Stable id for providers that give none: provider + country + name + scheduled UTC DATE (a time change within
    the day keeps the id; a move to another day creates a new id - documented limitation)."""
    blob = f"{provider}|{(country or '').upper()}|{name.strip().lower()}|{scheduled_at.astimezone(UTC).date()}"
    return hashlib.sha1(blob.encode()).hexdigest()[:20]


def parse_value(v: Any) -> float | str | None:
    """Provider value -> float when numeric (``"3.2%"``, ``"215K"``, ``"-0.1"``), the original text otherwise, None when
    missing. Empty strings and nulls are None - never zero."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if s == "" or s.lower() in ("none", "null", "nan", "-", "n/a"):
        return None
    s2 = re.sub(r"(?<=\d),(?=\d{3}(?:\D|$))", "", s)          # thousands separators: 1,234.5 -> 1234.5
    m = re.fullmatch(r"([-+]?\d+(?:\.\d+)?)\s*([%KMBT]?)", s2, re.IGNORECASE)
    if m:
        num = float(m.group(1))
        mult = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}.get(m.group(2).upper(), 1)
        return num * mult
    return s


# ------------------------------------------------------------------------------------------ classification
class EventClassifier:
    def __init__(self, cfg: MIConfig):
        c = cfg.section("classification")
        self.country_currency = {str(k).upper(): str(v).upper() for k, v in (c.get("country_currency") or {}).items()}
        self.rules = []
        for r in c.get("rules") or []:
            pats = [re.compile(p, re.IGNORECASE) for p in r.get("patterns") or []]
            self.rules.append({**r, "_patterns": pats,
                               "_currencies": {x.upper() for x in r.get("currencies") or []}})

    def currency_for(self, country: str | None, currency: str | None) -> str | None:
        if currency:
            return currency.upper()
        if country:
            return self.country_currency.get(country.strip().upper())
        return None

    def classify(self, ev: EconomicEvent) -> EconomicEvent:
        ev.currency = self.currency_for(ev.country, ev.currency)
        if ev.category != "other":               # already classified by its source (official schedules, agency rules)
            if ev.importance is not None and ev.importance_source is None:
                ev.importance_source = "provider"
            return ev
        for r in self.rules:
            if not any(p.search(ev.name) for p in r["_patterns"]):
                continue
            if r["_currencies"] and (ev.currency or "") not in r["_currencies"]:
                continue
            ev.category, ev.subcategory = r["category"], r.get("subcategory")
            if ev.importance is None and r.get("default_importance"):
                ev.importance, ev.importance_source = r["default_importance"], f"rule:{r.get('subcategory')}"
            break
        if ev.importance is not None and ev.importance_source is None:
            ev.importance_source = "provider"
        return ev


def normalise_importance(v: Any) -> str | None:
    if v is None or v == "":
        return None
    s = str(v).strip().lower()
    if s in ("1", "low", "l"):
        return "low"
    if s in ("2", "medium", "moderate", "m", "med"):
        return "medium"
    if s in ("3", "high", "h"):
        return "high"
    return None


# ------------------------------------------------------------------------------------------ impact
class ImpactMapper:
    def __init__(self, cfg: MIConfig):
        imp = cfg.section("impact")
        self.by_currency = {k.upper(): v for k, v in (imp.get("currencies") or {}).items()}
        self.extra = imp.get("extra") or []

    def affected(self, ev: EconomicEvent, instruments: list[str] | None = None) -> list[dict[str, Any]]:
        """Instruments the event is potentially relevant to, each with relation (direct | macro) and the reason.
        Never a direction."""
        out: dict[str, dict[str, Any]] = {}
        rank = {None: -1, "low": 0, "medium": 1, "high": 2}
        for m in self.by_currency.get((ev.currency or "").upper(), []):
            cats = m.get("categories")
            if cats and ev.category not in cats:
                continue
            out.setdefault(m["instrument"], {"instrument": m["instrument"], "relation": m["relation"],
                                             "currency": ev.currency, "explanation": m["explanation"]})
        for m in self.extra:
            if m.get("categories") and ev.category not in m["categories"]:
                continue
            if m.get("currencies") and (ev.currency or "") not in m["currencies"]:
                continue
            if m.get("min_importance") and rank.get(ev.importance, -1) < rank[m["min_importance"]]:
                continue
            out.setdefault(m["instrument"], {"instrument": m["instrument"], "relation": m["relation"],
                                             "currency": ev.currency, "explanation": m["explanation"]})
        res = list(out.values())
        if instruments:
            res = [r for r in res if r["instrument"] in instruments]
        for r in res:
            r["direction"] = None          # relevance only - AURUM does not predict the reaction
        return res


# ------------------------------------------------------------------------------------------ event risk
def visible_event(ev: dict[str, Any], as_of: datetime) -> dict[str, Any]:
    """Hide outcome values that were not public at ``as_of`` (actual before its release time)."""
    e = dict(ev)
    if e["scheduled_at"] > as_of:
        e["actual"] = None
        if e.get("status") in ("released", "revised"):
            e["status"] = "scheduled"
    return e


def risk_windows(cfg: MIConfig, importance: str | None, symbol: str | None = None) -> dict[str, int]:
    er = cfg.section("event_risk")
    w = dict((er.get("windows") or {}).get(importance or "low") or {})
    over = ((er.get("instrument_overrides") or {}).get((symbol or "").upper()) or {}).get(importance or "low") or {}
    w.update(over)
    return {k: int(w.get(k, d)) for k, d in (("approaching", 60), ("high_risk", 0), ("release", 5), ("post", 15))}


def event_state(cfg: MIConfig, ev: dict[str, Any], t: datetime, symbol: str | None = None) -> dict[str, Any]:
    """Risk state of one event at ``t``."""
    w = risk_windows(cfg, ev.get("importance"), symbol)
    mins = (ev["scheduled_at"] - t).total_seconds() / 60
    if ev.get("status") == "cancelled":
        state = "NORMAL"
    elif ev.get("time_precision") == "date_only":
        same_day = ev["scheduled_at"].date() == t.astimezone(UTC).date()
        state = "EVENT_APPROACHING" if (0 <= mins <= w["approaching"] or same_day) else "NORMAL"
    elif 0 < mins <= w["high_risk"]:
        state = "HIGH_EVENT_RISK"
    elif 0 < mins <= w["approaching"]:
        state = "EVENT_APPROACHING"
    elif -w["release"] < mins <= 0:
        state = "RELEASE_WINDOW"
    elif -(w["release"] + w["post"]) < mins <= -w["release"]:
        state = "POST_EVENT_VOLATILITY"
    else:
        state = "NORMAL"
    er = cfg.section("event_risk")
    label = None
    if mins > 0:
        label = next((f"within_{m}m" for m in sorted(er.get("reported_windows") or []) if mins <= m), None)
    elif mins > -max(er.get("post_reported_windows") or [60]):
        label = next((f"post_{m}m" for m in sorted(er.get("post_reported_windows") or []) if -mins <= m), None)
    return {"state": state, "minutes_until_release": round(mins, 1), "window_label": label, "windows": w,
            "releasing_now": state == "RELEASE_WINDOW", "in_post_release_window": state == "POST_EVENT_VOLATILITY"}


STATE_RANK = {s: i for i, s in enumerate(("NORMAL", "EVENT_APPROACHING", "POST_EVENT_VOLATILITY", "HIGH_EVENT_RISK",
                                          "RELEASE_WINDOW"))}


def instrument_event_risk(cfg: MIConfig, symbol: str, events: list[dict[str, Any]], t: datetime,
                          coverage: dict[str, Any]) -> dict[str, Any]:
    """Aggregate risk for one instrument from events affecting it. With partial calendar coverage and no event in any
    window the state is UNKNOWN (absence of evidence is not evidence of a quiet calendar)."""
    rel = []
    for ev in events:
        aff = next((a for a in ev.get("affected_instruments") or [] if a["instrument"] == symbol), None)
        if aff is None:
            continue
        st = event_state(cfg, ev, t, symbol)
        rel.append({**ev, "impact": aff, **st})
    active = [r for r in rel if r["state"] != "NORMAL"]
    upcoming = sorted((r for r in rel if r["minutes_until_release"] > 0 and r.get("status") != "cancelled"),
                      key=lambda r: r["scheduled_at"])
    if active:
        top = max(active, key=lambda r: (STATE_RANK[r["state"]], IMPORTANCE.index(r["importance"])
                                         if r.get("importance") in IMPORTANCE else -1))
        state, reason = top["state"], f"{top['name']} ({top.get('currency')}, {top.get('importance') or 'unrated'})"
    elif not coverage.get("complete"):
        state, reason = "UNKNOWN", coverage.get("reason") or "Economic calendar coverage is incomplete."
    elif coverage.get("level") == "official":
        state, reason = "NORMAL", ("No relevant official release or central-bank decision inside any configured "
                                   "window. Coverage is official schedules only - see coverage.gaps.")
    else:
        state, reason = "NORMAL", "No relevant event inside any configured window."
    return {"symbol": symbol, "as_of": t, "state": state, "reason": reason,
            "next_event": upcoming[0] if upcoming else None, "active_events": active,
            "upcoming_events": upcoming[:10], "coverage": coverage,
            "note": "Analytical risk context only - not a trade restriction and not a volatility forecast."}
