"""Shared builders for Module 04 tests. All candles are synthetic test fixtures (never presented as market data)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pandas as pd

from aurum.config import Settings
from aurum.market_intelligence.candles import FrameCandleSource
from aurum.market_intelligence.config import load_mi_config
from aurum.market_intelligence.http import HttpClient

UTC = timezone.utc
FX_CLOSED = ((4, 21), (6, 21))      # Fri 21:00 UTC .. Sun 21:00 UTC, matches the fixture trading hours


def cfg(**overrides: Any):
    return load_mi_config(overrides=overrides or None)


def _in_weekend(ts: pd.DatetimeIndex) -> np.ndarray:
    wd, h = ts.day_of_week, ts.hour
    return ((wd == 4) & (h >= 21)) | (wd == 5) | ((wd == 6) & (h < 21))


def walk(start: datetime, end: datetime, minutes: int, seed: int = 7, price: float = 1.10, step: float = 0.0004,
         symbol: str = "EURUSD", tf: str = "H1", skip_weekends: bool = True) -> pd.DataFrame:
    """Deterministic random-walk OHLC frame in the Module 01 column layout."""
    ts = pd.date_range(start, end, freq=f"{minutes}min", tz="UTC", inclusive="left")
    if skip_weekends:
        ts = ts[~_in_weekend(ts)]
    rng = np.random.default_rng(seed)
    close = price + np.cumsum(rng.normal(0, step, len(ts)))
    opn = np.concatenate([[price], close[:-1]])
    spread = np.abs(rng.normal(0, step, len(ts))) + step / 4
    return pd.DataFrame({"symbol": symbol, "timeframe": tf, "ts_utc": ts, "open": opn,
                         "high": np.maximum(opn, close) + spread, "low": np.minimum(opn, close) - spread,
                         "close": close, "tick_volume": rng.integers(50, 500, len(ts)), "is_complete": True})


def resample(df: pd.DataFrame, rule: str, tf: str) -> pd.DataFrame:
    d = df.set_index("ts_utc")
    out = d.resample(rule, label="left", closed="left").agg({"open": "first", "high": "max", "low": "min",
                                                              "close": "last", "tick_volume": "sum"}).dropna()
    out = out.reset_index()
    out["symbol"], out["timeframe"], out["is_complete"] = df["symbol"].iloc[0], tf, True
    return out


def source(symbol: str = "EURUSD", start: datetime = datetime(2026, 1, 5, tzinfo=UTC),
           end: datetime = datetime(2026, 3, 14, tzinfo=UTC), seed: int = 7) -> FrameCandleSource:
    m5 = walk(start, end, 5, seed=seed, step=0.00015, symbol=symbol, tf="M5")
    return FrameCandleSource({(symbol, "M5"): m5, (symbol, "M15"): resample(m5, "15min", "M15"),
                              (symbol, "H1"): resample(m5, "1h", "H1"), (symbol, "D1"): resample(m5, "1D", "D1")})


def settings(tmp_path: Path, **kw: Any) -> Settings:
    base = dict(_env_file=None, mt5_server_timezone="Etc/UTC", data_dir=tmp_path / "data", log_dir=tmp_path / "logs",
                database_url=f"sqlite:///{(tmp_path / 'mi.db').as_posix()}", economic_calendar_provider=None,
                economic_calendar_api_key=None, fred_api_key=None, official_data_enabled=True, news_enabled=True)
    base.update(kw)
    return Settings(**base)


def mock_http(handler, secrets: list[str] | None = None) -> HttpClient:
    """HttpClient whose transport is an in-process handler (no network); sleeps are recorded, not slept."""
    slept: list[float] = []
    c = HttpClient(client=httpx.Client(transport=httpx.MockTransport(handler)), sleep=slept.append,
                   secrets=secrets, max_retries=2)
    c.slept = slept  # type: ignore[attr-defined]
    return c
