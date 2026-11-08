"""Deterministic decision score: a transparent sum of evidence, each part with its points and one sentence.

The score measures how much of the evidence agrees. It is not a probability of profit, and realised results are shown
next to it (see ``calibration``) rather than being used to remap it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from aurum.decision.candidates.setups import BIAS, Candidate
from aurum.decision.config import DecisionConfig
from aurum.decision.context import DecisionContext, cot_lean
from aurum.learning.features import session_of

SIDE = {"long": "buy", "short": "sell"}
GRADE_POINTS = {"A": 1.0, "B": 0.75, "C": 0.5}


@dataclass
class Component:
    name: str
    points: float
    max_points: float
    sentence: str
    sources: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Score:
    confidence: float
    grade: str
    components: list[Component]


def _setup_quality(ctx: DecisionContext, c: Candidate, weight: float) -> Component:
    a = ctx.analyses[c.timeframe]
    grade = getattr(a.score, "grade", "none") if a.score else "none"
    share = 0.6 * GRADE_POINTS.get(grade, 0.2)
    recent = [e for e in a.structure_events if e.displacement and e.confirmed and e.direction == BIAS[c.direction]]
    if recent:
        share += 0.25
    zone = next((o for o in a.order_blocks if o.id == c.zone_id), None)
    fresh = zone is None or zone.state == "fresh"
    if fresh:
        share += 0.15
    words = f"{c.timeframe} Module 03 grade {grade}; {'displacement' if recent else 'no displacement'}; " \
            f"zone {'fresh' if fresh else 'partly mitigated'}."
    return Component("setup_quality", round(weight * min(share, 1.0), 2), weight, words, c.sources)


def _htf_alignment(ctx: DecisionContext, c: Candidate, cfg: DecisionConfig, weight: float) -> Component:
    want = BIAS[c.direction]
    frames = cfg.htf_timeframes
    agree = [tf for tf in frames if tf in ctx.analyses and getattr(ctx.analyses[tf].structure.get("external"), "bias", None) == want]
    return Component("htf_alignment", round(weight * len(agree) / len(frames), 2), weight,
                     f"{len(agree)} of {len(frames)} higher timeframes have a {want} external bias.")


def _session_fit(ctx: DecisionContext, weight: float) -> Component:
    if ctx.intelligence is None:
        return Component("session_fit", 0.0, weight, "Module 04 is unavailable, so session fit is unknown.")
    sessions = ctx.sessions
    in_kill_zone = bool(sessions.get("active_kill_zones"))
    in_session = bool(sessions.get("active_sessions"))
    normal_vol = ctx.regime in ("NORMAL", "HIGH")
    share = 0.4 * in_session + 0.3 * in_kill_zone + 0.3 * normal_vol
    return Component("session_fit", round(weight * share, 2), weight,
                     f"Sessions {sessions.get('active_sessions') or 'none'}, kill zone {'active' if in_kill_zone else 'inactive'}, "
                     f"volatility {ctx.regime}.")


def _liquidity_context(ctx: DecisionContext, c: Candidate, weight: float) -> Component:
    a = ctx.analyses[c.timeframe]
    atr = a.atr or 0.0
    target_is_pool = any(s.get("kind", "").startswith("target: liquidity pool") for s in c.sources)
    side = "sell_side" if c.direction == "long" else "buy_side"
    stop_in_pool = any(p.side == side and p.state == "active" and abs(p.level - c.stop_loss) <= 0.1 * atr for p in a.liquidity_pools)
    share = 0.6 * target_is_pool + 0.4 * (not stop_in_pool)
    return Component("liquidity_context", round(weight * share, 2), weight,
                     f"Target is {'' if target_is_pool else 'not '}resting liquidity; stop is "
                     f"{'close to' if stop_in_pool else 'clear of'} obvious liquidity.")


def _cot(ctx: DecisionContext, c: Candidate, weight: float) -> Component:
    if not ctx.cot.get("available"):
        return Component("cot_agreement", 0.0, weight, "COT is unavailable, so it adds nothing.")
    lean = cot_lean(ctx.cot)
    if BIAS[c.direction] in lean:
        return Component("cot_agreement", weight, weight, f"COT leans with the trade ({lean}).")
    opposing = "bearish" if c.direction == "long" else "bullish"
    if opposing in lean:
        return Component("cot_agreement", 0.0, weight, f"COT leans against the trade ({lean}).")
    return Component("cot_agreement", weight * 0.5, weight, f"COT is not directional ({lean or 'unknown'}).")


def _learning(ctx: DecisionContext, c: Candidate, weight: float) -> Component:
    features = {"symbol": c.symbol, "side": SIDE[c.direction], "session": session_of(c.as_of)}
    matched = [p for p in ctx.patterns if p["pattern"] and all(features.get(k) == v for k, v in p["pattern"])]
    passed = sum(1 for p in matched if p["passed"])
    failed = len(matched) - passed
    neutral = weight * 0.5
    if not matched:
        return Component("learning", neutral, weight, "No tested Learning Agent pattern matches this trade.")
    points = max(0.0, min(weight, neutral + neutral * (passed - failed)))
    return Component("learning", points, weight, f"Matches {passed} pattern(s) that passed a forward trial and {failed} that failed.")


def _data_deduction(ctx: DecisionContext, cfg: DecisionConfig) -> Component:
    missing = [k for k, v in ctx.availability.items() if not v["available"] and k not in ("learning", "account")]
    per_item = cfg.score["data_quality_deduction"]
    return Component("data_quality", -per_item * len(missing), 0.0,
                     f"Missing inputs: {', '.join(missing)}." if missing else "All key inputs are available.")


def grade_of(confidence: float, cfg: DecisionConfig) -> str:
    grades = cfg.score["grades"]
    return next((g for g in ("A", "B", "C", "D") if confidence >= grades[g]), "F")


def score(ctx: DecisionContext, c: Candidate, cfg: DecisionConfig) -> Score:
    w = cfg.score["weights"]
    parts = [_setup_quality(ctx, c, w["setup_quality"]), _htf_alignment(ctx, c, cfg, w["htf_alignment"]),
             _session_fit(ctx, w["session_fit"]), _liquidity_context(ctx, c, w["liquidity_context"]), _cot(ctx, c, w["cot_agreement"]),
             _learning(ctx, c, w["learning"]), _data_deduction(ctx, cfg)]
    confidence = round(max(0.0, min(100.0, sum(p.points for p in parts))), 1)
    return Score(confidence, grade_of(confidence, cfg), parts)


def calibration(resolved: list[dict[str, Any]], minimum: int) -> dict[str, Any]:
    """Realised results by confidence bucket, per template, once a template has enough resolved outcomes. Reported beside
    the score; never applied to it."""
    by_template: dict[str, list[dict[str, Any]]] = {}
    for row in resolved:
        by_template.setdefault(row["template"], []).append(row)
    out: dict[str, Any] = {}
    for template, rows in by_template.items():
        if len(rows) < minimum:
            out[template] = {"status": "not_enough_outcomes", "resolved": len(rows), "required": minimum}
            continue
        buckets: dict[str, list[float]] = {}
        for r in rows:
            low = int(r["confidence"] // 20) * 20
            buckets.setdefault(f"{low}-{low + 20}", []).append(r["r_multiple"])
        out[template] = {"status": "ok", "resolved": len(rows), "buckets": {
            k: {"trades": len(v), "hit_rate": round(sum(1 for x in v if x > 0) / len(v), 3), "expectancy_r": round(sum(v) / len(v), 3)}
            for k, v in sorted(buckets.items())}}
    return out
