"""Smart Money Concepts / ICT toolkit.

All detections are causal (no look-ahead): a swing at bar i is only *known* at bar i+length.
Provides: swings, market structure (BOS / CHoCH), order blocks, fair value gaps,
liquidity sweeps (stop hunts), premium/discount, and Optimal Trade Entry (OTE) zone.
"""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import pandas as pd
from .data import atr


# ---------------------------------------------------------------- swings
def swings(df: pd.DataFrame, length: int = 5) -> pd.DataFrame:
    h, l = df["high"].values, df["low"].values
    n = len(df)
    sh = np.full(n, np.nan); sl = np.full(n, np.nan)          # value placed on CONFIRMATION bar
    sh_at = np.full(n, -1); sl_at = np.full(n, -1)
    for i in range(length, n - length):
        win_h = h[i - length:i + length + 1]; win_l = l[i - length:i + length + 1]
        if h[i] == win_h.max():
            sh[i + length] = h[i]; sh_at[i + length] = i
        if l[i] == win_l.min():
            sl[i + length] = l[i]; sl_at[i + length] = i
    out = df.copy()
    out["swing_high"], out["swing_low"] = sh, sl
    out["swing_high_idx"], out["swing_low_idx"] = sh_at, sl_at
    out["last_sh"] = out["swing_high"].ffill()
    out["last_sl"] = out["swing_low"].ffill()
    return out


# ---------------------------------------------------------------- structure
def market_structure(df: pd.DataFrame) -> pd.DataFrame:
    """trend: +1 bullish / -1 bearish. bos: break with trend. choch: break against trend (reversal)."""
    out = df.copy()
    trend = 0
    tr, bos, choch = [], [], []
    broken_h = broken_l = None
    for c, sh, sl in zip(out["close"], out["last_sh"], out["last_sl"]):
        b = ch = 0
        if not np.isnan(sh) and c > sh and sh != broken_h:
            (ch, b) = (1, 0) if trend == -1 else (0, 1)
            trend, broken_h = 1, sh
        elif not np.isnan(sl) and c < sl and sl != broken_l:
            (ch, b) = (-1, 0) if trend == 1 else (0, -1)
            trend, broken_l = -1, sl
        tr.append(trend); bos.append(b); choch.append(ch)
    out["trend"], out["bos"], out["choch"] = tr, bos, choch
    return out


# ---------------------------------------------------------------- FVG
def fair_value_gaps(df: pd.DataFrame, min_atr: float = 0.25) -> pd.DataFrame:
    """3-candle imbalance. Bullish FVG: low[i] > high[i-2]. Known at close of bar i."""
    out = df.copy()
    a = atr(df)
    bull = (df["low"] > df["high"].shift(2)) & ((df["low"] - df["high"].shift(2)) > min_atr * a)
    bear = (df["high"] < df["low"].shift(2)) & ((df["low"].shift(2) - df["high"]) > min_atr * a)
    out["fvg_bull_top"] = np.where(bull, df["low"], np.nan)
    out["fvg_bull_bot"] = np.where(bull, df["high"].shift(2), np.nan)
    out["fvg_bear_top"] = np.where(bear, df["low"].shift(2), np.nan)
    out["fvg_bear_bot"] = np.where(bear, df["high"], np.nan)
    return out


# ---------------------------------------------------------------- order blocks
@dataclass
class Zone:
    kind: str          # "bull_ob" | "bear_ob" | "bull_fvg" | "bear_fvg"
    top: float
    bottom: float
    created: int       # bar index when it became known
    mitigated: bool = False


def order_blocks(df: pd.DataFrame, lookback: int = 60) -> list[Zone]:
    """Bullish OB = last down-candle before the impulse that produced a bullish BOS/CHoCH.
    Bearish OB = last up-candle before the impulse that produced a bearish BOS/CHoCH."""
    zones: list[Zone] = []
    o, c, h, l = (df[k].values for k in ("open", "close", "high", "low"))
    brk = (df["bos"] + df["choch"]).values
    for i in np.nonzero(brk)[0]:
        direction = brk[i]
        for j in range(i - 1, max(i - lookback, 0), -1):
            if direction > 0 and c[j] < o[j]:
                zones.append(Zone("bull_ob", h[j], l[j], i)); break
            if direction < 0 and c[j] > o[j]:
                zones.append(Zone("bear_ob", h[j], l[j], i)); break
    return zones


def fvg_zones(df: pd.DataFrame) -> list[Zone]:
    z = []
    for i, (bt, bb, st, sb) in enumerate(df[["fvg_bull_top", "fvg_bull_bot", "fvg_bear_top", "fvg_bear_bot"]].values):
        if not np.isnan(bt): z.append(Zone("bull_fvg", bt, bb, i))
        if not np.isnan(st): z.append(Zone("bear_fvg", st, sb, i))
    return z


def active_zones(zones: list[Zone], df: pd.DataFrame, upto: int, max_age: int = 300) -> list[Zone]:
    """Zones created before `upto` that have not been fully traded through (unmitigated)."""
    h, l = df["high"].values, df["low"].values
    live = []
    for z in zones:
        if z.created >= upto or upto - z.created > max_age:
            continue
        seg_l, seg_h = l[z.created + 1:upto], h[z.created + 1:upto]
        if z.kind.startswith("bull") and len(seg_l) and seg_l.min() < z.bottom:
            continue  # closed through -> invalid
        if z.kind.startswith("bear") and len(seg_h) and seg_h.max() > z.top:
            continue
        live.append(z)
    return live


# ---------------------------------------------------------------- liquidity
def liquidity_sweeps(df: pd.DataFrame) -> pd.DataFrame:
    """Sweep = wick beyond prior swing/session extreme but CLOSE back inside (stop hunt)."""
    out = df.copy()
    ref_h = out["last_sh"].shift()
    ref_l = out["last_sl"].shift()
    if "asian_high" in out:
        ref_h = pd.concat([ref_h, out["asian_high"]], axis=1).min(axis=1)
        ref_l = pd.concat([ref_l, out["asian_low"]], axis=1).max(axis=1)
    out["sweep_high"] = (out["high"] > ref_h) & (out["close"] < ref_h)   # bearish signal
    out["sweep_low"] = (out["low"] < ref_l) & (out["close"] > ref_l)     # bullish signal
    return out


# ---------------------------------------------------------------- dealing range / OTE
def dealing_range(df: pd.DataFrame, upto: int, length: int = 50):
    seg = df.iloc[max(0, upto - length):upto]
    return seg["high"].max(), seg["low"].min()


def premium_discount(price: float, hi: float, lo: float) -> float:
    """0 = range low, 1 = range high. <0.5 discount (buy), >0.5 premium (sell)."""
    return (price - lo) / (hi - lo) if hi > lo else 0.5


def ote_zone(hi: float, lo: float, direction: int, a: float = 0.62, b: float = 0.79):
    """ICT OTE: 62–79% retracement of the impulse leg (70.5% = sweet spot)."""
    rng = hi - lo
    if direction > 0:   # bullish leg lo->hi, buy the retrace
        return hi - b * rng, hi - a * rng
    return lo + a * rng, lo + b * rng


def enrich(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    d = swings(df, cfg.get("swing_length", 5))
    d = market_structure(d)
    d = fair_value_gaps(d, cfg.get("fvg_min_atr", 0.25))
    d = liquidity_sweeps(d)
    d["atr"] = atr(d)
    return d
