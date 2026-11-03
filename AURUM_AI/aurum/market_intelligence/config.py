"""Module 04 configuration: ``config/market_intelligence.yaml`` + ``config/central_banks.yaml``.

Session / kill-zone windows are wall-clock times in an IANA zone (``zoneinfo``), so daylight-saving changes are
resolved by the tz database and never by fixed UTC offsets. Everything is validated when loaded (unknown zone,
bad time, unknown weekday -> ValueError naming the key), and the effective configuration has a fingerprint that is
stored with every persisted result.
"""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

from aurum.config import DAYS, PROJECT_ROOT

MODULE_VERSION = "4.0.0"
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "market_intelligence.yaml"
DEFAULT_BANKS = PROJECT_ROOT / "config" / "central_banks.yaml"
UTC = timezone.utc


class ConfigError(ValueError):
    pass


@lru_cache(maxsize=None)
def zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ConfigError(f"Unknown IANA time zone '{name}' (on Windows: pip install tzdata).") from exc


def _time(v: Any, where: str) -> time:
    try:
        return time.fromisoformat(str(v))
    except ValueError as exc:
        raise ConfigError(f"{where}: '{v}' is not a HH:MM time.") from exc


def _days(v: Any, where: str) -> frozenset[int]:
    out = set()
    for d in v or DAYS[:5]:
        d3 = str(d).strip().upper()[:3]
        if d3 not in DAYS:
            raise ConfigError(f"{where}: unknown weekday '{d}'. Use MON..SUN.")
        out.add(DAYS.index(d3))
    return frozenset(out)


@dataclass(frozen=True)
class WindowDef:
    """A recurring local-time window (trading session or kill zone)."""

    name: str
    kind: str                                    # session | kill_zone
    timezone: str
    open: time
    close: time
    weekdays: frozenset[int]                     # local weekday on which the window STARTS (0 = Monday)
    holidays: frozenset[date] = frozenset()      # local dates on which it does not open
    pre_minutes: int = 0
    post_minutes: int = 0

    @property
    def tz(self) -> ZoneInfo:
        return zone(self.timezone)

    @property
    def crosses_midnight(self) -> bool:
        return self.close <= self.open

    def occurrence(self, local_day: date) -> tuple[datetime, datetime] | None:
        """UTC [start, end) of the window that starts on ``local_day`` (local calendar date), or None when the
        window does not run that day (weekday / holiday). Local times are converted with the zone's rules for
        that specific date, so London BST/GMT, New York EDT/EST and Sydney AEDT/AEST are each handled."""
        if local_day.weekday() not in self.weekdays or local_day in self.holidays:
            return None
        start_local = datetime.combine(local_day, self.open, tzinfo=self.tz)
        end_day = local_day + timedelta(days=1) if self.crosses_midnight else local_day
        end_local = datetime.combine(end_day, self.close, tzinfo=self.tz)
        return start_local.astimezone(UTC), end_local.astimezone(UTC)

    def describe(self) -> dict[str, Any]:
        return {"name": self.name, "kind": self.kind, "timezone": self.timezone, "open": self.open.strftime("%H:%M"),
                "close": self.close.strftime("%H:%M"), "weekdays": [DAYS[d] for d in sorted(self.weekdays)],
                "holidays": sorted(h.isoformat() for h in self.holidays), "crosses_midnight": self.crosses_midnight,
                "pre_minutes": self.pre_minutes, "post_minutes": self.post_minutes}


def _window(name: str, kind: str, d: dict[str, Any]) -> WindowDef:
    where = f"{kind} '{name}'"
    if not isinstance(d, dict) or "timezone" not in d:
        raise ConfigError(f"{where} needs timezone, open and close.")
    zone(str(d["timezone"]))
    hols = frozenset(date.fromisoformat(str(h)) for h in d.get("holidays") or [])
    return WindowDef(name, kind, str(d["timezone"]), _time(d.get("open"), where), _time(d.get("close"), where),
                     _days(d.get("weekdays"), where), hols, int(d.get("pre_minutes") or 0),
                     int(d.get("post_minutes") or 0))


@dataclass(frozen=True)
class OverlapDef:
    name: str
    sessions: tuple[str, str]
    transition_minutes: int = 0


