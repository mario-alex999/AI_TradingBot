"""Read-only access to Module 01 candles for Module 04, always cut at an ``as_of`` instant.

Only candles that are complete, not ``invalid`` and CLOSED by ``as_of`` (open time + timeframe <= as_of) are
returned, so nothing calculated from them can see the future. A forming candle is never returned.
"""
from __future__ import annotations

import time as _time
from datetime import datetime, timedelta
from typing import Protocol

import pandas as pd

from aurum.config import TIMEFRAME_SECONDS

COLUMNS = ["ts", "close_t", "open", "high", "low", "close", "tick_volume"]


def _empty() -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(dtype="datetime64[ns, UTC]" if c in ("ts", "close_t") else "float64")
                         for c in COLUMNS})


def normalise(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Module 01 frame (ts_utc, open..close, tick_volume, is_complete, quality_status) -> ts/close_t/OHLC frame."""
    if df is None or df.empty:
        return _empty()
    d = df.copy()
    ts_col = "ts_utc" if "ts_utc" in d.columns else "ts"
    d["ts"] = pd.to_datetime(d[ts_col], utc=True)
    if "is_complete" in d.columns:
        d = d.loc[d["is_complete"].fillna(True).astype(bool)]
    if "quality_status" in d.columns:
        d = d.loc[d["quality_status"] != "invalid"]
    for c in ("open", "high", "low", "close"):
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d = d.dropna(subset=["open", "high", "low", "close"])
    d["tick_volume"] = pd.to_numeric(d["tick_volume"], errors="coerce") if "tick_volume" in d.columns else float("nan")
    d["close_t"] = d["ts"] + pd.Timedelta(seconds=TIMEFRAME_SECONDS[timeframe])
    d = d.drop_duplicates("ts", keep="last").sort_values("ts")
    return d[COLUMNS].reset_index(drop=True)


class CandleSource(Protocol):
    def frame(self, symbol: str, timeframe: str, as_of: datetime) -> pd.DataFrame: ...


class FrameCandleSource:
    """In-memory source (tests, exports): ``{(symbol, timeframe): DataFrame}``."""

    def __init__(self, frames: dict[tuple[str, str], pd.DataFrame]):
        self._frames = {k: normalise(v, k[1]) for k, v in frames.items()}

    def frame(self, symbol: str, timeframe: str, as_of: datetime) -> pd.DataFrame:
        df = self._frames.get((symbol.upper(), timeframe.upper()))
        if df is None or df.empty:
            return _empty()
        return df.loc[df["close_t"] <= pd.Timestamp(as_of)].reset_index(drop=True)


class RepositoryCandleSource:
    """Module 01 candles with a cache: one analysis reads each series once, and later reads fetch only the newest
    rows (incremental, re-reading the last ``overlap`` candles) with a full reload every ``full_reload_seconds`` so
    re-collected history is picked up. Reads use a read-only Core SELECT of the needed columns when the repository
    exposes its engine (fast path), else Module 01's ``Repository.load_candles``."""

    def __init__(self, repo, ttl_seconds: float = 30.0, full_reload_seconds: float = 600.0, overlap: int = 5):
        self.repo = repo
        self.ttl, self.full_reload, self.overlap = ttl_seconds, full_reload_seconds, overlap
        self._cache: dict[tuple[str, str], tuple[float, float, pd.DataFrame]] = {}

    def _load(self, symbol: str, timeframe: str, since=None) -> pd.DataFrame:
        engine = getattr(self.repo, "engine", None)
        if engine is None:
            return normalise(self.repo.load_candles(symbol, timeframe), timeframe)
        from sqlalchemy import select
        from aurum.database.models import Instrument, MarketCandle
        q = (select(MarketCandle.ts_utc, MarketCandle.open, MarketCandle.high, MarketCandle.low, MarketCandle.close,
                    MarketCandle.tick_volume)
             .join(Instrument, Instrument.id == MarketCandle.instrument_id)
             .where(Instrument.symbol == symbol, MarketCandle.timeframe == timeframe,
                    MarketCandle.quality_status != "invalid", MarketCandle.is_complete.is_(True))
             .order_by(MarketCandle.ts_utc))
        if since is not None:
            q = q.where(MarketCandle.ts_utc >= since.to_pydatetime())
        with engine.connect() as c:
            rows = c.execute(q).all()
        df = pd.DataFrame(rows, columns=["ts_utc", "open", "high", "low", "close", "tick_volume"])
        return normalise(df, timeframe)

    def series(self, symbol: str, timeframe: str) -> pd.DataFrame:
        key = (symbol.upper(), timeframe.upper())
        now = _time.monotonic()
        hit = self._cache.get(key)
        if hit is None or now - hit[1] > self.full_reload:
            df = self._load(*key)
            self._cache[key] = (now, now, df)
            return df
        loaded, full_at, df = hit
        if now - loaded <= self.ttl:
            return df
        since = df["ts"].iloc[-self.overlap] if len(df) >= self.overlap else None
        new = self._load(key[0], key[1], since) if since is not None else self._load(*key)
        if since is not None:
            df = pd.concat([df.loc[df["ts"] < since], new]).drop_duplicates("ts", keep="last").reset_index(drop=True)
        else:
            df = new
        self._cache[key] = (now, full_at, df)
        return df

    def frame(self, symbol: str, timeframe: str, as_of: datetime) -> pd.DataFrame:
        df = self.series(symbol, timeframe)
        if df.empty:
            return df
        k = int(df["close_t"].searchsorted(pd.Timestamp(as_of), side="right"))
        return df.iloc[:k]

    def clear(self) -> None:
        self._cache.clear()


def window(df: pd.DataFrame, start: datetime, end: datetime) -> pd.DataFrame:
    """Candles whose open time is in [start, end) (``df`` is sorted by ``ts``)."""
    if df.empty:
        return df
    ts = df["ts"]
    a = int(ts.searchsorted(pd.Timestamp(start), side="left"))
    b = int(ts.searchsorted(pd.Timestamp(end), side="left"))
    return df.iloc[a:b]


def covers(df: pd.DataFrame, start: datetime, timeframe: str) -> bool:
    """True when the stored history of ``df`` reaches back to ``start`` (first candle opens at/before it)."""
    if df.empty:
        return False
    return bool(df["ts"].iloc[0] < pd.Timestamp(start) + pd.Timedelta(seconds=TIMEFRAME_SECONDS[timeframe]))


def period_seconds(timeframe: str) -> timedelta:
    return timedelta(seconds=TIMEFRAME_SECONDS[timeframe])
