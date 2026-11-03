"""Previous / current session, trading-day, week and month highs and lows from stored candles.

Rules (all causal - only candles closed by ``as_of`` are used):

* A period's level uses the candles whose OPEN time lies in the period, from the finest configured timeframe whose
  stored history reaches back to the period start (``levels.timeframes``, e.g. M5 -> M15 -> H1). Session windows are
  whole hours in every configured zone, so H1 gives the same extremes as M5 - only the source-candle precision
  differs.
* A COMPLETED level (period end <= as_of) is final and its ``formed_at`` is the period end. A period still running
  at ``as_of`` gives a PROVISIONAL level (``provisional: True``) built from the candles closed so far.
* After a completed level formed, later closed candles decide its state: ``swept`` (first wick beyond with the close
  back inside), ``broken`` (first close beyond). Both times are reported; nothing earlier than ``formed_at`` counts.
* Trading days roll at ``trading_day.roll`` in ``trading_day.timezone`` (17:00 New York by default); weeks run from
  the Sunday roll to the Friday roll; months are UTC calendar months. When intraday history does not reach the
  period start, D1 (UTC-day) candles are used and the method says so.
* Missing candles: the number of expected candles in market-open time that are absent is reported per level.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Any

import pandas as pd

from aurum.config import TIMEFRAME_SECONDS, TradingHours
from aurum.market_intelligence.candles import CandleSource, covers, window
from aurum.market_intelligence.config import MIConfig, zone
from aurum.market_intelligence.sessions import Interval, current_occurrence, previous_occurrence
from aurum.services.validation_service import period_is_closure

UTC = timezone.utc


@dataclass
class Period:
    kind: str                  # session name | trading_day | week | month
    start: datetime
    end: datetime
    label: str


def _ts(v: Any) -> datetime:
    return pd.Timestamp(v).to_pydatetime()


def choose_frame(src: CandleSource, symbol: str, start: datetime, as_of: datetime,
                 timeframes: list[str]) -> tuple[str, pd.DataFrame] | None:
    for tf in timeframes:
        df = src.frame(symbol, tf, as_of)
        if covers(df, start, tf):
            return tf, df
    return None


def expected_candles(start: datetime, end: datetime, timeframe: str, hours: TradingHours | None) -> int:
    step = timedelta(seconds=TIMEFRAME_SECONDS[timeframe])
    n, t = 0, start
    while t < end:
        if hours is None or not period_is_closure(t, int(step.total_seconds()), hours):
            n += 1
        t += step
    return n


def period_extremes(df: pd.DataFrame, p: Period, as_of: datetime, timeframe: str,
                    hours: TradingHours | None = None) -> dict[str, Any] | None:
    w = window(df, p.start, min(p.end, as_of))
    if w.empty:
        return None
    hi_i, lo_i = w["high"].idxmax(), w["low"].idxmin()
    complete = p.end <= as_of
    cut = min(p.end, as_of)
    exp = expected_candles(p.start, cut, timeframe, hours) if timeframe != "D1" else None
    return {"period": p.kind, "period_label": p.label, "period_start": p.start, "period_end": p.end,
            "high": float(w.at[hi_i, "high"]), "high_time": _ts(w.at[hi_i, "ts"]),
            "low": float(w.at[lo_i, "low"]), "low_time": _ts(w.at[lo_i, "ts"]),
            "open": float(w["open"].iloc[0]), "close": float(w["close"].iloc[-1]),
            "first_candle": _ts(w["ts"].iloc[0]), "last_candle": _ts(w["ts"].iloc[-1]),
            "last_close_time": _ts(w["close_t"].iloc[-1]),
            "candles": int(len(w)), "expected_candles": exp,
            "missing_candles": max(0, exp - len(w)) if exp is not None else None,
            "complete": complete, "provisional": not complete, "timeframe": timeframe}


def level_state(df: pd.DataFrame, level: float, side: str, formed_at: datetime) -> dict[str, Any]:
    """First sweep (wick beyond, close inside) and first break (close beyond) after ``formed_at``."""
    after = df.loc[df["ts"] >= pd.Timestamp(formed_at)]
    swept_at = broken_at = sweep_candle = break_candle = None
    for r in after.itertuples(index=False):
        beyond = r.high > level if side == "high" else r.low < level
        if not beyond:
            continue
        closed_beyond = r.close > level if side == "high" else r.close < level
        if closed_beyond and broken_at is None:
            broken_at, break_candle = _ts(r.close_t), _ts(r.ts)
            break
        if not closed_beyond and swept_at is None:
            swept_at, sweep_candle = _ts(r.close_t), _ts(r.ts)
    state = "broken" if broken_at else "swept" if swept_at else "untouched"
    return {"state": state, "swept_at": swept_at, "sweep_candle_time": sweep_candle,
            "broken_at": broken_at, "break_candle_time": break_candle,
            "checked_until": _ts(after["close_t"].iloc[-1]) if not after.empty else None}


# ----------------------------------------------------------------------------------------- periods
def trading_day_period(cfg: MIConfig, t: datetime) -> Period:
    """Trading day containing instant ``t`` (rolls at the configured local time)."""
    tz = zone(cfg.trading_day_tz)
    local = t.astimezone(tz)
    roll_today = datetime.combine(local.date(), cfg.trading_day_roll, tzinfo=tz)
    start_local = roll_today if local >= roll_today else datetime.combine(local.date() - timedelta(days=1),
                                                                            cfg.trading_day_roll, tzinfo=tz)
    end_local = datetime.combine(start_local.date() + timedelta(days=1), cfg.trading_day_roll, tzinfo=tz)
    return Period("trading_day", start_local.astimezone(UTC), end_local.astimezone(UTC), end_local.date().isoformat())


def previous_trading_day(cfg: MIConfig, t: datetime, df: pd.DataFrame | None = None, max_back: int = 7) -> Period | None:
    """Latest COMPLETED trading day before ``t`` that has candles (weekend 'days' with no trading are skipped)."""
    p = trading_day_period(cfg, t)
    for _ in range(max_back):
        p = trading_day_period(cfg, p.start - timedelta(minutes=1))
        if p.end > t:
            continue
        local_end = p.end.astimezone(zone(cfg.trading_day_tz))
        if local_end.weekday() >= 5:            # trading days end Mon..Fri at the roll
            continue
        if df is None or not window(df, p.start, p.end).empty:
            return p
    return None


def week_period(cfg: MIConfig, t: datetime) -> Period:
    """Trading week containing ``t``: Sunday roll -> Friday roll (local)."""
    tz = zone(cfg.trading_day_tz)
    local = t.astimezone(tz)
    days_since_sun = (local.weekday() + 1) % 7
    sunday = local.date() - timedelta(days=days_since_sun)
    start_local = datetime.combine(sunday, cfg.trading_day_roll, tzinfo=tz)
    if local < start_local:
        start_local = datetime.combine(sunday - timedelta(days=7), cfg.trading_day_roll, tzinfo=tz)
    end_local = datetime.combine(start_local.date() + timedelta(days=5), cfg.trading_day_roll, tzinfo=tz)
    return Period("week", start_local.astimezone(UTC), end_local.astimezone(UTC),
                  f"week of {start_local.date() + timedelta(days=1)}")


def previous_week(cfg: MIConfig, t: datetime) -> Period:
    p = week_period(cfg, t)
    if p.end <= t:          # weekend: the week containing t has already closed
        return p
    return week_period(cfg, p.start - timedelta(days=2))


def month_period(t: datetime) -> Period:
    start = datetime(t.year, t.month, 1, tzinfo=UTC)
    end = datetime(t.year + (t.month == 12), t.month % 12 + 1, 1, tzinfo=UTC)
    return Period("month", start, end, start.strftime("%Y-%m"))


def previous_month(t: datetime) -> Period:
    cur = month_period(t)
    return month_period(cur.start - timedelta(days=1))


def _d1_period(p: Period) -> Period:
    """Same period expressed in whole UTC days (for the D1 fallback)."""
    s = datetime.combine(p.start.astimezone(UTC).date() + (timedelta(days=1) if p.start.astimezone(UTC).hour >= 12
                                                           else timedelta()), time(0), tzinfo=UTC)
    e = datetime.combine(p.end.astimezone(UTC).date() + (timedelta(days=1) if p.end.astimezone(UTC).hour >= 12
                                                         else timedelta()), time(0), tzinfo=UTC)
    return Period(p.kind, s, e, p.label)


# ----------------------------------------------------------------------------------------- levels
def _levels_from(ext: dict[str, Any], name: str, symbol: str, method: str, df: pd.DataFrame,
                 note: str | None = None) -> list[dict[str, Any]]:
    out = []
    for side in ("high", "low"):
        price = ext[side]
        lv = {"symbol": symbol, "level_type": f"{name}_{side}", "side": side, "price": price,
              "source_candle_time": ext[f"{side}_time"], "formed_at": ext["period_end"] if ext["complete"]
              else ext["last_close_time"], "method": method, "note": note, **{k: v for k, v in ext.items()
                                                                               if k not in ("high", "low", "high_time",
                                                                                            "low_time")}}
        if ext["complete"]:
            lv.update(level_state(df, price, side, ext["period_end"]))
        else:
            lv.update({"state": "provisional", "swept_at": None, "broken_at": None, "sweep_candle_time": None,
                       "break_candle_time": None, "checked_until": ext["last_close_time"]})
        out.append(lv)
    return out


def session_levels(cfg: MIConfig, src: CandleSource, symbol: str, as_of: datetime,
                   hours: TradingHours | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Previous and current session highs/lows for ``levels.sessions``. Returns (levels, issues)."""
    lvcfg = cfg.section("levels")
    tfs = list(lvcfg.get("timeframes") or ["M5", "M15", "H1"])
    out, issues = [], []
    for name in lvcfg.get("sessions") or ["asian", "london", "new_york"]:
        w = cfg.sessions.get(name)
        if w is None:
            issues.append({"issue": "unknown_session", "session": name})
            continue
        for label, occ in (("previous", previous_occurrence(w, as_of, completed=True)),
                           ("current", current_occurrence(w, as_of))):
            if occ is None:
                continue
            chosen = choose_frame(src, symbol, occ.start, as_of, tfs)
            if chosen is None:
                issues.append({"issue": "insufficient_history", "level": f"{label}_{name}",
                               "period_start": occ.start, "message": f"No stored candles reach back to the {name} "
                                                                    f"session starting {occ.start:%Y-%m-%d %H:%M} UTC."})
                continue
            tf, df = chosen
            ext = period_extremes(df, Period(name, occ.start, occ.end, occ.local_date or ""), as_of, tf, hours)
            if ext is None:
                if label == "previous":
                    issues.append({"issue": "no_candles_in_period", "level": f"previous_{name}",
                                   "period_start": occ.start, "period_end": occ.end})
                continue
            out += _levels_from(ext, f"{label}_{name}", symbol, f"session window {w.timezone} "
                                f"{w.open:%H:%M}-{w.close:%H:%M} from {tf} candles", df)
    return out, issues


