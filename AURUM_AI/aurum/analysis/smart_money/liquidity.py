"""Liquidity pools (equal highs/lows, swing highs/lows, previous-day high/low) and what price did with them.

* Buy-side liquidity rests above highs, sell-side below lows.
* Sweep: a candle trades beyond the level but CLOSES back inside (stop run, implication against the sweep).
* Break: a candle CLOSES beyond the level (+ optional ATR buffer) - acceptance beyond the pool.
A pool is consumed by its first sweep or break.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

import numpy as np

from aurum.analysis.config import LiquidityConfig
from aurum.analysis.models.candle import CandleSeries
from aurum.analysis.models.liquidity_pool import LiquidityEvent, LiquidityPool
from aurum.analysis.models.swing import Swing


def _equal_pools(series: CandleSeries, atr: np.ndarray, swings: list[Swing], cfg: LiquidityConfig) -> list[LiquidityPool]:
    pools: list[LiquidityPool] = []
    for kind in ("high", "low"):
        members = [s for s in swings if s.kind == kind]
        used: set[int] = set()
        for k, s in enumerate(members):
            tol = cfg.equal_tolerance_atr * atr[s.confirmed_index]
            group = [s]
            for p in reversed(members[:k]):
                if s.index - p.index > cfg.lookback_bars:
                    break
                if p.index in used or abs(p.price - s.price) > tol:
                    continue
                between = (series.high[p.index + 1:s.index] if kind == "high" else series.low[p.index + 1:s.index])
                level = max(p.price, s.price) if kind == "high" else min(p.price, s.price)
                if len(between) and ((between.max() > level + tol) if kind == "high" else (between.min() < level - tol)):
                    continue                     # price already took the earlier level: nothing rests there
                group.append(p)
            if len(group) >= cfg.min_touches:
                used.update(g.index for g in group)
                level = max(g.price for g in group) if kind == "high" else min(g.price for g in group)
                pools.append(LiquidityPool(
                    id=f"LQ-{series.timeframe}-{'EQH' if kind == 'high' else 'EQL'}-{s.timestamp:%Y%m%d%H%M}",
                    side="buy_side" if kind == "high" else "sell_side",
                    kind="equal_highs" if kind == "high" else "equal_lows", timeframe=series.timeframe,
                    level=float(level), touches=len(group), member_times=sorted(g.timestamp for g in group),
                    created_index=s.confirmed_index, created_at=s.confirmed_at))
    return pools


def _swing_pools(swings: list[Swing], tf: str) -> list[LiquidityPool]:
    return [LiquidityPool(id=f"LQ-{tf}-S{s.kind[0].upper()}-{s.timestamp:%Y%m%d%H%M}",
                          side="buy_side" if s.kind == "high" else "sell_side",
                          kind="swing_high" if s.kind == "high" else "swing_low", timeframe=tf, level=float(s.price),
                          touches=1, member_times=[s.timestamp], created_index=s.confirmed_index,
                          created_at=s.confirmed_at) for s in swings]


def _previous_day_pools(series: CandleSeries) -> list[LiquidityPool]:
    """Previous UTC day high/low. A day's pool is known when its last candle closes at midnight, or - for a day
    that closed early (e.g. Friday) - when the next day's first candle appears. Causal either way."""
    if series.period.total_seconds() >= 86400 or len(series) == 0:
        return []
    days: dict = defaultdict(list)
    for i, t in enumerate(series.times):
        days[t.date()].append(i)
    ordered = sorted(days)
    pools: list[LiquidityPool] = []
    for k, d in enumerate(ordered):
        idx = days[d]
        last = idx[-1]
        midnight = series.times[idx[0]].normalize() + timedelta(days=1)
        if series.close_time(last) >= midnight.to_pydatetime():
            created = last
        elif k + 1 < len(ordered):
            created = days[ordered[k + 1]][0]
        else:
            continue                                   # day still in progress
        hi, lo = series.high[idx].max(), series.low[idx].min()
        for side, kind, level in (("buy_side", "previous_day_high", hi), ("sell_side", "previous_day_low", lo)):
            pools.append(LiquidityPool(id=f"LQ-{series.timeframe}-{'PDH' if kind.endswith('high') else 'PDL'}-{d:%Y%m%d}",
                                       side=side, kind=kind, timeframe=series.timeframe, level=float(level),
                                       touches=1, member_times=[series.time(idx[0])], created_index=created,
                                       created_at=series.close_time(created)))
    return pools


def detect_liquidity(series: CandleSeries, atr: np.ndarray, internal_swings: list[Swing],
                     external_swings: list[Swing], cfg: LiquidityConfig | None = None
                     ) -> tuple[list[LiquidityPool], list[LiquidityEvent]]:
    cfg = cfg or LiquidityConfig()
    pools = _equal_pools(series, atr, internal_swings, cfg)
    if cfg.include_swing_pools:
        pools += _swing_pools(external_swings, series.timeframe)
    if cfg.include_previous_day:
        pools += _previous_day_pools(series)
    events: list[LiquidityEvent] = []
    for p in pools:
        above = p.side == "buy_side"
        for u in range(p.created_index + 1, len(series)):
            ext = series.high[u] if above else series.low[u]
            if (ext <= p.level) if above else (ext >= p.level):
                continue
            buf = cfg.break_buffer_atr * atr[u]
            cl = series.close[u]
            broke = (cl > p.level + buf) if above else (cl < p.level - buf)
            kind = "break" if broke else "sweep"
            if broke:
                p.state, p.broken_at = "broken", series.time(u)
                impl = "bullish" if above else "bearish"
                text = f"Close beyond the {p.kind.replace('_', ' ')} at {p.level:.6g}: price accepted beyond the pool."
            else:
                p.state, p.swept_at = "swept", series.time(u)
                impl = "bearish" if above else "bullish"
                text = (f"Wick through the {p.kind.replace('_', ' ')} at {p.level:.6g} with a close back inside: "
                        f"{'buy' if above else 'sell'}-side liquidity swept.")
            events.append(LiquidityEvent(kind, p.side, p.id, p.kind, series.timeframe, p.level, u, series.time(u),
                                         series.close_time(u), float(ext), float(cl), impl, text))
            break
    events.sort(key=lambda e: e.index)
    return pools, events
