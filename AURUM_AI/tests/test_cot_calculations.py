"""Positioning metrics, classification, change detection and participant comparisons."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pandas as pd
import pytest

from aurum.cot import calculations as calc
from aurum.cot.config import AnalyticsConfig, ClassificationConfig
from aurum.cot.positioning import (change_driver, change_type, classification_score, classify, compare_participants,
                                   compute_metrics, consecutive_direction, direction)

C = ClassificationConfig()


def test_net_weekly_change_and_pct_oi():
    assert calc.net_position(120, 80) == 40 and calc.net_position(80, 120) == -40 and calc.net_position(5, 5) == 0
    assert calc.net_position(None, 5) is None
    assert calc.weekly_change(40, 25) == 15 and calc.weekly_change(40, None) is None
    assert calc.pct_of_open_interest(50, 200) == 25.0
    assert calc.pct_of_open_interest(50, 0) is None and calc.pct_of_open_interest(50, None) is None
    assert calc.pct_of_open_interest(50, -10) is None


def test_percentile_mid_rank_definition():
    assert calc.percentile_rank([1, 2, 3, 4], 4) == pytest.approx(87.5)     # (3 + 0.5) / 4
    assert calc.percentile_rank([1, 2, 3, 4], 1) == pytest.approx(12.5)
    assert calc.percentile_rank([5, 5, 5], 5) == 50.0
    assert calc.percentile_rank([], 5) is None


def test_cot_index_and_degenerate_range():
    assert calc.cot_index([10, 20, 30], 30) == 100.0
    assert calc.cot_index([10, 20, 30], 10) == 0.0
    assert calc.cot_index([10, 20, 30], 20) == 50.0
    assert calc.cot_index([7, 7, 7], 7) is None                              # max == min -> no division by zero


def test_z_score_and_zero_std():
    assert calc.z_score([1, 2, 3, 4, 5], 5) == pytest.approx((5 - 3) / (2 ** 0.5))
    assert calc.z_score([4, 4, 4], 4) is None
    assert calc.z_score([], 1) is None


def test_trailing_window_never_looks_forward_and_requires_history():
    dates = [date(2024, 1, 2) + timedelta(weeks=i) for i in range(60)]
    vals = list(range(60))
    w = calc.trailing_window(dates, vals, 30, 52)
    assert max(w) == 30 and len(w) == 31                                    # only values up to index 30
    vals_future_changed = vals[:31] + [10_000] * 29
    assert calc.windowed_stat(dates, vals, 30, 52, 26, calc.percentile_rank) == \
        calc.windowed_stat(dates, vals_future_changed, 30, 52, 26, calc.percentile_rank)
    # 52-week lookback needs >= max(26, 0.8*52=42) observations
    assert calc.windowed_stat(dates, vals, 30, 52, 26, calc.cot_index) is None
    assert calc.windowed_stat(dates, vals, 45, 52, 26, calc.cot_index) == 100.0


def test_classification_thresholds_and_direction_rule():
    assert classify(95, 10, C) == "extremely_bullish"
    assert classify(95, -10, C) == "strong_bullish"                          # extreme needs net long
    assert classify(80, 1, C) == "strong_bullish"
    assert classify(65, 1, C) == "moderately_bullish"
    assert classify(50, 1, C) == "neutral"
    assert classify(35, 1, C) == "moderately_bearish"
    assert classify(20, 1, C) == "strong_bearish"
    assert classify(5, -1, C) == "extremely_bearish"
    assert classify(5, 3, C) == "strong_bearish"                             # extreme bearish needs net short
    assert classify(None, 1, C) is None
    assert classification_score(80, 60) == 70 and classification_score(None, 60) == 60
    assert classification_score(None, None) is None
    assert classify(95, -10, ClassificationConfig(require_direction_for_extremes=False)) == "extremely_bullish"


def test_direction_change_types_and_drivers():
    assert direction(5) == "net_long" and direction(-5) == "net_short" and direction(0) == "balanced"
    assert change_type(100, 150) == "net_long_accumulation"
    assert change_type(150, 100) == "net_long_reduction"
    assert change_type(-100, -150) == "net_short_accumulation"
    assert change_type(-150, -100) == "net_short_reduction"
    assert change_type(100, -20) == "reversal_to_net_short"
    assert change_type(-100, 20) == "reversal_to_net_long"
    assert change_type(0, 20) == "new_net_long" and change_type(0, -20) == "new_net_short"
    assert change_type(None, 5) is None and change_type(7, 7) == "unchanged"
    assert change_driver(500, 100) == "longs_added" and change_driver(-500, 100) == "longs_cut"
    assert change_driver(10, 400) == "shorts_added" and change_driver(10, -400) == "shorts_covered"
    assert change_driver(None, 1) is None and change_driver(0, 0) == "no_change"


def test_consecutive_direction():
    assert consecutive_direction([None, 5, -3, -2, -1]) == ("decreasing", 3)
    assert consecutive_direction([1, 2]) == ("increasing", 2)
    assert consecutive_direction([None]) == (None, 0)


def _positions(nets: list[int], cat: str = "leveraged_funds") -> pd.DataFrame:
    rows = []
    for i, n in enumerate(nets):
        d = date(2023, 1, 3) + timedelta(weeks=i)
        rows.append({"report_id": i + 1, "report_date": d,
                     "release_timestamp": datetime(d.year, d.month, d.day, 20, 30, tzinfo=timezone.utc) + timedelta(days=3),
                     "report_open_interest": 1000, "trader_category": cat, "long_positions": 500 + max(n, 0),
                     "short_positions": 500 + max(-n, 0), "spreading_positions": 10, "net_positions": n})
    return pd.DataFrame(rows)


def test_compute_metrics_pipeline():
    nets = [i * 10 + (i % 3) * 4 for i in range(200)] + [-500]      # rising, with small weekly variation
    rows = compute_metrics(_positions(nets), AnalyticsConfig())
    assert len(rows) == 201
    first, last, prev = rows[0], rows[-1], rows[-2]
    assert first["weekly_net_change"] is None and first["historical_percentile_36m"] is None
    assert prev["cot_index"] == 100.0 and prev["positioning_strength"] == "extremely_bullish"
    assert last["net_position"] == -500 and last["weekly_net_change"] == -500 - nets[-2]
    assert last["change_type"] == "reversal_to_net_short" and last["significant_change"]
    assert last["cot_index"] == 0.0 and last["positioning_direction"] == "net_short"
    assert last["net_position_pct_oi"] == -50.0
    assert last["historical_percentile_48m"] is not None                     # 201 obs >= ceil(0.8*208)=167
    assert rows[30]["historical_percentile_48m"] is None                     # too little history -> None
    assert rows[-1]["calculation_version"] == "1.0"


def test_constant_history_cannot_judge_significance():
    rows = compute_metrics(_positions([i * 10 for i in range(60)] + [-500]), AnalyticsConfig())
    assert rows[-1]["significant_change"] is False          # std of past changes = 0 -> not judged (no div by 0)


def test_compute_metrics_empty():
    assert compute_metrics(pd.DataFrame(), AnalyticsConfig()) == []


def test_participant_comparisons_within_family_only():
    latest = {"dealer": {"net_position": -100, "weekly_net_change": 20, "historical_percentile_36m": 90},
              "leveraged_funds": {"net_position": 80, "weekly_net_change": -15, "historical_percentile_36m": 10},
              "asset_manager": {"net_position": 50, "weekly_net_change": 5, "historical_percentile_36m": 60},
              "non_reportable": {"net_position": None}}
    out = compare_participants(latest, "tff")
    d = {(c["group_a"], c["group_b"]): c for c in out}
    assert d[("dealer", "leveraged_funds")]["positioning_relation"] == "opposed"
    assert d[("dealer", "leveraged_funds")]["flow_relation"] == "diverging_flows"
    assert d[("dealer", "leveraged_funds")]["percentile_gap"] == 80
    assert d[("asset_manager", "leveraged_funds")]["positioning_relation"] == "aligned"
    assert ("leveraged_funds", "non_reportable") not in d                    # missing value -> no comparison
    assert compare_participants(latest, "legacy") == []                      # never across families
