from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from aurum.analysis.models.base import Serializable

Direction = Literal["bullish", "bearish"]
Bias = Literal["bullish", "bearish", "ranging"]


@dataclass
class StructureEvent(Serializable):
    event_type: Literal["BOS", "CHOCH"]
    direction: Direction
    scope: Literal["internal", "external"]
    timeframe: str
    broken_level: float
    broken_swing_index: int
    broken_swing_time: datetime
    break_index: int
    break_candle_time: datetime     # open time of the breakout candle
    confirmed_at: datetime          # close time of the breakout candle
    previous_bias: Bias
    new_bias: Direction
    break_mode: str
    confirmed: bool                 # False when the breakout candle is still forming
    initial: bool = False           # first break that establishes a bias (no prior bias to continue/reverse)
    displacement: bool = False
    displacement_strength: float = 0.0
    explanation: str = ""


@dataclass
class Displacement(Serializable):
    direction: Direction
    index: int
    timestamp: datetime
    kind: Literal["single", "sequence"]
    body_ratio: float               # |close-open| / ATR (sequence: combined body / ATR)
    range_ratio: float              # (high-low) / ATR
    body_to_range: float
    close_position: float           # where the close sits in the candle, measured in the move's direction
    atr: float
    strength: float                 # body_ratio, + 0.25 when usable volume expanded
    volume_ratio: float | None = None
    caused_structure_break: bool = False
    related_event: str | None = None
    note: str = "Price-action classification only; it does not identify who placed the orders."


@dataclass
class ChochStatus(Serializable):
    status: Literal["none", "potential", "confirmed"]
    scope: Literal["internal", "external"]
    direction: Direction | None = None
    level: float | None = None
    since: datetime | None = None
    explanation: str = ""


@dataclass
class StructureState(Serializable):
    scope: Literal["internal", "external"]
    bias: Bias
    previous_bias: Bias
    state: Literal["trending", "transitioning", "ranging", "insufficient_data"]
    confidence: float               # 0..1
    last_swing_high: dict | None
    last_swing_low: dict | None
    recent_labels: list[str] = field(default_factory=list)
    choch: ChochStatus | None = None
    explanation: str = ""
