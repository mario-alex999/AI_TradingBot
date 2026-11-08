"""What happened to a candidate after it was proposed. Outcomes are labels for evaluation only.

The entry is a limit at the zone mid-point, which is what the risk-reward is quoted from. So ``touched`` means price reached
the zone, while ``filled`` needs it to reach the mid-point. Candles do not show the order of events inside them, so
anything undecidable within one candle is ``ambiguous`` and counts as a loss.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pandas as pd

AMBIGUOUS_R = -1.0


@dataclass
class Outcome:
    touched: bool = False
    filled: bool = False
    result: str = "open"              # tp | sl | expired | ambiguous | open
    r_multiple: float | None = None
    mfe_r: float | None = None
    mae_r: float | None = None
    resolved_at: datetime | None = None


def label(direction: str, entry_low: float, entry_high: float, stop: float, target: float, expires_at: datetime,
          candles: pd.DataFrame, max_hold_candles: int) -> Outcome:
    """Walk candles closed after the candidate's ``as_of``, oldest first. ``candles`` needs ts_utc, high, low and close."""
    long = direction == "long"
    mid = (entry_low + entry_high) / 2
    risk = abs(mid - stop)
    out = Outcome()
    if risk <= 0:
        out.result = "expired"
        return out
    best, worst, held = 0.0, 0.0, 0

    def finish(result: str, r: float, at: Any) -> Outcome:
        out.result, out.r_multiple, out.resolved_at = result, round(r, 3), at
        out.mfe_r, out.mae_r = round(best, 3), round(worst, 3)
        return out

    reward = abs(target - mid) / risk
    for row in candles.itertuples(index=False):
        high, low, at = row.high, row.low, row.ts_utc
        if pd.isna(high) or pd.isna(low):
            continue
        hit_stop = low <= stop if long else high >= stop
        hit_target = high >= target if long else low <= target
        if not out.filled:
            if at >= expires_at:
                out.result, out.resolved_at = "expired", expires_at
                return out
            out.touched = out.touched or (low <= entry_high and high >= entry_low)
            if not (low <= mid <= high):
                continue
            out.filled = True
            best, worst = max(best, _favourable(high, low, mid, risk, long)), min(worst, _adverse(high, low, mid, risk, long))
            if hit_target:     # the target may have traded before the fill, so this candle cannot prove a win
                return finish("ambiguous", AMBIGUOUS_R, at)
            if hit_stop:
                return finish("sl", -1.0, at)
            continue
        held += 1
        best, worst = max(best, _favourable(high, low, mid, risk, long)), min(worst, _adverse(high, low, mid, risk, long))
        if hit_stop and hit_target:
            return finish("ambiguous", AMBIGUOUS_R, at)
        if hit_stop:
            return finish("sl", -1.0, at)
        if hit_target:
            return finish("tp", reward, at)
        if held >= max_hold_candles:
            r = (row.close - mid) / risk * (1 if long else -1)
            return finish("expired", r, at)
    return out


def _favourable(high: float, low: float, mid: float, risk: float, long: bool) -> float:
    return (high - mid) / risk if long else (mid - low) / risk


def _adverse(high: float, low: float, mid: float, risk: float, long: bool) -> float:
    return (low - mid) / risk if long else (mid - high) / risk
