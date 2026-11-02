from datetime import datetime, timezone

from aurum.analysis.models.analysis_result import PremiumDiscount
from aurum.analysis.smart_money.ote import ote_zone

T = datetime(2026, 3, 3, tzinfo=timezone.utc)


def pdz(price, leg):
    return PremiumDiscount(120, 100, T, T, 110, price, (price - 100) / 20, "discount", leg)


def test_bullish_ote_is_62_to_79_percent_retracement():
    z = ote_zone(pdz(105, "bullish"), "bullish", [], [])
    assert round(z.lower, 4) == 104.2 and round(z.upper, 4) == 107.6 and round(z.sweet_spot, 4) == 105.9
    assert z.price_inside and "Agrees" in z.explanation


def test_bearish_ote_and_counter_bias_note():
    z = ote_zone(pdz(114, "bearish"), "bullish", [], [])
    assert round(z.lower, 4) == 112.4 and round(z.upper, 4) == 115.8 and z.price_inside
    assert "counter" in z.explanation


def test_price_outside_and_missing_range():
    assert not ote_zone(pdz(119, "bullish"), "bullish", [], []).price_inside
    assert ote_zone(None, "bullish", [], []) is None
