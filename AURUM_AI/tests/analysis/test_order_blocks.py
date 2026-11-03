from dataclasses import replace

from aurum.analysis.config import OrderBlockConfig
from aurum.analysis.technical_engine import analyze_series
from ta_helpers import series, small_cfg

# swing high at 10.5 (idx 2), a bearish candle (idx 5), then a strong bullish displacement that closes above it
BASE = [(10.0, 10.2, 9.8, 10.1), (10.1, 10.3, 9.9, 10.2), (10.2, 10.5, 10.0, 10.3), (10.3, 10.35, 9.9, 10.0),
        (10.0, 10.1, 9.7, 9.8), (9.8, 9.9, 9.5, 9.6),
        (9.6, 11.2, 9.55, 11.1),                       # displacement: breaks 10.5 by close
        (11.1, 11.3, 10.9, 11.2), (11.2, 11.4, 11.0, 11.3)]


def _cfg(**ob):
    return replace(small_cfg(), order_blocks=OrderBlockConfig(**ob))


def _bull(a):
    return [o for o in a.order_blocks if o.direction == "bullish"]


def test_bullish_order_block_is_last_bearish_candle_before_displacement():
    a = analyze_series(series(BASE), _cfg())
    ob = _bull(a)[0]
    assert ob.source_index == 5 and ob.source_time == a.first_candle.replace(hour=5)
    assert ob.detected_index == 6
    assert ob.detected_at >= ob.source_time and ob.detected_at == a.first_candle.replace(hour=7)
    assert (ob.initial_bottom, ob.initial_top) == (9.5, 9.9) and ob.state == "fresh"
    assert ob.break_event["type"] in ("BOS", "CHOCH") and any("Last bearish candle" in x for x in ob.explanation)


def test_zone_modes():
    assert (_bull(analyze_series(series(BASE), _cfg(zone_mode="body")))[0].initial_bottom,
            _bull(analyze_series(series(BASE), _cfg(zone_mode="body")))[0].initial_top) == (9.6, 9.8)
    ob = _bull(analyze_series(series(BASE), _cfg(zone_mode="open_extreme")))[0]
    assert (ob.initial_bottom, ob.initial_top) == (9.5, 9.8)


def test_mitigation_modes_and_invalidation():
    touch = BASE + [(11.0, 11.0, 9.85, 10.5)]             # wicks 0.05 into the 9.5-9.9 zone (12.5%)
    half = BASE + [(11.0, 11.0, 9.65, 10.5)]              # 62.5% into the zone
    assert _bull(analyze_series(series(touch), _cfg(mitigation_mode="first_touch")))[0].state == "mitigated"
    assert _bull(analyze_series(series(touch), _cfg(mitigation_mode="fifty_percent")))[0].state == "partially_mitigated"
    ob = _bull(analyze_series(series(half), _cfg(mitigation_mode="fifty_percent")))[0]
    assert ob.state == "mitigated" and ob.mitigation_pct == 62.5 and ob.current_top == 9.65
    assert _bull(analyze_series(series(half), _cfg(mitigation_mode="full")))[0].state == "partially_mitigated"
    broken = BASE + [(11.0, 11.0, 9.3, 9.4)]               # closes below the distal edge
    assert _bull(analyze_series(series(broken), _cfg()))[0].state == "invalidated"


def test_requires_displacement_by_default():
    from aurum.analysis.config import DisplacementConfig
    strict = DisplacementConfig(min_body_atr=50, sequence_min_body_atr=50)      # nothing qualifies as displacement
    no_disp = replace(_cfg(), displacement=strict)
    assert _bull(analyze_series(series(BASE), no_disp)) == []
    relaxed = replace(no_disp, order_blocks=OrderBlockConfig(require_displacement=False))
    assert _bull(analyze_series(series(BASE), relaxed))
