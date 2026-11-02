"""Data-quality and context filters that reduce confidence in a score (never increase it)."""
from __future__ import annotations

from aurum.analysis.models.analysis_result import ScoreComponent, TimeframeAnalysis


def quality_penalties(a: TimeframeAnalysis, min_candles: int) -> tuple[list[ScoreComponent], list[str]]:
    pen: list[ScoreComponent] = []
    warn: list[str] = []
    dq = a.data_quality
    if a.candles_used < min_candles * 2:
        pen.append(ScoreComponent("limited_history", "neutral", -5, 5,
                                  f"Only {a.candles_used} candles; structure needs more history to be reliable."))
    if a.provisional:
        pen.append(ScoreComponent("provisional_candle", "neutral", -5, 5,
                                  "The last candle is still forming; anything it confirms may change."))
    excluded = dq.get("excluded_candles", 0)
    if excluded and excluded / max(a.candles_used + excluded, 1) > 0.02:
        pen.append(ScoreComponent("excluded_candles", "neutral", -10, 10,
                                  f"{excluded} candles were excluded as invalid or forming."))
    gaps = dq.get("unexpected_gaps", 0)
    if gaps:
        pen.append(ScoreComponent("unexpected_gaps", "neutral", -min(10, 2 * gaps), 10,
                                  f"{gaps} unexpected gap(s) in the candle history."))
    ext, intl = a.structure.get("external"), a.structure.get("internal")
    if ext and intl and ext.bias in ("bullish", "bearish") and intl.bias in ("bullish", "bearish") and ext.bias != intl.bias:
        warn.append(f"Internal structure ({intl.bias}) disagrees with external structure ({ext.bias}): "
                    "possible pullback or developing reversal.")
    if ext and ext.state == "insufficient_data":
        warn.append("External structure has too few confirmed swings for a reliable bias.")
    return pen, warn
