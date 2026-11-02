"""Instrument availability checks and specification snapshots."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from aurum.config import InstrumentConfig
from aurum.gateways.mt5_gateway import MT5Gateway

log = logging.getLogger(__name__)


@dataclass
class SpecResult:
    available: dict[str, dict[str, Any]] = field(default_factory=dict)   # symbol -> spec
    unavailable: dict[str, str] = field(default_factory=dict)            # symbol -> reason


def collect_specs(gateway: MT5Gateway, instruments: list[InstrumentConfig]) -> SpecResult:
    """Check every instrument; unavailable ones are reported and skipped, never fatal."""
    res = SpecResult()
    for inst in instruments:
        try:
            spec = gateway.symbol_spec(inst.broker_symbol)
        except Exception as exc:  # keep going with the remaining symbols
            log.exception("Failed reading symbol spec", extra={"symbol": inst.symbol})
            res.unavailable[inst.symbol] = f"error: {exc}"
            continue
        if spec is None:
            res.unavailable[inst.symbol] = (f"broker symbol '{inst.broker_symbol}' not found - check "
                                            f"broker_symbol in config/instruments.yaml")
            continue
        res.available[inst.symbol] = {"symbol": inst.symbol, "asset_class": inst.asset_class, **spec}
    return res
