from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from aurum.analysis.models.base import Serializable

SwingKind = Literal["high", "low"]
Scope = Literal["internal", "external"]


@dataclass
class Swing(Serializable):
    kind: SwingKind
    price: float
    index: int                      # source candle position
    timestamp: datetime             # source candle open time (UTC)
    confirmed_index: int            # candle whose close confirms the swing (index + right window)
    confirmed_at: datetime          # close time of that candle
    timeframe: str
    scope: Scope
    strength: int                   # bars on the left this swing dominates (plus the right window)
    prominence_atr: float           # swing height above/below its window's opposite extreme, in ATR
    label: str | None = None        # HH / HL / LH / LL / EQH / EQL (set by the structure engine)
    provisional: bool = False       # confirmed only by a still-forming candle
