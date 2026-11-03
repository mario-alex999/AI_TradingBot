"""Trading sessions (Asian / London / New York) + session highs/lows as liquidity."""
from __future__ import annotations
import pandas as pd

DEFAULT = {"asian": ("19:00", "00:00"), "london": ("02:00", "05:00"), "newyork": ("07:00", "10:00")}


def _in(t, start, end):
    return (start <= t < end) if start < end else (t >= start or t < end)  # handles midnight wrap


def tag_sessions(df: pd.DataFrame, cfg: dict | None = None, tz: str = "America/New_York") -> pd.DataFrame:
    cfg = cfg or DEFAULT
    out = df.copy()
    local = out.index.tz_convert(tz)
    times = local.time
    for name, (s, e) in cfg.items():
        if name == "trade_only_in":
            continue
        s_t, e_t = pd.Timestamp(s).time(), pd.Timestamp(e).time()
        out[f"sess_{name}"] = [_in(t, s_t, e_t) for t in times]
    # "trading day" starts at 19:00 NY (Asian open) — shift so Asian range belongs to next day
    out["trade_day"] = (local + pd.Timedelta(hours=5)).date
    return out


def session_ranges(df: pd.DataFrame) -> pd.DataFrame:
    """Per trading day: Asian high/low, London high/low (classic liquidity pools)."""
    rows = {}
    for name in ("asian", "london", "newyork"):
        col = f"sess_{name}"
        if col not in df:
            continue
        g = df[df[col]].groupby("trade_day")
        rows[f"{name}_high"] = g["high"].max()
        rows[f"{name}_low"] = g["low"].min()
    return pd.DataFrame(rows)


def active_session(row) -> str | None:
    for name in ("london", "newyork", "asian"):
        if row.get(f"sess_{name}", False):
            return name
    return None
