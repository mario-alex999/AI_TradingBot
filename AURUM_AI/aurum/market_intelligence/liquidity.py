"""Session liquidity: sweeps, breakouts and false breakouts of session / day / week levels.

Classification of the first interaction of closed candles with a completed level (high side; low side mirrored):

* ``wick_sweep``               - a candle trades above the level but CLOSES at or below it.
* ``confirmed_breakout``       - a candle closes above the level and the next max(``confirm_bars``,
                                 ``false_breakout_bars``) candles also close above it. ``confirmation_time`` = close of
                                 the last of those candles.
* ``false_breakout_candidate`` - a candle closes above the level, then within ``false_breakout_bars`` candles a close
                                 returns inside. ``confirmation_time`` = close of the candle that returned inside.
* ``unconfirmed``              - a close above the level whose follow-through cannot be judged yet (fewer candles
                                 closed by ``as_of`` than the rules need).
* ``range_expansion``          - a session's range is >= ``expansion_ratio`` x the mean of its previous sessions.

None of these is interpreted as a reversal or a signal; the evidence and the rule are returned with each event.
Module 03 BOS/CHoCH events that confirm within ``module03_match_minutes`` after the event are attached as context.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pandas as pd

from aurum.config import TradingHours
from aurum.market_intelligence.candles import CandleSource, window
from aurum.market_intelligence.config import MIConfig
from aurum.market_intelligence.levels import Period, choose_frame, period_extremes
from aurum.market_intelligence.sessions import occurrences, previous_occurrence

RULES = {
    "wick_sweep": "Wick beyond the level, candle closed back inside.",
    "confirmed_breakout": "Closed beyond the level and the next max({confirm}, {fb}) candle(s) also closed beyond it.",
    "false_breakout_candidate": "Closed beyond the level, then closed back inside within {fb} candle(s).",
    "unconfirmed": "Closed beyond the level; not enough closed candles yet to confirm or reject the breakout.",
    "range_expansion": "Session range >= {ratio} x the mean range of the previous {n} completed sessions.",
}
NOT_A_SIGNAL = "Liquidity interaction only - not a reversal or continuation forecast and not a trade signal."


def _ts(v: Any) -> datetime:
    return pd.Timestamp(v).to_pydatetime()


def classify_interaction(cands: pd.DataFrame, level: float, side: str, confirm_bars: int,
                         false_bars: int) -> list[dict[str, Any]]:
    """First wick sweep and first close-beyond sequence of ``cands`` against ``level``."""
    rows = list(cands.itertuples(index=False))
    events: list[dict[str, Any]] = []
    swept = False
    for i, r in enumerate(rows):
        beyond = r.high > level if side == "high" else r.low < level
        if not beyond:
            continue
        closed_beyond = r.close > level if side == "high" else r.close < level
        extreme = float(r.high if side == "high" else r.low)
        base = {"event_time": _ts(r.ts), "source_candle_time": _ts(r.ts), "extreme": extreme, "close": float(r.close)}
        if not closed_beyond:
            if not swept:
                events.append({**base, "event_type": "wick_sweep", "confirmation_time": _ts(r.close_t),
                               "closed_beyond": False, "returned_inside": True, "returned_at": _ts(r.close_t)})
                swept = True
            continue
        need = max(confirm_bars, false_bars)           # both rules must be decidable before a verdict
        follow = rows[i + 1:i + 1 + need]
        back = next((j for j, f in enumerate(follow[:false_bars])
                     if (f.close <= level if side == "high" else f.close >= level)), None)
        if back is not None:
            f = follow[back]
            events.append({**base, "event_type": "false_breakout_candidate", "confirmation_time": _ts(f.close_t),
                           "closed_beyond": True, "returned_inside": True, "returned_at": _ts(f.close_t)})
        elif len(follow) >= need:
            conf = follow[need - 1] if need else r
            events.append({**base, "event_type": "confirmed_breakout", "confirmation_time": _ts(conf.close_t),
                           "closed_beyond": True, "returned_inside": False, "returned_at": None})
        else:
            events.append({**base, "event_type": "unconfirmed", "confirmation_time": None, "closed_beyond": True,
                           "returned_inside": False, "returned_at": None})
        break
    return events


def pair_level_period(lv_def, sw) -> Period | None:
    """Range of the latest ``lv_def`` occurrence that started before the sweeper session ``sw`` opened, cut at that
    open. None when that occurrence ended more than 12 hours before (not the preceding session)."""
    lv = previous_occurrence(lv_def, sw.start, completed=False)
    if lv is None or lv.start >= sw.start or lv.end < sw.start - timedelta(hours=12):
        return None
    return Period(lv_def.name, lv.start, min(lv.end, sw.start), lv.local_date or "")


def _related_structure(m03: dict[str, Any] | None, t: datetime, side: str, minutes: int) -> dict[str, Any] | None:
    if not m03 or not m03.get("available"):
        return None
    lim = t + timedelta(minutes=minutes)
    for e in m03.get("structure_events", []):
        bt = e["break_candle_time"]
        if t <= bt <= lim:
            return {k: e[k] for k in ("event_type", "direction", "scope", "timeframe", "broken_level",
                                      "break_candle_time", "confirmed_at") if k in e}
    return None


def _session_at(cfg: MIConfig, t: datetime) -> str | None:
    for name in ("london", "new_york", "asian", "tokyo", "sydney"):
        w = cfg.sessions.get(name)
        if w and any(o.contains(t) for o in occurrences(w, t - timedelta(days=1), t + timedelta(minutes=1))):
            return name
    return None


def session_liquidity(cfg: MIConfig, src: CandleSource, symbol: str, as_of: datetime, levels: list[dict[str, Any]],
                      m03: dict[str, Any] | None = None, hours: TradingHours | None = None) -> dict[str, Any]:
    lq = cfg.section("liquidity")
    confirm, fb = int(lq.get("confirm_bars", 2)), int(lq.get("false_breakout_bars", 3))
    match_min = int(lq.get("module03_match_minutes", 120))
    tfs = list(cfg.section("levels").get("timeframes") or ["M5", "M15", "H1"])
    events: list[dict[str, Any]] = []
    notes: list[str] = []

    def emit(raw: list[dict[str, Any]], session: str | None, level_type: str, price: float, side: str, tf: str):
        for e in raw:
            e.update({"symbol": symbol, "session": session or _session_at(cfg, e["event_time"]),
                      "level_type": level_type, "level": price, "side": side,
                      "direction": "buy_side" if side == "high" else "sell_side", "timeframe": tf,
                      "rule": RULES[e["event_type"]].format(confirm=confirm, fb=fb),
                      "related_structure_event": _related_structure(m03, e["event_time"], side, match_min),
                      "interpretation": NOT_A_SIGNAL})
            events.append(e)

    # 1) session-vs-session pairs (London sweeps Asia, New York sweeps London, ...). The level is the range the
    #    level session had built by the time the sweeper session opened (London is still open when New York opens,
    #    so "New York sweeps London" uses London's range up to the New York open).
    for pair in lq.get("pairs") or []:
        sw_def, lv_def = cfg.sessions.get(pair["sweeper"]), cfg.sessions.get(pair["level"])
        if not sw_def or not lv_def:
            continue
        sw = previous_occurrence(sw_def, as_of, completed=False)        # current or latest started sweeper session
        if sw is None:
            continue
        cut_lv = pair_level_period(lv_def, sw)
        if cut_lv is None:
            continue
        chosen = choose_frame(src, symbol, cut_lv.start, as_of, tfs)
        if chosen is None:
            notes.append(f"No candles reach back to the {lv_def.name} session of {cut_lv.label}.")
            continue
        tf, df = chosen
        ext = period_extremes(df, cut_lv, as_of, tf, hours)
        if ext is None or not ext["complete"]:
            continue
        cands = window(df, max(sw.start, cut_lv.end), min(sw.end, as_of))
        for side in ("high", "low"):
            raw = classify_interaction(cands, ext[side], side, confirm, fb)
            for e in raw:
                e["level_period"] = {"start": cut_lv.start, "end": cut_lv.end,
                                     "source_candle_time": ext[f"{side}_time"]}
            emit(raw, sw_def.name, f"{lv_def.name}_{side}", ext[side], side, tf)

    # 2) previous day / week levels against every candle since they formed
    for lv in levels:
        if lv["level_type"] not in ("previous_day_high", "previous_day_low", "previous_week_high", "previous_week_low"):
            continue
        tf = lv["timeframe"]
        if tf == "D1":
            tf = tfs[-1]
        df = src.frame(symbol, tf, as_of)
        cands = df.loc[df["ts"] >= pd.Timestamp(lv["period_end"])]
        emit(classify_interaction(cands, lv["price"], lv["side"], confirm, fb), None, lv["level_type"],
             lv["price"], lv["side"], tf)

    # 3) range expansion of the current / latest session vs its own history
    ratio, n = float(lq.get("expansion_ratio", 1.5)), int(lq.get("expansion_lookback", 10))
    for name in cfg.section("levels").get("sessions") or []:
        w = cfg.sessions.get(name)
        if w is None:
            continue
        occ = occurrences(w, as_of - timedelta(days=n * 2 + 7), as_of)
        occ = [o for o in occ if o.start <= as_of]
        if len(occ) < 2:
            continue
        chosen = choose_frame(src, symbol, occ[0].start, as_of, ["H1"]) or choose_frame(src, symbol, occ[-1].start,
                                                                                        as_of, tfs)
        if chosen is None:
            continue
        tf, df = chosen
        cur = occ[-1]
        ext = period_extremes(df, Period(name, cur.start, cur.end, cur.local_date or ""), as_of, tf, hours)
        hist = [period_extremes(df, Period(name, o.start, o.end, ""), as_of, tf, hours) for o in occ[:-1]]
        hist = [h for h in hist if h and h["complete"]][-n:]
        if ext is None or len(hist) < 3:
            continue
        mean = sum(h["high"] - h["low"] for h in hist) / len(hist)
        rng = ext["high"] - ext["low"]
        if mean > 0 and rng / mean >= ratio:
            events.append({"symbol": symbol, "session": name, "event_type": "range_expansion",
                           "level_type": f"{name}_range", "level": None, "side": None, "direction": None,
                           "event_time": ext["last_candle"], "confirmation_time": ext["last_close_time"],
                           "source_candle_time": ext["last_candle"], "closed_beyond": None, "returned_inside": None,
                           "returned_at": None, "extreme": None, "close": ext["close"], "timeframe": tf,
                           "range": rng, "mean_range": mean, "ratio": round(rng / mean, 3), "samples": len(hist),
                           "provisional": not ext["complete"],
                           "rule": RULES["range_expansion"].format(ratio=ratio, n=len(hist)),
                           "related_structure_event": None, "interpretation": NOT_A_SIGNAL})

    events.sort(key=lambda e: e["event_time"])
    future = [e for e in events if (e.get("confirmation_time") or e["event_time"]) > as_of]
    if future:   # impossible by construction (sources return closed candles only); fail loudly if it ever happens
        raise RuntimeError(f"look-ahead guard: {len(future)} liquidity event(s) dated after as_of {as_of}")
    m03_status = ({"available": bool(m03 and m03.get("available")), "reason": (m03 or {}).get("reason"),
                   "timeframe": (m03 or {}).get("timeframe")})
    return {"symbol": symbol, "as_of": as_of, "events": events, "notes": notes, "module03": m03_status,
            "rules": {k: v.format(confirm=confirm, fb=fb, ratio=ratio, n=n) for k, v in RULES.items()},
            "interpretation": NOT_A_SIGNAL}
