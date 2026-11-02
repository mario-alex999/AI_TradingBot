"""Positioning classification, change detection, participant comparisons and the metric pipeline.

Classification (AURUM convention, configurable in config/cot.yaml - not an industry standard):
    score = mean of the available values among {36-month percentile, COT index}
    score >= extremely_bullish -> extremely_bullish     ... score < extremely_bearish -> extremely_bearish
    with require_direction_for_extremes, an "extremely bullish" label also needs a net-long position and
    "extremely bearish" a net-short one; otherwise the label is capped at "strong".
The label describes positioning *relative to its own history*, not a forecast.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd

from aurum.cot import calculations as calc
from aurum.cot.config import AnalyticsConfig, ClassificationConfig
from aurum.cot.fields import CATEGORY_LABELS, COMPARISONS

CALCULATION_VERSION = "1.0"
STRENGTHS = ["extremely_bearish", "strong_bearish", "moderately_bearish", "neutral",
             "moderately_bullish", "strong_bullish", "extremely_bullish"]


def direction(net: float | None) -> str | None:
    if net is None:
        return None
    return "net_long" if net > 0 else "net_short" if net < 0 else "balanced"


def classification_score(percentile_36m: float | None, cot_idx: float | None) -> float | None:
    vals = [v for v in (percentile_36m, cot_idx) if v is not None]
    return sum(vals) / len(vals) if vals else None


def classify(score: float | None, net: float | None, c: ClassificationConfig) -> str | None:
    if score is None:
        return None
    if score >= c.extremely_bullish:
        label = "extremely_bullish"
    elif score >= c.strong_bullish:
        label = "strong_bullish"
    elif score >= c.moderately_bullish:
        label = "moderately_bullish"
    elif score > c.moderately_bearish:
        label = "neutral"
    elif score > c.strong_bearish:
        label = "moderately_bearish"
    elif score > c.extremely_bearish:
        label = "strong_bearish"
    else:
        label = "extremely_bearish"
    if c.require_direction_for_extremes and net is not None:
        if label == "extremely_bullish" and net <= 0:
            label = "strong_bullish"
        if label == "extremely_bearish" and net >= 0:
            label = "strong_bearish"
    return label


def change_type(prev_net: float | None, net: float | None) -> str | None:
    """new_net_long / new_net_short = sign reversal; *_accumulation / *_reduction otherwise."""
    if prev_net is None or net is None:
        return None
    if prev_net <= 0 < net:
        return "reversal_to_net_long" if prev_net < 0 else "new_net_long"
    if prev_net >= 0 > net:
        return "reversal_to_net_short" if prev_net > 0 else "new_net_short"
    d = net - prev_net
    if d == 0:
        return "unchanged"
    if net > 0:
        return "net_long_accumulation" if d > 0 else "net_long_reduction"
    if net < 0:
        return "net_short_accumulation" if d < 0 else "net_short_reduction"
    return "unchanged"


def change_driver(long_change: float | None, short_change: float | None) -> str | None:
    """Which side moved the net position more (bullish drivers: longs added / shorts covered)."""
    if long_change is None or short_change is None:
        return None
    if long_change == 0 and short_change == 0:
        return "no_change"
    if abs(long_change) >= abs(short_change):
        return "longs_added" if long_change > 0 else "longs_cut"
    return "shorts_added" if short_change > 0 else "shorts_covered"


def compute_metrics(positions: pd.DataFrame, cfg: AnalyticsConfig) -> list[dict[str, Any]]:
    """Metric rows for one contract mapping. ``positions`` = CotRepository.positions_frame output."""
    if positions.empty:
        return []
    now = datetime.now(timezone.utc)
    lo, hi = cfg.unusual_percentile
    rows: list[dict[str, Any]] = []
    for cat, g in positions.groupby("trader_category", sort=False):
        g = g.sort_values("report_date").reset_index(drop=True)
        dates = list(g["report_date"])
        net = [None if pd.isna(v) else float(v) for v in g["net_positions"]]
        longs = [None if pd.isna(v) else float(v) for v in g["long_positions"]]
        shorts = [None if pd.isna(v) else float(v) for v in g["short_positions"]]
        changes = [None] + [calc.weekly_change(net[i], net[i - 1]) for i in range(1, len(net))]
        for i in range(len(g)):
            pct = {k: calc.windowed_stat(dates, net, i, w, cfg.min_history_weeks, calc.percentile_rank)
                   for k, w in cfg.percentile_lookbacks_weeks.items()}
            idx = calc.windowed_stat(dates, net, i, cfg.cot_index_lookback_weeks, cfg.min_history_weeks, calc.cot_index)
            z = calc.windowed_stat(dates, net, i, cfg.zscore_lookback_weeks, cfg.min_history_weeks, calc.z_score)
            score = classification_score(pct.get("36m"), idx)
            cz = calc.change_zscore(changes, i, cfg.min_history_weeks)
            p36 = pct.get("36m")
            oi = g.at[i, "report_open_interest"]
            rows.append({
                "cot_report_id": int(g.at[i, "report_id"]), "trader_category": cat, "report_date": dates[i],
                "release_timestamp": g.at[i, "release_timestamp"],
                "net_position": None if net[i] is None else int(net[i]),
                "long_change": None if i == 0 or longs[i] is None or longs[i - 1] is None else int(longs[i] - longs[i - 1]),
                "short_change": None if i == 0 or shorts[i] is None or shorts[i - 1] is None else int(shorts[i] - shorts[i - 1]),
                "weekly_net_change": None if changes[i] is None else int(changes[i]),
                "net_position_pct_oi": calc.pct_of_open_interest(net[i], None if pd.isna(oi) else float(oi)),
                "historical_percentile_12m": pct.get("12m"), "historical_percentile_24m": pct.get("24m"),
                "historical_percentile_36m": pct.get("36m"), "historical_percentile_48m": pct.get("48m"),
                "cot_index": idx, "z_score": z,
                "history_weeks": len(calc.trailing_window(dates, net, i, max(cfg.percentile_lookbacks_weeks.values()))),
                "positioning_direction": direction(net[i]),
                "positioning_strength": classify(score, net[i], cfg.classification),
                "classification_score": score,
                "change_type": change_type(net[i - 1] if i else None, net[i]),
                "change_driver": None if i == 0 else change_driver(
                    None if longs[i] is None or longs[i - 1] is None else longs[i] - longs[i - 1],
                    None if shorts[i] is None or shorts[i - 1] is None else shorts[i] - shorts[i - 1]),
                "significant_change": bool(cz is not None and abs(cz) >= cfg.significant_change_z),
                "unusual_positioning": bool(p36 is not None and (p36 <= lo or p36 >= hi)),
                "calculation_version": CALCULATION_VERSION, "calculation_timestamp": now,
            })
    return rows


def consecutive_direction(changes: list[float | None]) -> tuple[str | None, int]:
    """Direction of the latest weekly change and how many consecutive reports it has persisted."""
    seq = [c for c in changes if c is not None]
    if not seq or seq[-1] == 0:
        return None, 0
    sign = 1 if seq[-1] > 0 else -1
    n = 0
    for c in reversed(seq):
        if c * sign > 0:
            n += 1
        else:
            break
    return ("increasing" if sign > 0 else "decreasing"), n


def compare_participants(latest: dict[str, dict[str, Any]], family: str) -> list[dict[str, Any]]:
    """Within-family comparisons of the latest metrics (never across report families)."""
    out = []
    for a, b in COMPARISONS.get(family, []):
        ma, mb = latest.get(a), latest.get(b)
        if not ma or not mb or ma.get("net_position") is None or mb.get("net_position") is None:
            continue
        da, db = direction(ma["net_position"]), direction(mb["net_position"])
        ca, cb = ma.get("weekly_net_change"), mb.get("weekly_net_change")
        relation = "opposed" if {da, db} == {"net_long", "net_short"} else "aligned" if da == db else "mixed"
        flow = None
        if ca is not None and cb is not None and ca != 0 and cb != 0:
            flow = "diverging_flows" if (ca > 0) != (cb > 0) else "same_direction_flows"
        pa, pb = ma.get("historical_percentile_36m"), mb.get("historical_percentile_36m")
        out.append({
            "family": family, "group_a": a, "group_b": b,
            "label": f"{CATEGORY_LABELS.get(a, a)} vs {CATEGORY_LABELS.get(b, b)}",
            "group_a_net": ma["net_position"], "group_b_net": mb["net_position"],
            "group_a_direction": da, "group_b_direction": db, "positioning_relation": relation,
            "group_a_weekly_change": ca, "group_b_weekly_change": cb, "flow_relation": flow,
            "group_a_percentile_36m": pa, "group_b_percentile_36m": pb,
            "percentile_gap": None if pa is None or pb is None else pa - pb,
        })
    return out
