"""Hand-built candle sequences for Module 03 tests."""
from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd

from aurum.analysis.config import AnalysisConfig, SwingConfig, SwingWindow
from aurum.analysis.data import atr, prepare_candles

START = "2026-03-03 00:00"          # a Tuesday: no weekend inside short H1 sequences


def frame(rows, freq: str = "h", start: str = START, last_incomplete: bool = False) -> pd.DataFrame:
    """rows: list of (open, high, low, close)."""
    ts = pd.date_range(start, periods=len(rows), freq=freq, tz="UTC")
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"])
    df.insert(0, "ts_utc", ts)
    df["tick_volume"] = 100
    df["is_complete"] = True
    if last_incomplete and len(df):
        df.loc[len(df) - 1, "is_complete"] = False
    return df


def path(closes, wick: float = 0.1, first_open: float | None = None):
    """Candles whose open is the previous close; wicks extend ``wick`` beyond the body."""
    rows, prev = [], closes[0] if first_open is None else first_open
    for c in closes:
        o = prev
        rows.append((o, max(o, c) + wick, min(o, c) - wick, c))
        prev = c
    return rows


def hl(highs, lows):
    """Candles with explicit highs/lows (open/close at the midpoint)."""
    return [((h + l) / 2, h, l, (h + l) / 2) for h, l in zip(highs, lows)]


def series(rows, tf: str = "H1", include_provisional: bool = False, last_incomplete: bool = False, symbol="TEST"):
    return prepare_candles(frame(rows, last_incomplete=last_incomplete), symbol, tf,
                           include_provisional=include_provisional)


def small_cfg(**over) -> AnalysisConfig:
    cfg = AnalysisConfig(min_candles=5, swings=SwingConfig(internal=SwingWindow(1, 1), external=SwingWindow(2, 2)))
    return replace(cfg, **over) if over else cfg


def random_rows(n: int = 400, seed: int = 7, start_price: float = 1.10):
    rng = np.random.default_rng(seed)
    drift = np.repeat(rng.normal(0, 0.0007, n // 40 + 1), 40)[:n]
    c = start_price * np.exp(np.cumsum(drift + rng.normal(0, 0.0015, n)))
    o = np.r_[start_price, c[:-1]]
    sp = np.abs(rng.normal(0, 0.0009, n)) * c
    return list(zip(o, np.maximum(o, c) + sp, np.minimum(o, c) - sp, c))


def atr_of(s, period: int = 14):
    return atr(s, period)
