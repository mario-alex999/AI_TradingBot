from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from aurum.analysis.models.base import Serializable

Side = Literal["buy_side", "sell_side"]


@dataclass
class LiquidityPool(Serializable):
    id: str
    side: Side                      # buy-side = resting above highs, sell-side = resting below lows
    kind: Literal["equal_highs", "equal_lows", "swing_high", "swing_low", "previous_day_high", "previous_day_low"]
    timeframe: str
    level: float
    touches: int
    member_times: list[datetime]
    created_index: int
    created_at: datetime
    state: Literal["active", "swept", "broken"] = "active"
    swept_at: datetime | None = None
    broken_at: datetime | None = None


@dataclass
class LiquidityEvent(Serializable):
    event: Literal["sweep", "break"]
    side: Side
    pool_id: str
    pool_kind: str
    timeframe: str
    level: float
    index: int
    timestamp: datetime             # open time of the candle
    confirmed_at: datetime          # its close time
    extreme: float                  # wick high/low beyond the level
    close: float
    implication: Literal["bullish", "bearish"]
    explanation: str = ""


@dataclass
class InducementCandidate(Serializable):
    direction: Literal["bullish", "bearish"]   # the bias the inducement sits inside
    timeframe: str
    level: float
    swing_time: datetime
    confirmed_at: datetime
    related_break_at: datetime
    target_poi: dict | None
    state: Literal["pending", "swept"] = "pending"
    swept_at: datetime | None = None
    explanation: list[str] = field(default_factory=list)
