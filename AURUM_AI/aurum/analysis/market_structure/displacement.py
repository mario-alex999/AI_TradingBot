"""Displacement: unusually strong directional candles (or short runs of them), measured against ATR.

A displacement is a price-action classification. It does not prove institutional participation.
"""
from __future__ import annotations

import numpy as np

from aurum.analysis.config import DisplacementConfig
from aurum.analysis.models.candle import CandleSeries
from aurum.analysis.models.structure_event import Displacement


def candle_metrics(series: CandleSeries, atr: np.ndarray, i: int) -> dict[str, float]:
    o, h, l, c = series.open[i], series.high[i], series.low[i], series.close[i]
    rng = h - l
    body = abs(c - o)
    bullish = c >= o
    close_pos = ((c - l) / rng if bullish else (h - c) / rng) if rng > 0 else 0.0
    return {"body_ratio": body / atr[i], "range_ratio": rng / atr[i],
            "body_to_range": body / rng if rng > 0 else 0.0, "close_position": close_pos,
            "direction": 1.0 if c > o else -1.0 if c < o else 0.0}


def detect_displacements(series: CandleSeries, atr: np.ndarray, cfg: DisplacementConfig | None = None) -> list[Displacement]:
    cfg = cfg or DisplacementConfig()
    out: list[Displacement] = []
    vol = series.volume if series.volume_usable else None
    for i in range(len(series)):
        m = candle_metrics(series, atr, i)
        if m["direction"] == 0:
            continue
        direction = "bullish" if m["direction"] > 0 else "bearish"
        vol_ratio = None
        if vol is not None and i >= 20:
            avg = float(np.mean(vol[i - 20:i]))
            vol_ratio = round(float(vol[i] / avg), 3) if avg > 0 else None
        bonus = 0.25 if vol_ratio is not None and vol_ratio >= cfg.volume_expansion else 0.0
        single = (m["body_ratio"] >= cfg.min_body_atr and m["range_ratio"] >= cfg.min_range_atr
                  and m["body_to_range"] >= cfg.min_body_to_range and m["close_position"] >= cfg.min_close_position)
        if single:
            out.append(Displacement(direction, i, series.time(i), "single", round(m["body_ratio"], 4),
                                    round(m["range_ratio"], 4), round(m["body_to_range"], 4),
                                    round(m["close_position"], 4), round(float(atr[i]), 10),
                                    round(m["body_ratio"] + bonus, 4), vol_ratio))
            continue
        k = cfg.sequence_length
        if k >= 2 and i >= k - 1:
            idx = range(i - k + 1, i + 1)
            dirs = {np.sign(series.close[j] - series.open[j]) for j in idx}
            if dirs == {m["direction"]}:
                combined = abs(series.close[i] - series.open[i - k + 1]) / atr[i]
                span = (series.high[i - k + 1:i + 1].max() - series.low[i - k + 1:i + 1].min()) / atr[i]
                if combined >= cfg.sequence_min_body_atr:
                    out.append(Displacement(direction, i, series.time(i), "sequence", round(combined, 4),
                                            round(span, 4), round(m["body_to_range"], 4),
                                            round(m["close_position"], 4), round(float(atr[i]), 10),
                                            round(combined + bonus, 4), vol_ratio))
    return out


def strongest_before(displacements: list[Displacement], direction: str, start: int, end: int) -> Displacement | None:
    """Strongest displacement in ``direction`` with index in [start, end]."""
    best = None
    for d in displacements:
        if d.direction == direction and start <= d.index <= end and (best is None or d.strength > best.strength):
            best = d
    return best
