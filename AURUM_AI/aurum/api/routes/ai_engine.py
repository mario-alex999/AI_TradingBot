"""/api/v1/ai - the analysis surface of Module 05, over the same service as /analysis/signals. There are no trading endpoints.

POST /ai/analyze, /ai/analyze/batch        admin: can spend on the AI provider
GET  /ai/analysis[/{id}[/evidence|/decision]]
GET  /ai/bias/{symbol}, /ai/regime/{symbol}, /ai/setups/{symbol}, /ai/trade-ideas
GET  /ai/strategies, /ai/providers, /ai/health, /ai/config
PUT  /ai/config                            admin
POST /ai/evaluation/run                    admin, background job
GET  /ai/evaluation/runs, /ai/evaluation/{id}
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Callable

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from aurum.api.app import AdminDep, AppState, camelize
from aurum.api.routes.decision import guarded
from aurum.decision import DISCLAIMER
from aurum.decision.lifecycle import STATUSES
from aurum.decision.service import DecisionService


class Analyze(BaseModel):
    symbol: str
    as_of: datetime | None = Field(None, alias="asOf")
    ai_review: bool = Field(True, alias="aiReview")
    timeframes: list[str] | None = None
    model_config = {"populate_by_name": True}


class Batch(BaseModel):
    symbols: list[str] | None = None
    ai_review: bool = Field(True, alias="aiReview")
    model_config = {"populate_by_name": True}


class ConfigChange(BaseModel):
    reviewer: str = Field(min_length=1, max_length=60)
    overrides: dict[str, Any]


class Evaluation(BaseModel):
    start: date = Field(alias="from")
    end: date = Field(alias="to")
    symbols: list[str] | None = None
    template: str | None = None
    model_config = {"populate_by_name": True}


def build_router(state: AppState, svc: Callable[[], DecisionService]) -> APIRouter:
    r = APIRouter(prefix="/ai")

    def job(kind: str, params: dict[str, Any], fn: Callable[[Callable[[str], None]], dict[str, Any]]) -> dict[str, Any]:
        j = state.jobs.submit(kind, params, fn)
        return {"jobId": j.id, "type": kind, "status": j.status}

    @r.post("/analyze", dependencies=[AdminDep])
    def analyze(body: Analyze) -> dict[str, Any]:
        return camelize(guarded(lambda: svc().run(body.symbol, body.as_of, body.ai_review, body.timeframes)))

    @r.post("/analyze/batch", status_code=202, dependencies=[AdminDep])
    def analyze_batch(body: Batch) -> dict[str, Any]:
        def work(say: Callable[[str], None]) -> dict[str, Any]:
            done = svc().run_all(body.symbols, None, body.ai_review)
            return {"results": [{"symbol": d["symbol"], "decision": d["analysis"]["decision"]["decision"]} for d in done]}
        return job("ai_analyze_batch", {"symbols": body.symbols}, work)

    @r.get("/analysis")
    def analyses(symbol: str | None = None, limit: int = Query(50, ge=1, le=200)) -> list[dict[str, Any]]:
        return camelize(guarded(lambda: svc().analyses(symbol, limit)))

    @r.get("/analysis/{uid}")
    def analysis(uid: str) -> dict[str, Any]:
        return camelize(guarded(lambda: svc().analysis(uid)))

    @r.get("/analysis/{uid}/evidence")
    def evidence(uid: str) -> dict[str, Any]:
        s = guarded(lambda: svc().analysis(uid))["summary"] or {}
        return camelize({"evidence": s.get("evidence", []), "explanation": s.get("explanation"), "conflicts": s.get("conflicts", []), "disclaimer": DISCLAIMER})

    @r.get("/analysis/{uid}/decision")
    def decision(uid: str) -> dict[str, Any]:
        s = guarded(lambda: svc().analysis(uid))["summary"] or {}
        return camelize({"decision": s.get("decision"), "human_review": s.get("human_review"), "disclaimer": DISCLAIMER})

    @r.get("/bias/{symbol}")
    def bias(symbol: str) -> dict[str, Any]:
        return camelize(guarded(lambda: svc().bias(symbol)))

    @r.get("/regime/{symbol}")
    def regime(symbol: str) -> dict[str, Any]:
        return camelize(guarded(lambda: svc().regime(symbol)))

    @r.get("/setups/{symbol}")
    def setups(symbol: str) -> dict[str, Any]:
        return camelize(guarded(lambda: svc().setups(symbol)))

    @r.get("/trade-ideas")
    def trade_ideas(status: str | None = None, instrument: str | None = None, limit: int = Query(100, ge=1, le=500)) -> list[dict[str, Any]]:
        if status and status not in STATUSES:
            raise_unknown(status)
        return camelize(guarded(lambda: svc().trade_ideas(status, instrument, limit)))

    @r.get("/strategies")
    def strategies() -> list[dict[str, Any]]:
        return camelize(svc().strategies())

    @r.get("/config")
    def config() -> dict[str, Any]:
        return guarded(lambda: svc().config_view())

    @r.put("/config", dependencies=[AdminDep])
    def config_update(body: ConfigChange) -> dict[str, Any]:
        return guarded(lambda: svc().config_update(body.overrides, body.reviewer))

    @r.get("/providers")
    def providers() -> dict[str, Any]:
        return camelize(guarded(lambda: svc().providers()))

    @r.get("/health")
    def health() -> dict[str, Any]:
        return camelize(guarded(lambda: svc().health()))

    @r.post("/evaluation/run", status_code=202, dependencies=[AdminDep])
    def evaluation_run(body: Evaluation) -> dict[str, Any]:
        def work(say: Callable[[str], None]) -> dict[str, Any]:
            rep = svc().backtest(body.start, body.end, body.symbols, body.template, say)
            return {"trades": rep["metrics"]["totalTrades"], "run": rep["run"]["id"]}
        return job("ai_evaluation", {"from": str(body.start), "to": str(body.end)}, work)

    @r.get("/evaluation/runs")
    def evaluation_runs(limit: int = Query(20, ge=1, le=100)) -> list[dict[str, Any]]:
        return camelize(guarded(lambda: svc().evaluations(limit)))

    @r.get("/evaluation/{uid}")
    def evaluation(uid: str) -> dict[str, Any]:
        rep = guarded(lambda: svc().evaluation(uid))
        return {**rep, "details": camelize(rep.get("details")), "run": camelize(rep.get("run")), "disclaimer": DISCLAIMER}

    return r


def raise_unknown(status: str) -> None:
    from fastapi import HTTPException
    raise HTTPException(422, f"Unknown status '{status}'.")
