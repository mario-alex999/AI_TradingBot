"""Module 03 configuration.

Every threshold the engine uses lives here, with defaults that can be overridden in
``config/analysis.yaml`` (key by key). Nothing is symbol-specific: sizes are expressed in ATR multiples
so the same settings work for EURUSD, XAUUSD and US30.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

from aurum.config import PROJECT_ROOT

ENGINE_VERSION = "3.0.0"

BreakMode = Literal["close", "wick"]
ZoneMode = Literal["wick", "body", "open_extreme"]
MitigationMode = Literal["first_touch", "fifty_percent", "full"]


@dataclass(frozen=True)
class SwingWindow:
    left: int = 3
    right: int = 3


@dataclass(frozen=True)
class SwingConfig:
    internal: SwingWindow = SwingWindow(2, 2)
    external: SwingWindow = SwingWindow(5, 5)
    min_separation_bars: int = 1          # same-type swings closer than this: the earlier one is kept
    strength_lookback: int = 100          # left-side bars scanned to measure how dominant a swing is
    equal_tolerance_atr: float = 0.05     # |price difference| below this * ATR -> "equal" (EQH / EQL)


@dataclass(frozen=True)
class StructureConfig:
    break_mode: BreakMode = "close"       # "wick" is optional; close confirmation is the default
    break_buffer_atr: float = 0.0         # close must exceed the level by this * ATR
    min_break_price: float = 0.0          # absolute minimum excess (instrument price units)
    min_swings_for_trend: int = 2         # HH+HL (or LL+LH) pairs needed before a sequence counts as a trend


@dataclass(frozen=True)
class DisplacementConfig:
    min_body_atr: float = 1.0             # |close-open| / ATR
    min_range_atr: float = 1.2            # (high-low) / ATR
    min_body_to_range: float = 0.6        # body / range
    min_close_position: float = 0.7       # bullish: (close-low)/range >= x ; bearish: (high-close)/range >= x
    sequence_length: int = 3              # consecutive same-direction candles counted as one move
    sequence_min_body_atr: float = 1.5    # their combined body / ATR
    volume_expansion: float = 1.5         # volume / 20-bar average that counts as expansion (only if volume is usable)
    lookback_for_break: int = 5           # bars before a structure break searched for displacement


@dataclass(frozen=True)
class OrderBlockConfig:
    zone_mode: ZoneMode = "wick"
    require_structure_break: bool = True
    require_displacement: bool = True
    max_lookback: int = 10                # bars before the displacement searched for the opposite candle
    mitigation_mode: MitigationMode = "fifty_percent"
    expiry_bars: int = 500
    scopes: tuple[str, ...] = ("internal", "external")


@dataclass(frozen=True)
class FvgConfig:
    min_size_atr: float = 0.1
    min_size_price: float = 0.0
    invalidate_on_close_beyond: bool = True
    expiry_bars: int = 500


@dataclass(frozen=True)
class BprConfig:
    enabled: bool = True
    min_overlap_ratio: float = 0.25       # overlap / smaller gap
    max_bars_apart: int = 20


@dataclass(frozen=True)
class LiquidityConfig:
    equal_tolerance_atr: float = 0.1
    min_touches: int = 2
    lookback_bars: int = 300
    include_swing_pools: bool = True
    include_previous_day: bool = True     # previous UTC day high/low for intraday timeframes
    break_buffer_atr: float = 0.0


@dataclass(frozen=True)
class PremiumDiscountConfig:
    equilibrium_band: float = 0.05        # +/- fraction of the range treated as "equilibrium"
    fib_levels: tuple[float, ...] = (0.0, 0.236, 0.382, 0.5, 0.618, 0.705, 0.786, 1.0)
    ote_low: float = 0.62
    ote_high: float = 0.79
    ote_sweet_spot: float = 0.705


@dataclass(frozen=True)
class SupportResistanceConfig:
    cluster_tolerance_atr: float = 0.5
    zone_padding_atr: float = 0.1
    min_touches: int = 2
    max_zones: int = 12
    recency_half_life_bars: int = 200


@dataclass(frozen=True)
class MultiTimeframeConfig:
    timeframes: tuple[str, ...] = ("D1", "H4", "H1", "M15", "M5")
    weights: dict[str, float] = field(default_factory=lambda: {"D1": 5, "H4": 4, "H1": 3, "M15": 2, "M5": 1})
    min_candles: int = 50


@dataclass(frozen=True)
class ScoringConfig:
    weights: dict[str, float] = field(default_factory=lambda: {
        "external_structure": 20, "internal_structure": 10, "recent_break": 10, "order_block": 12,
        "fair_value_gap": 8, "premium_discount": 10, "ote": 8, "liquidity_sweep": 10,
        "support_resistance": 6, "htf_alignment": 6, "cot": 0})
    grade_thresholds: dict[str, float] = field(default_factory=lambda: {"A": 75, "B": 60, "C": 45})
    cot_weight_when_available: float = 5.0


@dataclass(frozen=True)
class AnalysisConfig:
    atr_period: int = 14
    min_candles: int = 50
    max_candles: int = 2000               # most recent candles analysed per timeframe (bounds compute time)
    swings: SwingConfig = SwingConfig()
    structure: StructureConfig = StructureConfig()
    displacement: DisplacementConfig = DisplacementConfig()
    order_blocks: OrderBlockConfig = OrderBlockConfig()
    fvg: FvgConfig = FvgConfig()
    bpr: BprConfig = BprConfig()
    liquidity: LiquidityConfig = LiquidityConfig()
    premium_discount: PremiumDiscountConfig = PremiumDiscountConfig()
    support_resistance: SupportResistanceConfig = SupportResistanceConfig()
    multi_timeframe: MultiTimeframeConfig = MultiTimeframeConfig()
    scoring: ScoringConfig = ScoringConfig()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def fingerprint(self) -> str:
        """Short hash of the effective configuration, stored with every persisted analysis."""
        raw = json.dumps(self.to_dict(), sort_keys=True, default=str)
        return hashlib.sha256(f"{ENGINE_VERSION}|{raw}".encode()).hexdigest()[:16]


def _build(cls: type, data: dict[str, Any] | None) -> Any:
    """Recursively build a frozen dataclass from a (partial) dict; unknown keys are rejected."""
    if not data:
        return cls()
    names = {f.name: f for f in fields(cls)}
    unknown = set(data) - set(names)
    if unknown:
        raise ValueError(f"Unknown {cls.__name__} setting(s): {sorted(unknown)}")
    defaults = cls()
    kwargs: dict[str, Any] = {}
    for name, f in names.items():
        if name not in data:
            continue
        current = getattr(defaults, name)
        value = data[name]
        if is_dataclass(current):
            kwargs[name] = _build(type(current), value)
        elif isinstance(current, tuple):
            kwargs[name] = tuple(value)
        elif isinstance(current, dict):
            kwargs[name] = {**current, **(value or {})}
        else:
            kwargs[name] = value
    return cls(**kwargs)


def load_analysis_config(path: Path | None = None) -> AnalysisConfig:
    """Load ``config/analysis.yaml`` (missing file -> built-in defaults)."""
    path = path or PROJECT_ROOT / "config" / "analysis.yaml"
    if not path.exists():
        return AnalysisConfig()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return _build(AnalysisConfig, raw)
