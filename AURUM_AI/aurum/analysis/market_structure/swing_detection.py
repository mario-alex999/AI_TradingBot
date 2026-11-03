"""Swing high / swing low detection (fractal pivots) with explicit confirmation.

Swing high at candle i:  high[i] >  every high in the LEFT window  and  high[i] >= every high in the RIGHT window.
Swing low  at candle i:  low[i]  <  every low  in the LEFT window  and  low[i]  <= every low  in the RIGHT window.

Ties are resolved deterministically: in a run of equal highs only the FIRST candle can qualify (it is the only
one strictly above its left neighbours). A swing is confirmed by the close of candle ``i + right``; it never
exists before that candle closes and is never removed afterwards (non-repainting).
"""
from __future__ import annotations

import numpy as np

from aurum.analysis.config import SwingConfig, SwingWindow
from aurum.analysis.models.candle import CandleSeries
from aurum.analysis.models.swing import Swing


def _left_dominance(values: np.ndarray, i: int, lookback: int, higher: bool) -> int:
    count = 0
    for k in range(i - 1, max(-1, i - 1 - lookback), -1):
        if (values[k] < values[i]) if higher else (values[k] > values[i]):
            count += 1
        else:
            break
    return count


def detect_swings(series: CandleSeries, atr: np.ndarray, window: SwingWindow, scope: str,
                  cfg: SwingConfig | None = None) -> list[Swing]:
    """All confirmed swings, ordered by confirmation (then source) index."""
    cfg = cfg or SwingConfig()
    left, right = window.left, window.right
    if left < 1 or right < 1:
        raise ValueError("Swing windows need at least one candle on each side.")
    h, l, n = series.high, series.low, len(series)
    out: list[Swing] = []
    last_index = {"high": -10**9, "low": -10**9}
    for i in range(left, n - right):
        ci = i + right
        lo_win = l[i - left:ci + 1]
        hi_win = h[i - left:ci + 1]
        candidates = []
        if h[i] > h[i - left:i].max() and h[i] >= h[i + 1:ci + 1].max():
            candidates.append(("high", h[i], (h[i] - lo_win.min()) / atr[ci],
                               _left_dominance(h, i, cfg.strength_lookback, True)))
        if l[i] < l[i - left:i].min() and l[i] <= l[i + 1:ci + 1].min():
            candidates.append(("low", l[i], (hi_win.max() - l[i]) / atr[ci],
                               _left_dominance(l, i, cfg.strength_lookback, False)))
        for kind, price, prominence, dominance in candidates:
            if i - last_index[kind] < cfg.min_separation_bars:
                continue        # keep the earlier swing; replacing it later would repaint history
            last_index[kind] = i
            out.append(Swing(kind=kind, price=float(price), index=i, timestamp=series.time(i), confirmed_index=ci,
                             confirmed_at=series.close_time(ci), timeframe=series.timeframe, scope=scope,
                             strength=int(dominance + right), prominence_atr=round(float(prominence), 4),
                             provisional=series.is_provisional(ci)))
    out.sort(key=lambda s: (s.confirmed_index, s.index, s.kind))
    return out


def swings_known_at(swings: list[Swing], t: int) -> list[Swing]:
    """Swings whose confirming candle closed at or before candle ``t``."""
    return [s for s in swings if s.confirmed_index <= t]
