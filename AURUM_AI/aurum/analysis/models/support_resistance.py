from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from aurum.analysis.models.base import Serializable


@dataclass
class SupportResistanceZone(Serializable):
    id: str
    timeframe: str
    top: float
    bottom: float
    mid: float
    touches: int
    swing_highs: int
    swing_lows: int
    first_time: datetime
    last_time: datetime
    role: Literal["support", "resistance", "flip"]
    position: Literal["below_price", "above_price", "at_price"]
    score: float = 0.0
    score_breakdown: dict[str, float] = field(default_factory=dict)
