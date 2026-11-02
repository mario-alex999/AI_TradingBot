"""Explainable technical-analysis score.

Each component awards points to the bullish or bearish side with a sentence explaining why. Scores are the
share of available points (0-100) per side; quality filters subtract from the favoured side. The score
describes how much price-action evidence lines up - it is not a probability and not a trade decision.
"""
from __future__ import annotations

from typing import Any

from aurum.analysis.config import AnalysisConfig
from aurum.analysis.models.analysis_result import ScoreComponent, TechnicalScore, TimeframeAnalysis
from aurum.analysis.scoring.quality_filters import quality_penalties

RECENT_BARS = 20


def _inside(price: float, lo: float, hi: float) -> bool:
    return lo <= price <= hi


def timeframe_components(a: TimeframeAnalysis) -> list[ScoreComponent]:
    """Components without weights applied: points are 0..1 strength; ``max_points`` is filled in by the weighter."""
    comps: list[ScoreComponent] = []
    price = a.current_price
    last = (a.candles_used - 1) if a.candles_used else 0
    for scope, name in (("external", "external_structure"), ("internal", "internal_structure")):
        st = a.structure.get(scope)
        if st and st.bias in ("bullish", "bearish"):
            comps.append(ScoreComponent(name, st.bias, st.confidence, 1, st.explanation))
    recent = [e for e in a.structure_events if last - e.break_index <= RECENT_BARS]
    if recent:
        e = recent[-1]
        comps.append(ScoreComponent("recent_break", e.direction, 1.0 if e.displacement else 0.5, 1, e.explanation))
    if price is not None:
        for ob in a.order_blocks:
            if ob.active and _inside(price, ob.current_bottom, ob.current_top):
                comps.append(ScoreComponent("order_block", ob.direction, 1.0 if ob.state == "fresh" else 0.7, 1,
                                            f"Price is inside the {ob.state.replace('_', ' ')} {ob.direction} order block "
                                            f"{ob.current_bottom:.6g}-{ob.current_top:.6g}."))
                break
        for g in a.fair_value_gaps:
            if g.active and _inside(price, g.current_lower, g.current_upper):
                comps.append(ScoreComponent("fair_value_gap", g.direction, 1.0, 1,
                                            f"Price is inside the {g.state.replace('_', ' ')} {g.direction} FVG "
                                            f"{g.current_lower:.6g}-{g.current_upper:.6g}."))
                break
        pd_ = a.premium_discount
        if pd_ and pd_.zone in ("premium", "discount"):
            d = "bullish" if pd_.zone == "discount" else "bearish"
            comps.append(ScoreComponent("premium_discount", d, 1.0, 1,
                                        f"Price is in {pd_.zone} ({pd_.position:.0%} of the {pd_.range_low:.6g}-"
                                        f"{pd_.range_high:.6g} range): {'buying' if d == 'bullish' else 'selling'} "
                                        "is favoured from here."))
        if a.ote and a.ote.price_inside:
            comps.append(ScoreComponent("ote", a.ote.direction, 1.0 if a.ote.overlapping_zones else 0.7, 1,
                                        a.ote.explanation + (f" Overlaps {', '.join(a.ote.overlapping_zones)}."
                                                             if a.ote.overlapping_zones else "")))
        sweeps = [e for e in a.liquidity_events if e.event == "sweep" and last - e.index <= RECENT_BARS]
        if sweeps:
            s = sweeps[-1]
            comps.append(ScoreComponent("liquidity_sweep", s.implication, 1.0, 1, s.explanation))
        atr = a.atr or 0
        for z in a.support_resistance:
            near_sup = z.role in ("support", "flip") and (z.position == "at_price" or 0 <= price - z.top <= 0.5 * atr)
            near_res = z.role in ("resistance", "flip") and (z.position == "at_price" or 0 <= z.bottom - price <= 0.5 * atr)
            if near_sup or near_res:
                d = "bullish" if near_sup and not near_res else "bearish" if near_res and not near_sup else "neutral"
                if d != "neutral":
                    comps.append(ScoreComponent("support_resistance", d, min(z.score / 100 + 0.3, 1.0), 1,
                                                f"Price is at a {z.role} zone {z.bottom:.6g}-{z.top:.6g} "
                                                f"({z.touches} touches, score {z.score:.0f})."))
                    break
    return comps


