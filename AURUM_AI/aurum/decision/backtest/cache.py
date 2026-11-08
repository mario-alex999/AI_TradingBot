"""Speed for replay. Nearly all of a step's time goes on reloading and revalidating the same candles, not on detection.

The stored frames are kept in memory, and a prepared series is reused only while the set of candles closed by ``as_of`` is
exactly the same, so caching cannot change what a step is allowed to see. Module 03 itself is unchanged.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd

from aurum.analysis.data import prepare_candles
from aurum.analysis.multi_timeframe.timeframe_analysis import align_to_as_of
from aurum.analysis.services.analysis_service import TechnicalAnalysisService
from aurum.analysis.services.analysis_cache import AnalysisCache


class CachedCandles:
    """The candle store, with each frame read from the database once."""

    def __init__(self, candles: Any):
        self.inner = candles
        self.frames: dict[tuple[Any, ...], pd.DataFrame] = {}

    def load_candles(self, symbol: str, timeframe: str, **options: Any) -> pd.DataFrame:
        key = (symbol, timeframe, tuple(sorted(options.items())))
        if key not in self.frames:
            self.frames[key] = self.inner.load_candles(symbol, timeframe, **options)
        return self.frames[key]

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


class ReplayTechnical(TechnicalAnalysisService):
    def __init__(self, source: TechnicalAnalysisService):
        super().__init__(source.settings, source.engine, source.cfg, CachedCandles(source.candles), source.cot, AnalysisCache(),
                         source.instruments)
        self.prepared: dict[tuple[Any, ...], Any] = {}

    def load_series(self, symbol: str, timeframe: str, as_of: datetime | None = None, include_provisional: bool = False) -> Any:
        inst = self.instrument(symbol)
        tf = self.check_timeframe(timeframe)
        t = as_of.astimezone(timezone.utc) if as_of else datetime.now(timezone.utc)
        df = align_to_as_of(self.candles.load_candles(inst.symbol, tf, include_invalid=True, include_incomplete=True), tf, t,
                            include_provisional)
        if len(df) > self.cfg.max_candles:
            df = df.iloc[-self.cfg.max_candles:]
        key = (inst.symbol, tf, include_provisional, len(df), df["ts_utc"].iloc[-1] if len(df) else None)
        if key not in self.prepared:
            self.prepared = {k: v for k, v in self.prepared.items() if k[:3] != key[:3]}      # only the latest per timeframe
            self.prepared[key] = prepare_candles(df.copy(), inst.symbol, tf, hours=inst.trading_hours, include_provisional=include_provisional)
        return self.prepared[key]
