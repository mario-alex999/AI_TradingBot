"""Explainable Module 04 scores (0-100). Methodology version: ``scoring.methodology_version``.

None of these is a probability of profit or loss and none is a trade signal.

Event-risk score
    base      = max over relevant events of importance_weight x time_factor
                (time_factor: proximity table before release, 1.0 inside the release window, post_decay after it);
                the MAX is used so one release reached through several currencies is counted once.
    modifier  = 1 + sum over AVAILABLE adjustments of weight x (2 x value - 1), value in 0..1:
                  historical_sensitivity = clamp(mean past post-release range ratio / 2, 0, 1)
                  current_volatility     = ATR percentile / 100
                A missing adjustment contributes 0 (neutral): missing data is neither low nor high risk.
                With the default weights (0.15 each) the modifier stays within 0.7 .. 1.3.
    score     = min(100, 100 x base x modifier)
    When calendar coverage is incomplete and no event is in a window, the score is None (UNKNOWN), not 0.

Session-activity score
    weighted mean of available components (each 0..1):
      range_vs_history      = clamp((current session range / mean range of the last 20 sessions) / 2, 0, 1)
      volatility_percentile = ATR percentile / 100
      overlap_history       = clamp(measured overlap activity ratio / 2, 0, 1) - only while an overlap is active
    components without enough samples are excluded (reported as missing).

Volatility score = ATR percentile (0-100).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from aurum.market_intelligence.config import MIConfig


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _time_factor(cfg: MIConfig, ev: dict[str, Any]) -> float:
    sc = cfg.section("scoring").get("event_risk") or {}
    mins = ev["minutes_until_release"]
    state = ev["state"]
    if state == "RELEASE_WINDOW":
        return 1.0
    if state == "POST_EVENT_VOLATILITY":
        since = -mins
        decay = {int(k): float(v) for k, v in (sc.get("post_decay") or {}).items()}
        for m in sorted(decay):
            if since <= m:
                return decay[m]
        return 0.0
    if mins <= 0:
        return 0.0
    for row in sc.get("proximity") or []:
        if mins <= float(row["max_minutes"]):
            return float(row["factor"])
    return 0.0


def event_risk_score(cfg: MIConfig, risk: dict[str, Any], volatility: dict[str, Any] | None,
                     history: dict[str, Any] | None) -> dict[str, Any]:
    sc = cfg.section("scoring").get("event_risk") or {}
    imp_w = {k: float(v) for k, v in (sc.get("importance") or {}).items()}
    weights = {k: float(v) for k, v in (sc.get("weights") or {"historical_sensitivity": 0.15,
                                                                   "current_volatility": 0.15}).items()}
    version = cfg.methodology_version
    events = risk.get("active_events") or []
    if risk.get("state") == "UNKNOWN":
        return {"score": None, "state": "UNKNOWN", "reason": risk.get("reason"), "components": [],
                "methodology_version": version}
    best, best_ev = 0.0, None
    for ev in events:
        f = imp_w.get(ev.get("importance") or "low", 0.0) * _time_factor(cfg, ev)
        if f > best:
            best, best_ev = f, ev
    comps = [{"name": "event_importance_x_time", "value": round(best, 4), "weight": None,
              "available": True, "explanation": (f"{best_ev['name']} ({best_ev.get('importance') or 'unrated'}, "
                                                 f"{best_ev['minutes_until_release']:+.0f} min)") if best_ev
              else "No relevant event inside a configured window."}]
    hv = (history or {}).get("mean_ratio")
    comps.append({"name": "historical_sensitivity", "value": None if hv is None else round(_clamp(hv / 2), 4),
                  "weight": weights.get("historical_sensitivity", 0.15), "available": hv is not None,
                  "explanation": f"Mean post-release range ratio {hv:.2f} over {history.get('samples')} past "
                                 f"release(s)." if hv is not None else
                  "No stored history for this event type with candles around it (missing, not low)."})
    pct = (volatility or {}).get("atr_percentile")
    comps.append({"name": "current_volatility", "value": None if pct is None else round(pct / 100, 4),
                  "weight": weights.get("current_volatility", 0.15), "available": pct is not None,
                  "explanation": f"ATR percentile {pct:.0f}." if pct is not None else "Volatility unavailable."})
    modifier = 1.0 + sum(c["weight"] * (2 * c["value"] - 1) for c in comps[1:] if c["available"])
    score = 100 * best * modifier
    return {"score": round(min(100.0, score), 1), "state": risk.get("state"),
            "coverage": (risk.get("coverage") or {}).get("level"), "components": comps,
            "driver_event": best_ev.get("name") if best_ev else None, "methodology_version": version,
            "modifier": round(modifier, 4), "base": round(best, 4),
            "explanation": "Importance x time-to-release, adjusted by historical sensitivity and current volatility. "
                           "Not a probability and not a forecast of volatility."}


def session_activity_score(cfg: MIConfig, stats: dict[str, Any] | None, volatility: dict[str, Any] | None,
                           active_sessions: list[str], active_overlaps: list[str]) -> dict[str, Any]:
    w = (cfg.section("scoring").get("session_activity") or {}).get("weights") or {}
    weights = {"range_vs_history": float(w.get("range_vs_history", 0.5)),
               "volatility_percentile": float(w.get("volatility_percentile", 0.3)),
               "overlap_history": float(w.get("overlap_history", 0.2))}
    comps = []
    sess = (stats or {}).get("sessions") or {}
    cur = next((s for s in active_sessions if s in sess), None)
    if cur:
        latest, w20 = sess[cur].get("latest"), sess[cur]["windows"].get("20") or {}
        ok = bool(latest and w20.get("usable") and w20.get("mean_range"))
        comps.append({"name": "range_vs_history", "weight": weights["range_vs_history"], "available": ok,
                      "value": round(_clamp(latest["range"] / w20["mean_range"] / 2), 4) if ok else None,
                      "explanation": f"Latest completed {cur} range vs mean of {w20.get('sample_size')} sessions."
                      if ok else f"Not enough completed {cur} sessions."})
    else:
        comps.append({"name": "range_vs_history", "weight": weights["range_vs_history"], "available": False,
                      "value": None, "explanation": "No configured session is active."})
    pct = (volatility or {}).get("atr_percentile")
    comps.append({"name": "volatility_percentile", "weight": weights["volatility_percentile"],
                  "available": pct is not None, "value": None if pct is None else round(pct / 100, 4),
                  "explanation": f"ATR percentile {pct:.0f}." if pct is not None else "Volatility unavailable."})
    ov = next((o for o in active_overlaps if o in sess), None)
    ratio = (sess.get(ov) or {}).get("activity", {}).get("ratio_to_all_candles") if ov else None
    comps.append({"name": "overlap_history", "weight": weights["overlap_history"], "available": ratio is not None,
                  "value": None if ratio is None else round(_clamp(ratio / 2), 4),
                  "explanation": f"Measured historical activity of {ov}: {ratio:.2f}x the average candle."
                  if ratio is not None else "No active overlap (or no measured history) - excluded, not penalised."})
    av = [c for c in comps if c["available"]]
    score = round(100 * sum(c["weight"] * c["value"] for c in av) / sum(c["weight"] for c in av), 1) if av else None
    return {"score": score, "components": comps, "methodology_version": cfg.methodology_version,
            "explanation": "Weighted mean of available components; missing components are excluded."}


def volatility_score(volatility: dict[str, Any] | None) -> dict[str, Any]:
    pct = (volatility or {}).get("atr_percentile")
    return {"score": None if pct is None else round(pct, 1), "regime": (volatility or {}).get("regime", "UNKNOWN"),
            "explanation": "ATR percentile versus the instrument's own recent history."}


def score_bundle(cfg: MIConfig, as_of: datetime, **parts: Any) -> dict[str, Any]:
    return {"as_of": as_of, "methodology_version": cfg.methodology_version, **parts,
            "disclaimer": "Scores summarise context; they are not probabilities of profit or loss and not trade signals."}
