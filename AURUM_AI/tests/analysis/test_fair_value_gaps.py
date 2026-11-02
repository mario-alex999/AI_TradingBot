from dataclasses import replace

from aurum.analysis.config import FvgConfig
from aurum.analysis.data import atr
from aurum.analysis.smart_money.fair_value_gaps import detect_fvgs
from ta_helpers import series

BULL = [(10, 10.2, 9.9, 10.1), (10.1, 11.0, 10.05, 10.9), (10.9, 11.2, 10.5, 11.1), (11.1, 11.3, 11.0, 11.2)]
BEAR = [(11, 11.1, 10.8, 10.9), (10.9, 10.95, 10.0, 10.1), (10.1, 10.5, 9.9, 10.0), (10.0, 10.1, 9.8, 9.9)]


def _gaps(rows, **kw):
    s = series(rows, **{k: v for k, v in kw.items() if k in ("last_incomplete", "include_provisional")})
    return detect_fvgs(s, atr(s), [], kw.get("cfg"))


def test_bullish_fvg_boundaries():
    g = _gaps(BULL)[0]
    assert (g.direction, g.lower, g.upper) == ("bullish", 10.2, 10.5)
    assert g.detected_index == 2 and g.size == 10.5 - 10.2 and g.state == "unfilled"
    assert len(g.candle_times) == 3 and g.size_atr_pct > 0


def test_bearish_fvg_boundaries():
    g = [x for x in _gaps(BEAR) if x.direction == "bearish"][0]
    assert (g.lower, g.upper) == (10.5, 10.8)


def test_minimum_size_filters_small_gaps():
    assert _gaps(BULL, cfg=FvgConfig(min_size_atr=5.0)) == []


def test_partial_full_fill_and_invalidation():
    partial = BULL + [(11.2, 11.3, 10.4, 11.0)]
    g = _gaps(partial)[0]
    assert g.state == "partially_filled" and round(g.fill_pct, 1) == 33.3 and g.current_upper == 10.4
    full = BULL + [(11.2, 11.3, 10.15, 10.6)]
    assert _gaps(full)[0].state == "fully_filled"
    inval = BULL + [(11.2, 11.3, 10.0, 10.1)]
    assert _gaps(inval)[0].state == "invalidated"
    keep = _gaps(inval, cfg=FvgConfig(invalidate_on_close_beyond=False))[0]
    assert keep.state == "fully_filled"


def test_not_detected_before_third_candle_closes():
    rows = BULL[:3]
    assert _gaps(rows, last_incomplete=True, include_provisional=True) == []
    assert _gaps(rows)[0].direction == "bullish"
