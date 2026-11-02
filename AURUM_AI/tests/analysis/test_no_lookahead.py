"""Non-repainting / no look-ahead: analysing candles 0..k-1 must report exactly the swings, structure events,
order blocks, FVGs and liquidity pools that the full-history analysis reports as known by candle k-1."""
import pytest

from aurum.analysis.config import AnalysisConfig
from aurum.analysis.data import truncate
from aurum.analysis.technical_engine import analyze_series
from ta_helpers import random_rows, series

CFG = AnalysisConfig(min_candles=30)


@pytest.fixture(scope="module")
def full():
    s = series(random_rows(500, seed=11))
    return s, analyze_series(s, CFG)


@pytest.mark.parametrize("k", [120, 233, 341, 499])
def test_prefix_analysis_matches_full_history(full, k):
    s, a = full
    p = analyze_series(truncate(s, k), CFG)

    sw = lambda res, lim: sorted((x.scope, x.kind, x.index, x.price, x.confirmed_index, x.label)
                                 for x in res.swings if x.confirmed_index < lim)
    assert sw(p, k) == sw(a, k)
    ev = lambda res, lim: [(e.scope, e.event_type, e.direction, e.broken_level, e.break_index, e.previous_bias)
                           for e in res.structure_events if e.break_index < lim]
    assert ev(p, k) == ev(a, k)
    ob = lambda res, lim: sorted((o.id, o.source_index, o.detected_index, o.initial_top, o.initial_bottom)
                                 for o in res.order_blocks if o.detected_index < lim)
    assert ob(p, k) == ob(a, k)
    fv = lambda res, lim: sorted((g.id, g.lower, g.upper) for g in res.fair_value_gaps if g.detected_index < lim)
    assert fv(p, k) == fv(a, k)
    lq = lambda res, lim: sorted((x.id, x.level, x.touches) for x in res.liquidity_pools if x.created_index < lim)
    assert lq(p, k) == lq(a, k)


def test_nothing_is_dated_before_it_could_be_known(full):
    s, a = full
    for x in a.swings:
        assert x.confirmed_at == s.close_time(x.confirmed_index) and x.confirmed_index >= x.index
    for e in a.structure_events:
        assert e.break_index > e.broken_swing_index and e.confirmed_at == s.close_time(e.break_index)
    for o in a.order_blocks:
        assert o.detected_index > o.source_index and o.detected_at == s.close_time(o.detected_index)
        if o.first_touch_at:
            assert o.first_touch_at > o.source_time and o.first_touch_at >= s.time(o.detected_index + 1)
    for g in a.fair_value_gaps:
        assert g.detected_at == s.close_time(g.detected_index)
        if g.partially_filled_at:
            assert g.partially_filled_at >= s.time(g.detected_index + 1)
    for p in a.liquidity_pools:
        if p.swept_at or p.broken_at:
            assert (p.swept_at or p.broken_at) >= s.time(p.created_index + 1)
