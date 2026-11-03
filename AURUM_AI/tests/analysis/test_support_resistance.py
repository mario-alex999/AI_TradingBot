from aurum.analysis.config import SupportResistanceConfig, SwingConfig, SwingWindow
from aurum.analysis.data import atr
from aurum.analysis.market_structure.swing_detection import detect_swings
from aurum.analysis.support_resistance.zone_detection import detect_zones
from ta_helpers import hl, series


def test_repeated_reactions_form_a_flip_zone():
    highs = [1.0, 2.0, 1.5, 1.2, 2.02, 1.4, 2.6, 3.0, 2.5, 2.1, 2.4, 2.0, 2.4, 2.8]
    lows = [h - 0.3 for h in highs]
    s = series(hl(highs, lows))
    a = atr(s)
    sw = detect_swings(s, a, SwingWindow(1, 1), "internal", SwingConfig())
    zones = detect_zones(s, a, sw, SupportResistanceConfig(cluster_tolerance_atr=0.3, zone_padding_atr=0.0))
    z = next(z for z in zones if z.bottom <= 2.0 <= z.top)
    assert z.touches >= 3 and z.swing_highs >= 2
    assert z.score > 0 and set(z.score_breakdown) == {"touches", "recency", "swing_strength", "role_flip"}


def test_single_touch_is_not_a_zone():
    s = series(hl([1, 2, 1, 3, 1], [0.5, 1.5, 0.5, 2.5, 0.5]))
    a = atr(s)
    sw = detect_swings(s, a, SwingWindow(1, 1), "internal", SwingConfig())
    assert detect_zones(s, a, sw, SupportResistanceConfig(cluster_tolerance_atr=0.01)) == []
