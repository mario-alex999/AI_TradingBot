from aurum.analysis.config import SwingConfig, SwingWindow
from aurum.analysis.market_structure.swing_detection import detect_swings
from ta_helpers import atr_of, hl, series

W1 = SwingWindow(1, 1)


def _swings(rows, window=W1, **kw):
    s = series(rows, **kw)
    return s, detect_swings(s, atr_of(s), window, "internal", SwingConfig())


def test_valid_swing_high_and_low_with_confirmation():
    s, sw = _swings(hl([1, 2, 3, 2, 1, 2, 3], [0.5, 1.5, 2.5, 1.5, 0.5, 1.5, 2.5]))
    high = next(x for x in sw if x.kind == "high")
    low = next(x for x in sw if x.kind == "low")
    assert (high.index, high.price, high.confirmed_index) == (2, 3, 3)
    assert high.confirmed_at == s.close_time(3) and high.timestamp == s.time(2)
    assert (low.index, low.price, low.confirmed_index) == (4, 0.5, 5)


def test_consecutive_swing_highs_and_lows():
    _, sw = _swings(hl([2, 4, 3, 5, 3, 6, 3], [1, 3, 2, 4, 2, 5, 2]))
    assert [x.index for x in sw if x.kind == "high"] == [1, 3, 5]
    _, sw = _swings(hl([6, 4, 5, 3, 5, 2, 5], [5, 3, 4, 2, 4, 1, 4]))
    assert [x.index for x in sw if x.kind == "low"] == [1, 3, 5]


def test_equal_highs_resolve_to_first_candle_and_equal_lows_too():
    _, sw = _swings(hl([1, 3, 3, 1, 2], [0.5, 2, 2, 0.5, 1]))
    assert [x.index for x in sw if x.kind == "high"] == [1]
    _, sw = _swings(hl([3, 1.5, 1.5, 3, 2], [2, 1, 1, 2, 1.5]))
    assert [x.index for x in sw if x.kind == "low"] == [1]


def test_insufficient_candles_give_no_swings():
    _, sw = _swings(hl([1, 2], [0, 1]))
    assert sw == []


def test_swing_needs_right_side_candles():
    _, sw = _swings(hl([1, 2, 3], [0, 1, 2]))     # last candle could be a high but nothing confirms it
    assert sw == []


def test_unfinished_candle_marks_swing_provisional_or_is_excluded():
    rows = hl([1, 2, 3, 2], [0.5, 1.5, 2.5, 1.5])
    _, kept = _swings(rows, last_incomplete=True, include_provisional=True)
    assert kept[0].provisional is True
    _, dropped = _swings(rows, last_incomplete=True)            # default: forming candle excluded -> unconfirmed
    assert dropped == []


def test_different_confirmation_windows():
    rows = hl([1, 2, 3, 2.5, 2.8, 2, 1, 1.5], [0.5, 1.5, 2.5, 2, 2.3, 1.5, 0.5, 1])
    _, w1 = _swings(rows, SwingWindow(1, 1))
    _, w2 = _swings(rows, SwingWindow(2, 2))
    assert {x.index for x in w1 if x.kind == "high"} == {2, 4}
    assert {x.index for x in w2 if x.kind == "high"} == {2}
    assert all(x.confirmed_index == x.index + 2 for x in w2)
