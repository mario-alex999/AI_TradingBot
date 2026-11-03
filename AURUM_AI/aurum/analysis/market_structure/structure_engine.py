"""Market-structure state per symbol/timeframe/scope: HH/HL/LH/LL labels, bias, confidence, CHoCH status."""
from __future__ import annotations

import numpy as np

from aurum.analysis.config import StructureConfig, SwingConfig
from aurum.analysis.market_structure.bos_choch import BreakWalk
from aurum.analysis.models.candle import CandleSeries
from aurum.analysis.models.structure_event import ChochStatus, StructureEvent, StructureState
from aurum.analysis.models.swing import Swing


def label_swings(swings: list[Swing], atr: np.ndarray, cfg: SwingConfig) -> None:
    """Label each swing against the previous swing of the same kind (in place)."""
    prev: dict[str, Swing] = {}
    for s in sorted(swings, key=lambda x: (x.confirmed_index, x.index)):
        p = prev.get(s.kind)
        if p is not None:
            tol = cfg.equal_tolerance_atr * atr[s.confirmed_index]
            diff = s.price - p.price
            if abs(diff) <= tol:
                s.label = "EQH" if s.kind == "high" else "EQL"
            elif s.kind == "high":
                s.label = "HH" if diff > 0 else "LH"
            else:
                s.label = "HL" if diff > 0 else "LL"
        prev[s.kind] = s


def sequence_bias(swings: list[Swing], need: int) -> str:
    """'bullish' when the last ``need`` labelled highs are HH and lows are HL; mirror for bearish."""
    highs = [s.label for s in swings if s.kind == "high" and s.label][-need:]
    lows = [s.label for s in swings if s.kind == "low" and s.label][-need:]
    if len(highs) < need or len(lows) < need:
        return "insufficient"
    if all(x == "HH" for x in highs) and all(x == "HL" for x in lows):
        return "bullish"
    if all(x == "LH" for x in highs) and all(x == "LL" for x in lows):
        return "bearish"
    return "mixed"


def choch_status(series: CandleSeries, walk: BreakWalk, scope: str, internal_events: list[StructureEvent] | None,
                 ) -> ChochStatus:
    events = walk.events
    if events and events[-1].event_type == "CHOCH":
        e = events[-1]
        state = "confirmed" if e.confirmed else "potential"
        text = ("Confirmed: a candle closed beyond the level protecting the previous structure. No continuation BOS "
                "in the new direction yet.") if e.confirmed else \
            "Potential: the break happened on a candle that has not closed yet; it can still disappear."
        return ChochStatus(state, scope, e.direction, e.broken_level, e.confirmed_at, text)
    bias = walk.bias
    if bias in ("bullish", "bearish") and len(series):
        t = series.last_index
        guard = walk.active_low if bias == "bullish" else walk.active_high
        if guard is not None:
            pierced = series.low[t] < guard.price if bias == "bullish" else series.high[t] > guard.price
            if pierced:
                d = "bearish" if bias == "bullish" else "bullish"
                return ChochStatus("potential", scope, d, guard.price, series.close_time(t),
                                   f"Potential: the last candle traded through the {guard.kind} at {guard.price:.6g} "
                                   f"that protects the {bias} structure but did not close beyond it.")
        if scope == "external" and internal_events:
            last_int = internal_events[-1]
            if last_int.event_type == "CHOCH" and last_int.direction != bias:
                return ChochStatus("potential", scope, last_int.direction, last_int.broken_level,
                                   last_int.confirmed_at,
                                   f"Potential: internal structure turned {last_int.direction} against the external "
                                   f"{bias} bias; no external level has been broken yet.")
    return ChochStatus("none", scope, None, None, None,
                       "None: no confirmed or developing break against the current structure.")


def build_state(series: CandleSeries, swings: list[Swing], walk: BreakWalk, scope: str, cfg: StructureConfig,
                internal_events: list[StructureEvent] | None = None) -> StructureState:
    highs = [s for s in swings if s.kind == "high"]
    lows = [s for s in swings if s.kind == "low"]
    if len(highs) < 2 or len(lows) < 2:
        # few swings: report the bias implied by confirmed breaks (if any) but with capped confidence
        bias = walk.bias
        conf = 0.3 if bias != "ranging" else 0.0
        return StructureState(scope, bias, walk.previous_bias, "insufficient_data", conf,
                              highs[-1].to_dict() if highs else None, lows[-1].to_dict() if lows else None, [],
                              ChochStatus("none", scope, explanation="Not enough confirmed swings."),
                              f"Insufficient data: {len(highs)} swing highs and {len(lows)} swing lows confirmed "
                              f"({scope}); at least two of each are needed for a reliable classification"
                              + (f". Confirmed breaks so far point {bias} (confidence capped at 30%)."
                                 if bias != "ranging" else "."))
    seq = sequence_bias(swings, cfg.min_swings_for_trend)
    bias = walk.bias
    last = walk.events[-1] if walk.events else None
    if bias == "ranging":
        state, conf = "ranging", 0.0
        text = f"Ranging: no {scope} structure level has been broken by a qualifying close yet (sequence: {seq})."
    else:
        conf = 0.4 + (0.3 if seq == bias else 0.0) + (0.2 if last and last.displacement else 0.0)
        same_dir = 0
        for e in reversed(walk.events):
            if e.direction != bias:
                break
            same_dir += 1
        conf += 0.1 if same_dir >= 2 else 0.0
        if last and last.event_type == "CHOCH" or (seq not in (bias, "insufficient")):
            state = "transitioning"
            conf = min(conf, 0.6)
        else:
            state = "trending"
        text = (f"{bias.title()} {scope} structure: last event {last.event_type} at {last.broken_level:.6g} "
                f"({last.confirmed_at:%Y-%m-%d %H:%M} UTC); swing sequence is {seq}"
                + (" (agrees)." if seq == bias else ".") if last else f"{bias} bias.")
    labels = [f"{s.label}@{s.price:.6g}" for s in swings if s.label][-6:]
    return StructureState(scope, bias, walk.previous_bias, state, round(min(conf, 1.0), 2),
                          walk.last_high.to_dict() if walk.last_high else None,
                          walk.last_low.to_dict() if walk.last_low else None, labels,
                          choch_status(series, walk, scope, internal_events), text)
