"""Support / resistance zones from clusters of confirmed swing prices (internal + external)."""
from __future__ import annotations

import numpy as np

from aurum.analysis.config import SupportResistanceConfig
from aurum.analysis.models.candle import CandleSeries
from aurum.analysis.models.support_resistance import SupportResistanceZone
from aurum.analysis.models.swing import Swing
from aurum.analysis.support_resistance.zone_scoring import score_zone


def detect_zones(series: CandleSeries, atr: np.ndarray, swings: list[Swing],
                 cfg: SupportResistanceConfig | None = None) -> list[SupportResistanceZone]:
    cfg = cfg or SupportResistanceConfig()
    if not swings or len(series) == 0:
        return []
    last = series.last_index
    tol = cfg.cluster_tolerance_atr * atr[last]
    pad = cfg.zone_padding_atr * atr[last]
    unique = {(s.index, s.kind): s for s in swings}.values()        # same swing may appear in both scopes
    pts = sorted(unique, key=lambda s: (s.price, s.index))
    clusters: list[list[Swing]] = []
    for s in pts:
        if clusters and s.price - clusters[-1][0].price <= 2 * tol and s.price - clusters[-1][-1].price <= tol:
            clusters[-1].append(s)
        else:
            clusters.append([s])
    price = float(series.close[last])
    zones: list[SupportResistanceZone] = []
    for cl in clusters:
        if len(cl) < cfg.min_touches:
            continue
        top, bottom = max(s.price for s in cl) + pad, min(s.price for s in cl) - pad
        highs, lows = sum(s.kind == "high" for s in cl), sum(s.kind == "low" for s in cl)
        role = "flip" if highs and lows else "resistance" if highs else "support"
        position = "at_price" if bottom <= price <= top else "below_price" if top < price else "above_price"
        times = sorted(s.timestamp for s in cl)
        z = SupportResistanceZone(id=f"SR-{series.timeframe}-{bottom:.6g}-{top:.6g}", timeframe=series.timeframe,
                                  top=float(top), bottom=float(bottom), mid=float((top + bottom) / 2), touches=len(cl),
                                  swing_highs=highs, swing_lows=lows, first_time=times[0], last_time=times[-1],
                                  role=role, position=position)
        z.score, z.score_breakdown = score_zone(cl, last, cfg)
        zones.append(z)
    zones.sort(key=lambda z: -z.score)
    return zones[:cfg.max_zones]
