"""Premium / discount of the current dealing range (latest confirmed external swing high and low), with
equilibrium and Fibonacci retracement levels measured from the leg's end."""
from __future__ import annotations

from aurum.analysis.config import PremiumDiscountConfig
from aurum.analysis.models.analysis_result import PremiumDiscount
from aurum.analysis.models.swing import Swing


def fib_levels(high: float, low: float, leg: str, ratios: tuple[float, ...]) -> dict[str, float]:
    """Retracement prices: 0 = end of the leg, 1 = its start. Bullish leg (low -> high) retraces downward."""
    r = high - low
    return {f"{x:g}": (high - x * r) if leg == "bullish" else (low + x * r) for x in ratios}


def premium_discount(swing_high: Swing | None, swing_low: Swing | None, price: float | None,
                     cfg: PremiumDiscountConfig | None = None) -> PremiumDiscount | None:
    cfg = cfg or PremiumDiscountConfig()
    if swing_high is None or swing_low is None or price is None or swing_high.price <= swing_low.price:
        return None
    hi, lo = swing_high.price, swing_low.price
    eq = (hi + lo) / 2
    pos = (price - lo) / (hi - lo)
    if pos > 1:
        zone = "above_range"
    elif pos < 0:
        zone = "below_range"
    elif abs(pos - 0.5) <= cfg.equilibrium_band:
        zone = "equilibrium"
    else:
        zone = "premium" if pos > 0.5 else "discount"
    leg = "bullish" if swing_low.index < swing_high.index else "bearish"
    return PremiumDiscount(hi, lo, swing_high.timestamp, swing_low.timestamp, eq, price, round(pos, 4), zone, leg,
                           fib_levels(hi, lo, leg, cfg.fib_levels))
