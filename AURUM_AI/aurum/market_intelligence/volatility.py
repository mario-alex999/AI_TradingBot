"""Price-derived volatility (Module 01 candles, causal).

For a symbol and timeframe at ``as_of`` (closed candles only):

* ATR            - Wilder ATR(``atr_period``); Module 03's causal implementation is reused.
* ATR percentile - share of the previous ``percentile_lookback`` ATR values that are below the current one. Only
                   PAST values enter the distribution, so the classification never uses later observations.
* returns std    - standard deviation of the last ``returns_window`` log returns (%); realised volatility =
                   sqrt(sum r^2) over that window; annualised = std * sqrt(bars per year), bars per year =
                   252 trading days * (86400 / timeframe seconds) for intraday, 252 for D1.
* expansion      - mean true range of the last ``short`` bars / of the last ``long`` bars.
* abnormal move  - last candle range / ATR >= ``abnormal_range_atr`` or |last return| z-score >= ``abnormal_return_z``.
* regime         - VERY_LOW / LOW / NORMAL / HIGH / VERY_HIGH from the ATR percentile cut-offs (no fixed price
                   thresholds); UNKNOWN when fewer than ``min_bars`` candles exist.

Volume is not used: broker tick volume is not centralised exchange volume.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd

from aurum.analysis.data import atr as wilder_atr, prepare_candles
from aurum.config import TIMEFRAME_SECONDS, TradingHours
from aurum.market_intelligence.candles import CandleSource, window
from aurum.market_intelligence.config import MIConfig
from aurum.market_intelligence.levels import trading_day_period, week_period

REGIMES = ("VERY_LOW", "LOW", "NORMAL", "HIGH", "VERY_HIGH")


def classify_regime(percentile: float | None, cuts: dict[str, float]) -> tuple[str, str]:
    if percentile is None:
        return "UNKNOWN", "Not enough history to rank the current ATR."
    vl, lo, hi, vh = (float(cuts.get(k, d)) for k, d in (("very_low", 10), ("low", 30), ("high", 70), ("very_high", 90)))
    if percentile < vl:
        r = "VERY_LOW"
    elif percentile < lo:
        r = "LOW"
    elif percentile < hi:
        r = "NORMAL"
    elif percentile < vh:
        r = "HIGH"
    else:
        r = "VERY_HIGH"
    return r, (f"Current ATR is at the {percentile:.0f}th percentile of its recent history "
               f"(cut-offs {vl:.0f}/{lo:.0f}/{hi:.0f}/{vh:.0f}).")


def _bars_per_year(tf: str) -> float:
    sec = TIMEFRAME_SECONDS[tf]
    return 252.0 if sec >= 86400 else 252.0 * 86400 / sec


# Same keys in every response (null when not computable) so API consumers see a stable shape.
METRIC_KEYS = ("price", "atr", "atr_pct_of_price", "atr_period", "atr_percentile", "percentile_sample", "returns_std_pct",
               "realised_vol_pct", "annualised_vol_pct", "realised_vol_percentile", "returns_window", "expansion_ratio",
               "expansion_state", "last_range_atr", "last_return_z", "abnormal_movement")


def series_volatility(df: pd.DataFrame, symbol: str, tf: str, cfg: MIConfig,
                      hours: TradingHours | None = None) -> dict[str, Any]:
    v = cfg.section("volatility")
    period, rw = int(v.get("atr_period", 14)), int(v.get("returns_window", 20))
    look, min_bars = int(v.get("percentile_lookback", 250)), int(v.get("min_bars", 60))
    exp = v.get("expansion") or {}
    short, long_ = int(exp.get("short", 5)), int(exp.get("long", 20))
    n = len(df)
    base = {"symbol": symbol, "timeframe": tf, "candles": n,
            "last_candle": df["ts"].iloc[-1].to_pydatetime() if n else None,
            "last_close_time": df["close_t"].iloc[-1].to_pydatetime() if n else None}
    if n < min_bars:
        return {**base, **dict.fromkeys(METRIC_KEYS), "status": "insufficient_data", "regime": "UNKNOWN",
                "reason": f"{n} closed {tf} candles; at least {min_bars} are needed."}
    series = prepare_candles(df.rename(columns={"ts": "ts_utc"}).drop(columns=["close_t"]), symbol, tf, hours=hours)
    a = wilder_atr(series, period)
    close = series.close
    price = float(close[-1])
    cur_atr = float(a[-1])
    hist = a[max(0, len(a) - 1 - look):-1]
    hist = hist[~np.isnan(hist)]
    pct = float((hist < cur_atr).mean() * 100) if len(hist) >= min(look, max(30, min_bars // 2)) else None
    rets = np.diff(np.log(close))
    r_win = rets[-rw:]
    std = float(np.std(r_win, ddof=1)) if len(r_win) > 1 else float("nan")
    realised = float(math.sqrt(float(np.sum(r_win ** 2)))) if len(r_win) else float("nan")
    # rolling realised-vol percentile (causal)
    rv_hist = [math.sqrt(float(np.sum(rets[i - rw:i] ** 2))) for i in range(rw, len(rets))][-look - 1:-1]
    rv_pct = float(np.mean(np.array(rv_hist) < realised) * 100) if len(rv_hist) >= 30 else None
    h, l = series.high, series.low
    prev_c = np.r_[close[0], close[:-1]]
    tr = np.maximum.reduce([h - l, np.abs(h - prev_c), np.abs(l - prev_c)])
    expansion = float(tr[-short:].mean() / tr[-long_:].mean()) if tr[-long_:].mean() > 0 else None
    state = "neutral"
    if expansion is not None:
        if expansion >= float(exp.get("expansion_ratio", 1.25)):
            state = "expansion"
        elif expansion <= float(exp.get("contraction_ratio", 0.8)):
            state = "contraction"
    last_range_atr = float((h[-1] - l[-1]) / cur_atr) if cur_atr > 0 else None
    prev_std = float(np.std(rets[-rw - 1:-1], ddof=1)) if len(rets) > rw + 1 else float("nan")
    z = float(rets[-1] / prev_std) if prev_std and not math.isnan(prev_std) and prev_std > 0 else None
    abnormal = bool((last_range_atr or 0) >= float(v.get("abnormal_range_atr", 2.5))
                    or (z is not None and abs(z) >= float(v.get("abnormal_return_z", 3.0))))
    regime, why = classify_regime(pct, v.get("regimes") or {})
    return {**base, "status": "ok", "price": price, "atr": cur_atr, "atr_pct_of_price": cur_atr / price * 100,
            "atr_period": period, "atr_percentile": pct, "percentile_sample": int(len(hist)),
            "returns_std_pct": std * 100, "realised_vol_pct": realised * 100,
            "annualised_vol_pct": std * math.sqrt(_bars_per_year(tf)) * 100 if not math.isnan(std) else None,
            "realised_vol_percentile": rv_pct, "returns_window": rw,
            "expansion_ratio": expansion, "expansion_state": state,
            "last_range_atr": last_range_atr, "last_return_z": z, "abnormal_movement": abnormal,
            "regime": regime, "reason": why}


def volatility_report(cfg: MIConfig, src: CandleSource, symbol: str, as_of: datetime,
                      hours: TradingHours | None = None, timeframes: tuple[str, ...] = ("H1", "D1"),
                      session_stats: dict[str, Any] | None = None) -> dict[str, Any]:
    per_tf = {tf: series_volatility(src.frame(symbol, tf, as_of), symbol, tf, cfg, hours) for tf in timeframes}
    # current trading-day and week range vs history (intraday candles, provisional)
    intraday = src.frame(symbol, "H1", as_of)
    d1 = src.frame(symbol, "D1", as_of)
    daily = weekly = None
    if not intraday.empty and not d1.empty:
        day = trading_day_period(cfg, as_of)
        wk = week_period(cfg, as_of)
        dw, ww = window(intraday, day.start, as_of), window(intraday, wk.start, as_of)
        adr = float((d1["high"] - d1["low"]).iloc[-20:].mean())
        if not dw.empty:
            r = float(dw["high"].max() - dw["low"].min())
            daily = {"current_range": r, "average_daily_range_20": adr, "ratio": r / adr if adr else None,
                     "provisional": True, "since": day.start,
                     "note": "Current trading day so far vs mean D1 range of the last 20 days (UTC D1 candles)."}
        wr = d1.assign(week=d1["ts"].dt.strftime("%G-W%V")).groupby("week").agg(h=("high", "max"), l=("low", "min"))
        wr = (wr["h"] - wr["l"]).iloc[:-1]
        if not ww.empty and len(wr):
            r = float(ww["high"].max() - ww["low"].min())
            awr = float(wr.iloc[-10:].mean())
            weekly = {"current_range": r, "average_weekly_range_10": awr, "ratio": r / awr if awr else None,
                      "provisional": True, "since": wk.start, "sample_size": int(min(10, len(wr)))}
    sess = None
    if session_stats and session_stats.get("available"):
        sess = {}
        for name, s in session_stats["sessions"].items():
            w20 = s["windows"].get("20") or next(iter(s["windows"].values()))
            latest = s.get("latest")
            sess[name] = {"mean_range_20": w20["mean_range"], "sample_size": w20["sample_size"],
                          "latest_range": latest["range"] if latest else None,
                          "latest_vs_mean": (latest["range"] / w20["mean_range"])
                          if latest and w20["mean_range"] else None,
                          "mean_realised_vol_pct": w20["mean_realised_vol_pct"]}
    primary = per_tf.get("H1") or next(iter(per_tf.values()))
    return {"symbol": symbol, "as_of": as_of, "timeframes": per_tf, "regime": primary.get("regime", "UNKNOWN"),
            "regime_timeframe": primary["timeframe"], "regime_reason": primary.get("reason"),
            "daily": daily, "weekly": weekly, "sessions": sess,
            "method": "Price-derived (OHLC) only; regimes from ATR percentiles of past values."}


def event_window_range(src: CandleSource, symbol: str, release: datetime, minutes: int, as_of: datetime,
                       timeframes: tuple[str, ...] = ("M5", "M15", "H1")) -> dict[str, Any] | None:
    """High-low range in [release, release + minutes) relative to the ATR before the release (historical context)."""
    end = release + timedelta(minutes=minutes)
    if end > as_of:
        return None
    for tf in timeframes:
        df = src.frame(symbol, tf, as_of)
        if df.empty or df["ts"].iloc[0] > pd.Timestamp(release) - pd.Timedelta(hours=24):
            continue
        w = window(df, release, end)
        before = df.loc[df["close_t"] <= pd.Timestamp(release)].iloc[-50:]
        if w.empty or len(before) < 15:
            continue
        b_tr = (before["high"] - before["low"]).mean()
        r = float(w["high"].max() - w["low"].min())
        bars = max(1, minutes * 60 // TIMEFRAME_SECONDS[tf])
        return {"release": release, "timeframe": tf, "range": r, "pre_release_mean_bar_range": float(b_tr),
                "ratio": float(r / (b_tr * math.sqrt(bars))) if b_tr > 0 else None, "bars": int(len(w))}
    return None
