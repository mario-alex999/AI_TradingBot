"""Historical session statistics (completed sessions only, causal).

Per instrument and session (plus configured overlaps), for each rolling window (5/10/20/30/60 completed occurrences):

* range               = high - low of the session (price units) and as % of the session open
* movement            = |last close - first open|
* realised volatility = sqrt(sum of squared log returns of the session's candles), in %
* session ATR         = mean true range of the session's candles
* tick volume         = mean broker tick volume per candle - BROKER TICK COUNTS, not centralised exchange volume
* breakout / sweep frequency vs the preceding session's range (configured ``liquidity.pairs``)
* average daily / weekly range (D1 candles, UTC days / ISO weeks)

Only sessions whose end <= as_of are used. A window with fewer completed samples than requested is still reported
with ``sufficient: False`` and the actual ``sample_size``; sessions without any stored candle are counted as
``missing_sessions``.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from statistics import mean, median
from typing import Any

import numpy as np
import pandas as pd

from aurum.config import TIMEFRAME_SECONDS, TradingHours
from aurum.market_intelligence.candles import CandleSource, window
from aurum.market_intelligence.config import MIConfig
from aurum.market_intelligence.liquidity import classify_interaction, pair_level_period
from aurum.market_intelligence.sessions import Interval, occurrences, overlap_intervals


def _session_metrics(df: pd.DataFrame, iv: Interval) -> dict[str, Any] | None:
    w = window(df, iv.start, iv.end)
    if w.empty:
        return None
    h, l, c, o = w["high"].to_numpy(), w["low"].to_numpy(), w["close"].to_numpy(), w["open"].to_numpy()
    prev_c = np.r_[o[0], c[:-1]]
    tr = np.maximum.reduce([h - l, np.abs(h - prev_c), np.abs(l - prev_c)])
    rets = np.diff(np.log(np.r_[o[0], c]))
    tv = pd.to_numeric(w["tick_volume"], errors="coerce")
    rng = float(h.max() - l.min())
    return {"start": iv.start, "end": iv.end, "high": float(h.max()), "low": float(l.min()), "range": rng,
            "range_pct": rng / o[0] * 100 if o[0] else None, "movement": float(abs(c[-1] - o[0])),
            "realised_vol_pct": float(math.sqrt(float(np.sum(rets ** 2)))) * 100, "atr": float(tr.mean()),
            "tick_volume": float(tv.mean()) if tv.notna().any() else None, "candles": int(len(w))}


def _summarise(samples: list[dict[str, Any]], requested: int, min_sample: int, missing: int) -> dict[str, Any]:
    s = samples[-requested:]
    def m(key: str, fn=mean):
        vals = [x[key] for x in s if x.get(key) is not None]
        return float(fn(vals)) if vals else None
    return {"window": requested, "sample_size": len(s), "sufficient": len(s) >= requested,
            "usable": len(s) >= min_sample, "missing_sessions": missing,
            "mean_range": m("range"), "median_range": m("range", median), "mean_range_pct": m("range_pct"),
            "mean_movement": m("movement"), "mean_realised_vol_pct": m("realised_vol_pct"), "session_atr": m("atr"),
            "mean_tick_volume": m("tick_volume"),
            "first": s[0]["start"] if s else None, "last": s[-1]["end"] if s else None}


def session_statistics(cfg: MIConfig, src: CandleSource, symbol: str, as_of: datetime,
                       hours: TradingHours | None = None) -> dict[str, Any]:
    st = cfg.section("statistics")
    windows = [int(w) for w in st.get("windows") or [5, 10, 20, 30, 60]]
    tf = str(st.get("timeframe") or "H1")
    min_sample = int(st.get("min_sample") or 3)
    df = src.frame(symbol, tf, as_of)
    biggest = max(windows)
    if df.empty:
        return {"symbol": symbol, "as_of": as_of, "timeframe": tf, "available": False,
                "reason": f"No closed {tf} candles stored for {symbol}.", "sessions": {}}
    history_start = df["ts"].iloc[0].to_pydatetime()
    span_start = as_of - timedelta(days=biggest * 2 + 14)
    out: dict[str, Any] = {}
    per_session: dict[str, list[dict[str, Any]]] = {}
    ivsets: dict[str, list[Interval]] = {}
    for name, w in cfg.sessions.items():
        ivsets[name] = [o for o in occurrences(w, span_start, as_of) if o.end <= as_of]
    for o in cfg.overlaps:
        ivsets[o.name] = [i for i in overlap_intervals(cfg, o, span_start, as_of) if i.end <= as_of]
    # overall mean true range per candle (all stored closed candles) - the yardstick for "elevated activity"
    _h, _l, _c = df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()
    _pc = np.r_[_c[0], _c[:-1]]
    overall_tr = float(np.maximum.reduce([_h - _l, np.abs(_h - _pc), np.abs(_l - _pc)]).mean())
    for name, ivs in ivsets.items():
        ivs = ivs[-biggest:]
        samples, missing = [], 0
        for iv in ivs:
            if iv.start < history_start:
                continue                       # before stored history: not "missing", simply not available
            mtr = _session_metrics(df, iv)
            if mtr is None:
                missing += 1
            else:
                samples.append(mtr)
        per_session[name] = samples
        wins = {str(w): _summarise(samples, w, min_sample, missing) for w in windows}
        ref = wins.get("20") or wins[str(windows[-1])]
        ratio = (ref["session_atr"] / overall_tr) if ref["session_atr"] and overall_tr and ref["usable"] else None
        activity = None if ratio is None else "elevated" if ratio >= 1.2 else "subdued" if ratio <= 0.8 else "typical"
        out[name] = {"kind": "overlap" if name in {o.name for o in cfg.overlaps} else "session",
                     "windows": wins, "latest": samples[-1] if samples else None,
                     "activity": {"ratio_to_all_candles": ratio, "classification": activity,
                                  "sample_size": ref["sample_size"],
                                  "rule": "mean true range of the window's candles / mean true range of all stored "
                                          "candles: >= 1.2 elevated, <= 0.8 subdued (historical, not a forecast)"}}

    # breakout / sweep frequency of each sweeper session vs the preceding level session
    lq = cfg.section("liquidity")
    confirm, fb = int(lq.get("confirm_bars", 2)), int(lq.get("false_breakout_bars", 3))
    freq = {}
    for pair in lq.get("pairs") or []:
        sw_name, lv_name = pair["sweeper"], pair["level"]
        sws = ivsets.get(sw_name, [])
        rows = []
        lv_def = cfg.sessions.get(lv_name)
        for sw in sws[-biggest:]:
            lv = pair_level_period(lv_def, sw) if lv_def else None
            if lv is None or lv.start < history_start:
                continue
            lvw = window(df, lv.start, lv.end)
            cands = window(df, max(sw.start, lv.end), sw.end)
            if lvw.empty or cands.empty:
                continue
            kinds = set()
            for side, price in (("high", float(lvw["high"].max())), ("low", float(lvw["low"].min()))):
                kinds |= {e["event_type"] for e in classify_interaction(cands, price, side, confirm, fb)}
            rows.append(kinds)
        n = len(rows)
        freq[f"{sw_name}_vs_{lv_name}"] = {
            "sample_size": n,
            "breakout_frequency": sum("confirmed_breakout" in k for k in rows) / n if n else None,
            "false_breakout_frequency": sum("false_breakout_candidate" in k for k in rows) / n if n else None,
            "sweep_frequency": sum("wick_sweep" in k for k in rows) / n if n else None,
            "note": "Share of sessions with at least one such interaction with the preceding session's high or low."}

    # average daily / weekly range from D1 candles
    d1 = src.frame(symbol, "D1", as_of)
    daily = weekly = None
    if not d1.empty:
        rng = (d1["high"] - d1["low"]).to_numpy()
        daily = {str(w): {"window": w, "sample_size": int(min(w, len(rng))),
                          "mean_range": float(rng[-w:].mean()) if len(rng) else None,
                          "sufficient": len(rng) >= w} for w in windows}
        wk = d1.assign(week=d1["ts"].dt.tz_convert("UTC").dt.strftime("%G-W%V")).groupby("week")
        wr = (wk["high"].max() - wk["low"].min())
        last_close = d1["close_t"].iloc[-1]
        # drop the current (possibly incomplete) ISO week
        if not d1.empty and last_close.isocalendar()[:2] == pd.Timestamp(as_of).isocalendar()[:2]:
            wr = wr.iloc[:-1]
        wr = wr.to_numpy()
        weekly = {str(w): {"window": w, "sample_size": int(min(w, len(wr))),
                           "mean_range": float(wr[-w:].mean()) if len(wr) else None,
                           "sufficient": len(wr) >= w} for w in windows if w <= 30}
    return {"symbol": symbol, "as_of": as_of, "timeframe": tf, "available": True,
            "history_start": history_start, "candles": int(len(df)), "overall_mean_true_range": overall_tr,
            "sessions": out,
            "breakout_frequency": freq, "daily_range": daily, "weekly_range": weekly,
            "volume_note": "tick_volume is the broker's tick count, not centralised exchange volume.",
            "method": f"Completed sessions only (end <= as_of), {tf} candles; windows count completed occurrences.",
            "timeframe_seconds": TIMEFRAME_SECONDS[tf]}
