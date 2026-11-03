"""Market-condition tags (several may apply at once), each with confidence, evidence and source references.

| tag                 | evidence (source)                                                                     |
|---------------------|---------------------------------------------------------------------------------------|
| TRENDING            | Module 03 external structure state 'trending' (module_03); efficiency ratio >= threshold (module_01 price) |
| RANGING             | Module 03 state 'ranging'; efficiency ratio <= threshold; volatility contraction       |
| HIGH_VOLATILITY     | volatility regime HIGH / VERY_HIGH; abnormal movement (module_04 volatility)          |
| LOW_VOLATILITY      | volatility regime LOW / VERY_LOW                                                       |
| EVENT_DRIVEN        | event-risk state HIGH_EVENT_RISK / RELEASE_WINDOW / POST_EVENT_VOLATILITY (calendar)  |
| SESSION_TRANSITION  | within ``transition_minutes`` of a session open/close, or inside an overlap/transition |
| LIQUIDITY_EXPANSION | session sweep / breakout / range expansion within ``liquidity_lookback_hours``         |
| UNCERTAIN           | conflicting evidence (trending and ranging), Module 03 timeframes disagree, or too little data |

Efficiency ratio (Kaufman) = |close_n - close_0| / sum |close_i - close_{i-1}| over ``efficiency_window`` closed candles.
These are discretionary analytical classifications, not facts and not signals.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd

from aurum.market_intelligence.config import MIConfig

TAGS = ("TRENDING", "RANGING", "HIGH_VOLATILITY", "LOW_VOLATILITY", "EVENT_DRIVEN", "SESSION_TRANSITION",
        "LIQUIDITY_EXPANSION", "UNCERTAIN")


def efficiency_ratio(closes: np.ndarray, window: int) -> float | None:
    if len(closes) <= window:
        return None
    c = closes[-(window + 1):]
    path = float(np.abs(np.diff(c)).sum())
    return float(abs(c[-1] - c[0]) / path) if path > 0 else 0.0


def classify(cfg: MIConfig, symbol: str, as_of: datetime, *, frame: pd.DataFrame, volatility: dict[str, Any],
             risk: dict[str, Any], sessions: dict[str, Any], liquidity: dict[str, Any],
             m03: dict[str, Any] | None, confluence: dict[str, Any] | None) -> dict[str, Any]:
    c = cfg.section("conditions")
    tf = str(c.get("timeframe") or "H1")
    er = efficiency_ratio(frame["close"].to_numpy(), int(c.get("efficiency_window", 20))) if not frame.empty else None
    cand: dict[str, dict[str, Any]] = {t: {"tag": t, "score": 0.0, "evidence": []} for t in TAGS}

    def add(tag: str, weight: float, source: str, fact: str) -> None:
        cand[tag]["score"] += weight
        cand[tag]["evidence"].append({"source": source, "fact": fact, "weight": weight})

    vol = (volatility.get("timeframes") or {}).get(tf) or {}
    # --- trend / range
    if er is not None:
        if er >= float(c.get("trending_efficiency", 0.35)):
            add("TRENDING", 0.45, "module_01.candles", f"{tf} efficiency ratio {er:.2f} (>= trending threshold).")
        elif er <= float(c.get("ranging_efficiency", 0.20)):
            add("RANGING", 0.45, "module_01.candles", f"{tf} efficiency ratio {er:.2f} (<= ranging threshold).")
    if m03 and m03.get("available"):
        st, bias = m03.get("external_state"), m03.get("external_bias")
        if st == "trending":
            add("TRENDING", 0.45, f"module_03.structure.external[{m03['timeframe']}]", f"External structure trending ({bias}).")
        elif st == "ranging":
            add("RANGING", 0.45, f"module_03.structure.external[{m03['timeframe']}]", "External structure ranging.")
        elif st == "transitioning":
            add("UNCERTAIN", 0.3, f"module_03.structure.external[{m03['timeframe']}]",
                f"External structure transitioning (latest bias {bias}).")
    else:
        add("UNCERTAIN", 0.15, "module_03", f"Module 03 structure unavailable: {(m03 or {}).get('reason')}")
    if vol.get("expansion_state") == "contraction":
        add("RANGING", 0.15, "module_04.volatility", f"Volatility contraction (short/long TR {vol.get('expansion_ratio'):.2f}).")
    if confluence and confluence.get("available"):
        al = confluence.get("alignment") or {}
        if al.get("consensus") == "mixed":
            add("UNCERTAIN", 0.3, "module_03.confluence", "Module 03 timeframes disagree on external structure.")
    # --- volatility
    reg = volatility.get("regime")
    if reg in ("HIGH", "VERY_HIGH"):
        add("HIGH_VOLATILITY", 0.7 if reg == "VERY_HIGH" else 0.55, "module_04.volatility", volatility.get("regime_reason") or reg)
    if reg in ("LOW", "VERY_LOW"):
        add("LOW_VOLATILITY", 0.7 if reg == "VERY_LOW" else 0.55, "module_04.volatility", volatility.get("regime_reason") or reg)
    if vol.get("abnormal_movement"):
        add("HIGH_VOLATILITY", 0.3, "module_04.volatility",
            f"Abnormal last candle (range {vol.get('last_range_atr') or 0:.2f} x ATR, return z {vol.get('last_return_z')}).")
    if vol.get("expansion_state") == "expansion":
        add("HIGH_VOLATILITY", 0.15, "module_04.volatility", f"Volatility expansion (ratio {vol.get('expansion_ratio'):.2f}).")
    if reg == "UNKNOWN":
        add("UNCERTAIN", 0.2, "module_04.volatility", volatility.get("regime_reason") or "Volatility unknown.")
    # --- events
    rs = risk.get("state")
    if rs in ("HIGH_EVENT_RISK", "RELEASE_WINDOW", "POST_EVENT_VOLATILITY"):
        add("EVENT_DRIVEN", 0.8 if rs != "HIGH_EVENT_RISK" else 0.6, "module_04.event_risk", f"{rs}: {risk.get('reason')}")
    elif rs == "EVENT_APPROACHING":
        add("EVENT_DRIVEN", 0.3, "module_04.event_risk", f"Event approaching: {risk.get('reason')}")
    # --- sessions
    tmin = float(c.get("transition_minutes", 30))
    for s in sessions.get("sessions") or []:
        if s.get("minutes_until_open") is not None and 0 < s["minutes_until_open"] <= tmin:
            add("SESSION_TRANSITION", 0.5, "module_04.sessions", f"{s['name']} opens in {s['minutes_until_open']:.0f} min.")
        if s.get("minutes_until_close") is not None and s["minutes_until_close"] <= tmin:
            add("SESSION_TRANSITION", 0.5, "module_04.sessions", f"{s['name']} closes in {s['minutes_until_close']:.0f} min.")
    for o in sessions.get("overlaps") or []:
        if o.get("active"):
            add("SESSION_TRANSITION", 0.4, "module_04.sessions", f"Inside {o['name']} {o.get('kind') or 'overlap'}.")
    # --- liquidity
    lb = as_of - timedelta(hours=float(c.get("liquidity_lookback_hours", 6)))
    recent = [e for e in liquidity.get("events") or [] if e["event_time"] >= lb
              and e["event_type"] in ("wick_sweep", "confirmed_breakout", "false_breakout_candidate", "range_expansion")]
    for e in recent[-3:]:
        add("LIQUIDITY_EXPANSION", 0.35, "module_04.session_liquidity",
            f"{e['event_type']} of {e['level_type']} at {e['event_time']:%Y-%m-%d %H:%M} UTC.")
    if cand["TRENDING"]["score"] >= 0.45 and cand["RANGING"]["score"] >= 0.45:
        add("UNCERTAIN", 0.4, "module_04.conditions", "Trend and range evidence conflict.")

    thr = float((cfg.section("scoring").get("conditions") or {}).get("min_confidence", 0.35))
    rows = []
    for t in TAGS:
        x = cand[t]
        if not x["evidence"]:
            continue
        x["confidence"] = round(min(1.0, x["score"]), 2)
        x["active"] = x["confidence"] >= thr
        del x["score"]
        rows.append(x)
    active = [r for r in rows if r["active"]]
    if not active:
        cand_u = {"tag": "UNCERTAIN", "confidence": 0.35, "active": True,
                  "evidence": [{"source": "module_04.conditions", "fact": "No condition reached the confidence threshold.",
                                "weight": 0.35}]}
        rows.append(cand_u)
        active = [cand_u]
    summary = [f"{', '.join(sessions.get('active_sessions') or []) or 'No major'} session"]
    summary += [r["tag"].replace("_", " ").title() for r in active]
    if m03 and m03.get("available") and m03.get("external_bias"):
        summary.append(f"{m03['external_bias'].title()} {m03['timeframe']} structure (Module 03)")
    if risk.get("next_event"):
        ne = risk["next_event"]
        summary.append(f"Next relevant event: {ne['name']} in {ne['minutes_until_release']:.0f} min")
    return {"symbol": symbol, "as_of": as_of, "timeframe": tf, "efficiency_ratio": er,
            "tags": [r["tag"] for r in active], "conditions": sorted(rows, key=lambda r: -r["confidence"]),
            "summary": summary, "methodology_version": cfg.methodology_version,
            "note": "Discretionary analytical classifications with their evidence - not facts and not signals."}
