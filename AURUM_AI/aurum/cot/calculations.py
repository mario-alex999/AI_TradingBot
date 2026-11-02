"""Positioning metrics - pure, deterministic functions (no I/O). See docs/COT_METHODOLOGY.md.

Look-ahead rule: every historical statistic for report *t* uses only reports with ``report_date <= t``.
Because the CFTC releases reports in chronological order, all of those were public by t's release time.

Definitions
-----------
net            = long - short
weekly change  = net(t) - net(previous stored report of the same contract and category)
net % OI       = net / open interest * 100           (None when OI is missing or <= 0)
window(L)      = reports of the same series with report_date in (t - L weeks, t]  (current included)
percentile     = (count(x < v) + 0.5 * count(x == v)) / n * 100   ("mid-rank" percentile, 0..100)
COT index      = (v - min) / (max - min) * 100         (None when max == min)
z-score        = (v - mean) / std   (population std, ddof=0; None when std == 0)
A statistic is None ("insufficient history") when the window holds fewer than
max(min_history_weeks, ceil(coverage * L)) observations (coverage = 0.8).
"""
from __future__ import annotations

import bisect
import math
from datetime import date, timedelta
from typing import Sequence

import numpy as np

COVERAGE = 0.8


def net_position(long: int | None, short: int | None) -> int | None:
    if long is None or short is None:
        return None
    return int(long) - int(short)


def weekly_change(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None:
        return None
    return current - previous


def pct_of_open_interest(net: float | None, open_interest: float | None) -> float | None:
    if net is None or open_interest is None or open_interest <= 0:
        return None
    return float(net) / float(open_interest) * 100.0


def percentile_rank(window: Sequence[float], value: float) -> float | None:
    vals = [v for v in window if v is not None and not _nan(v)]
    if not vals or value is None or _nan(value):
        return None
    below = sum(1 for v in vals if v < value)
    equal = sum(1 for v in vals if v == value)
    return (below + 0.5 * equal) / len(vals) * 100.0


def cot_index(window: Sequence[float], value: float) -> float | None:
    vals = [v for v in window if v is not None and not _nan(v)]
    if not vals or value is None or _nan(value):
        return None
    lo, hi = min(vals), max(vals)
    if hi == lo:
        return None
    return (value - lo) / (hi - lo) * 100.0


def z_score(window: Sequence[float], value: float) -> float | None:
    vals = np.array([v for v in window if v is not None and not _nan(v)], dtype="float64")
    if vals.size == 0 or value is None or _nan(value):
        return None
    sd = vals.std(ddof=0)
    if sd == 0:
        return None
    return float((value - vals.mean()) / sd)


def required_observations(lookback_weeks: int, min_history_weeks: int) -> int:
    return max(min_history_weeks, math.ceil(COVERAGE * lookback_weeks))


def trailing_window(dates: Sequence[date], values: Sequence[float | None], i: int, lookback_weeks: int) -> list[float]:
    """Values of reports in (dates[i] - lookback, dates[i]]; ``dates`` sorted ascending. Never looks forward."""
    j = bisect.bisect_right(dates, dates[i] - timedelta(weeks=lookback_weeks), 0, i + 1)
    return [float(v) for v in values[j:i + 1] if v is not None and v == v]   # v == v drops NaN


def windowed_stat(dates: Sequence[date], values: Sequence[float | None], i: int, lookback_weeks: int,
                  min_history_weeks: int, fn) -> float | None:
    v = values[i]
    if v is None or _nan(v):
        return None
    w = trailing_window(dates, values, i, lookback_weeks)
    if len(w) < required_observations(lookback_weeks, min_history_weeks):
        return None
    return fn(w, float(v))


def change_zscore(changes: Sequence[float | None], i: int, min_history: int) -> float | None:
    """z of change i against all *earlier* changes (strictly past)."""
    past = [c for c in changes[:i] if c is not None and not _nan(c)]
    c = changes[i]
    if c is None or _nan(c) or len(past) < min_history:
        return None
    arr = np.array(past, dtype="float64")
    sd = arr.std(ddof=0)
    return None if sd == 0 else float((c - arr.mean()) / sd)


def _nan(v) -> bool:
    try:
        return math.isnan(v)
    except TypeError:
        return False
