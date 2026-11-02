from aurum.analysis.technical_engine import analyze_series
from ta_helpers import path, series, small_cfg

# three-candle pullbacks so both internal (1/1) and external (2/2) swings form
UPTREND = [10, 11, 12, 11.6, 11.3, 11.1, 12.5, 13.5, 14, 13.6, 13.3, 13.1, 14.5, 15.5, 16, 15.6, 15.3, 15.1,
           16.5, 17.5, 18, 17.6, 17.3, 17.1, 18.5]


def test_bullish_structure_labels_and_bias():
    a = analyze_series(series(path(UPTREND)), small_cfg())
    ext = a.structure["external"]
    labels = [s.label for s in a.swings if s.scope == "internal" and s.label]
    assert "HH" in labels and "HL" in labels and "LL" not in labels
    assert ext.bias == "bullish" and ext.state in ("trending", "transitioning")
    assert 0 < ext.confidence <= 1 and ext.explanation


def test_bearish_structure_is_the_mirror():
    a = analyze_series(series(path([30 - x for x in UPTREND])), small_cfg())
    assert a.structure["external"].bias == "bearish"


def test_flat_market_is_not_forced_into_a_trend():
    flat = [10, 10.4, 10, 10.4, 10, 10.4, 10, 10.4, 10, 10.4, 10, 10.4, 10]
    a = analyze_series(series(path(flat, wick=0.05)), small_cfg())
    for st in a.structure.values():
        assert st.bias == "ranging" and st.confidence == 0.0


def test_insufficient_data_status():
    a = analyze_series(series(path([10, 11, 12])), small_cfg())
    assert a.status == "insufficient_data"
    short = analyze_series(series(path([10, 11, 12, 11, 10, 11])), small_cfg())
    assert short.structure["external"].state == "insufficient_data"
    assert short.structure["external"].confidence <= 0.3
