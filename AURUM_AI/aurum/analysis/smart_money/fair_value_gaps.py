"""Three-candle Fair Value Gaps.

Bullish FVG: low(c3) > high(c1)  -> gap [high(c1), low(c3)].
Bearish FVG: high(c3) < low(c1)  -> gap [high(c3), low(c1)].
Detected only once candle 3 has closed; fill state uses later candles only.
"""
from __future__ import annotations

import numpy as np

from aurum.analysis.config import FvgConfig
from aurum.analysis.market_structure.displacement import candle_metrics
from aurum.analysis.models.candle import CandleSeries
from aurum.analysis.models.fair_value_gap import FairValueGap
from aurum.analysis.models.structure_event import Displacement


def detect_fvgs(series: CandleSeries, atr: np.ndarray, displacements: list[Displacement] | None = None,
                cfg: FvgConfig | None = None) -> list[FairValueGap]:
    cfg = cfg or FvgConfig()
    disp_idx = {d.index for d in displacements or []}
    h, l = series.high, series.low
    gaps: list[FairValueGap] = []
    for i in range(2, len(series)):
        if series.is_provisional(i):
            continue                                   # candle 3 must be closed
        for direction, lower, upper in (("bullish", h[i - 2], l[i]), ("bearish", h[i], l[i - 2])):
            size = upper - lower
            if size <= 0 or size < max(cfg.min_size_atr * atr[i], cfg.min_size_price):
                continue
            m = candle_metrics(series, atr, i - 1)
            g = FairValueGap(
                id=f"FVG-{series.timeframe}-{direction[:4]}-{series.time(i - 1):%Y%m%d%H%M}", direction=direction,
                timeframe=series.timeframe, candle_times=[series.time(i - 2), series.time(i - 1), series.time(i)],
                detected_index=i, detected_at=series.close_time(i), lower=float(lower), upper=float(upper),
                size=float(size), size_atr_pct=round(float(size / atr[i] * 100), 2),
                middle_body_ratio=round(m["body_ratio"], 4), middle_range_ratio=round(m["range_ratio"], 4),
                middle_is_displacement=(i - 1) in disp_idx, current_lower=float(lower), current_upper=float(upper))
            _track(g, series, cfg)
            gaps.append(g)
    return gaps


def _track(g: FairValueGap, series: CandleSeries, cfg: FvgConfig) -> None:
    bull = g.direction == "bullish"
    filled = 0.0
    for u in range(g.detected_index + 1, len(series)):
        if g.state in ("invalidated", "expired"):
            break
        if g.state in ("unfilled", "partially_filled") and u - g.detected_index > cfg.expiry_bars:
            g.state, g.expired_at = "expired", series.time(u)
            break
        lo, hi, cl = series.low[u], series.high[u], series.close[u]
        if bull and lo < g.upper:
            filled = max(filled, (g.upper - max(lo, g.lower)) / g.size)
            g.current_upper = max(g.lower, min(g.current_upper, lo))
        elif not bull and hi > g.lower:
            filled = max(filled, (min(hi, g.upper) - g.lower) / g.size)
            g.current_lower = min(g.upper, max(g.current_lower, hi))
        if filled > 0 and g.state == "unfilled":
            g.state, g.partially_filled_at = "partially_filled", series.time(u)
        if filled >= 1.0 and g.state != "fully_filled":
            g.state, g.filled_at = "fully_filled", series.time(u)
        if cfg.invalidate_on_close_beyond and ((cl < g.lower) if bull else (cl > g.upper)):
            g.state, g.invalidated_at = "invalidated", series.time(u)
    g.fill_pct = round(min(filled, 1.0) * 100, 2)
