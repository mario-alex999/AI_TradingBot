"""Historical OHLCV candle collection via ``copy_rates_from_pos``.

Look-ahead protection: the currently forming candle (its close time is after "now") is marked
``is_complete=False`` and, when EXCLUDE_FORMING_CANDLE=true, removed from the batch. The removal is
reported in :class:`CandleBatch` (never silent).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from aurum.config import TIMEFRAME_SECONDS, InstrumentConfig
from aurum.gateways.mt5_gateway import BrokerClock, MT5Gateway

log = logging.getLogger(__name__)

CANDLE_COLUMNS = ["symbol", "broker_symbol", "timeframe", "ts_utc", "open", "high", "low", "close",
                  "tick_volume", "real_volume", "spread", "mt5_time", "is_complete"]


@dataclass
class CandleBatch:
    symbol: str
    timeframe: str
    raw: pd.DataFrame            # exactly what MT5 returned (plus received position)
    candles: pd.DataFrame        # normalised, UTC, forming candle handled
    requested: int
    excluded_forming: int


def rates_to_frame(rates: np.ndarray | None) -> pd.DataFrame:
    """Convert MT5's structured array to a DataFrame, keeping MT5's original order."""
    if rates is None or len(rates) == 0:
        return pd.DataFrame(columns=["time", "open", "high", "low", "close", "tick_volume", "spread", "real_volume"])
    df = pd.DataFrame(rates)
    df.insert(0, "received_pos", np.arange(len(df)))
    return df


def normalize_rates(raw: pd.DataFrame, inst: InstrumentConfig, timeframe: str, clock: BrokerClock,
                    now_utc: datetime) -> pd.DataFrame:
    """Map raw MT5 rates to AURUM's candle schema. Values are not altered; order is preserved."""
    if raw.empty:
        return pd.DataFrame(columns=CANDLE_COLUMNS)
    period = pd.Timedelta(seconds=TIMEFRAME_SECONDS[timeframe])
    out = pd.DataFrame({
        "symbol": inst.symbol,
        "broker_symbol": inst.broker_symbol,
        "timeframe": timeframe,
        "ts_utc": clock.series_to_utc(raw["time"].to_numpy()).to_numpy(),
        "open": raw["open"].astype("float64").to_numpy(),
        "high": raw["high"].astype("float64").to_numpy(),
        "low": raw["low"].astype("float64").to_numpy(),
        "close": raw["close"].astype("float64").to_numpy(),
        "tick_volume": raw.get("tick_volume", pd.Series(np.nan, index=raw.index)).to_numpy(),
        "real_volume": raw.get("real_volume", pd.Series(np.nan, index=raw.index)).to_numpy(),
        "spread": raw.get("spread", pd.Series(np.nan, index=raw.index)).to_numpy(),
        "mt5_time": raw["time"].to_numpy(),
    })
    out["ts_utc"] = pd.to_datetime(out["ts_utc"], utc=True)
    now = pd.Timestamp(now_utc).tz_convert("UTC")
    out["is_complete"] = (out["ts_utc"] + period) <= now
    return out[CANDLE_COLUMNS]


def collect_candles(gateway: MT5Gateway, inst: InstrumentConfig, timeframe: str, count: int,
                    exclude_forming: bool = True, now_utc: datetime | None = None) -> CandleBatch:
    """Fetch up to ``count`` candles (complete ones when ``exclude_forming``)."""
    now_utc = now_utc or datetime.now(timezone.utc)
    request = count + 1 if exclude_forming else count  # +1 so the forming bar can be dropped
    raw = rates_to_frame(gateway.copy_rates(inst.broker_symbol, timeframe, 0, request))
    if gateway.clock is None:
        raise RuntimeError("Gateway has no broker clock - connect() first")
    candles = normalize_rates(raw, inst, timeframe, gateway.clock, now_utc)
    excluded = 0
    if exclude_forming and not candles.empty:
        forming = ~candles["is_complete"].astype(bool)
        excluded = int(forming.sum())
        if excluded:
            log.info("Excluded forming candle(s)", extra={"symbol": inst.symbol, "timeframe": timeframe,
                                                          "count": excluded,
                                                          "ts_utc": [str(t) for t in candles.loc[forming, "ts_utc"]]})
        candles = candles.loc[~forming]
        if len(candles) > count:  # keep the newest `count` complete candles, original order retained
            newest = candles["ts_utc"].nlargest(count).index
            candles = candles.loc[candles.index.isin(newest)]
    return CandleBatch(inst.symbol, timeframe, raw, candles.reset_index(drop=True), request, excluded)


def incremental_count(last_ts: datetime | None, timeframe: str, max_count: int, now_utc: datetime,
                      overlap: int = 5) -> int:
    """How many bars to request so the newest stored candle is re-fetched (overlap) without
    re-downloading full history. Over-estimates across weekends, which is harmless (upsert)."""
    if last_ts is None:
        return max_count
    elapsed = (now_utc - last_ts) / timedelta(seconds=TIMEFRAME_SECONDS[timeframe])
    return int(max(1, min(max_count, np.ceil(elapsed) + overlap)))
