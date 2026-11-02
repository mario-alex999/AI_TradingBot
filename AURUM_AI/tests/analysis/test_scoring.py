from aurum.analysis.config import AnalysisConfig
from aurum.analysis.models.analysis_result import ScoreComponent
from aurum.analysis.scoring.technical_scoring import add_cot_component, combine
from aurum.analysis.technical_engine import analyze_series
from ta_helpers import random_rows, series, small_cfg

CFG = AnalysisConfig()
ACTIVE = {"external_structure", "internal_structure", "recent_break", "order_block", "fair_value_gap",
          "premium_discount", "ote", "liquidity_sweep", "support_resistance"}


def comp(name, d, pts=1.0):
    return ScoreComponent(name, d, pts, 1, f"{name} {d}")


def test_weights_totals_and_grade():
    s = combine([comp("external_structure", "bullish"), comp("internal_structure", "bullish"),
                 comp("premium_discount", "bullish"), comp("order_block", "bullish"), comp("recent_break", "bullish"),
                 comp("liquidity_sweep", "bullish"), comp("ote", "bullish"), comp("fair_value_gap", "bearish")],
                CFG, ACTIVE, [], [])
    available = sum(CFG.scoring.weights[k] for k in ACTIVE)                # 94 points on offer
    assert s.bullish_score == round(80 / available * 100, 1) and s.bearish_score == round(8 / available * 100, 1)
    assert s.bias == "bullish" and s.grade == "A"
    assert all(c.max_points > 0 for c in s.components) and "Main factors" in s.explanation


def test_close_contest_is_neutral_and_penalties_reduce_score():
    s = combine([comp("external_structure", "bullish"), comp("internal_structure", "bearish", 1.5)], CFG, ACTIVE, [], [])
    assert s.bias == "neutral" and s.grade == "none"
    p = [ScoreComponent("provisional_candle", "neutral", -5, 5, "forming")]
    base = combine([comp("external_structure", "bullish"), comp("premium_discount", "bullish")], CFG, ACTIVE, [], [])
    pen = combine([comp("external_structure", "bullish"), comp("premium_discount", "bullish")], CFG, ACTIVE, p, [])
    assert pen.score == base.score - 5


def test_cot_component_only_when_real_data_is_available():
    comps, active = add_cot_component([], set(ACTIVE), {"available": False}, CFG)
    assert comps == [] and "cot" not in active
    comps, active = add_cot_component([], set(ACTIVE), {"available": True, "stance": "bullish", "confidence": "high"}, CFG)
    assert comps[0].name == "cot" and comps[0].direction == "bullish" and "cot" in active
    s = combine(comps, CFG, active, [], [])
    assert s.components[0].max_points == CFG.scoring.cot_weight_when_available


def test_engine_score_is_explainable():
    a = analyze_series(series(random_rows(300)), small_cfg())
    assert a.score is not None and a.score.explanation
    for c in a.score.components:
        assert c.explanation and c.direction in ("bullish", "bearish")
