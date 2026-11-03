"""/api/v1/analysis/* - Module 03 endpoints (read-only analysis; no trading endpoints).

GET  /analysis/technical/{symbol}?timeframe=H1      single-timeframe analysis (detail=summary|full)
GET  /analysis/confluence/{symbol}?timeframes=...   multi-timeframe confluence report
GET  /analysis/market-structure?instrument=         frontend MarketStructureReport
GET  /analysis/market-structure?instrument=&timeframe=&view=overlays   frontend ChartOverlay[]
GET  /analysis/config                               effective configuration
GET  /analysis/runs, /analysis/zones                persisted history
POST /analysis/run                                  admin: analyse and persist
All timestamps are ISO-8601 UTC; keys are camelCase.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from aurum.api.app import AdminDep, AppState, camelize
from aurum.analysis.services.analysis_service import TechnicalAnalysisService, UnknownInstrumentError
from aurum.analysis.services.frontend_views import market_structure_report, overlays


class RunRequest(BaseModel):
    symbol: str
    timeframes: list[str] | None = None
    as_of: datetime | None = Field(None, alias="asOf")
    include_cot: bool = Field(True, alias="includeCot")
    model_config = {"populate_by_name": True}

    @field_validator("symbol")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper()


def _summary(a) -> dict[str, Any]:
    d = a.to_dict()
    active = {"order_blocks": [z for z in d["order_blocks"] if z["state"] in ("fresh", "partially_mitigated")],
              "fair_value_gaps": [g for g in d["fair_value_gaps"] if g["state"] in ("unfilled", "partially_filled")],
              "liquidity_pools": [p for p in d["liquidity_pools"] if p["state"] == "active"]}
    keep = ("symbol", "timeframe", "as_of", "generated_at", "engine_version", "config_fingerprint", "provisional",
            "candles_used", "first_candle", "last_candle", "current_price", "atr", "data_quality", "structure",
            "premium_discount", "ote", "support_resistance", "inducements", "score", "status", "messages", "disclaimer")
    out = {k: d[k] for k in keep}
    out.update(active)
    out["recent_structure_events"] = d["structure_events"][-10:]
    out["recent_liquidity_events"] = d["liquidity_events"][-10:]
    return out


def build_router(state: AppState, service: TechnicalAnalysisService | None = None) -> APIRouter:
    r = APIRouter()
    svc = service or TechnicalAnalysisService(state.settings, state.engine)

    def run(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except UnknownInstrumentError as exc:
            raise HTTPException(404, str(exc.args[0])) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001 - database down etc.; details stay in the server log
            if "OperationalError" in type(exc).__name__ or "ProgrammingError" in type(exc).__name__:
                raise HTTPException(503, f"Market data unavailable ({type(exc).__name__}).") from exc
            raise

    def tfs(value: str | None) -> list[str] | None:
        return [t.strip().upper() for t in value.split(",") if t.strip()] if value else None

    @r.get("/technical/{symbol}", summary="Single-timeframe technical / SMC analysis")
    def technical(symbol: str, timeframe: str = Query("H1"), as_of: datetime | None = Query(None, alias="asOf"),
                  include_provisional: bool = Query(False, alias="includeProvisional"),
                  include_cot: bool = Query(True, alias="includeCot"),
                  detail: Literal["summary", "full"] = Query("summary")):
        a = run(svc.analyze, symbol, timeframe, as_of, include_provisional, include_cot)
        return camelize(a.to_dict() if detail == "full" else _summary(a))

    @r.get("/confluence/{symbol}", summary="Multi-timeframe confluence report")
    def confluence_(symbol: str, timeframes: str | None = Query(None, description="e.g. D1,H4,H1,M15,M5"),
                    as_of: datetime | None = Query(None, alias="asOf"),
                    include_provisional: bool = Query(False, alias="includeProvisional"),
                    include_cot: bool = Query(True, alias="includeCot")):
        rep = run(svc.analyze_confluence, symbol, tfs(timeframes), as_of, include_provisional, include_cot)
        return camelize(rep.to_dict())

    @r.get("/market-structure", summary="Market structure for the dashboard (frontend contract)")
    def market_structure(instrument: str = Query(...), timeframe: str | None = Query(None),
                         view: Literal["report", "overlays"] = Query("report")):
        if view == "overlays":
            if not timeframe:
                raise HTTPException(422, "view=overlays needs a timeframe.")
            return camelize(overlays(run(svc.analyze, instrument, timeframe, include_cot=False)))
        inst = run(svc.instrument, instrument)
        results = {tf: run(svc.analyze, inst.symbol, tf, include_cot=False)
                   for tf in (tfs(timeframe) or list(svc.cfg.multi_timeframe.timeframes))}
        return camelize(market_structure_report(inst.symbol, results))

    @r.get("/config", summary="Effective Module 03 configuration")
    def config():
        return camelize({"fingerprint": svc.cfg.fingerprint, "config": svc.cfg.to_dict()})

    @r.get("/runs", summary="Persisted analysis runs")
    def runs(symbol: str | None = None, timeframe: str | None = None, limit: int = Query(50, ge=1, le=500)):
        if not run(svc.store.schema_ready):
            raise HTTPException(503, "Module 03 tables are not initialised: run `python -m aurum.cli init-db`.")
        return camelize({"items": run(svc.store.runs, symbol.upper() if symbol else None,
                                      timeframe.upper() if timeframe else None, limit)})

    @r.get("/zones", summary="Persisted zones (order blocks, FVGs, BPRs, liquidity, S/R)")
    def zones(symbol: str, timeframe: str, kind: str | None = None, active_only: bool = Query(False, alias="activeOnly")):
        if not run(svc.store.schema_ready):
            raise HTTPException(503, "Module 03 tables are not initialised: run `python -m aurum.cli init-db`.")
        return camelize({"items": run(svc.store.zones, symbol.upper(), timeframe.upper(), kind, active_only)})

    @r.post("/run", status_code=201, dependencies=[AdminDep], summary="Admin: analyse and persist")
    def run_and_store(req: RunRequest):
        if not run(svc.store.schema_ready):
            raise HTTPException(503, "Module 03 tables are not initialised: run `python -m aurum.cli init-db`.")
        rep = run(svc.analyze_confluence, req.symbol, req.timeframes, req.as_of, False, req.include_cot, True)
        return camelize({"symbol": rep.symbol, "as_of": rep.as_of, "bias": rep.score.bias, "score": rep.score.score,
                         "grade": rep.score.grade, "timeframes": rep.timeframes})

    return r
