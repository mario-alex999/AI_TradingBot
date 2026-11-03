from aurum.analysis.config import LiquidityConfig, SwingConfig, SwingWindow
from aurum.analysis.data import atr
from aurum.analysis.market_structure.swing_detection import detect_swings
from aurum.analysis.smart_money.liquidity import detect_liquidity
from ta_helpers import hl, series

HIGHS = [1, 2, 3.0, 2, 1.5, 2, 3.0, 2, 1.5]
LOWS = [0.5, 1.5, 2.5, 1.5, 1.0, 1.5, 2.5, 1.5, 1.0]


def _run(rows, cfg=None):
    s = series(rows)
    a = atr(s)
    sw = detect_swings(s, a, SwingWindow(1, 1), "internal", SwingConfig())
    return detect_liquidity(s, a, sw, [], cfg or LiquidityConfig(include_previous_day=False))


def test_equal_highs_form_buy_side_pool():
    pools, events = _run(hl(HIGHS, LOWS))
    eqh = [p for p in pools if p.kind == "equal_highs"]
    assert len(eqh) == 1 and eqh[0].side == "buy_side" and eqh[0].level == 3.0 and eqh[0].touches == 2
    assert eqh[0].state == "active" and events == []


def test_sweep_wick_through_and_close_back_inside():
    rows = hl(HIGHS, LOWS) + [(2.0, 3.3, 1.9, 2.4)]
    pools, events = _run(rows)
    ev = [e for e in events if e.pool_kind == "equal_highs"][0]
    assert ev.event == "sweep" and ev.implication == "bearish" and ev.extreme == 3.3
    assert [p for p in pools if p.kind == "equal_highs"][0].state == "swept"


def test_break_closes_beyond_the_pool():
    rows = hl(HIGHS, LOWS) + [(2.0, 3.5, 1.9, 3.4)]
    _, events = _run(rows)
    ev = [e for e in events if e.pool_kind == "equal_highs"][0]
    assert ev.event == "break" and ev.implication == "bullish"


def test_previous_day_high_low_only_after_the_day_ends():
    rows = [(1, 1.2 + (i % 5) * 0.1, 0.9 - (i % 3) * 0.05, 1.05) for i in range(30)]
    s = series(rows)
    pools, _ = detect_liquidity(s, atr(s), [], [], LiquidityConfig())
    pdh = [p for p in pools if p.kind == "previous_day_high"]
    assert len(pdh) == 1 and pdh[0].created_at == s.close_time(23) == s.time(24)
