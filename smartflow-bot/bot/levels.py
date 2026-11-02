"""Support & resistance from clustered pivots, ranked by touches."""
from __future__ import annotations
import numpy as np
import pandas as pd


def sr_levels(df: pd.DataFrame, upto: int, window: int = 10, cluster_atr: float = 0.5, lookback: int = 500):
    seg = df.iloc[max(0, upto - lookback):upto]
    if len(seg) < 2 * window + 1:
        return []
    h, l = seg["high"].values, seg["low"].values
    pivots = []
    for i in range(window, len(seg) - window):
        if h[i] == h[i - window:i + window + 1].max(): pivots.append(h[i])
        if l[i] == l[i - window:i + window + 1].min(): pivots.append(l[i])
    if not pivots:
        return []
    tol = cluster_atr * float(seg["atr"].iloc[-1] if "atr" in seg else (h - l).mean())
    pivots.sort()
    clusters = [[pivots[0]]]
    for p in pivots[1:]:
        (clusters[-1].append(p) if p - clusters[-1][-1] <= tol else clusters.append([p]))
    return sorted(({"level": float(np.mean(c)), "touches": len(c)} for c in clusters),
                  key=lambda x: -x["touches"])


def near_level(price: float, levels: list, tol: float, min_touches: int = 2) -> dict | None:
    for lv in levels:
        if lv["touches"] >= min_touches and abs(price - lv["level"]) <= tol:
            return lv
    return None
