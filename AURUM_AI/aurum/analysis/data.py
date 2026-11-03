"""Candle preparation for Module 03.

Candles come from Module 01 (``Repository.load_candles``) or any DataFrame with the same columns.
Validation reuses Module 01's ``ValidationService`` (OHLC rules, non-positive/missing values, duplicates,
ordering, closure-aware gap detection) so both modules apply identical rules. Rows Module 01 marks
``invalid`` never reach a detector; they are listed in ``CandleSeries.excluded_rows``.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator

import numpy as np
import pandas as pd

from aurum.analysis.models.candle import CandleSeries
from aurum.config import TIMEFRAME_SECONDS, TradingHours
from aurum.services.validation_service import ValidationService

log = logging.getLogger(__name__)

PRICE_COLS = ["open", "high", "low", "close"]
_TS_ALIASES = ("ts_utc", "timestamp", "time", "datetime")


_QUIET: ContextVar[bool] = ContextVar("aurum_ta_quiet_validation", default=False)


class _QuietValidation(logging.Filter):
    """Module 01 logs a WARNING per data issue when it collects. Module 03 re-validates on every analysis; those
    repeats are suppressed here (the issues are still returned in ``CandleSeries.quality_issues``)."""

    def filter(self, record: logging.LogRecord) -> bool:
        return not _QUIET.get()


logging.getLogger("aurum.services.validation_service").addFilter(_QuietValidation())


@contextmanager
def _quiet() -> Iterator[None]:
    token = _QUIET.set(True)
    try:
        yield
    finally:
        _QUIET.reset(token)


class CandleDataError(ValueError):
    """Raised when candles cannot be analysed at all (unknown timeframe, missing columns)."""


def _normalise_columns(raw: pd.DataFrame) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    notes: list[dict[str, Any]] = []
    df = raw.copy()
    df.columns = [str(c).lower() for c in df.columns]
    ts_col = next((c for c in _TS_ALIASES if c in df.columns), None)
    if ts_col is None:
        raise CandleDataError(f"Candles need a timestamp column (one of {_TS_ALIASES}).")
    missing = [c for c in PRICE_COLS if c not in df.columns]
    if missing:
        raise CandleDataError(f"Candles are missing column(s): {missing}")
    ts = pd.to_datetime(df[ts_col], errors="coerce")
    if ts.dt.tz is None:
        ts = ts.dt.tz_localize("UTC")
        notes.append({"issue_type": "naive_timestamps_normalised", "severity": "info",
                      "message": "Timestamps had no time zone and were interpreted as UTC."})
    else:
        ts = ts.dt.tz_convert("UTC")
    df["ts_utc"] = ts
    for c in PRICE_COLS:
        df[c] = pd.to_numeric(df[c], errors="coerce")   # non-numeric -> NaN -> flagged invalid below
    for c in ("tick_volume", "real_volume"):
        df[c] = pd.to_numeric(df[c], errors="coerce") if c in df.columns else np.nan
    if "is_complete" not in df.columns:
        df["is_complete"] = True
    df["is_complete"] = df["is_complete"].fillna(True).astype(bool)
    if "source" not in df.columns:
        df["source"] = "unknown"
    return df, notes


def prepare_candles(raw: pd.DataFrame, symbol: str, timeframe: str, *, hours: TradingHours | None = None,
                    include_provisional: bool = False, validator: ValidationService | None = None,
                    source: str = "module_01") -> CandleSeries:
    """Validate, de-duplicate and sort candles; drop invalid rows and (by default) forming candles.

    Only the LAST candle may be provisional, and only when ``include_provisional`` is True; any other
    incomplete candle is excluded because a forming candle in the middle of history is a data error.
    """
    if timeframe not in TIMEFRAME_SECONDS:
        raise CandleDataError(f"Unsupported timeframe '{timeframe}'. Supported: {list(TIMEFRAME_SECONDS)}")
    if raw is None or raw.empty:
        empty = pd.DataFrame(columns=["ts_utc", *PRICE_COLS, "tick_volume", "real_volume", "is_complete", "source"])
        return CandleSeries(symbol, timeframe, empty, quality_issues=[{"issue_type": "no_data", "severity": "error",
                                                                       "message": "No candles available."}])
    df, notes = _normalise_columns(raw)
    df["symbol"], df["timeframe"] = symbol, timeframe
    if "mt5_time" not in df.columns:
        df["mt5_time"] = df["ts_utc"].astype("int64") // 10**9

    with _quiet():
        result = (validator or ValidationService()).validate(df, timeframe, hours or TradingHours(), symbol)
    issues = notes + [{"issue_type": i.issue_type, "severity": i.severity, "ts_utc": i.ts_utc, "details": i.details}
                      for i in result.issues]
    clean = result.candles
    excluded: list[dict[str, Any]] = []
    if clean.empty:
        return CandleSeries(symbol, timeframe, clean, quality_issues=issues, source=source)

    invalid = clean["quality_status"] == "invalid"
    for r in clean.loc[invalid].to_dict("records"):
        excluded.append({"ts_utc": r["ts_utc"], "reason": f"invalid: {r.get('quality_flags')}"})
    clean = clean.loc[~invalid]

    incomplete = ~clean["is_complete"].astype(bool)
    keep_last_forming = include_provisional and len(clean) > 0 and bool(incomplete.iloc[-1])
    drop = incomplete.copy()
    if keep_last_forming:
        drop.iloc[-1] = False
    for r in clean.loc[drop].to_dict("records"):
        excluded.append({"ts_utc": r["ts_utc"], "reason": "forming candle (closed candles only)"})
    clean = clean.loc[~drop].reset_index(drop=True)

    cols = ["ts_utc", *PRICE_COLS, "tick_volume", "real_volume", "is_complete", "source", "quality_status"]
    series = CandleSeries(symbol, timeframe, clean[cols].reset_index(drop=True), provisional=keep_last_forming,
                          quality_issues=issues, excluded_rows=excluded, source=source)
    if excluded:
        log.info("Candles excluded from analysis", extra={"symbol": symbol, "timeframe": timeframe,
                                                          "count": len(excluded)})
    return series


def truncate(series: CandleSeries, upto: int) -> CandleSeries:
    """Series containing candles 0..upto-1 only (used for replay / look-ahead testing)."""
    df = series.df.iloc[:upto].reset_index(drop=True)
    return CandleSeries(series.symbol, series.timeframe, df,
                        provisional=series.provisional and upto == len(series),
                        quality_issues=series.quality_issues, excluded_rows=series.excluded_rows,
                        source=series.source)


def atr(series: CandleSeries, period: int = 14) -> np.ndarray:
    """Wilder ATR. Causal: value ``i`` uses candles 0..i only (simple mean until ``period`` bars exist)."""
    n = len(series)
    out = np.full(n, np.nan)
    if n == 0:
        return out
    h, l, c = series.high, series.low, series.close
    prev_close = np.r_[c[0], c[:-1]]
    tr = np.maximum.reduce([h - l, np.abs(h - prev_close), np.abs(l - prev_close)])
    tr[0] = h[0] - l[0]
    running = 0.0
    for i in range(n):
        if i < period:
            running += tr[i]
            out[i] = running / (i + 1)
        else:
            out[i] = (out[i - 1] * (period - 1) + tr[i]) / period
    # flat candles give ATR 0; carry the last positive value forward (causal) so ratios stay finite
    out = pd.Series(np.where(out > 0, out, np.nan)).ffill().to_numpy()
    floor = np.maximum(np.abs(c) * 1e-6, 1e-12)
    return np.where(np.isnan(out), floor, out)
