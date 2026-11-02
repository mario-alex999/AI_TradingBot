"""Time alignment for multi-timeframe work: each timeframe only sees candles that had CLOSED at ``as_of``.

A D1 candle opened today is not visible to an M15 analysis at 10:00 (unless provisional analysis is requested,
in which case it is kept as an explicitly forming candle).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pandas as pd

from aurum.analysis.models.analysis_result import TimeframeAnalysis
from aurum.config import TIMEFRAME_SECONDS


def align_to_as_of(df: pd.DataFrame, timeframe: str, as_of: datetime, include_provisional: bool) -> pd.DataFrame:
    if df.empty:
        return df
    period = timedelta(seconds=TIMEFRAME_SECONDS[timeframe])
    ts = pd.to_datetime(df["ts_utc"], utc=True)
    close_t = ts + period
    as_of_ts = pd.Timestamp(as_of)
    closed = close_t <= as_of_ts
    forming = (ts <= as_of_ts) & (close_t > as_of_ts)
    out = df.loc[closed | (forming if include_provisional else False)].copy()
    if "is_complete" not in out.columns:
        out["is_complete"] = True
    out.loc[forming.loc[out.index], "is_complete"] = False   # forming at as_of, whatever the stored flag says
    return out


def snapshot(a: TimeframeAnalysis) -> dict[str, Any]:
    """Compact per-timeframe view used by the confluence engine and API."""
    price = a.current_price
    ext, intl = a.structure.get("external"), a.structure.get("internal")
    inside = []
    if price is not None:
        inside += [f"{z.direction} order block {z.current_bottom:.6g}-{z.current_top:.6g} ({z.state})"
                   for z in a.order_blocks if z.active and z.current_bottom <= price <= z.current_top]
        inside += [f"{g.direction} FVG {g.current_lower:.6g}-{g.current_upper:.6g} ({g.state})"
                   for g in a.fair_value_gaps if g.active and g.current_lower <= price <= g.current_upper]
    last_event = a.structure_events[-1] if a.structure_events else None
    return {"timeframe": a.timeframe, "status": a.status, "as_of": a.as_of, "provisional": a.provisional,
            "candles_used": a.candles_used, "current_price": price,
            "external_bias": ext.bias if ext else "ranging", "external_state": ext.state if ext else None,
            "external_confidence": ext.confidence if ext else 0.0,
            "internal_bias": intl.bias if intl else "ranging",
            "choch": ext.choch.to_dict() if ext and ext.choch else None,
            "premium_discount": a.premium_discount.zone if a.premium_discount else None,
            "in_ote": bool(a.ote and a.ote.price_inside), "price_inside": inside,
            "last_event": last_event.to_dict() if last_event else None,
            "score": a.score.to_dict() if a.score else None, "messages": a.messages}
