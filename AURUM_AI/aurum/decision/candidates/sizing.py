"""Informational position size. It is a suggestion shown to a person; nothing is ever sent anywhere."""
from __future__ import annotations

import math
from typing import Any


def suggest_volume(risk_pct: float, equity: float | None, stop_distance: float, spec: dict[str, Any] | None,
                   max_risk_pct: float | None = None) -> tuple[float | None, str | None]:
    """(lots, reason). Lots is None when it cannot be computed honestly; the reason says why. A reason alongside a volume
    is a warning: the broker's minimum lot risks more than requested."""
    if equity is None or equity <= 0:
        return None, "Account equity is unavailable."
    if spec is None:
        return None, "Instrument specification is unavailable."
    tick_size, tick_value = spec.get("tick_size"), spec.get("tick_value")
    step, low, high = spec.get("volume_step"), spec.get("volume_min"), spec.get("volume_max")
    if not tick_size or not tick_value:
        return None, "Tick size or tick value is unavailable."
    if not step or not low or not high:
        return None, "Volume limits are unavailable."
    if stop_distance <= 0:
        return None, "Stop distance is not positive."

    pct = min(risk_pct, max_risk_pct) if max_risk_pct else risk_pct
    money_per_lot = stop_distance / tick_size * tick_value
    raw = pct / 100 * equity / money_per_lot
    lots = round(math.floor(raw / step + 1e-9) * step, 8)
    if lots > high:
        return high, None
    if lots < low:
        actual_pct = low * money_per_lot / equity * 100
        if max_risk_pct and actual_pct > max_risk_pct:
            return None, (f"The minimum lot {low} would risk {actual_pct:.2f}% of equity, above "
                          f"RISK_MAX_PER_TRADE_PCT ({max_risk_pct}%).")
        return low, f"The minimum lot {low} risks {actual_pct:.2f}% of equity, more than the {pct}% requested."
    return lots, None
