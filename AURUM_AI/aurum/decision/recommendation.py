"""The per-instrument analysis: conflicts, final decision and a plain explanation, all from deterministic results.

A high score alone never produces a setup. The decision needs a candidate that passed every gate, agreement with the bias and
no blocking conflict, and the reason for the choice is always recorded. Every outcome still waits for a person.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from aurum.decision.candidates.setups import BIAS, Candidate
from aurum.decision.config import DecisionConfig
from aurum.decision.context import DecisionContext, cot_lean
from aurum.decision.gates import FAIL, WARN, GateResult
from aurum.decision.scoring import Score

DECISIONS = ("STRONG_BUY_SETUP", "BUY_SETUP", "STRONG_SELL_SETUP", "SELL_SETUP", "WAIT", "NO_TRADE", "INSUFFICIENT_DATA")
MARKET_BLOCKS = ("event_risk", "market_open", "spread_volatility")


@dataclass
class Assessed:
    candidate: Candidate
    score: Score
    gates: list[GateResult]
    ai_verdict: str | None = None


def find_conflicts(ctx: DecisionContext, regime: dict[str, Any], bias: dict[str, Any], assessed: list[Assessed]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    if bias["overall"] == "mixed":
        out.append({"kind": "timeframes", "detail": f"Timeframes disagree; agreement is {bias['agreement']:.0%}."})
    elif bias["disagreeing"]:
        out.append({"kind": "timeframes", "detail": f"{', '.join(bias['disagreeing'])} oppose the {bias['overall']} bias."})
    for item in regime["conflicting"]:
        out.append({"kind": "regime", "detail": item})
    lean = cot_lean(ctx.cot)
    if ctx.cot.get("available") and bias["overall"] in ("bullish", "bearish") and lean in ("bullish", "bearish") and lean != bias["overall"]:
        out.append({"kind": "cot", "detail": f"COT positioning is {lean}, against the {bias['overall']} price bias. COT is slow context, not a trigger."})
    for a in assessed:
        want = BIAS[a.candidate.direction]
        if bias["overall"] in ("bullish", "bearish") and want != bias["overall"]:
            out.append({"kind": "setup", "detail": f"{a.candidate.template} is {a.candidate.direction}, against the {bias['overall']} bias."})
    return out


def _insufficient(ctx: DecisionContext, cfg: DecisionConfig) -> list[str]:
    missing = []
    for tf in dict.fromkeys([*cfg.htf_timeframes, *cfg.entry_timeframes]):
        a = ctx.analyses.get(tf)
        if a is None or a.status != "ok":
            missing.append(f"{tf} analysis is {getattr(a, 'status', 'missing')}.")
    if ctx.intelligence is None:
        missing.append("Module 04 analysis is unavailable.")
    return missing


def decide(ctx: DecisionContext, cfg: DecisionConfig, regime: dict[str, Any], bias: dict[str, Any], assessed: list[Assessed],
           conflicts: list[dict[str, str]]) -> dict[str, Any]:
    d = cfg.decision
    gaps = _insufficient(ctx, cfg)
    if len(gaps) >= d["insufficient_after_missing"]:
        return {"decision": "INSUFFICIENT_DATA", "reasons": gaps, "candidate_key": None}
    clean = [a for a in assessed if not any(g.result == FAIL for g in a.gates) and a.ai_verdict != "reject"]
    if not clean:
        blocked = [(a, next(g for g in a.gates if g.result == FAIL)) for a in assessed if any(g.result == FAIL for g in a.gates)]
        if any(g.gate == "data_quality" for _, g in blocked):
            return {"decision": "INSUFFICIENT_DATA", "reasons": [f"{a.candidate.template}: data quality gate failed: {g.evidence.get('problems')}" for a, g in blocked if g.gate == "data_quality"],
                    "candidate_key": None}
        market = [f"{a.candidate.template}: {g.gate} gate failed ({g.evidence.get('reason') or 'see its evidence'})." for a, g in blocked if g.gate in MARKET_BLOCKS]
        if market:
            return {"decision": "NO_TRADE", "reasons": market, "candidate_key": None}
        if blocked:
            return {"decision": "NO_TRADE", "reasons": [f"{a.candidate.template}: {g.gate} gate failed." for a, g in blocked], "candidate_key": None}
        if assessed:
            return {"decision": "NO_TRADE", "reasons": ["The AI reviewer rejected every candidate."], "candidate_key": None}
        return {"decision": "WAIT", "reasons": ["No template found a valid entry." if bias["overall"] != "mixed" else "Timeframes disagree and no template found a valid entry."],
                "candidate_key": None}
    best = clean[0]
    c, s = best.candidate, best.score
    if bias["overall"] == "mixed" and cfg.template(c.template)["require_alignment"]:
        return {"decision": "WAIT", "reasons": ["Timeframes disagree, and this template requires alignment."], "candidate_key": c.key}
    if s.grade not in d["min_grades"]:
        return {"decision": "WAIT", "reasons": [f"Best candidate is grade {s.grade}; a setup needs one of {', '.join(d['min_grades'])}."], "candidate_key": c.key}
    against = bias["overall"] in ("bullish", "bearish") and BIAS[c.direction] != bias["overall"]
    if against and cfg.template(c.template)["require_alignment"]:
        return {"decision": "WAIT", "reasons": [f"{c.template} runs against the {bias['overall']} bias."], "candidate_key": c.key}
    warnings = [g.gate for g in best.gates if g.result == WARN and g.gate not in d["ignored_warnings"]]
    strong = s.grade == d["strong_grade"] and not against and not warnings and not [x for x in conflicts if x["kind"] != "regime"] and best.ai_verdict != "caution"
    side = "BUY" if c.direction == "long" else "SELL"
    label = f"STRONG_{side}_SETUP" if strong else f"{side}_SETUP"
    reasons = [f"{c.template} on {c.timeframe} passed every gate at grade {s.grade}.", f"Regime {regime['regime']}, bias {bias['overall']}."]
    if warnings:
        reasons.append(f"Warnings: {', '.join(warnings)}.")
    if not strong:
        reasons.append("Not strong: a warning, a conflict or the grade holds it back.")
    return {"decision": label, "reasons": reasons, "candidate_key": c.key}


def explain(ctx: DecisionContext, regime: dict[str, Any], bias: dict[str, Any], assessed: list[Assessed], conflicts: list[dict[str, str]],
            decision: dict[str, Any]) -> dict[str, Any]:
    """The deterministic fallback: a complete explanation that needs no AI provider. Facts only, and no new numbers."""
    bullish, bearish = [], []
    for tf, v in bias["timeframes"].items():
        if v["bias"] == "bullish":
            bullish.append(f"{tf} external structure is bullish.")
        elif v["bias"] == "bearish":
            bearish.append(f"{tf} external structure is bearish.")
    cot = cot_lean(ctx.cot)
    if ctx.cot.get("available") and cot in ("bullish", "bearish"):
        (bullish if cot == "bullish" else bearish).append(f"COT context is {cot} (slow-moving, not a trigger).")
    for a in assessed:
        side = bullish if a.candidate.direction == "long" else bearish
        side.append(f"{a.candidate.template} on {a.candidate.timeframe}: confidence {a.score.confidence}, grade {a.score.grade}.")
    missing = [f"{k}: {v.get('reason')}" for k, v in ctx.availability.items() if not v["available"]]
    return {"source": "deterministic", "bullish_evidence": bullish, "bearish_evidence": bearish, "conflicts": [c["detail"] for c in conflicts],
            "missing_data": missing, "market": f"Regime {regime['regime']}; volatility {ctx.regime}; event risk {ctx.event_state}.",
            "decision": f"{decision['decision']}: {' '.join(decision['reasons'])}"}


def evidence_summary(a: Assessed, conflicts: list[dict[str, str]]) -> dict[str, Any]:
    """Positive, negative, conflicting and missing evidence for one candidate, with its deductions."""
    positive = [p.sentence for p in a.score.components if p.max_points and p.points >= p.max_points * 0.6]
    negative = [p.sentence for p in a.score.components if p.max_points and p.points < p.max_points * 0.4]
    deductions = [p.sentence for p in a.score.components if p.points < 0]
    missing = [g.evidence.get("reason") for g in a.gates if g.result == WARN and g.evidence.get("reason")]
    return {"final_score": a.score.confidence, "grade": a.score.grade, "positive": positive, "negative": negative,
            "conflicting": [c["detail"] for c in conflicts], "missing": missing, "quality_deductions": deductions}
