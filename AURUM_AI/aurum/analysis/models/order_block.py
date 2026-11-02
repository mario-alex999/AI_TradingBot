from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from aurum.analysis.models.base import Serializable

ZoneState = Literal["fresh", "partially_mitigated", "mitigated", "invalidated", "expired"]


@dataclass
class OrderBlock(Serializable):
    id: str
    direction: Literal["bullish", "bearish"]
    timeframe: str
    source_index: int
    source_time: datetime
    source_open: float
    source_high: float
    source_low: float
    source_close: float
    detected_index: int             # breakout candle: the OB cannot be known before it closes
    detected_at: datetime           # close time of the breakout candle
    break_event: dict               # the qualifying BOS/CHoCH
    zone_mode: str
    full_top: float
    full_bottom: float
    body_top: float
    body_bottom: float
    initial_top: float
    initial_bottom: float
    current_top: float
    current_bottom: float
    size: float
    size_atr: float
    displacement_strength: float
    state: ZoneState = "fresh"
    mitigation_pct: float = 0.0
    first_touch_at: datetime | None = None
    mitigated_at: datetime | None = None
    invalidated_at: datetime | None = None
    expired_at: datetime | None = None
    explanation: list[str] = field(default_factory=list)

    @property
    def proximal(self) -> float:
        return self.initial_top if self.direction == "bullish" else self.initial_bottom

    @property
    def distal(self) -> float:
        return self.initial_bottom if self.direction == "bullish" else self.initial_top

    @property
    def active(self) -> bool:
        return self.state in ("fresh", "partially_mitigated")
