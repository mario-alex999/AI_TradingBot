"""Market regime and multi-timeframe directional bias, derived from Module 03 structure and Module 04 volatility.

No new indicators: these read what those modules measured and say which reading won, what agreed and what did not.
"""
from __future__ import annotations

from typing import Any

from aurum.config import TIMEFRAME_SECONDS
from aurum.decision.config import DecisionConfig
from aurum.decision.context import DecisionContext

REGIMES = ("BULLISH_TREND", "BEARISH_TREND", "RANGE", "CONSOLIDATION", "VOLATILITY_EXPANSION", "VOLATILITY_CONTRACTION", "BREAKOUT",
           "POTENTIAL_REVERSAL", "UNCLEAR")
TREND = {"bullish": "BULLISH_TREND", "bearish": "BEARISH_TREND"}


def _usable(a: Any) -> bool:
    return a is not None and a.status == "ok"


def _recent_choch(external: Any, a: Any, max_age: int) -> bool:
    """A confirmed CHoCH at most ``max_age`` candles old. Module 03 keeps reporting one until the next break, however old it is."""
    choch = getattr(external, "choch", None)
    if getattr(choch, "status", None) != "confirmed":
        return False
    since = getattr(choch, "since", None)
    return since is None or (a.as_of - since).total_seconds() <= max_age * TIMEFRAME_SECONDS[a.timeframe]


def assess_regime(ctx: DecisionContext, cfg: DecisionConfig) -> dict[str, Any]:
    r = cfg.regime
    a = ctx.analyses.get(r["timeframe"])
    if not _usable(a):
        return {"regime": "UNCLEAR", "supporting": [], "conflicting": [], "quality": "insufficient", "timeframe": r["timeframe"],
                "reason": f"No usable {r['timeframe']} analysis."}
    external = a.structure.get("external")
    state, bias = getattr(external, "state", None), getattr(external, "bias", None)
    vol = ((ctx.intelligence or {}).get("volatility") or {}).get("timeframes", {}).get(r["timeframe"]) or {}
    expansion = vol.get("expansion_state")
    recent = _recent_choch(external, a, r["reversal_max_age_candles"])
    fresh = [e for e in a.structure_events if e.displacement and e.confirmed and a.candles_used - 1 - e.break_index <= r["breakout_max_age_candles"]]
    supporting = [f"External structure on {r['timeframe']} is {state}, bias {bias}.", f"Volatility regime is {ctx.regime}."]
    conflicting: list[str] = []

    # Not state == "transitioning": Module 03 also uses it when the swing sequence lags the bias, which on real data is nearly always.
    if recent:
        regime = "POTENTIAL_REVERSAL"
        supporting.append("A recent confirmed CHoCH has no continuation yet.")
    elif ctx.regime in r["expansion_regimes"] and expansion == "expansion":
        regime = "VOLATILITY_EXPANSION"
        supporting.append("Short-term range is expanding against its average.")
    elif ctx.regime in r["contraction_regimes"] and expansion == "contraction" and state != "trending":
        regime = "VOLATILITY_CONTRACTION"
        supporting.append("Short-term range is contracting.")
    elif state == "ranging" and fresh:
        regime = "BREAKOUT"
        supporting.append(f"A displacement {fresh[-1].event_type} just broke the range.")
    elif state == "ranging":
        regime = "CONSOLIDATION" if ctx.regime in r["contraction_regimes"] else "RANGE"
    elif state in ("trending", "transitioning") and bias in TREND:
        regime = TREND[bias]
        if state == "transitioning":
            conflicting.append(f"The swing sequence has not confirmed the {bias} bias.")
    else:
        regime = "UNCLEAR"
        conflicting.append(f"Structure state {state} with bias {bias} fits no regime.")

    other = ctx.analyses.get(r["confirm_timeframe"])
    other_bias = getattr(other.structure.get("external"), "bias", None) if _usable(other) else None
    if regime in ("BULLISH_TREND", "BEARISH_TREND") and other_bias in TREND and other_bias != bias:
        conflicting.append(f"{r['confirm_timeframe']} bias is {other_bias}, against the {r['timeframe']} trend.")
    if ctx.regime == "UNKNOWN":
        conflicting.append("Volatility regime is unknown.")
    return {"regime": regime, "timeframe": r["timeframe"], "volatility_regime": ctx.regime, "expansion_state": expansion, "supporting": supporting,
            "conflicting": conflicting, "quality": "low" if ctx.intelligence is None or conflicting else "good"}


def assess_bias(ctx: DecisionContext, cfg: DecisionConfig) -> dict[str, Any]:
    b = cfg.bias
    weights = b["weights"]
    per: dict[str, dict[str, Any]] = {}
    votes = {"bullish": 0.0, "bearish": 0.0}
    known = 0.0
    for tf in b["timeframes"]:
        a = ctx.analyses.get(tf)
        if not _usable(a):
            per[tf] = {"bias": "unknown", "weight": weights[tf], "state": None, "reason": getattr(a, "status", "missing")}
            continue
        external = a.structure.get("external")
        bias = getattr(external, "bias", None) or "unknown"
        per[tf] = {"bias": bias, "weight": weights[tf], "state": getattr(external, "state", None), "reason": None}
        if bias != "unknown":
            known += weights[tf]
        if bias in votes:
            votes[bias] += weights[tf]
    total = sum(weights[tf] for tf in b["timeframes"])
    top = max(votes, key=lambda k: votes[k])
    share = votes[top] / known if known else 0.0
    if known < b["min_known_share"] * total:
        overall = "unknown"
    elif votes["bullish"] == votes["bearish"] or share < b["min_agreement"]:
        overall = "mixed"
    else:
        overall = top
    agree = [tf for tf, v in per.items() if v["bias"] == overall]
    against = [tf for tf, v in per.items() if v["bias"] in votes and v["bias"] != overall] if overall in votes else []
    return {"overall": overall, "agreement": round(share, 3), "timeframes": per, "agreeing": agree, "disagreeing": against,
            "note": "Higher timeframes set the context and lower ones the timing, so they need not all agree."}