def periodic_levels(cfg: MIConfig, src: CandleSource, symbol: str, as_of: datetime,
                    hours: TradingHours | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Previous trading-day, week and month highs/lows (+ current, provisional)."""
    lvcfg = cfg.section("levels")
    tfs = list(lvcfg.get("timeframes") or ["M5", "M15", "H1"])
    fallback = str(lvcfg.get("daily_fallback_timeframe") or "D1")
    out, issues = [], []
    coarse = src.frame(symbol, tfs[-1], as_of)   # coarsest intraday timeframe = longest history (skips empty days)
    periods: list[tuple[str, Period | None]] = [
        ("previous_day", previous_trading_day(cfg, as_of, coarse)), ("current_day", trading_day_period(cfg, as_of)),
        ("previous_week", previous_week(cfg, as_of)), ("current_week", week_period(cfg, as_of)),
        ("previous_month", previous_month(as_of)), ("current_month", month_period(as_of))]
    for name, p in periods:
        if p is None:
            issues.append({"issue": "insufficient_history", "level": name})
            continue
        if p.start > as_of:
            continue
        chosen = choose_frame(src, symbol, p.start, as_of, tfs)
        note = None
        if chosen is None:
            d1 = src.frame(symbol, fallback, as_of)
            p2 = p if p.kind == "month" else _d1_period(p)
            if not covers(d1, p2.start, fallback):
                issues.append({"issue": "insufficient_history", "level": name, "period_start": p.start,
                               "message": f"Stored candles do not reach back to {p.start:%Y-%m-%d %H:%M} UTC."})
                continue
            chosen = (fallback, d1)
            if p.kind != "month":
                note = (f"Intraday history does not reach {p.start:%Y-%m-%d}; D1 candles (UTC days) were used, "
                        f"so the boundary is UTC midnight instead of the {cfg.trading_day_roll:%H:%M} "
                        f"{cfg.trading_day_tz} roll.")
                p = p2
        tf, df = chosen
        ext = period_extremes(df, p, as_of, tf, hours)
        if ext is None:
            issues.append({"issue": "no_candles_in_period", "level": name, "period_start": p.start})
            continue
        method = {"trading_day": f"trading day rolling at {cfg.trading_day_roll:%H:%M} {cfg.trading_day_tz}",
                  "week": f"week Sunday->Friday {cfg.trading_day_roll:%H:%M} {cfg.trading_day_tz}",
                  "month": "UTC calendar month"}[p.kind]
        out += _levels_from(ext, name, symbol, f"{method} from {tf} candles", df, note)
    return out, issues


def all_levels(cfg: MIConfig, src: CandleSource, symbol: str, as_of: datetime,
               hours: TradingHours | None = None) -> dict[str, Any]:
    s_lv, s_iss = session_levels(cfg, src, symbol, as_of, hours)
    p_lv, p_iss = periodic_levels(cfg, src, symbol, as_of, hours)
    return {"symbol": symbol, "as_of": as_of, "levels": s_lv + p_lv, "issues": s_iss + p_iss,
            "rules": ("Completed levels are final at period end; current-period levels are provisional. "
                      "State after formation: swept = wick beyond with close back inside; broken = close beyond.")}


def occurrence_period(o: Interval) -> Period:
    return Period(o.name, o.start, o.end, o.local_date or "")


def trading_days_back(cfg: MIConfig, as_of: datetime, n: int) -> list[Period]:
    """The ``n`` most recent completed weekday trading days (newest last)."""
    out: list[Period] = []
    p = trading_day_period(cfg, as_of)
    guard = 0
    while len(out) < n and guard < n * 3 + 10:
        guard += 1
        p = trading_day_period(cfg, p.start - timedelta(minutes=1))
        if p.end > as_of:
            continue
        if p.end.astimezone(zone(cfg.trading_day_tz)).weekday() < 5:
            out.append(p)
    return list(reversed(out))
