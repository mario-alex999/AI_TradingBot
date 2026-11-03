from datetime import datetime, timezone

from aurum.analysis.models.swing import Swing
from aurum.analysis.smart_money.premium_discount import fib_levels, premium_discount

T = datetime(2026, 3, 3, tzinfo=timezone.utc)


def sw(kind, price, idx):
    return Swing(kind, price, idx, T, idx + 2, T, "H1", "external", 3, 1.0)


def test_zones_and_equilibrium():
    hi, lo = sw("high", 120, 10), sw("low", 100, 5)
    assert premium_discount(hi, lo, 115).zone == "premium"
    assert premium_discount(hi, lo, 104).zone == "discount"
    assert premium_discount(hi, lo, 110.5).zone == "equilibrium"
    assert premium_discount(hi, lo, 125).zone == "above_range"
    pd_ = premium_discount(hi, lo, 104)
    assert pd_.equilibrium == 110 and pd_.position == 0.2 and pd_.leg_direction == "bullish"


def test_fib_levels_follow_leg_direction():
    up = fib_levels(120, 100, "bullish", (0.0, 0.5, 0.618, 1.0))
    assert up == {"0": 120, "0.5": 110, "0.618": 120 - 0.618 * 20, "1": 100}
    down = fib_levels(120, 100, "bearish", (0.0, 1.0))
    assert down == {"0": 100, "1": 120}


def test_missing_or_inverted_range_returns_none():
    assert premium_discount(None, sw("low", 1, 1), 1) is None
    assert premium_discount(sw("high", 1, 1), sw("low", 2, 2), 1.5) is None
