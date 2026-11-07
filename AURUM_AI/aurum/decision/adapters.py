"""Thin read-only adapters to Modules 01-04, the account mirror and the Learning Agent.

Each one reports what it could not get instead of raising or inventing a value, so the decision engine can say which
input was missing. The Null* versions stand in when a source is switched off (backtests have no account history).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy.engine import Engine

from aurum.config import TIMEFRAME_SECONDS, Settings

log = logging.getLogger(__name__)


class NullIntelligence:
    def analyze(self, symbol: str, as_of: datetime) -> dict[str, Any] | None:
        return None


class IntelligenceAdapter:
    """Module 04, as of a timestamp. ``include_module03=False`` skips its own Module 03 run, which replay needs for speed:
    the decision engine already holds those results."""

    def __init__(self, service: Any, include_module03: bool = True):
        self.service, self.include_module03 = service, include_module03

    def analyze(self, symbol: str, as_of: datetime) -> dict[str, Any] | None:
        try:
            return self.service.analyze(symbol, as_of, include_module03=self.include_module03)
        except Exception as exc:  # noqa: BLE001 - a missing input is reported, it does not stop the run
            log.warning("Module 04 unavailable", extra={"symbol": symbol, "error": type(exc).__name__})
            return None


class NullAccount:
    def state(self, now: datetime) -> dict[str, Any]:
        return {"available": False, "reason": "Account mirror is not used here.", "open_positions": []}


class AccountAdapter:
    """DEMO account mirror plus the optional RISK_MAX_* limits."""

    def __init__(self, engine: Engine, settings: Settings):
        self.engine, self.settings = engine, settings

    def state(self, now: datetime) -> dict[str, Any]:
        from aurum.account.queries import AccountQueries
        s = self.settings
        limits = {"max_per_trade_pct": s.risk_max_per_trade_pct, "max_daily_loss_pct": s.risk_max_daily_loss_pct,
                  "max_open_positions": s.risk_max_open_positions, "max_total_exposure_pct": s.risk_max_total_exposure_pct,
                  "max_correlated_exposure_pct": s.risk_max_correlated_exposure_pct}
        try:
            q = AccountQueries(self.engine, s)
            snap = q.latest_snapshot()
            if snap is None:
                return {"available": False, "reason": "No account snapshot stored.", "open_positions": [], "limits": limits}
            risk = q.risk(now)
            positions = [{"symbol": t["instrument"], "side": t["side"]} for t in q.open_trades(now)]
        except Exception as exc:  # noqa: BLE001 - account tables may be absent
            log.warning("Account mirror unavailable", extra={"error": type(exc).__name__})
            return {"available": False, "reason": f"Account mirror failed ({type(exc).__name__}).", "open_positions": [],
                    "limits": limits}
        return {"available": True, "captured_at": snap.captured_at, "equity": snap.equity, "currency": snap.currency,
                "daily_drawdown_pct": risk.get("dailyDrawdownPct"), "account_risk_pct": risk.get("accountRiskPct"),
                "exposure_by_instrument": {e["name"]: e["value"] for e in risk.get("exposureByInstrument", [])},
                "open_positions": positions, "limits": limits}


class NullLearning:
    def patterns(self) -> list[dict[str, Any]]:
        return []


class LearningAdapter:
    """Patterns with finished forward trials, from the Learning Agent's stored lineage."""

    def __init__(self, path: Path):
        self.path = path

    def patterns(self) -> list[dict[str, Any]]:
        try:
            lineage = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return [{"pattern": [tuple(c) for c in t.get("pattern") or []], "passed": bool(t.get("passed"))}
                for a in lineage.get("agents", []) for t in a.get("trials", [])]


