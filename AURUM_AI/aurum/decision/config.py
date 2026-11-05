"""Module 05 configuration: the defaults below, overridden by config/ai_engine.yaml. Unknown keys are rejected."""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from aurum.config import PROJECT_ROOT

CONFIG_PATH = PROJECT_ROOT / "config" / "ai_engine.yaml"

DEFAULTS: dict[str, Any] = {
    "entry_timeframes": ["H1", "M15"],
    "htf_timeframes": ["D1", "H4"],
    "templates": {
        "order_block_continuation": {"enabled": True, "require_alignment": True, "expiry_candles": 12,
                                     "max_break_age_candles": 40},
        "fair_value_gap_retracement": {"enabled": True, "require_alignment": True, "expiry_candles": 10, "max_distance_atr": 1.0},
        "premium_discount_ote": {"enabled": True, "require_alignment": True, "expiry_candles": 10},
        "bos_continuation": {"enabled": True, "require_alignment": True, "expiry_candles": 8, "max_break_age_candles": 12,
                             "retest_zone_atr": 0.25, "stop_atr": 1.0},
        "choch_reversal": {"enabled": True, "require_alignment": False, "expiry_candles": 8, "max_choch_age_candles": 16},
        "session_breakout": {"enabled": True, "require_alignment": False, "expiry_candles": 8, "timeframes": ["M15"],
                             "sessions": ["london", "new_york"], "max_break_age_candles": 12, "retest_zone_atr": 0.25, "stop_atr": 1.0},
        "liquidity_sweep_reversal": {"enabled": True, "require_alignment": False, "expiry_candles": 8,
                                     "max_sweep_age_candles": 16},
        "range_edge_rejection": {"enabled": True, "require_alignment": False, "expiry_candles": 12, "min_zone_score": 60.0,
                                 "max_distance_atr": 0.5, "expanding_regimes": ["HIGH", "VERY_HIGH"]},
    },
    "levels": {"stop_buffer_atr": 0.15, "max_stop_atr": 4.0, "target_level_types": ["previous_day_high", "previous_day_low",
                                                                                    "previous_week_high", "previous_week_low"]},
    "sizing": {"default_risk_pct": 0.5},
    "regime": {"timeframe": "H1", "confirm_timeframe": "H4", "breakout_max_age_candles": 5, "reversal_max_age_candles": 8, "expansion_regimes": ["HIGH", "VERY_HIGH"],
               "contraction_regimes": ["LOW", "VERY_LOW"]},
    "bias": {"timeframes": ["D1", "H4", "H1", "M15", "M5"], "weights": {"D1": 5.0, "H4": 4.0, "H1": 3.0, "M15": 2.0, "M5": 1.0},
             "min_agreement": 0.6, "min_known_share": 0.5},
    "decision": {"min_grades": ["A", "B"], "strong_grade": "A", "insufficient_after_missing": 2,
                 "ignored_warnings": ["account_risk"]},
    "gates": {
        "min_rr": 2.0,
        "stale_candle_multiple": 3.0,
        "max_invalid_candles": 0,
        "quality_lookback_candles": 100,
        "rollover_buffer_minutes": 15,
        "weekly_close_buffer_minutes": 60,
        "session_filters": {},                # {template: {symbol: [blocked session names]}}
        "max_spread_multiple": 3.0,
        "min_stop_spread_multiple": 3.0,
        "blocked_volatility_regimes": ["VERY_HIGH"],
        "correlation_groups": [["EURUSD", "GBPUSD"]],
        "account_stale_minutes": 120,
        "unknown_event_confidence_cap": 60.0,
        "cot_oppose_warns": True,
    },
    "score": {
        "weights": {"setup_quality": 30.0, "htf_alignment": 20.0, "session_fit": 15.0, "liquidity_context": 15.0,
                    "cot_agreement": 10.0, "learning": 10.0},
        "data_quality_deduction": 5.0,
        "grades": {"A": 75.0, "B": 60.0, "C": 45.0, "D": 25.0},
        "min_outcomes_for_calibration": 100,
    },
    "ai_review": {"prompt_version": "1", "unavailable_confidence_cap": 70.0, "min_grade": "C", "max_retries": 0},
    "outcomes": {"max_hold_candles": 96, "candle_timeframe": "M5"},
    "backtest": {"step_timeframe": "H1", "slippage_r": 0.05, "commission_r": 0.0, "split_date": None, "small_sample": 30,
                 "account_start": 10000.0},
}


class DecisionConfigError(ValueError):
    pass


def _merge(base: dict[str, Any], over: dict[str, Any], path: str = "") -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in over.items():
        where = f"{path}{key}"
        if key not in base:
            raise DecisionConfigError(f"Unknown key in decision config: {where}")
        # Free-form maps (session_filters) are empty in the defaults and taken as given.
        if isinstance(base[key], dict) and base[key]:
            if not isinstance(value, dict):
                raise DecisionConfigError(f"{where} must be a mapping")
            out[key] = _merge(base[key], value, f"{where}.")
        else:
            out[key] = copy.deepcopy(value)
    return out


@dataclass(frozen=True)
class DecisionConfig:
    data: dict[str, Any]
    fingerprint: str

    def __getattr__(self, name: str) -> Any:
        try:
            return self.data[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def template(self, name: str) -> dict[str, Any]:
        return self.data["templates"][name]


def build_config(overrides: dict[str, Any] | None = None) -> DecisionConfig:
    data = _merge(DEFAULTS, overrides or {})
    if data["gates"]["min_rr"] <= 0:
        raise DecisionConfigError("gates.min_rr must be positive")
    if sum(data["score"]["weights"].values()) != 100.0:
        raise DecisionConfigError("score.weights must add up to 100")
    fingerprint = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()[:16]
    return DecisionConfig(data, fingerprint)


def with_overrides(cfg: DecisionConfig, overrides: dict[str, Any] | None) -> DecisionConfig:
    """``cfg`` with ``overrides`` on top, validated like a config file."""
    return build_config(_merge(cfg.data, overrides)) if overrides else cfg


def load_decision_config(path: Path | None = None) -> DecisionConfig:
    p = path or CONFIG_PATH
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else None
    return build_config(raw or {})
