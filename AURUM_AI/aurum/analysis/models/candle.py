"""Validated candle series handed to every detector."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd

from aurum.config import TIMEFRAME_SECONDS


@dataclass
class CandleSeries:
    """Chronological, de-duplicated, validated candles for one symbol and timeframe.

    ``df`` columns: ts_utc (tz-aware UTC, candle OPEN time), open, high, low, close, tick_volume,
    real_volume, is_complete, source. Row position == the integer index used by every detector.
    """

    symbol: str
    timeframe: str
    df: pd.DataFrame
    provisional: bool = False                      # last candle is still forming and was kept on request
    quality_issues: list[dict[str, Any]] = field(default_factory=list)
    excluded_rows: list[dict[str, Any]] = field(default_factory=list)
    source: str = "module_01"

    def __post_init__(self) -> None:
        d = self.df
        self.open = d["open"].to_numpy(dtype="float64")
        self.high = d["high"].to_numpy(dtype="float64")
        self.low = d["low"].to_numpy(dtype="float64")
        self.close = d["close"].to_numpy(dtype="float64")
        self.times = list(pd.to_datetime(d["ts_utc"], utc=True))
        self.period = timedelta(seconds=TIMEFRAME_SECONDS[self.timeframe])
        vol = d["real_volume"] if "real_volume" in d and d["real_volume"].fillna(0).gt(0).any() else d.get("tick_volume")
        self.volume = None if vol is None else vol.fillna(0).to_numpy(dtype="float64")
        self.complete = d["is_complete"].astype(bool).to_numpy() if "is_complete" in d else np.ones(len(d), dtype=bool)

    def __len__(self) -> int:
        return len(self.df)

    def time(self, i: int) -> datetime:
        return self.times[i].to_pydatetime()

    def close_time(self, i: int) -> datetime:
        """When candle ``i`` closes - the earliest moment anything it confirms is known."""
        return (self.times[i] + self.period).to_pydatetime()

    def is_provisional(self, i: int) -> bool:
        return bool(not self.complete[i])

    @property
    def volume_usable(self) -> bool:
        """Volume is only used when it actually varies (many CFD feeds report constant or zero volume)."""
        return self.volume is not None and len(self.volume) > 20 and float(np.std(self.volume)) > 0

    @property
    def last_index(self) -> int:
        return len(self) - 1
