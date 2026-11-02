"""Break of Structure (BOS) and Change of Character (CHoCH), walked candle by candle.

* The relevant levels are the most recent confirmed swing high and swing low (of one scope: internal or
  external). A swing can only be broken by candles AFTER the candle that confirmed it.
* Default confirmation is a candle CLOSE beyond the level plus an optional buffer (ATR multiple / absolute);
  wick mode is optional.
* A break in the direction of the current bias is a BOS; a break against it is a CHoCH; the first break when no
  bias exists yet is an initial BOS that establishes the bias.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from aurum.analysis.config import DisplacementConfig, StructureConfig
from aurum.analysis.market_structure.displacement import strongest_before
from aurum.analysis.models.candle import CandleSeries
from aurum.analysis.models.structure_event import Displacement, StructureEvent
from aurum.analysis.models.swing import Swing


@dataclass
class BreakWalk:
    events: list[StructureEvent] = field(default_factory=list)
    bias: str = "ranging"
    previous_bias: str = "ranging"
    active_high: Swing | None = None      # most recent confirmed, unbroken swing high
    active_low: Swing | None = None
    last_high: Swing | None = None        # most recent confirmed swing high (broken or not)
    last_low: Swing | None = None


def walk_breaks(series: CandleSeries, swings: list[Swing], atr: np.ndarray, scope: str,
                cfg: StructureConfig | None = None, displacements: list[Displacement] | None = None,
                dcfg: DisplacementConfig | None = None) -> BreakWalk:
    cfg, dcfg = cfg or StructureConfig(), dcfg or DisplacementConfig()
    displacements = displacements or []
    w = BreakWalk()
    pending = sorted(swings, key=lambda s: (s.confirmed_index, s.index))
    p = 0
    c, h, l, o = series.close, series.high, series.low, series.open
    for t in range(len(series)):
        while p < len(pending) and pending[p].confirmed_index < t:   # known before candle t opens
            s = pending[p]
            if s.kind == "high":
                w.active_high = w.last_high = s
            else:
                w.active_low = w.last_low = s
            p += 1
        buf = max(cfg.break_buffer_atr * atr[t], cfg.min_break_price)
        up_px, dn_px = (c[t], c[t]) if cfg.break_mode == "close" else (h[t], l[t])
        up = w.active_high is not None and up_px > w.active_high.price + buf
        dn = w.active_low is not None and dn_px < w.active_low.price - buf
        if up and dn:                     # only possible in wick mode: the candle body decides
            up, dn = c[t] >= o[t], c[t] < o[t]
        if up:
            _record(w, series, atr, t, "bullish", w.active_high, scope, cfg, displacements, dcfg)
            w.active_high = None
        elif dn:
            _record(w, series, atr, t, "bearish", w.active_low, scope, cfg, displacements, dcfg)
            w.active_low = None
    return w


def _record(w: BreakWalk, series: CandleSeries, atr: np.ndarray, t: int, direction: str, level: Swing, scope: str,
            cfg: StructureConfig, displacements: list[Displacement], dcfg: DisplacementConfig) -> None:
    prev = w.bias
    initial = prev == "ranging"
    etype = "CHOCH" if (prev != "ranging" and prev != direction) else "BOS"
    disp = strongest_before(displacements, direction, max(level.index + 1, t - dcfg.lookback_for_break + 1), t)
    word = "above" if direction == "bullish" else "below"
    how = "closed" if cfg.break_mode == "close" else "traded"
    if etype == "CHOCH":
        why = (f"{scope.title()} CHoCH: bias was {prev}; price {how} {word} the {level.kind} at {level.price:.6g} "
               f"({level.timestamp:%Y-%m-%d %H:%M} UTC), the level protecting the {prev} structure.")
    elif initial:
        why = (f"Initial {scope} BOS: no prior bias; price {how} {word} the swing {level.kind} at "
               f"{level.price:.6g}, establishing a {direction} bias.")
    else:
        why = f"{scope.title()} BOS: {direction} continuation, price {how} {word} the swing {level.kind} at {level.price:.6g}."
    if disp:
        why += f" Displacement {disp.strength:.2f}x ATR."
        disp.caused_structure_break = True
        disp.related_event = f"{scope}_{etype}_{series.time(t):%Y%m%d%H%M}"
    w.events.append(StructureEvent(
        event_type=etype, direction=direction, scope=scope, timeframe=series.timeframe,
        broken_level=float(level.price), broken_swing_index=level.index, broken_swing_time=level.timestamp,
        break_index=t, break_candle_time=series.time(t), confirmed_at=series.close_time(t),
        previous_bias=prev, new_bias=direction, break_mode=cfg.break_mode, confirmed=not series.is_provisional(t),
        initial=initial, displacement=disp is not None, displacement_strength=disp.strength if disp else 0.0,
        explanation=why))
    if prev != direction:
        w.previous_bias = prev
    w.bias = direction
