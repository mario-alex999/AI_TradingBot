"""Order blocks: the last opposite-coloured candle before a displacement that produced a qualifying BOS/CHoCH.

Bullish OB = last bearish candle before the bullish displacement that broke structure up (mirror for bearish).
An OB is *detected* when the breakout candle closes (``detected_at``), never earlier, and its state is
tracked only with candles after that.
"""
from __future__ import annotations

import numpy as np

from aurum.analysis.config import OrderBlockConfig
from aurum.analysis.models.candle import CandleSeries
from aurum.analysis.models.order_block import OrderBlock
from aurum.analysis.models.structure_event import Displacement, StructureEvent


def _move_start(d: Displacement, seq_len: int) -> int:
    return d.index - (seq_len - 1) if d.kind == "sequence" else d.index


def _zone(cfg: OrderBlockConfig, direction: str, o: float, h: float, l: float, c: float) -> tuple[float, float]:
    if cfg.zone_mode == "body":
        return max(o, c), min(o, c)
    if cfg.zone_mode == "open_extreme":
        return (o, l) if direction == "bullish" else (h, o)
    return h, l


def detect_order_blocks(series: CandleSeries, atr: np.ndarray, events: list[StructureEvent],
                        displacements: list[Displacement], cfg: OrderBlockConfig | None = None,
                        sequence_length: int = 3) -> list[OrderBlock]:
    cfg = cfg or OrderBlockConfig()
    o, h, l, c = series.open, series.high, series.low, series.close
    blocks: list[OrderBlock] = []
    seen: set[tuple[int, str]] = set()
    triggers = [e for e in events if e.scope in cfg.scopes] if cfg.require_structure_break else events
    for e in sorted(triggers, key=lambda x: x.break_index):
        t, d = e.break_index, e.direction
        moves = [x for x in displacements if x.direction == d and t - cfg.max_lookback <= x.index <= t]
        if moves:
            start = min(_move_start(x, sequence_length) for x in moves)
            disp_strength = max(x.strength for x in moves)
        elif cfg.require_displacement:
            continue
        else:
            start, disp_strength = t, 0.0
        opposite = (lambda j: c[j] < o[j]) if d == "bullish" else (lambda j: c[j] > o[j])
        j = next((k for k in range(start - 1, max(-1, start - 1 - cfg.max_lookback), -1) if opposite(k)), None)
        if j is None or (j, d) in seen:
            continue
        top, bottom = _zone(cfg, d, o[j], h[j], l[j], c[j])
        if top <= bottom:
            continue
        seen.add((j, d))
        ob = OrderBlock(
            id=f"OB-{series.timeframe}-{d[:4]}-{series.time(j):%Y%m%d%H%M}", direction=d, timeframe=series.timeframe,
            source_index=j, source_time=series.time(j), source_open=float(o[j]), source_high=float(h[j]),
            source_low=float(l[j]), source_close=float(c[j]), detected_index=t, detected_at=series.close_time(t),
            break_event={"type": e.event_type, "scope": e.scope, "level": e.broken_level,
                         "confirmed_at": e.confirmed_at},
            zone_mode=cfg.zone_mode, full_top=float(h[j]), full_bottom=float(l[j]), body_top=float(max(o[j], c[j])),
            body_bottom=float(min(o[j], c[j])), initial_top=float(top), initial_bottom=float(bottom),
            current_top=float(top), current_bottom=float(bottom), size=float(top - bottom),
            size_atr=round(float((top - bottom) / atr[t]), 4), displacement_strength=round(float(disp_strength), 4),
            explanation=[
                f"Last {'bearish' if d == 'bullish' else 'bullish'} candle ({series.time(j):%Y-%m-%d %H:%M} UTC) "
                f"before the {d} move.",
                f"The move produced a {e.scope} {e.event_type} through {e.broken_level:.6g}, confirmed "
                f"{e.confirmed_at:%Y-%m-%d %H:%M} UTC.",
                (f"Displacement {disp_strength:.2f}x ATR." if disp_strength else "No qualifying displacement (allowed by config)."),
                f"Zone mode '{cfg.zone_mode}': {bottom:.6g} - {top:.6g}."])
        _track(ob, series, cfg)
        blocks.append(ob)
    return blocks


def _track(ob: OrderBlock, series: CandleSeries, cfg: OrderBlockConfig) -> None:
    """Update mitigation / invalidation / expiry using candles after detection only."""
    top, bottom, size = ob.initial_top, ob.initial_bottom, ob.initial_top - ob.initial_bottom
    bull = ob.direction == "bullish"
    pen = 0.0
    for u in range(ob.detected_index + 1, len(series)):
        if ob.state in ("invalidated", "expired"):
            break
        if ob.state in ("fresh", "partially_mitigated") and u - ob.detected_index > cfg.expiry_bars:
            ob.state, ob.expired_at = "expired", series.time(u)
            break
        lo, hi, cl = series.low[u], series.high[u], series.close[u]
        touched = lo <= top if bull else hi >= bottom
        if touched:
            if ob.first_touch_at is None:
                ob.first_touch_at = series.time(u)
            depth = (top - max(lo, bottom)) / size if bull else (min(hi, top) - bottom) / size
            pen = max(pen, min(max(depth, 0.0), 1.0))
            if bull:
                ob.current_top = max(bottom, min(ob.current_top, lo))
            else:
                ob.current_bottom = min(top, max(ob.current_bottom, hi))
            need = {"first_touch": 0.0, "fifty_percent": 0.5, "full": 1.0}[cfg.mitigation_mode]
            if ob.state != "mitigated":
                if pen >= need:
                    ob.state, ob.mitigated_at = "mitigated", series.time(u)
                else:
                    ob.state = "partially_mitigated"
        if (cl < bottom) if bull else (cl > top):
            ob.state, ob.invalidated_at = "invalidated", series.time(u)
    ob.mitigation_pct = round(pen * 100, 2)
