"""Optimal Trade Entry: the 62-79% retracement (sweet spot 70.5%) of the current dealing-range leg."""
from __future__ import annotations

from aurum.analysis.config import PremiumDiscountConfig
from aurum.analysis.models.analysis_result import OteZone, PremiumDiscount
from aurum.analysis.models.fair_value_gap import FairValueGap
from aurum.analysis.models.order_block import OrderBlock


def ote_zone(pd_: PremiumDiscount | None, structure_bias: str, order_blocks: list[OrderBlock],
             gaps: list[FairValueGap], cfg: PremiumDiscountConfig | None = None) -> OteZone | None:
    cfg = cfg or PremiumDiscountConfig()
    if pd_ is None:
        return None
    hi, lo, r = pd_.range_high, pd_.range_low, pd_.range_high - pd_.range_low
    d = pd_.leg_direction
    if d == "bullish":
        lower, upper, sweet = hi - cfg.ote_high * r, hi - cfg.ote_low * r, hi - cfg.ote_sweet_spot * r
    else:
        lower, upper, sweet = lo + cfg.ote_low * r, lo + cfg.ote_high * r, lo + cfg.ote_sweet_spot * r
    overlaps = [z.id for z in order_blocks if z.direction == d and z.active
                and z.current_bottom <= upper and z.current_top >= lower]
    overlaps += [g.id for g in gaps if g.direction == d and g.active
                 and g.current_lower <= upper and g.current_upper >= lower]
    agrees = structure_bias == d
    text = (f"{d.title()} leg {lo:.6g}-{hi:.6g}: OTE {lower:.6g}-{upper:.6g}, sweet spot {sweet:.6g}. "
            + ("Agrees with the external structure bias." if agrees else
               f"Note: external structure bias is {structure_bias}, so this OTE is counter to it."))
    return OteZone(d, float(lower), float(upper), float(sweet), lower <= pd_.current_price <= upper, overlaps, text)
