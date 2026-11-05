"""DecisionContext: everything the engine knows about one instrument as of a timestamp.

Live mode is simply ``as_of = now``. Nothing here reads past ``as_of``: Module 03 aligns candles to it, Module 04 and
Module 02 are point-in-time, and spreads come from candles closed by then.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from aurum.config import InstrumentConfig, TIMEFRAME_SECONDS

from .adapters import utc
from .config import DecisionConfig


@dataclass
class DecisionContext:
    symbol: str
    as_of: datetime
    instrument: Any
    spec: dict[str, Any] | None
    analyses: dict[str, Any]                 # timeframe -> Module 03 TimeframeAnalysis
    confluence: Any                          # Module 03 ConfluenceReport
    intelligence: dict[str, Any] | None      # Module 04 analysis
    cot: dict[str, Any]
    account: dict[str, Any]
    patterns: list[dict[str, Any]]
    spread: dict[str, Any]
    availability: dict[str, dict[str, Any]] = field(default_factory=dict)
    input_hash: str = ""
    recent_quality: dict[str, dict[str, int]] = field(default_factory=dict)   # timeframe -> gaps, invalid
    cot_positioning: dict[str, Any] = field(default_factory=lambda: {"available": False, "reason": "Not requested."})

    @property
    def consensus(self) -> str | None:
        return (getattr(self.confluence, "alignment", None) or {}).get("consensus")

    @property
    def event_state(self) -> str:
        return ((self.intelligence or {}).get("event_risk") or {}).get("state") or "UNKNOWN"

    @property
    def regime(self) -> str:
        return ((self.intelligence or {}).get("volatility") or {}).get("regime") or "UNKNOWN"

    @property
    def tags(self) -> list[str]:
        return ((self.intelligence or {}).get("conditions") or {}).get("tags") or []

    @property
    def sessions(self) -> dict[str, Any]:
        return (self.intelligence or {}).get("sessions") or {}

    @property
    def point(self) -> float | None:
        return (self.spec or {}).get("point")

    @property
    def spread_price(self) -> float | None:
        """Current spread in price units, or None when the spread or the point size is unknown."""
        points = self.spread.get("spread_points")
        return None if points is None or not self.point else points * self.point


class ContextBuilder:
    def __init__(self, cfg: DecisionConfig, technical: Any, intelligence: Any, account: Any, learning: Any, spreads: Any,
                 specs: Any, cot: Any = None):
        self.cfg, self.technical, self.intelligence = cfg, technical, intelligence
        self.account, self.learning, self.spreads, self.specs = account, learning, spreads, specs
        self.cot = cot

    def _recent_quality(self, symbol: str, analyses: dict[str, Any], t: datetime) -> dict[str, dict[str, int]]:
        """Gaps and invalid candles inside the gate's lookback. Module 03 counts them over its whole window, up to 2000 candles,
        where one old hole would block every candidate for years. Exact times are read only when that count is not zero."""
        out: dict[str, dict[str, int]] = {}
        for tf, a in analyses.items():
            dq = a.data_quality or {}
            out[tf] = _clean_quality()
            if not (dq.get("unexpected_gaps") or dq.get("invalid_candles")):
                continue
            since = t - timedelta(seconds=candle_seconds(tf) * self.cfg.gates["quality_lookback_candles"])
            series = self.technical.load_series(symbol, tf, t)
            out[tf]["gaps"] = sum(1 for i in series.quality_issues if i["issue_type"] == "missing_candles" and utc(i["ts_utc"]) >= since)
            out[tf]["invalid"] = sum(1 for r in series.excluded_rows if str(r["reason"]).startswith("invalid") and utc(r["ts_utc"]) >= since)
        return out

    def build(self, symbol: str, as_of: datetime | None = None) -> DecisionContext:
        t = utc(as_of)
        inst: InstrumentConfig = self.technical.instrument(symbol)
        timeframes = list(dict.fromkeys([*self.cfg.htf_timeframes, *self.cfg.entry_timeframes, *self.cfg.bias["timeframes"]]))
        analyses = {tf: self.technical.analyze(inst.symbol, tf, t, include_cot=False) for tf in timeframes}
        confluence = self.technical.analyze_confluence(inst.symbol, as_of=t, include_cot=False)
        cot = self.technical.cot_context(inst.symbol, t, True)
        intel = self.intelligence.analyze(inst.symbol, t)
        spec = self.specs(inst.symbol)
        account = self.account.state(t)
        spread = self.spreads.latest(inst.symbol, t)
        recent = self._recent_quality(inst.symbol, analyses, t)
        ctx = DecisionContext(inst.symbol, t, inst, spec, analyses, confluence, intel, cot, account,
                              self.learning.patterns(), spread, recent_quality=recent)
        if self.cot is not None:
            ctx.cot_positioning = self.cot(inst.symbol, t)
        ctx.availability = _availability(ctx)
        ctx.input_hash = _input_hash(ctx)
        return ctx


def cot_lean(cot: dict[str, Any]) -> str:
    """The direction of COT positioning, in lower case. Module 02 sets ``lean`` only when its stance is neutral, so a clear stance
    arrives with ``lean`` empty: reading ``lean`` alone would treat the strongest positioning as having no direction."""
    return str(cot.get("lean") or cot.get("stance") or "").lower()


def _clean_quality() -> dict[str, int]:
    return {"gaps": 0, "invalid": 0}


def _availability(ctx: DecisionContext) -> dict[str, dict[str, Any]]:
    def entry(ok: bool, reason: str | None = None, **extra: Any) -> dict[str, Any]:
        return {"available": ok, "reason": None if ok else reason, **extra}

    out = {f"module_03_{tf}": entry(a.status == "ok", "; ".join(a.messages) or a.status, last_candle=a.last_candle)
           for tf, a in ctx.analyses.items()}
    out["module_02_cot"] = entry(bool(ctx.cot.get("available")), ctx.cot.get("reason"))
    out["module_04"] = entry(ctx.intelligence is not None, "Module 04 analysis failed or is not used here.")
    if ctx.intelligence is not None:
        coverage = (ctx.intelligence.get("event_risk") or {}).get("coverage") or {}
        out["economic_calendar"] = entry(bool(coverage.get("complete")), coverage.get("reason") or "Coverage is incomplete.",
                                         level=coverage.get("level"))
    out["account"] = entry(bool(ctx.account.get("available")), ctx.account.get("reason"),
                           captured_at=ctx.account.get("captured_at"))
    out["instrument_spec"] = entry(ctx.spec is not None, "No stored instrument specification.")
    out["spread"] = entry(ctx.spread_price is not None, "No spread on stored candles, or no point size.")
    out["learning"] = entry(bool(ctx.patterns), "No finished Learning Agent trials.")
    return out


def _input_hash(ctx: DecisionContext) -> str:
    """Identifies what the run saw. Candle times are included, so it changes as new candles close."""
    state = {"symbol": ctx.symbol, "candles": {tf: str(a.last_candle) for tf, a in ctx.analyses.items()},
             "consensus": ctx.consensus, "event": ctx.event_state, "regime": ctx.regime, "tags": sorted(ctx.tags),
             "cot": cot_lean(ctx.cot), "positions": ctx.account.get("open_positions"), "spread": ctx.spread}
    return hashlib.sha256(json.dumps(state, sort_keys=True, default=str).encode()).hexdigest()


def candle_seconds(timeframe: str) -> int:
    return TIMEFRAME_SECONDS[timeframe]
