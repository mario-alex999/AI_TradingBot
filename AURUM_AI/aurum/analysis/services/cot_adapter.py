"""Optional COT confluence from Module 02.

Read-only: uses Module 02's stored reports through ``aurum.cot.intelligence.instrument_report`` and never
fetches, creates or estimates COT data. When Module 02 is absent, its schema is missing or no report is stored,
the adapter says so (``available: False`` with a reason) and Module 03 runs on price data alone.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Protocol

from sqlalchemy.engine import Engine

log = logging.getLogger(__name__)


class CotProvider(Protocol):
    def context(self, symbol: str, as_of: datetime | None = None) -> dict[str, Any]: ...


class NullCotProvider:
    def context(self, symbol: str, as_of: datetime | None = None) -> dict[str, Any]:
        return {"available": False, "source": None, "reason": "COT confluence disabled."}


class Module02CotAdapter:
    def __init__(self, engine: Engine):
        self.engine = engine

    def context(self, symbol: str, as_of: datetime | None = None) -> dict[str, Any]:
        try:
            from aurum.cot.config import load_cot_config
            from aurum.cot.intelligence import instrument_report
            from aurum.cot.repository import CotRepository
        except ImportError:
            return {"available": False, "source": None, "reason": "Module 02 (COT) is not installed."}
        try:
            repo = CotRepository(self.engine)
            if not repo.schema_ready():
                return {"available": False, "source": "module_02", "reason": "COT tables are not initialised."}
            cfg = load_cot_config()
            if not cfg.mappings_for(symbol.upper()):
                return {"available": False, "source": "module_02", "reason": f"No COT mapping for {symbol}."}
            ids = {(m["aurum_symbol"], m["report_type"]): m["id"] for m in repo.mappings()}
            rep = instrument_report(repo, cfg, symbol, ids, None, as_of=as_of)
        except Exception as exc:  # noqa: BLE001 - COT is optional; never break price analysis
            log.warning("COT context unavailable", extra={"symbol": symbol, "error": type(exc).__name__})
            return {"available": False, "source": "module_02", "reason": f"COT context failed ({type(exc).__name__})."}
        overall = rep.get("overall", {})
        stance = overall.get("stance")
        if stance in (None, "unavailable"):
            return {"available": False, "source": "module_02", "reason": overall.get("interpretation", "No COT data.")}
        return {"available": True, "source": "module_02", "stance": stance, "lean": overall.get("lean"),
                "confidence": overall.get("confidence"), "interpretation": overall.get("interpretation"),
                "as_of": as_of, "disclaimer": rep.get("disclaimer")}
