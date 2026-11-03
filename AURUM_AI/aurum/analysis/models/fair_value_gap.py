from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from aurum.analysis.models.base import Serializable

GapState = Literal["unfilled", "partially_filled", "fully_filled", "invalidated", "expired"]


@dataclass
class FairValueGap(Serializable):
    id: str
    direction: Literal["bullish", "bearish"]
    timeframe: str
    candle_times: list[datetime]    # the three source candles (open times)
    detected_index: int             # candle 3
    detected_at: datetime           # close time of candle 3
    lower: float
    upper: float
    size: float
    size_atr_pct: float             # gap size as % of ATR
    middle_body_ratio: float
    middle_range_ratio: float
    middle_is_displacement: bool
    state: GapState = "unfilled"
    fill_pct: float = 0.0
    current_lower: float = 0.0
    current_upper: float = 0.0
    partially_filled_at: datetime | None = None
    filled_at: datetime | None = None
    invalidated_at: datetime | None = None
    expired_at: datetime | None = None

    @property
    def active(self) -> bool:
        return self.state in ("unfilled", "partially_filled")


@dataclass
class BalancedPriceRange(Serializable):
    id: str
    direction: Literal["bullish", "bearish"]   # direction of the more recent gap
    timeframe: str
    bullish_fvg_id: str
    bearish_fvg_id: str
    detected_index: int
    detected_at: datetime
    lower: float
    upper: float
    overlap_ratio: float
    state: Literal["active", "invalidated"] = "active"
    invalidated_at: datetime | None = None
    explanation: list[str] = field(default_factory=list)
