"""Price vs positioning divergence (uses Module 01 candles; read-only).

Alignment: for each COT report date T (positions as of Tuesday), the price is the close of the last
*completed* Module 01 D1 candle whose day is <= T (the Tuesday daily close; the candle closes before the
Friday release, so no future candle is used). A report with no candle within 4 days of T gets no price.
When ``as_of`` is given, only reports released by then and candles closed by then are used.

Signals over the last ``window_reports`` report intervals (config/cot.yaml -> analytics.divergence):
* bearish_divergence : price change >= +min_pct while the group's net position fell
* bullish_divergence : price change <= -min_pct while the group's net position rose
* unconfirmed_high   : price at a new high of the last ``extreme_lookback_reports`` reports, net position not
* unconfirmed_low    : price at a new low of that window, net position not at its low
These describe disagreement between price and positioning; they are NOT reversal predictions.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd

from aurum.cot.config import DivergenceConfig

MAX_PRICE_LAG_DAYS = 4


@dataclass
class DivergenceResult:
    available: bool
    reason: str | None
    window_reports: int
    rows: list[dict[str, Any]]           # aligned series (report_date, price_close, price_date, net)
    signals: list[dict[str, Any]]        # all detected signals, oldest first
    latest: dict[str, Any] | None        # assessment at the latest aligned report
    price_coverage: tuple[str | None, str | None]


def align_prices(report_dates: list[date], candles: pd.DataFrame, as_of: datetime | None = None
                 ) -> list[tuple[float | None, date | None]]:
    if candles.empty:
        return [(None, None)] * len(report_dates)
    c = candles[["ts_utc", "close"]].dropna().copy()
    c["ts_utc"] = pd.to_datetime(c["ts_utc"], utc=True)
    if as_of is not None:
        c = c[c["ts_utc"] + pd.Timedelta(days=1) <= pd.Timestamp(as_of)]   # candle fully closed by as_of
    c["day"] = c["ts_utc"].dt.date
    c = c.sort_values("day").drop_duplicates("day", keep="last")
    days, closes = list(c["day"]), list(c["close"])
    out = []
    import bisect
    for d in report_dates:
        k = bisect.bisect_right(days, d) - 1
        if k < 0 or (d - days[k]).days > MAX_PRICE_LAG_DAYS:
            out.append((None, None))
        else:
            out.append((float(closes[k]), days[k]))
    return out


def analyse(metrics: pd.DataFrame, candles: pd.DataFrame, cfg: DivergenceConfig,
            as_of: datetime | None = None) -> DivergenceResult:
    """``metrics``: one category's metric rows (report_date, net_position, release_timestamp)."""
    m = metrics.sort_values("report_date").copy()
    if as_of is not None and not m.empty:
        m = m[pd.to_datetime(m["release_timestamp"], utc=True) <= pd.Timestamp(as_of)]
    coverage = (None, None)
    if not candles.empty:
        ts = pd.to_datetime(candles["ts_utc"], utc=True)
        coverage = (str(ts.min().date()), str(ts.max().date()))
    if m.empty:
        return DivergenceResult(False, "No COT metrics available.", cfg.window_reports, [], [], None, coverage)
    if candles.empty:
        return DivergenceResult(False, f"No Module 01 {cfg.price_timeframe} candles stored for this instrument.",
                                cfg.window_reports, [], [], None, coverage)
    dates = list(m["report_date"])
    prices = align_prices(dates, candles, as_of)
    rows = [{"report_date": d, "net_position": None if pd.isna(n) else int(n), "price_close": p, "price_date": pd_}
            for d, n, (p, pd_) in zip(dates, m["net_position"], prices)]
    aligned = [r for r in rows if r["price_close"] is not None and r["net_position"] is not None]
    if len(aligned) <= cfg.window_reports:
        return DivergenceResult(False, f"Only {len(aligned)} report(s) overlap with stored price history "
                                       f"({coverage[0]} to {coverage[1]}); need more than {cfg.window_reports}.",
                                cfg.window_reports, rows, [], None, coverage)
    signals = []
    latest = None
    n, k = cfg.window_reports, cfg.extreme_lookback_reports
    for i in range(n, len(aligned)):
        cur, base = aligned[i], aligned[i - n]
        pchg = (cur["price_close"] / base["price_close"] - 1) * 100
        nchg = cur["net_position"] - base["net_position"]
        found = []
        if pchg >= cfg.min_price_change_pct and nchg < 0:
            found.append("bearish_divergence")
        if pchg <= -cfg.min_price_change_pct and nchg > 0:
            found.append("bullish_divergence")
        prior = aligned[max(0, i - k):i]
        if len(prior) >= min(k, 8):
            if cur["price_close"] > max(r["price_close"] for r in prior) and \
                    cur["net_position"] < max(r["net_position"] for r in prior):
                found.append("unconfirmed_high")
            if cur["price_close"] < min(r["price_close"] for r in prior) and \
                    cur["net_position"] > min(r["net_position"] for r in prior):
                found.append("unconfirmed_low")
        assessment = {
            "report_date": cur["report_date"], "window_start_report_date": base["report_date"],
            "price_date": cur["price_date"], "window_start_price_date": base["price_date"],
            "price_change_pct": round(pchg, 3), "net_position_change": int(nchg),
            "signals": found, "reports_in_window": n,
        }
        if found:
            signals.append(assessment)
        latest = assessment
    return DivergenceResult(True, None, n, rows, signals, latest, coverage)


SIGNAL_TEXT = {
    "bearish_divergence": "price rose while this group reduced its net position",
    "bullish_divergence": "price fell while this group increased its net position",
    "unconfirmed_high": "price made a new high that this group's positioning did not confirm",
    "unconfirmed_low": "price made a new low while this group's net short positioning weakened",
}
