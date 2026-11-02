"""Single-timeframe technical analysis: runs every detector on one validated CandleSeries.

Pure and deterministic: the same candles and configuration always give the same result, and no detector reads
a candle after the one that confirms what it reports (see tests/analysis/test_no_lookahead.py).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from aurum.analysis.config import ENGINE_VERSION, AnalysisConfig
from aurum.analysis.data import atr as compute_atr
from aurum.analysis.market_structure.bos_choch import walk_breaks
from aurum.analysis.market_structure.displacement import detect_displacements
from aurum.analysis.market_structure.structure_engine import build_state, label_swings
from aurum.analysis.market_structure.swing_detection import detect_swings
from aurum.analysis.models.analysis_result import TimeframeAnalysis
from aurum.analysis.models.candle import CandleSeries
from aurum.analysis.scoring.technical_scoring import score_timeframe
from aurum.analysis.smart_money.balanced_price_ranges import detect_bprs
from aurum.analysis.smart_money.fair_value_gaps import detect_fvgs
from aurum.analysis.smart_money.inducement import detect_inducements
from aurum.analysis.smart_money.liquidity import detect_liquidity
from aurum.analysis.smart_money.order_blocks import detect_order_blocks
from aurum.analysis.smart_money.ote import ote_zone
from aurum.analysis.smart_money.premium_discount import premium_discount
from aurum.analysis.support_resistance.zone_detection import detect_zones


def data_quality_summary(series: CandleSeries) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for i in series.quality_issues:
        counts[i["issue_type"]] = counts.get(i["issue_type"], 0) + 1
    return {"source": series.source, "issue_counts": counts, "excluded_candles": len(series.excluded_rows),
            "excluded": series.excluded_rows[:20], "unexpected_gaps": counts.get("missing_candles", 0),
            "invalid_candles": sum(1 for r in series.excluded_rows if str(r.get("reason", "")).startswith("invalid")),
            "provisional_last_candle": series.provisional}


def analyze_series(series: CandleSeries, cfg: AnalysisConfig | None = None,
                   cot: dict[str, Any] | None = None) -> TimeframeAnalysis:
    cfg = cfg or AnalysisConfig()
    n = len(series)
    now = datetime.now(timezone.utc)
    base = dict(symbol=series.symbol, timeframe=series.timeframe,
                as_of=series.close_time(n - 1) if n else now, generated_at=now, engine_version=ENGINE_VERSION,
                config_fingerprint=cfg.fingerprint, provisional=series.provisional, candles_used=n,
                first_candle=series.time(0) if n else None, last_candle=series.time(n - 1) if n else None,
                current_price=float(series.close[-1]) if n else None, data_quality=data_quality_summary(series))
    if n == 0:
        return TimeframeAnalysis(**base, atr=None, status="no_data", messages=["No valid candles to analyse."])
    atr = compute_atr(series, cfg.atr_period)
    if n < cfg.min_candles:
        return TimeframeAnalysis(**base, atr=float(atr[-1]), status="insufficient_data",
                                 messages=[f"{n} valid candles; at least {cfg.min_candles} are needed."])

    displacements = detect_displacements(series, atr, cfg.displacement)
    internal = detect_swings(series, atr, cfg.swings.internal, "internal", cfg.swings)
    external = detect_swings(series, atr, cfg.swings.external, "external", cfg.swings)
    label_swings(internal, atr, cfg.swings)
    label_swings(external, atr, cfg.swings)
    w_int = walk_breaks(series, internal, atr, "internal", cfg.structure, displacements, cfg.displacement)
    w_ext = walk_breaks(series, external, atr, "external", cfg.structure, displacements, cfg.displacement)
    states = {"internal": build_state(series, internal, w_int, "internal", cfg.structure),
              "external": build_state(series, external, w_ext, "external", cfg.structure, w_int.events)}
    events = sorted(w_int.events + w_ext.events, key=lambda e: (e.break_index, e.scope))

    obs = detect_order_blocks(series, atr, events, displacements, cfg.order_blocks, cfg.displacement.sequence_length)
    gaps = detect_fvgs(series, atr, displacements, cfg.fvg)
    bprs = detect_bprs(series, gaps, cfg.bpr)
    pools, liq_events = detect_liquidity(series, atr, internal, external, cfg.liquidity)
    inducements = detect_inducements(series, w_ext.events, internal, obs, gaps)
    price = float(series.close[-1])
    pd_ = premium_discount(w_ext.last_high, w_ext.last_low, price, cfg.premium_discount)
    ote = ote_zone(pd_, states["external"].bias, obs, gaps, cfg.premium_discount)
    zones = detect_zones(series, atr, internal + external, cfg.support_resistance)

    result = TimeframeAnalysis(
        **base, atr=float(atr[-1]), swings=sorted(internal + external, key=lambda s: (s.confirmed_index, s.scope)),
        structure=states, structure_events=events, displacements=displacements, order_blocks=obs,
        fair_value_gaps=gaps, balanced_price_ranges=bprs, liquidity_pools=pools, liquidity_events=liq_events,
        inducements=inducements, premium_discount=pd_, ote=ote, support_resistance=zones)
    if series.provisional:
        result.messages.append("The last candle is still forming: results that depend on it are provisional.")
    result.score = score_timeframe(result, cfg, cot)
    return result
