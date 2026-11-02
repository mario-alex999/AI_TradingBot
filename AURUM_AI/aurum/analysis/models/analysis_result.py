from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from aurum.analysis.models.base import Serializable
from aurum.analysis.models.fair_value_gap import BalancedPriceRange, FairValueGap
from aurum.analysis.models.liquidity_pool import InducementCandidate, LiquidityEvent, LiquidityPool
from aurum.analysis.models.order_block import OrderBlock
from aurum.analysis.models.structure_event import Displacement, StructureEvent, StructureState
from aurum.analysis.models.support_resistance import SupportResistanceZone
from aurum.analysis.models.swing import Swing

DISCLAIMER = ("Deterministic price-action analysis for decision support. It is not a trade signal, does not "
              "place orders and does not approve execution; a human operator decides.")


@dataclass
class PremiumDiscount(Serializable):
    range_high: float
    range_low: float
    range_high_time: datetime
    range_low_time: datetime
    equilibrium: float
    current_price: float
    position: float                 # 0 = range low, 1 = range high
    zone: Literal["premium", "discount", "equilibrium", "above_range", "below_range"]
    leg_direction: Literal["bullish", "bearish"]   # bullish = low formed first, then the high
    fib_levels: dict[str, float] = field(default_factory=dict)


@dataclass
class OteZone(Serializable):
    direction: Literal["bullish", "bearish"]
    lower: float
    upper: float
    sweet_spot: float
    price_inside: bool
    overlapping_zones: list[str] = field(default_factory=list)
    explanation: str = ""


@dataclass
class ScoreComponent(Serializable):
    name: str
    direction: Literal["bullish", "bearish", "neutral"]
    points: float
    max_points: float
    explanation: str


@dataclass
class TechnicalScore(Serializable):
    bias: Literal["bullish", "bearish", "neutral"]
    bullish_score: float
    bearish_score: float
    score: float                    # score of the favoured side, 0..100
    grade: Literal["A", "B", "C", "none"]
    components: list[ScoreComponent] = field(default_factory=list)
    penalties: list[ScoreComponent] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    explanation: str = ""


@dataclass
class TimeframeAnalysis(Serializable):
    symbol: str
    timeframe: str
    as_of: datetime                 # close time of the last candle used
    generated_at: datetime
    engine_version: str
    config_fingerprint: str
    provisional: bool
    candles_used: int
    first_candle: datetime | None
    last_candle: datetime | None
    current_price: float | None
    atr: float | None
    data_quality: dict[str, Any]
    swings: list[Swing] = field(default_factory=list)
    structure: dict[str, StructureState] = field(default_factory=dict)
    structure_events: list[StructureEvent] = field(default_factory=list)
    displacements: list[Displacement] = field(default_factory=list)
    order_blocks: list[OrderBlock] = field(default_factory=list)
    fair_value_gaps: list[FairValueGap] = field(default_factory=list)
    balanced_price_ranges: list[BalancedPriceRange] = field(default_factory=list)
    liquidity_pools: list[LiquidityPool] = field(default_factory=list)
    liquidity_events: list[LiquidityEvent] = field(default_factory=list)
    inducements: list[InducementCandidate] = field(default_factory=list)
    premium_discount: PremiumDiscount | None = None
    ote: OteZone | None = None
    support_resistance: list[SupportResistanceZone] = field(default_factory=list)
    score: TechnicalScore | None = None
    status: Literal["ok", "insufficient_data", "no_data"] = "ok"
    messages: list[str] = field(default_factory=list)
    disclaimer: str = DISCLAIMER


@dataclass
class ConfluenceReport(Serializable):
    symbol: str
    as_of: datetime
    generated_at: datetime
    timeframes: list[str]
    alignment: dict[str, Any]
    score: TechnicalScore
    per_timeframe: dict[str, dict[str, Any]]
    cot: dict[str, Any]
    findings: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    disclaimer: str = DISCLAIMER
