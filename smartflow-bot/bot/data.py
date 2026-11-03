"""Market data loading: yfinance, CSV, or synthetic (for offline tests)."""
from __future__ import annotations
import numpy as np
import pandas as pd


def load_ohlc(symbol: str, interval: str = "1h", period: str = "730d") -> pd.DataFrame:
    """Return OHLC DataFrame with tz-aware DatetimeIndex and columns open/high/low/close/volume."""
    if symbol.endswith(".csv"):
        df = pd.read_csv(symbol, parse_dates=[0], index_col=0)
    else:
        import yfinance as yf
        df = yf.download(symbol, interval=interval, period=period, progress=False, auto_adjust=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
    df.columns = [c.lower() for c in df.columns]
    df = df[["open", "high", "low", "close"] + (["volume"] if "volume" in df else [])].dropna()
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    return df


def synthetic_ohlc(n: int = 3000, freq: str = "1h", seed: int = 7) -> pd.DataFrame:
    """Random-walk with regime drift; used for tests / offline demo only."""
    rng = np.random.default_rng(seed)
    drift = np.repeat(rng.normal(0, 0.0004, n // 200 + 1), 200)[:n]
    ret = drift + rng.normal(0, 0.0015, n)
    close = 1.10 * np.exp(np.cumsum(ret))
    open_ = np.r_[close[0], close[:-1]]
    spread = np.abs(rng.normal(0, 0.0012, n)) * close
    high = np.maximum(open_, close) + spread
    low = np.minimum(open_, close) - spread
    idx = pd.date_range("2024-01-01", periods=n, freq=freq, tz="UTC")
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": 0}, index=idx)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    pc = df["close"].shift()
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(n, min_periods=1).mean()
