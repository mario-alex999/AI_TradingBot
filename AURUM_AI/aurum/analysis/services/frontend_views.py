"""Views matching the existing frontend contracts in aurum-frontend/src/types/analysis.ts and market.ts:
``MarketStructureReport`` (GET /analysis/market-structure?instrument=) and ``ChartOverlay[]``
(GET /analysis/market-structure?instrument=&timeframe=&view=overlays)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from aurum.analysis.models.analysis_result import TimeframeAnalysis

MAX_EVENTS = 40
MAX_OVERLAYS_PER_KIND = 12


def _unix(dt: datetime | None) -> int | None:
    return int(dt.timestamp()) if dt else None


def timeframe_structure(a: TimeframeAnalysis) -> dict[str, Any]:
    ext = a.structure.get("external")
    if a.status != "ok" or ext is None:
        bias = "awaiting"
    else:
        bias = ext.bias
    events: list[dict[str, Any]] = []
    for s in a.swings:
        if s.scope == "external" and s.label in ("HH", "HL", "LH", "LL"):
            events.append({"type": s.label, "price": s.price, "time": s.timestamp,
                           "direction": "bullish" if s.label in ("HH", "HL") else "bearish"})
    for e in a.structure_events:
        events.append({"type": e.event_type, "price": e.broken_level, "time": e.confirmed_at, "direction": e.direction})
    for le in a.liquidity_events:
        if le.event == "sweep":
            events.append({"type": "liquidity_sweep", "price": le.level, "time": le.confirmed_at,
                           "direction": le.implication})
    for ob in a.order_blocks:
        if ob.active:
            events.append({"type": "order_block", "price": ob.proximal, "time": ob.detected_at, "direction": ob.direction})
    for g in a.fair_value_gaps:
        if g.active:
            events.append({"type": "fvg", "price": (g.current_lower + g.current_upper) / 2, "time": g.detected_at,
                           "direction": g.direction})
    events.sort(key=lambda x: x["time"])
    pd_ = a.premium_discount
    return {"timeframe": a.timeframe, "bias": bias, "events": events[-MAX_EVENTS:],
            "premium_discount": {"equilibrium": pd_.equilibrium, "range_high": pd_.range_high,
                                 "range_low": pd_.range_low} if pd_ else None}


def market_structure_report(symbol: str, results: dict[str, TimeframeAnalysis]) -> dict[str, Any]:
    return {"instrument": symbol, "generated_at": datetime.now(timezone.utc),
            "timeframes": [timeframe_structure(a) for a in results.values()]}


def overlays(a: TimeframeAnalysis) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    end = _unix(a.as_of)
    for z in a.support_resistance[:MAX_OVERLAYS_PER_KIND]:
        out.append({"id": z.id, "kind": "support_resistance", "label": f"{z.role.title()} ({z.touches} touches)",
                    "price": z.bottom, "price_to": z.top, "from": _unix(z.first_time), "to": end})
    for ob in [o for o in a.order_blocks if o.active][-MAX_OVERLAYS_PER_KIND:]:
        out.append({"id": ob.id, "kind": "order_block", "label": f"{ob.direction.title()} OB ({ob.state.replace('_', ' ')})",
                    "price": ob.current_bottom, "price_to": ob.current_top, "from": _unix(ob.source_time), "to": end,
                    "direction": ob.direction})
    for g in [g for g in a.fair_value_gaps if g.active][-MAX_OVERLAYS_PER_KIND:]:
        out.append({"id": g.id, "kind": "fair_value_gap", "label": f"{g.direction.title()} FVG ({g.state.replace('_', ' ')})",
                    "price": g.current_lower, "price_to": g.current_upper, "from": _unix(g.candle_times[1]), "to": end,
                    "direction": g.direction})
    for p in [p for p in a.liquidity_pools if p.state == "active"][-MAX_OVERLAYS_PER_KIND:]:
        kind = ("equal_highs_lows" if p.kind.startswith("equal") else
                "previous_day_hl" if p.kind.startswith("previous_day") else "liquidity_zone")
        out.append({"id": p.id, "kind": kind, "label": f"{p.kind.replace('_', ' ').title()} "
                    f"({'BSL' if p.side == 'buy_side' else 'SSL'})", "price": p.level,
                    "from": _unix(p.member_times[0]), "to": end,
                    "direction": "bearish" if p.side == "buy_side" else "bullish"})
    for e in a.structure_events[-MAX_OVERLAYS_PER_KIND:]:
        out.append({"id": f"{e.scope}-{e.event_type}-{_unix(e.break_candle_time)}",
                    "kind": "break_of_structure" if e.event_type == "BOS" else "change_of_character",
                    "label": f"{e.scope.title()} {e.event_type}", "price": e.broken_level,
                    "from": _unix(e.broken_swing_time), "to": _unix(e.confirmed_at), "direction": e.direction})
    return out
