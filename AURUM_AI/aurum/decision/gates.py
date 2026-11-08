"""Hard safety gates. Each returns pass, warn or fail with the evidence and thresholds it used; any fail blocks a candidate.

Missing data is never a pass: an unknown calendar, an empty account mirror or an unknown spread is a warning that says so.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Iterable

from aurum.config import TIMEFRAME_SECONDS
from aurum.decision.candidates.levels import risk_reward
from aurum.decision.candidates.setups import BIAS, Candidate
from aurum.decision.config import DecisionConfig
from aurum.decision.context import DecisionContext, cot_lean

PASS, WARN, FAIL = "pass", "warn", "fail"
SIDE = {"long": "buy", "short": "sell"}


@dataclass
class GateResult:
    gate: str
    result: str
    evidence: dict[str, Any]
    thresholds: dict[str, Any] = field(default_factory=dict)
    confidence_cap: float | None = None


def data_quality(ctx: DecisionContext, c: Candidate, cfg: DecisionConfig, active: Iterable[Any]) -> GateResult:
    g = cfg.gates
    limit = {"stale_candle_multiple": g["stale_candle_multiple"], "max_invalid_candles": g["max_invalid_candles"],
             "quality_lookback_candles": g["quality_lookback_candles"]}
    problems: list[str] = []
    market_open = ctx.sessions.get("market_open")
    for tf in dict.fromkeys([c.timeframe, *cfg.htf_timeframes]):
        a = ctx.analyses.get(tf)
        if a is None or a.status != "ok":
            problems.append(f"{tf}: Module 03 status is {getattr(a, 'status', 'missing')}.")
            continue
        age = (ctx.as_of - a.as_of).total_seconds()
        if market_open is not False and age > g["stale_candle_multiple"] * TIMEFRAME_SECONDS[tf]:
            problems.append(f"{tf}: last closed candle is {age / 3600:.1f} h old.")
        recent = ctx.recent_quality.get(tf) or {}
        if recent.get("invalid", 0) > g["max_invalid_candles"]:
            problems.append(f"{tf}: {recent['invalid']} invalid candles in the lookback.")
        if recent.get("gaps", 0):
            problems.append(f"{tf}: {recent['gaps']} unexpected gaps in the lookback.")
    return GateResult("data_quality", FAIL if problems else PASS, {"problems": problems}, limit)


def event_risk(ctx: DecisionContext, c: Candidate, cfg: DecisionConfig, active: Iterable[Any]) -> GateResult:
    state = ctx.event_state
    risk = (ctx.intelligence or {}).get("event_risk") or {}
    evidence = {"state": state, "reason": risk.get("reason"), "coverage": risk.get("coverage")}
    limit = {"blocked": ["HIGH_EVENT_RISK", "RELEASE_WINDOW"]}
    if state in limit["blocked"]:
        return GateResult("event_risk", FAIL, evidence, limit)
    if state == "UNKNOWN":
        # Missing calendar coverage, never a quiet calendar.
        cap = cfg.gates["unknown_event_confidence_cap"]
        return GateResult("event_risk", WARN, evidence, {**limit, "confidence_cap": cap}, cap)
    return GateResult("event_risk", WARN if state != "NORMAL" else PASS, evidence, limit)


def _minutes_to_weekly_close(ctx: DecisionContext) -> float | None:
    hours = getattr(ctx.instrument, "trading_hours", None)
    if hours is None:
        return None
    local = ctx.as_of.astimezone(hours.tz)
    close = datetime.combine(local.date(), hours.weekly_close_time, tzinfo=hours.tz)
    close += timedelta(days=(hours.weekly_close_day - local.weekday()) % 7)
    if close < local:
        close += timedelta(days=7)
    return (close - local).total_seconds() / 60


def _minutes_to_daily_break(ctx: DecisionContext) -> float | None:
    hours = getattr(ctx.instrument, "trading_hours", None)
    breaks = getattr(hours, "daily_breaks", ()) if hours else ()
    if not breaks:
        return None
    local = ctx.as_of.astimezone(hours.tz)
    gaps = []
    for start, _end in breaks:
        at = datetime.combine(local.date(), start, tzinfo=hours.tz)
        gaps.append(((at if at >= local else at + timedelta(days=1)) - local).total_seconds() / 60)
    return min(gaps)


def market_open(ctx: DecisionContext, c: Candidate, cfg: DecisionConfig, active: Iterable[Any]) -> GateResult:
    g = cfg.gates
    limit = {"rollover_buffer_minutes": g["rollover_buffer_minutes"], "weekly_close_buffer_minutes": g["weekly_close_buffer_minutes"]}
    if ctx.intelligence is None:
        return GateResult("market_open", WARN, {"reason": "Module 04 sessions are unavailable, so market hours are unknown."}, limit)
    sessions = ctx.sessions
    evidence: dict[str, Any] = {"market_open": sessions.get("market_open"), "active_sessions": sessions.get("active_sessions")}
    if sessions.get("market_open") is False:
        return GateResult("market_open", FAIL, {**evidence, "reason": "Market is closed."}, limit)
    to_close, to_break = _minutes_to_weekly_close(ctx), _minutes_to_daily_break(ctx)
    evidence.update(minutes_to_weekly_close=to_close, minutes_to_daily_break=to_break)
    if to_close is not None and to_close <= g["weekly_close_buffer_minutes"]:
        return GateResult("market_open", FAIL, {**evidence, "reason": "Too close to the weekly close."}, limit)
    if to_break is not None and to_break <= g["rollover_buffer_minutes"]:
        return GateResult("market_open", FAIL, {**evidence, "reason": "Too close to the daily break."}, limit)
    blocked = set((g["session_filters"].get(c.template) or {}).get(c.symbol) or [])
    hit = sorted(blocked & set(sessions.get("active_sessions") or []))
    if hit:
        return GateResult("market_open", FAIL, {**evidence, "reason": f"Template is filtered in session {', '.join(hit)}."}, limit)
    return GateResult("market_open", PASS, evidence, limit)


def spread_volatility(ctx: DecisionContext, c: Candidate, cfg: DecisionConfig, active: Iterable[Any]) -> GateResult:
    g = cfg.gates
    limit = {"max_spread_multiple": g["max_spread_multiple"], "min_stop_spread_multiple": g["min_stop_spread_multiple"],
             "blocked_volatility_regimes": g["blocked_volatility_regimes"]}
    spread, median = ctx.spread.get("spread_points"), ctx.spread.get("median_spread_points")
    price_spread = ctx.spread_price
    stop_distance = abs(c.entry_mid - c.stop_loss)
    evidence = {"spread_points": spread, "median_spread_points": median, "regime": ctx.regime, "stop_distance": stop_distance}
    if ctx.regime in g["blocked_volatility_regimes"]:
        return GateResult("spread_volatility", FAIL, {**evidence, "reason": f"Volatility regime is {ctx.regime}."}, limit)
    if spread is not None and median and spread > g["max_spread_multiple"] * median:
        return GateResult("spread_volatility", FAIL, {**evidence, "reason": "Spread is far above its median."}, limit)
    if price_spread is not None and stop_distance < g["min_stop_spread_multiple"] * price_spread:
        return GateResult("spread_volatility", FAIL, {**evidence, "reason": "Stop is too close for this spread."}, limit)
    if price_spread is None or ctx.regime == "UNKNOWN":
        return GateResult("spread_volatility", WARN, {**evidence, "reason": "Spread or volatility regime is unknown."}, limit)
    return GateResult("spread_volatility", PASS, evidence, limit)


def net_risk_reward(c: Candidate, spread_price: float | None) -> float:
    """Risk-reward after paying the spread at entry, from the zone mid-point (stored prices are bid prices)."""
    entry = c.entry_mid + (spread_price or 0.0) * (1 if c.direction == "long" else -1)
    return risk_reward(entry, c.stop_loss, c.take_profit)


def risk_reward_gate(ctx: DecisionContext, c: Candidate, cfg: DecisionConfig, active: Iterable[Any]) -> GateResult:
    minimum = cfg.gates["min_rr"]
    net = net_risk_reward(c, ctx.spread_price)
    evidence = {"gross_rr": c.risk_reward, "net_rr": round(net, 2)}
    limit = {"min_rr": minimum}
    if net < minimum:
        return GateResult("risk_reward", FAIL, evidence, limit)
    if ctx.spread_price is None:
        return GateResult("risk_reward", WARN, {**evidence, "reason": "Spread is unknown, so this is before spread."}, limit)
    return GateResult("risk_reward", PASS, evidence, limit)


def structure_conflict(ctx: DecisionContext, c: Candidate, cfg: DecisionConfig, active: Iterable[Any]) -> GateResult:
    want = BIAS[c.direction]
    opposed, unknown = [], []
    for tf in cfg.htf_timeframes:
        external = (ctx.analyses[tf].structure if tf in ctx.analyses else {}).get("external")
        bias = getattr(external, "bias", None)
        if bias is None:
            unknown.append(tf)
        elif bias in ("bullish", "bearish") and bias != want:
            opposed.append(tf)
    required = cfg.template(c.template)["require_alignment"]
    evidence = {"direction": want, "opposing_timeframes": opposed, "unknown_timeframes": unknown}
    limit = {"require_alignment": required}
    if opposed and required:
        return GateResult("structure_conflict", FAIL, evidence, limit)
    return GateResult("structure_conflict", WARN if opposed or unknown else PASS, evidence, limit)


def account_risk(ctx: DecisionContext, c: Candidate, cfg: DecisionConfig, active: Iterable[Any]) -> GateResult:
    a = ctx.account
    limits = a.get("limits") or {}
    if not a.get("available"):
        return GateResult("account_risk", WARN, {"reason": a.get("reason") or "Account mirror is empty."}, limits)
    age = (ctx.as_of - a["captured_at"]).total_seconds() / 60
    evidence: dict[str, Any] = {"snapshot_age_minutes": round(age, 1), "open_positions": len(a["open_positions"]),
                                "daily_drawdown_pct": a.get("daily_drawdown_pct")}
    if age > cfg.gates["account_stale_minutes"]:
        return GateResult("account_risk", WARN, {**evidence, "reason": "Account mirror is stale."}, limits)
    breaches = []
    if limits.get("max_daily_loss_pct") and (a.get("daily_drawdown_pct") or 0) >= limits["max_daily_loss_pct"]:
        breaches.append("Daily loss limit reached.")
    if limits.get("max_open_positions") and len(a["open_positions"]) >= limits["max_open_positions"]:
        breaches.append("Maximum open positions reached.")
    exposure = a.get("exposure_by_instrument") or {}
    if limits.get("max_total_exposure_pct") and sum(exposure.values()) >= limits["max_total_exposure_pct"]:
        breaches.append("Total exposure limit reached.")
    if limits.get("max_correlated_exposure_pct"):
        for group in cfg.gates["correlation_groups"]:
            if c.symbol in group and sum(exposure.get(s, 0.0) for s in group) >= limits["max_correlated_exposure_pct"]:
                breaches.append(f"Correlated exposure limit reached for {', '.join(group)}.")
    if breaches:
        return GateResult("account_risk", FAIL, {**evidence, "breaches": breaches}, limits)
    return GateResult("account_risk", PASS, evidence, limits)


def duplication(ctx: DecisionContext, c: Candidate, cfg: DecisionConfig, active: Iterable[Any]) -> GateResult:
    positions = [p for p in ctx.account.get("open_positions", []) if p["symbol"] == c.symbol and p["side"] == SIDE[c.direction]]
    others = [a for a in active if a["symbol"] == c.symbol and a["direction"] == c.direction and a["key"] != c.key]
    evidence = {"open_positions": len(positions), "active_candidates": [a["key"] for a in others]}
    return GateResult("duplication", FAIL if positions or others else PASS, evidence)


def cot(ctx: DecisionContext, c: Candidate, cfg: DecisionConfig, active: Iterable[Any]) -> GateResult:
    """Warn only, by design: COT is a weekly backdrop, not a timing signal."""
    data = ctx.cot
    if not data.get("available"):
        return GateResult("cot", WARN, {"status": "unavailable", "reason": data.get("reason")})
    lean = cot_lean(data)
    opposing = "bearish" if c.direction == "long" else "bullish"
    evidence = {"stance": data.get("stance"), "lean": data.get("lean"), "confidence": data.get("confidence")}
    if opposing in lean and cfg.gates["cot_oppose_warns"]:
        return GateResult("cot", WARN, {**evidence, "reason": "COT positioning leans against this direction."})
    return GateResult("cot", PASS, evidence)


GATES = (data_quality, event_risk, market_open, spread_volatility, risk_reward_gate, structure_conflict, account_risk,
         duplication, cot)


def run_gates(ctx: DecisionContext, c: Candidate, cfg: DecisionConfig, active: Iterable[Any] = ()) -> list[GateResult]:
    pending = list(active)
    return [g(ctx, c, cfg, pending) for g in GATES]


def first_failure(results: Iterable[GateResult]) -> str | None:
    return next((r.gate for r in results if r.result == FAIL), None)


def confidence_cap(results: Iterable[GateResult]) -> float | None:
    caps = [r.confidence_cap for r in results if r.confidence_cap is not None]
    return min(caps) if caps else None
