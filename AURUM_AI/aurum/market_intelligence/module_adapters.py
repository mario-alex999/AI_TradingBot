"""Adapters to Module 02 (COT) and Module 03 (technical analysis / SMC).

Module 04 never re-implements their algorithms: it asks the existing services for results at the same ``as_of``
and reads the fields it needs. Both integrations are optional - when a module, its tables or its data are missing
the adapter returns ``{"available": False, "reason": ...}`` and Module 04 carries on with price data alone.
"""
from __future__ import annotations

import logging
import time as _time
from datetime import datetime
from typing import Any, Callable

log = logging.getLogger(__name__)


def minute_key(t: datetime) -> str:
    """Every candle closes on a whole minute, so all instants inside one minute see the same closed candles: results
    computed for any instant in [m, m+60s) are identical in their candle inputs."""
    return t.replace(second=0, microsecond=0).isoformat()


class Module03Adapter:
    """Module 03 results are cached per (symbol, timeframe, as_of minute) for ``ttl_seconds`` - the analysis of an
    instant depends only on candles closed by it (see ``minute_key``), so a cached result is exact for that minute."""

    def __init__(self, factory: Callable[[], Any] | None = None, ttl_seconds: float = 120.0, max_entries: int = 256):
        self._factory = factory
        self._svc = None
        self._unavailable: str | None = None
        self.ttl, self.max_entries = ttl_seconds, max_entries
        self._cache: dict[tuple, tuple[float, dict[str, Any]]] = {}

    def _cached(self, key: tuple, fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        now = _time.monotonic()
        hit = self._cache.get(key)
        if hit is not None and now - hit[0] <= self.ttl:
            return hit[1]
        val = fn()
        if val.get("available"):
            if len(self._cache) >= self.max_entries:
                self._cache.pop(min(self._cache, key=lambda k: self._cache[k][0]))
            self._cache[key] = (now, val)
        return val

    def clear(self) -> None:
        self._cache.clear()

    def _service(self):
        if self._svc is None and self._unavailable is None:
            try:
                if self._factory is not None:
                    self._svc = self._factory()
                else:
                    from aurum.analysis.services.analysis_service import TechnicalAnalysisService
                    self._svc = TechnicalAnalysisService()
            except Exception as exc:  # noqa: BLE001 - optional integration
                self._unavailable = f"Module 03 unavailable ({type(exc).__name__})."
        return self._svc

    def context(self, symbol: str, timeframe: str, as_of: datetime) -> dict[str, Any]:
        return self._cached(("ctx", symbol, timeframe, minute_key(as_of)), lambda: self._context(symbol, timeframe,
                                                                                                as_of))

    def _context(self, symbol: str, timeframe: str, as_of: datetime) -> dict[str, Any]:
        svc = self._service()
        if svc is None:
            return {"available": False, "timeframe": timeframe, "reason": self._unavailable}
        try:
            a = svc.analyze(symbol, timeframe, as_of, include_provisional=False, include_cot=False)
        except Exception as exc:  # noqa: BLE001
            log.warning("Module 03 context failed", extra={"symbol": symbol, "error": type(exc).__name__})
            return {"available": False, "timeframe": timeframe, "reason": f"Module 03 analysis failed ({type(exc).__name__})."}
        if a.status != "ok":
            return {"available": False, "timeframe": timeframe, "reason": f"Module 03 status '{a.status}'.",
                    "messages": a.messages}
        d = a.to_dict()
        ext, intl = d["structure"].get("external") or {}, d["structure"].get("internal") or {}
        return {"available": True, "source": "module_03", "timeframe": timeframe, "as_of": d["as_of"],
                "engine_version": d["engine_version"], "config_fingerprint": d["config_fingerprint"],
                "external_bias": ext.get("bias"), "external_state": ext.get("state"),
                "external_confidence": ext.get("confidence"), "internal_bias": intl.get("bias"),
                "internal_state": intl.get("state"),
                "structure_events": [e for e in d["structure_events"] if e.get("confirmed")],
                "liquidity_events": d["liquidity_events"][-20:],
                "premium_discount": (d.get("premium_discount") or {}).get("zone"),
                "score": {k: (d.get("score") or {}).get(k) for k in ("bias", "score", "grade")}}

    def confluence(self, symbol: str, as_of: datetime) -> dict[str, Any]:
        return self._cached(("confl", symbol, minute_key(as_of)), lambda: self._confluence(symbol, as_of))

    def _confluence(self, symbol: str, as_of: datetime) -> dict[str, Any]:
        svc = self._service()
        if svc is None:
            return {"available": False, "reason": self._unavailable}
        try:
            rep = svc.analyze_confluence(symbol, None, as_of, False, include_cot=False)
        except Exception as exc:  # noqa: BLE001
            return {"available": False, "reason": f"Module 03 confluence failed ({type(exc).__name__})."}
        d = rep.to_dict()
        return {"available": True, "source": "module_03", "alignment": d["alignment"],
                "bias": d["score"]["bias"], "score": d["score"]["score"], "warnings": d["warnings"],
                "timeframes": {tf: {"external_bias": v.get("external_bias"), "external_state": v.get("external_state")}
                               for tf, v in d["per_timeframe"].items()}}


class Module02Adapter:
    """COT context through Module 03's existing read-only adapter (which in turn uses Module 02)."""

    def __init__(self, engine=None):
        self.engine = engine

    def context(self, symbol: str, as_of: datetime) -> dict[str, Any]:
        if self.engine is None:
            return {"available": False, "reason": "No database engine for Module 02."}
        try:
            from aurum.analysis.services.cot_adapter import Module02CotAdapter
            return Module02CotAdapter(self.engine).context(symbol, as_of)
        except Exception as exc:  # noqa: BLE001
            return {"available": False, "reason": f"Module 02 unavailable ({type(exc).__name__})."}


class NullModule03:
    def context(self, symbol: str, timeframe: str, as_of: datetime) -> dict[str, Any]:
        return {"available": False, "timeframe": timeframe, "reason": "Module 03 integration disabled."}

    def confluence(self, symbol: str, as_of: datetime) -> dict[str, Any]:
        return {"available": False, "reason": "Module 03 integration disabled."}