class SpreadSource:
    """Spread in points from stored candles, as of a timestamp, so replay sees the spread of the time rather than today's.
    ``cache`` keeps frames for the life of the object: right for a replay, wrong for a process that runs for days."""

    def __init__(self, candles: Any, timeframe: str = "M5", lookback: int = 500, cache: bool = False):
        self.candles, self.timeframe, self.lookback, self.cache = candles, timeframe, lookback, cache
        self._frames: dict[str, pd.DataFrame] = {}

    def latest(self, symbol: str, as_of: datetime) -> dict[str, Any]:
        if symbol not in self._frames:
            frame = self.candles.load_candles(symbol, self.timeframe)
            if not self.cache:
                return self._read(frame, as_of)
            self._frames[symbol] = frame
        return self._read(self._frames[symbol], as_of)

    def _read(self, df: pd.DataFrame, as_of: datetime) -> dict[str, Any]:
        if df.empty or "spread" not in df.columns:
            return {"spread_points": None, "median_spread_points": None}
        closed = df[df["ts_utc"] + timedelta(seconds=TIMEFRAME_SECONDS[self.timeframe]) <= as_of].tail(self.lookback)
        spreads = closed["spread"].dropna()
        if spreads.empty:
            return {"spread_points": None, "median_spread_points": None}
        return {"spread_points": float(spreads.iloc[-1]), "median_spread_points": float(spreads.median())}


COMMERCIAL = ("commercial", "dealer", "producer_merchant")
SPECULATIVE = ("non_commercial", "leveraged_funds", "managed_money")
SMALL = ("non_reportable",)
WINDOWS = {"12m": "historical_percentile_12m", "24m": "historical_percentile_24m", "48m": "historical_percentile_48m"}


def _category(cats: list[dict[str, Any]], names: tuple[str, ...]) -> dict[str, Any] | None:
    return next((c for c in cats if c["category"] in names), None)


def positioning_summary(report: dict[str, Any], heavy_high: float = 80.0, heavy_low: float = 20.0) -> dict[str, Any]:
    """Commercial, large and small trader positioning from a Module 02 report. Commercials heavily long against heavily short large
    speculators is flagged per rolling window where history allows. It is context: the flag is never a buy signal."""
    families = []
    for f in report.get("families") or []:
        if not f.get("available"):
            continue
        cats = f["categories"]
        commercial, large, small = _category(cats, COMMERCIAL), _category(cats, SPECULATIVE), _category(cats, SMALL)
        extreme: dict[str, dict[str, Any] | None] = {}
        for label, key in WINDOWS.items():
            c, s = (commercial or {}).get(key), (large or {}).get(key)
            extreme[label] = None if c is None or s is None else {
                "commercial_percentile": c, "large_spec_percentile": s,
                "commercial_long_vs_spec_short": c >= heavy_high and s <= heavy_low, "commercial_short_vs_spec_long": c <= heavy_low and s >= heavy_high}
        view = lambda c: None if c is None else {k: c.get(k) for k in ("label", "net_position", "weekly_net_change", "cot_index", "positioning_strength")}
        families.append({"report_type": f.get("report_type"), "commercial": view(commercial), "large_speculators": view(large), "small_traders": view(small),
                         "windows": extreme})
    return {"available": bool(families), "reason": None if families else "No stored COT positioning.", "families": families,
            "note": "Weekly context from reports already published at the analysis time. Not an intraday trigger or a signal."}


class CotPositioning:
    """Module 02 detail as of a timestamp. An instrument with no mapping reports that and nothing else."""

    def __init__(self, engine: Engine):
        self.engine = engine

    def __call__(self, symbol: str, as_of: datetime) -> dict[str, Any]:
        try:
            from aurum.cot.config import load_cot_config
            from aurum.cot.intelligence import instrument_report
            from aurum.cot.repository import CotRepository
            repo, cfg = CotRepository(self.engine), load_cot_config()
            if not repo.schema_ready() or not cfg.mappings_for(symbol):
                return {"available": False, "reason": f"No COT mapping or data for {symbol}."}
            ids = {(m["aurum_symbol"], m["report_type"]): m["id"] for m in repo.mappings()}
            return positioning_summary(instrument_report(repo, cfg, symbol, ids, None, as_of=as_of))
        except Exception as exc:  # noqa: BLE001 - COT is optional context
            log.warning("COT positioning unavailable", extra={"symbol": symbol, "error": type(exc).__name__})
            return {"available": False, "reason": f"COT positioning failed ({type(exc).__name__})."}


def utc(t: datetime | None) -> datetime:
    if t is None:
        return datetime.now(timezone.utc)
    return t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t.astimezone(timezone.utc)
