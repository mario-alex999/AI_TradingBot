"""Multi-timeframe confluence: weighted agreement of timeframe biases plus each timeframe's own score."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from aurum.analysis.config import AnalysisConfig
from aurum.analysis.models.analysis_result import ConfluenceReport, ScoreComponent, TechnicalScore, TimeframeAnalysis
from aurum.analysis.multi_timeframe.timeframe_analysis import snapshot


def confluence(symbol: str, results: dict[str, TimeframeAnalysis], cfg: AnalysisConfig, as_of: datetime,
               cot: dict[str, Any] | None = None) -> ConfluenceReport:
    weights = cfg.multi_timeframe.weights
    usable = {tf: a for tf, a in results.items() if a.status == "ok" and a.score is not None}
    order = sorted(results, key=lambda tf: -weights.get(tf, 0))
    per_tf = {tf: snapshot(results[tf]) for tf in order}
    findings: list[str] = []
    warnings: list[str] = []
    for tf in order:
        if results[tf].status != "ok":
            warnings.append(f"{tf}: {results[tf].status.replace('_', ' ')} - {'; '.join(results[tf].messages)}")

    total = sum(weights.get(tf, 0) for tf in usable) or 1.0
    bull_w = sum(weights.get(tf, 0) for tf, a in usable.items() if a.structure["external"].bias == "bullish")
    bear_w = sum(weights.get(tf, 0) for tf, a in usable.items() if a.structure["external"].bias == "bearish")
    consensus = "bullish" if bull_w >= 0.6 * total else "bearish" if bear_w >= 0.6 * total else "mixed"
    aligned = [tf for tf, a in usable.items() if a.structure["external"].bias == consensus]
    conflicting = [tf for tf, a in usable.items()
                   if a.structure["external"].bias in ("bullish", "bearish") and a.structure["external"].bias != consensus]
    alignment = {"consensus": consensus, "bullish_weight": bull_w, "bearish_weight": bear_w, "total_weight": total,
                 "ratio": round(max(bull_w, bear_w) / total, 3), "aligned_timeframes": aligned,
                 "conflicting_timeframes": conflicting if consensus != "mixed" else []}

    comps: list[ScoreComponent] = []
    bull = bear = 0.0
    for tf in order:
        a = usable.get(tf)
        if a is None:
            continue
        share = weights.get(tf, 0) / total
        bull += share * a.score.bullish_score
        bear += share * a.score.bearish_score
        comps.append(ScoreComponent(f"tf_{tf}", a.score.bias, round(share * a.score.score, 2),
                                    round(share * 100, 2), f"{tf}: {a.score.explanation}"))
        st = a.structure["external"]
        if st.bias != "ranging":
            findings.append(f"{tf} external structure {st.bias} ({st.state}, confidence {st.confidence:.0%}).")
        snap = per_tf[tf]
        for z in snap["price_inside"]:
            findings.append(f"{tf}: price inside {z}.")
        if snap["choch"] and snap["choch"]["status"] != "none":
            findings.append(f"{tf}: {snap['choch']['status']} CHoCH ({snap['choch']['direction']}).")

    w_align = cfg.scoring.weights.get("htf_alignment", 0)
    entry_tf = order[-1] if order else None
    if consensus != "mixed" and entry_tf in usable and usable[entry_tf].structure["internal"].bias == consensus:
        pts = w_align * alignment["ratio"]
        comps.append(ScoreComponent("htf_alignment", consensus, round(pts, 2), w_align,
                                    f"{entry_tf} internal structure agrees with the {consensus} higher-timeframe "
                                    f"consensus ({alignment['ratio']:.0%} of weight)."))
        bull, bear = (bull + pts, bear) if consensus == "bullish" else (bull, bear + pts)
    if cot and cot.get("available"):
        d = cot.get("stance") if cot.get("stance") in ("bullish", "bearish") else cot.get("lean")
        if d in ("bullish", "bearish"):
            pts = cfg.scoring.cot_weight_when_available * (1.0 if cot.get("stance") == d else 0.5)
            comps.append(ScoreComponent("cot", d, round(pts, 2), cfg.scoring.cot_weight_when_available,
                                        f"COT (Module 02) {cot.get('stance')}"
                                        + (f", leaning {cot.get('lean')}" if cot.get("lean") else "")
                                        + ": positioning context, not timing."))
            bull, bear = (bull + pts, bear) if d == "bullish" else (bull, bear + pts)
    penalties: list[ScoreComponent] = []
    if consensus == "mixed" and bull_w and bear_w:
        penalties.append(ScoreComponent("timeframe_conflict", "neutral", -5, 5,
                                        "Timeframes disagree on external structure; no weighted consensus."))
        warnings.append("Timeframes disagree on external structure.")
    missing = [tf for tf in order if tf not in usable]
    if missing:
        penalties.append(ScoreComponent("missing_timeframes", "neutral", -3 * len(missing), 3 * len(missing),
                                        f"No usable analysis for {', '.join(missing)}."))
    bull, bear = round(min(bull, 100), 1), round(min(bear, 100), 1)
    if abs(bull - bear) < 10:
        bias, score = "neutral", max(bull, bear)
    else:
        bias, score = ("bullish", bull) if bull > bear else ("bearish", bear)
    score = round(max(0.0, score + sum(p.points for p in penalties)), 1)
    grade = next((g for g in ("A", "B", "C") if bias != "neutral" and score >= cfg.scoring.grade_thresholds[g]), "none")
    text = (f"Multi-timeframe {bias} evidence {score:.0f}/100, grade {grade}; consensus {consensus} "
            f"({alignment['ratio']:.0%} of timeframe weight)." if bias != "neutral" else
            f"No clear multi-timeframe side (bullish {bull:.0f} vs bearish {bear:.0f}); consensus {consensus}.")
    score_obj = TechnicalScore(bias, bull, bear, score, grade, comps, penalties, warnings[:], text)
    return ConfluenceReport(symbol=symbol, as_of=as_of, generated_at=datetime.now(timezone.utc), timeframes=order,
                            alignment=alignment, score=score_obj, per_timeframe=per_tf,
                            cot=cot or {"available": False, "reason": "COT confluence not requested."},
                            findings=findings, warnings=warnings)
