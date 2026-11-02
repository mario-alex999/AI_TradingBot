"""Current market snapshot (bid / ask / spread / last / tick time)."""
from __future__ import annotations

from typing import Any

from aurum.config import InstrumentConfig
from aurum.gateways.mt5_gateway import MT5Gateway


def collect_tick(gateway: MT5Gateway, inst: InstrumentConfig) -> dict[str, Any] | None:
    tick = gateway.tick(inst.broker_symbol)
    if tick is None:
        return None
    return {"symbol": inst.symbol, **tick}