@dataclass
class MIConfig:
    raw: dict[str, Any]
    sessions: dict[str, WindowDef]
    kill_zones: dict[str, WindowDef]
    overlaps: list[OverlapDef]
    instrument_kill_zones: dict[str, list[str]]
    trading_day_tz: str
    trading_day_roll: time
    banks: dict[str, Any] = field(default_factory=dict)

    # convenience accessors ------------------------------------------------------------------------
    def section(self, key: str) -> dict[str, Any]:
        return self.raw.get(key) or {}

    @property
    def fingerprint(self) -> str:
        blob = json.dumps({"mi": self.raw, "banks": self.banks}, sort_keys=True, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()[:16]

    @property
    def methodology_version(self) -> str:
        return str(self.section("scoring").get("methodology_version", MODULE_VERSION))

    def kill_zones_for(self, symbol: str | None) -> list[WindowDef]:
        names = self.instrument_kill_zones.get((symbol or "").upper())
        if not names:
            return list(self.kill_zones.values())
        return [self.kill_zones[n] for n in names]

    def windows(self) -> dict[str, WindowDef]:
        return {**self.sessions, **self.kill_zones}

    def to_dict(self) -> dict[str, Any]:
        return {"module_version": MODULE_VERSION, "fingerprint": self.fingerprint,
                "sessions": [w.describe() for w in self.sessions.values()],
                "kill_zones": [w.describe() for w in self.kill_zones.values()],
                "overlaps": [{"name": o.name, "sessions": list(o.sessions), "transition_minutes": o.transition_minutes}
                             for o in self.overlaps],
                "instrument_kill_zones": self.instrument_kill_zones,
                "trading_day": {"timezone": self.trading_day_tz, "roll": self.trading_day_roll.strftime("%H:%M")},
                **{k: v for k, v in self.raw.items() if k not in ("sessions", "kill_zones", "overlaps",
                                                                  "instrument_kill_zones", "trading_day")}}


def _deep_merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        out[k] = _deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else copy.deepcopy(v)
    return out


def build_config(raw: dict[str, Any], banks: dict[str, Any] | None = None) -> MIConfig:
    sessions = {n: _window(n, "session", d) for n, d in (raw.get("sessions") or {}).items()}
    if not sessions:
        raise ConfigError("market_intelligence.yaml defines no sessions.")
    kzs = {n: _window(n, "kill_zone", d) for n, d in (raw.get("kill_zones") or {}).items()}
    overlaps = []
    for o in raw.get("overlaps") or []:
        pair = tuple(o.get("sessions") or ())
        if len(pair) != 2 or any(p not in sessions for p in pair):
            raise ConfigError(f"overlap '{o.get('name')}' must name two configured sessions, got {pair}.")
        overlaps.append(OverlapDef(str(o["name"]), (str(pair[0]), str(pair[1])), int(o.get("transition_minutes") or 0)))
    ikz = {str(k).upper(): list(v) for k, v in (raw.get("instrument_kill_zones") or {}).items()}
    for sym, names in ikz.items():
        bad = [n for n in names if n not in kzs]
        if bad:
            raise ConfigError(f"instrument_kill_zones.{sym}: unknown kill zone(s) {bad}.")
    td = raw.get("trading_day") or {}
    tz_name = str(td.get("timezone") or "America/New_York")
    zone(tz_name)
    return MIConfig(raw, sessions, kzs, overlaps, ikz, tz_name, _time(td.get("roll", "17:00"), "trading_day"),
                    banks or {})


def load_mi_config(path: Path | None = None, banks_path: Path | None = None,
                   overrides: dict[str, Any] | None = None) -> MIConfig:
    path = path or DEFAULT_CONFIG
    if not path.exists():
        raise ConfigError(f"Module 04 configuration not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if overrides:
        raw = _deep_merge(raw, overrides)
    banks_path = banks_path or DEFAULT_BANKS
    banks = yaml.safe_load(banks_path.read_text(encoding="utf-8")) if banks_path.exists() else {}
    overlay_path = banks_path.with_name(banks_path.stem + ".meetings.yaml")
    if banks and overlay_path.exists():                 # official dates written by `intelligence meetings-update`
        from aurum.market_intelligence.meetings import merge_overlay
        banks = merge_overlay(banks, yaml.safe_load(overlay_path.read_text(encoding="utf-8")) or {})
    return build_config(raw, banks or {})
