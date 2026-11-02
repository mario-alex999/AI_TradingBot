"""Inducement candidates: internal pullback swings that formed after the latest external break and sit between
price and an unmitigated point of interest (OB / FVG) in the break direction. Taking that liquidity before
price reaches the POI is a common pattern; these are candidates, not certainties."""
from __future__ import annotations

from aurum.analysis.models.candle import CandleSeries
from aurum.analysis.models.fair_value_gap import FairValueGap
from aurum.analysis.models.liquidity_pool import InducementCandidate
from aurum.analysis.models.order_block import OrderBlock
from aurum.analysis.models.structure_event import StructureEvent
from aurum.analysis.models.swing import Swing


def detect_inducements(series: CandleSeries, external_events: list[StructureEvent], internal_swings: list[Swing],
                       order_blocks: list[OrderBlock], gaps: list[FairValueGap], max_candidates: int = 3,
                       ) -> list[InducementCandidate]:
    if not external_events:
        return []
    e = external_events[-1]
    d = e.direction
    kind = "low" if d == "bullish" else "high"
    pulls = [s for s in internal_swings if s.kind == kind and s.index > e.break_index][-max_candidates:]
    pois = [{"type": "order_block", "id": z.id, "top": z.current_top, "bottom": z.current_bottom}
            for z in order_blocks if z.direction == d and z.active] + \
           [{"type": "fair_value_gap", "id": g.id, "top": g.current_upper, "bottom": g.current_lower}
            for g in gaps if g.direction == d and g.active]
    out: list[InducementCandidate] = []
    for s in pulls:
        beyond = [p for p in pois if (p["top"] < s.price if d == "bullish" else p["bottom"] > s.price)]
        target = (max(beyond, key=lambda p: p["top"]) if d == "bullish" else min(beyond, key=lambda p: p["bottom"])
                  ) if beyond else None
        c = InducementCandidate(direction=d, timeframe=series.timeframe, level=s.price, swing_time=s.timestamp,
                                confirmed_at=s.confirmed_at, related_break_at=e.confirmed_at, target_poi=target,
                                explanation=[f"Internal swing {kind} at {s.price:.6g} formed after the {d} external "
                                             f"{e.event_type} ({e.confirmed_at:%Y-%m-%d %H:%M} UTC)."])
        c.explanation.append(f"Unmitigated {target['type'].replace('_', ' ')} {target['bottom']:.6g}-{target['top']:.6g} "
                             f"lies beyond it." if target else "No unmitigated POI beyond it yet.")
        for u in range(s.confirmed_index + 1, len(series)):
            if (series.low[u] < s.price) if d == "bullish" else (series.high[u] > s.price):
                c.state, c.swept_at = "swept", series.time(u)
                break
        out.append(c)
    return out
