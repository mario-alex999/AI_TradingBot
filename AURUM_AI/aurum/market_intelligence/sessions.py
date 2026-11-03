"""Trading-session calendar: sessions, kill zones and overlaps in UTC from local-time definitions.

Every window is expanded per LOCAL calendar day with ``zoneinfo``, so each zone's own daylight-saving rules apply
(London and New York change on different weekends in March and Oct/Nov; Sydney changes in the opposite season).
Windows that cross local midnight end on the next local day. Weekends and configured holidays produce no
occurrence. When an instrument is given, a window is only "active" if that instrument's market is open
(Module 01 trading hours: weekly close/open, daily breaks, holidays).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from aurum.config import TradingHours
from aurum.market_intelligence.config import MIConfig, OverlapDef, WindowDef
from aurum.services.validation_service import is_market_open

UTC = timezone.utc


@dataclass(frozen=True)
class Interval:
    name: str
    kind: str                 # session | kill_zone | overlap
    start: datetime           # UTC, inclusive
    end: datetime             # UTC, exclusive
    local_date: str | None = None
    timezone: str | None = None

    def contains(self, t: datetime) -> bool:
        return self.start <= t < self.end

    @property
    def minutes(self) -> float:
        return (self.end - self.start).total_seconds() / 60

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "kind": self.kind, "start_utc": self.start, "end_utc": self.end,
                "local_date": self.local_date, "timezone": self.timezone, "duration_minutes": round(self.minutes)}


def _utc(t: datetime) -> datetime:
    if t.tzinfo is None:
        raise ValueError("Naive datetime: Module 04 works with UTC-aware datetimes only.")
    return t.astimezone(UTC)


def occurrences(w: WindowDef, start: datetime, end: datetime) -> list[Interval]:
    """All occurrences of ``w`` that overlap [start, end), sorted by start."""
    start, end = _utc(start), _utc(end)
    d = start.astimezone(w.tz).date() - timedelta(days=2)
    last = end.astimezone(w.tz).date() + timedelta(days=1)
    out: list[Interval] = []
    while d <= last:
        occ = w.occurrence(d)
        if occ and occ[1] > start and occ[0] < end:
            out.append(Interval(w.name, w.kind, occ[0], occ[1], d.isoformat(), w.timezone))
        d += timedelta(days=1)
    return out


def previous_occurrence(w: WindowDef, t: datetime, completed: bool = True, max_days: int = 21) -> Interval | None:
    """Latest occurrence that has ENDED by ``t`` (completed=True) or STARTED by ``t`` (completed=False)."""
    t = _utc(t)
    occ = occurrences(w, t - timedelta(days=max_days), t + timedelta(days=1))
    cands = [o for o in occ if (o.end <= t if completed else o.start <= t)]
    return cands[-1] if cands else None


def next_occurrence(w: WindowDef, t: datetime, max_days: int = 21) -> Interval | None:
    t = _utc(t)
    occ = [o for o in occurrences(w, t, t + timedelta(days=max_days)) if o.start > t]
    return occ[0] if occ else None


def current_occurrence(w: WindowDef, t: datetime) -> Interval | None:
    t = _utc(t)
    return next((o for o in occurrences(w, t - timedelta(days=2), t + timedelta(days=1)) if o.contains(t)), None)


def _minutes(a: datetime, b: datetime) -> float:
    return round((b - a).total_seconds() / 60, 1)


def window_status(w: WindowDef, t: datetime, hours: TradingHours | None = None) -> dict[str, Any]:
    """Status of one window at ``t``. ``status``: open | pre_session | post_session | closed | market_closed."""
    t = _utc(t)
    cur, nxt = current_occurrence(w, t), next_occurrence(w, t)
    prev = previous_occurrence(w, t, completed=True)
    market_open = is_market_open(t, hours) if hours is not None else None
    status = "closed"
    if cur:
        status = "open"
    elif nxt and w.pre_minutes and 0 < (nxt.start - t).total_seconds() <= w.pre_minutes * 60:
        status = "pre_session"
    elif prev and w.post_minutes and 0 <= (t - prev.end).total_seconds() < w.post_minutes * 60:
        status = "post_session"
    active = cur is not None and market_open is not False
    if cur is not None and market_open is False:
        status = "market_closed"
    local_now = t.astimezone(w.tz)
    return {"name": w.name, "kind": w.kind, "timezone": w.timezone, "local_time": local_now.strftime("%Y-%m-%d %H:%M"),
            "local_open": w.open.strftime("%H:%M"), "local_close": w.close.strftime("%H:%M"),
            "utc_offset_hours": local_now.utcoffset().total_seconds() / 3600 if local_now.utcoffset() else 0.0,
            "dst": bool(local_now.dst()), "status": status, "active": active,
            "market_open": market_open,
            "current": cur.to_dict() if cur else None, "next": nxt.to_dict() if nxt else None,
            "previous": prev.to_dict() if prev else None,
            "minutes_until_open": _minutes(t, nxt.start) if nxt and not cur else (0.0 if cur else None),
            "minutes_until_close": _minutes(t, cur.end) if cur else None,
            "minutes_since_close": _minutes(prev.end, t) if prev and not cur else None}


def overlap_intervals(cfg: MIConfig, o: OverlapDef, start: datetime, end: datetime) -> list[Interval]:
    """Intersections of the two sessions' occurrences. When they do not intersect and ``transition_minutes`` is set,
    a hand-over window of that length centred on the change-over is produced instead (kind 'transition')."""
    a_def, b_def = cfg.sessions[o.sessions[0]], cfg.sessions[o.sessions[1]]
    pad = timedelta(days=2)
    a_occ, b_occ = occurrences(a_def, start - pad, end + pad), occurrences(b_def, start - pad, end + pad)
    out: list[Interval] = []
    for a in a_occ:
        for b in b_occ:
            s, e = max(a.start, b.start), min(a.end, b.end)
            if s < e:
                out.append(Interval(o.name, "overlap", s, e, a.local_date, None))
            elif o.transition_minutes:
                gap = (b.start - a.end).total_seconds() / 60
                if 0 <= gap <= o.transition_minutes:
                    half = timedelta(minutes=o.transition_minutes / 2)
                    out.append(Interval(o.name, "transition", a.end - half, b.start + half, a.local_date, None))
    out = sorted({(i.start, i.end): i for i in out}.values(), key=lambda i: i.start)
    return [i for i in out if i.end > start and i.start < end]


def overlap_status(cfg: MIConfig, t: datetime, hours: TradingHours | None = None) -> list[dict[str, Any]]:
    t = _utc(t)
    res = []
    for prio, o in enumerate(cfg.overlaps, start=1):
        ivs = overlap_intervals(cfg, o, t - timedelta(days=3), t + timedelta(days=10))
        cur = next((i for i in ivs if i.contains(t)), None)
        nxt = next((i for i in ivs if i.start > t), None)
        prev = next((i for i in reversed(ivs) if i.end <= t), None)
        mo = is_market_open(t, hours) if hours is not None else None
        res.append({"name": o.name, "priority": prio, "sessions": list(o.sessions),
                    "kind": (cur or nxt or prev).kind if (cur or nxt or prev) else "overlap",
                    "active": cur is not None and mo is not False, "market_open": mo,
                    "current": cur.to_dict() if cur else None, "next": nxt.to_dict() if nxt else None,
                    "previous": prev.to_dict() if prev else None,
                    "minutes_remaining": _minutes(t, cur.end) if cur else None,
                    "minutes_until_start": _minutes(t, nxt.start) if nxt and not cur else None})
    return res


def sessions_snapshot(cfg: MIConfig, t: datetime, symbol: str | None = None,
                      hours: TradingHours | None = None) -> dict[str, Any]:
    """Everything about sessions at ``t``: per-session status, active / upcoming / previous session, overlaps and
    kill zones (per-instrument definitions when ``symbol`` has an override)."""
    t = _utc(t)
    sess = [window_status(w, t, hours) for w in cfg.sessions.values()]
    kzs = [window_status(w, t, hours) for w in cfg.kill_zones_for(symbol)]
    ovs = overlap_status(cfg, t, hours)
    active = [s["name"] for s in sess if s["active"]]
    upcoming = sorted((s for s in sess if s["next"] and not s["current"]), key=lambda s: s["next"]["start_utc"])
    previous = sorted((s for s in sess if s["previous"]), key=lambda s: s["previous"]["end_utc"])
    return {"as_of": t, "symbol": symbol, "market_open": is_market_open(t, hours) if hours is not None else None,
            "active_sessions": active,
            "upcoming_session": {"name": upcoming[0]["name"], **upcoming[0]["next"],
                                 "minutes_until_open": upcoming[0]["minutes_until_open"]} if upcoming else None,
            "previous_session": {"name": previous[-1]["name"], **previous[-1]["previous"]} if previous else None,
            "in_overlap": any(o["active"] for o in ovs),
            "active_kill_zones": [k["name"] for k in kzs if k["active"]],
            "next_kill_zone": min(({"name": k["name"], **k["next"], "minutes_until_open": k["minutes_until_open"]}
                                   for k in kzs if k["next"] and not k["current"]),
                                  key=lambda k: k["start_utc"], default=None),
            "sessions": sess, "overlaps": ovs, "kill_zones": kzs,
            "note": "Session and kill-zone windows are configurable reference periods, not market rules."}


def session_history(cfg: MIConfig, start: datetime, end: datetime, names: list[str] | None = None,
                    include_kill_zones: bool = False) -> list[Interval]:
    defs = [w for w in cfg.sessions.values() if not names or w.name in names]
    if include_kill_zones:
        defs += [w for w in cfg.kill_zones.values() if not names or w.name in names]
    out = [i for w in defs for i in occurrences(w, start, end)]
    return sorted(out, key=lambda i: (i.start, i.name))
