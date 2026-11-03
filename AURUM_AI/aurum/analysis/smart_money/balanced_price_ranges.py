"""Balanced Price Range: a bullish and a bearish FVG, formed close together, whose ranges overlap.

The overlap is the BPR; its direction is that of the more recent gap. It is detected when the later of the two
gaps is detected and is invalidated by a close through it against its direction.
"""
from __future__ import annotations

from aurum.analysis.config import BprConfig
from aurum.analysis.models.candle import CandleSeries
from aurum.analysis.models.fair_value_gap import BalancedPriceRange, FairValueGap


def detect_bprs(series: CandleSeries, gaps: list[FairValueGap], cfg: BprConfig | None = None) -> list[BalancedPriceRange]:
    cfg = cfg or BprConfig()
    if not cfg.enabled:
        return []
    bulls = [g for g in gaps if g.direction == "bullish"]
    bears = [g for g in gaps if g.direction == "bearish"]
    out: list[BalancedPriceRange] = []
    for b in bulls:
        for s in bears:
            if abs(b.detected_index - s.detected_index) > cfg.max_bars_apart:
                continue
            lower, upper = max(b.lower, s.lower), min(b.upper, s.upper)
            if upper <= lower:
                continue
            ratio = (upper - lower) / min(b.size, s.size)
            if ratio < cfg.min_overlap_ratio:
                continue
            later = b if b.detected_index >= s.detected_index else s
            bpr = BalancedPriceRange(
                id=f"BPR-{series.timeframe}-{later.detected_at:%Y%m%d%H%M}-{b.id[-12:]}", direction=later.direction,
                timeframe=series.timeframe, bullish_fvg_id=b.id, bearish_fvg_id=s.id,
                detected_index=later.detected_index, detected_at=later.detected_at, lower=float(lower),
                upper=float(upper), overlap_ratio=round(float(ratio), 4),
                explanation=[f"Bullish gap {b.lower:.6g}-{b.upper:.6g} and bearish gap {s.lower:.6g}-{s.upper:.6g} "
                             f"overlap by {ratio:.0%} of the smaller gap.",
                             f"Direction follows the more recent {later.direction} gap."])
            for u in range(bpr.detected_index + 1, len(series)):
                cl = series.close[u]
                if (cl < lower) if bpr.direction == "bullish" else (cl > upper):
                    bpr.state, bpr.invalidated_at = "invalidated", series.time(u)
                    break
            out.append(bpr)
    return sorted(out, key=lambda x: x.detected_index)
