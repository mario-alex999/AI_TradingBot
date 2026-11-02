from dataclasses import replace

from aurum.analysis.config import StructureConfig
from aurum.analysis.technical_engine import analyze_series
from ta_helpers import hl, path, series, small_cfg

# up-leg, pullback, BOS up, then a sell-off through the last higher low (CHoCH down)
SEQ = [10, 11, 12, 11.5, 11.2, 12.5, 13.5, 13, 12.6, 14, 15, 14.4, 14.0, 13.0, 12.0, 11.0]


def _events(a, scope="internal"):
    return [(e.event_type, e.direction) for e in a.structure_events if e.scope == scope]


def test_bos_then_choch_sequence():
    a = analyze_series(series(path(SEQ)), small_cfg())
    ev = _events(a)
    assert ev[0] == ("BOS", "bullish")
    assert ("CHOCH", "bearish") in ev
    first = a.structure_events[0]
    assert first.initial and first.previous_bias == "ranging"
    choch = next(e for e in a.structure_events if e.event_type == "CHOCH")
    assert choch.previous_bias == "bullish" and choch.new_bias == "bearish"
    assert choch.break_index > choch.broken_swing_index
    assert choch.confirmed_at == a.structure_events[a.structure_events.index(choch)].confirmed_at


def test_close_confirmation_ignores_wick_breaks_but_wick_mode_counts_them():
    highs = [1, 2, 3, 2, 1.5, 3.4, 2]           # candle 5 wicks above the swing high at 3 but closes below it
    lows = [0.5, 1.5, 2.5, 1.5, 1, 2.2, 1.5]
    rows = hl(highs, lows)
    rows[5] = (2.4, 3.4, 2.2, 2.6)
    close_cfg = small_cfg()
    a = analyze_series(series(rows), close_cfg)
    assert not [e for e in a.structure_events if e.scope == "internal" and e.direction == "bullish"]
    wick_cfg = replace(close_cfg, structure=StructureConfig(break_mode="wick"))
    b = analyze_series(series(rows), wick_cfg)
    assert [e for e in b.structure_events if e.scope == "internal" and e.direction == "bullish"]


def test_atr_buffer_filters_marginal_breaks():
    rows = path([10, 11, 12, 11.5, 11.2, 12.15, 11.8, 11.6])   # closes just 0.05 above the 12.1 high
    plain = analyze_series(series(rows), small_cfg())
    assert any(e.direction == "bullish" for e in plain.structure_events)
    buffered = analyze_series(series(rows), replace(small_cfg(), structure=StructureConfig(break_buffer_atr=0.5)))
    assert not any(e.direction == "bullish" and e.broken_level == 12.1 for e in buffered.structure_events)


def test_provisional_breakout_is_not_confirmed_and_choch_status_is_potential():
    rows = path(SEQ)
    a = analyze_series(series(rows, last_incomplete=True, include_provisional=True), small_cfg())
    last = a.structure_events[-1]
    if last.break_index == a.candles_used - 1:
        assert last.confirmed is False
    st = a.structure["internal"].choch
    assert st.status in ("none", "potential", "confirmed") and st.explanation


def test_choch_status_confirmed_after_reversal():
    a = analyze_series(series(path(SEQ)), small_cfg())
    st = a.structure["internal"].choch
    assert st.status == "confirmed" and st.direction == "bearish"