def combine(components: list[ScoreComponent], cfg: AnalysisConfig, active: set[str],
            penalties: list[ScoreComponent], warnings: list[str]) -> TechnicalScore:
    """Weight components, total each side, apply penalties and grade."""
    weights = {**cfg.scoring.weights}
    if "cot" in active and weights.get("cot", 0) <= 0:
        weights["cot"] = cfg.scoring.cot_weight_when_available
    available = sum(weights.get(k, 0) for k in active)
    weighted: list[ScoreComponent] = []
    totals = {"bullish": 0.0, "bearish": 0.0}
    for c in components:
        w = weights.get(c.name, 0)
        if w <= 0 or c.direction not in totals:
            continue
        pts = round(c.points * w, 2)
        totals[c.direction] += pts
        weighted.append(ScoreComponent(c.name, c.direction, pts, w, c.explanation))
    bull = round(totals["bullish"] / available * 100, 1) if available else 0.0
    bear = round(totals["bearish"] / available * 100, 1) if available else 0.0
    if abs(bull - bear) < 10:
        bias, score = "neutral", max(bull, bear)
    else:
        bias, score = ("bullish", bull) if bull > bear else ("bearish", bear)
    score = round(max(0.0, score + sum(p.points for p in penalties)), 1)
    grade = "none"
    if bias != "neutral":
        for g in ("A", "B", "C"):
            if score >= cfg.scoring.grade_thresholds[g]:
                grade = g
                break
    top = sorted(weighted, key=lambda c: -c.points)[:3]
    text = (f"{bias.title()} evidence {score:.0f}/100 (bullish {bull:.0f}, bearish {bear:.0f}), grade {grade}. "
            if bias != "neutral" else f"No clear side: bullish {bull:.0f} vs bearish {bear:.0f}. ")
    text += ("Main factors: " + "; ".join(f"{c.name.replace('_', ' ')} ({c.direction}, {c.points:g})" for c in top) + "."
             if top else "No component scored.")
    return TechnicalScore(bias, bull, bear, score, grade, weighted, penalties, warnings, text)


def score_timeframe(a: TimeframeAnalysis, cfg: AnalysisConfig, cot: dict[str, Any] | None = None) -> TechnicalScore:
    comps = timeframe_components(a)
    active = {k for k, w in cfg.scoring.weights.items() if w > 0 and k not in ("htf_alignment", "cot")}
    comps, active = add_cot_component(comps, active, cot, cfg)
    pen, warn = quality_penalties(a, cfg.min_candles)
    return combine(comps, cfg, active, pen, warn)


def add_cot_component(comps: list[ScoreComponent], active: set[str], cot: dict[str, Any] | None,
                      cfg: AnalysisConfig) -> tuple[list[ScoreComponent], set[str]]:
    """Optional COT confluence (Module 02). Only used when real stored COT data is available."""
    if not cot or not cot.get("available"):
        return comps, active
    stance, lean = cot.get("stance"), cot.get("lean")
    d = stance if stance in ("bullish", "bearish") else lean if lean in ("bullish", "bearish") else None
    if d is None:
        return comps, active
    strength = 1.0 if stance == d else 0.5
    comps = comps + [ScoreComponent("cot", d, strength, 1,
                                    f"COT positioning (Module 02): {stance}" + (f", leaning {lean}" if lean else "")
                                    + f" ({cot.get('confidence')} confidence). Context, not timing.")]
    return comps, active | {"cot"}
