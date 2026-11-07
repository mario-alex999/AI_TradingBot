"""/api/v1 - Module 05 endpoints. Candidates for human review; there are no trading endpoints.

GET  /analysis/signals                TradeSignal[] (?status=&instrument=&limit=)
GET  /analysis/signals/{id}           full dossier
POST /analysis/signals/{id}/approve   admin: records a human decision; sends nothing to MT5
POST /analysis/signals/{id}/reject    admin
POST /analysis/signals/{id}/reopen    admin: override the AI reviewer's rejection, with a note
GET  /decision/context/{symbol}       the assembled inputs (?asOf=)
POST /decision/run                    admin, background job
GET  /decision/status                 last runs, AI reviewer state, counts
GET  /decision/jobs/{id}              background job status
GET  /backtests/latest                PerformanceReport (404 until one has been run)
POST /backtests/run                   admin, background job
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from aurum.analysis.services.analysis_service import UnknownInstrumentError
from aurum.api.app import AdminDep, AppState, camelize
from aurum.decision import DISCLAIMER
from aurum.decision.lifecycle import STATUSES, IllegalTransition
from aurum.decision.service import ApprovalBlocked, CandidateNotFound, DecisionService, SchemaNotReady


class Decision(BaseModel):
    reviewer: str = Field(min_length=1, max_length=60)
    note: str = Field(min_length=1, max_length=2000)


class RunRequest(BaseModel):
    symbols: list[str] | None = None
    as_of: datetime | None = Field(None, alias="asOf")
    ai_review: bool = Field(True, alias="aiReview")
    model_config = {"populate_by_name": True}


class BacktestRequest(BaseModel):
    start: date = Field(alias="from")
    end: date = Field(alias="to")
    symbols: list[str] | None = None
    template: str | None = None
    model_config = {"populate_by_name": True}


def service_getter(state: AppState, service: DecisionService | None, technical: Any, intelligence: Any) -> Callable[[], DecisionService]:
    """One lazily built service, shared by the /analysis, /decision and /ai routes."""
    holder: dict[str, DecisionService] = {}

    def svc() -> DecisionService:
        if service is not None:
            return service
        if "s" not in holder:
            holder["s"] = DecisionService(state.settings, state.engine, technical=technical, intelligence=intelligence)
        return holder["s"]
    return svc


def guarded(fn: Callable[[], Any]) -> Any:
    try:
        return fn()
    except CandidateNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except UnknownInstrumentError as exc:
        raise HTTPException(404, str(exc.args[0])) from exc
    except (ApprovalBlocked, IllegalTransition) as exc:
        raise HTTPException(409, str(exc)) from exc
    except SchemaNotReady as exc:
        raise HTTPException(503, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


def build_router(state: AppState, svc: Callable[[], DecisionService]) -> APIRouter:
    r = APIRouter()

    @r.get("/analysis/signals")
    def signals(status: str | None = None, instrument: str | None = None, limit: int = Query(100, ge=1, le=500)) -> list[dict[str, Any]]:
        if status and status not in STATUSES:
            raise HTTPException(422, f"Unknown status. Use one of: {', '.join(STATUSES)}.")
        return camelize(guarded(lambda: svc().signals(status, instrument, limit)))

    @r.get("/analysis/signals/{uid}")
    def dossier(uid: str) -> dict[str, Any]:
        return camelize(guarded(lambda: svc().dossier(uid)))

    @r.post("/analysis/signals/{uid}/approve", dependencies=[AdminDep])
    def approve(uid: str, body: Decision) -> dict[str, Any]:
        return camelize(guarded(lambda: svc().approve(uid, body.reviewer, body.note)))

    @r.post("/analysis/signals/{uid}/reject", dependencies=[AdminDep])
    def reject(uid: str, body: Decision) -> dict[str, Any]:
        return camelize(guarded(lambda: svc().reject(uid, body.reviewer, body.note)))

    @r.post("/analysis/signals/{uid}/reopen", dependencies=[AdminDep])
    def reopen(uid: str, body: Decision) -> dict[str, Any]:
        return camelize(guarded(lambda: svc().reopen(uid, body.reviewer, body.note)))

    @r.get("/decision/context/{symbol}")
    def context(symbol: str, as_of: datetime | None = Query(None, alias="asOf")) -> dict[str, Any]:
        return camelize(guarded(lambda: svc().context(symbol, as_of)))

    @r.get("/decision/status")
    def status() -> dict[str, Any]:
        return camelize(guarded(lambda: svc().status()))

    def job(kind: str, params: dict[str, Any], fn: Callable[[Callable[[str], None]], dict[str, Any]]) -> dict[str, Any]:
        j = state.jobs.submit(kind, params, fn)
        return {"jobId": j.id, "type": kind, "status": j.status}

    @r.post("/decision/run", status_code=202, dependencies=[AdminDep])
    def run(body: RunRequest) -> dict[str, Any]:
        def work(say: Callable[[str], None]) -> dict[str, Any]:
            done = svc().run_all(body.symbols, body.as_of, body.ai_review)
            return {"results": [{"symbol": d["symbol"], "status": d["status"], "signals": len(d["signals"])} for d in done]}
        return job("decision_run", {"symbols": body.symbols}, work)

    @r.get("/decision/jobs/{job_id}")
    def job_status(job_id: str) -> dict[str, Any]:
        j = state.jobs.get(job_id)
        if j is None:
            raise HTTPException(404, "Unknown job id.")
        return camelize(j.public())

    @r.get("/backtests/latest")
    def latest() -> dict[str, Any]:
        rep = guarded(lambda: svc().latest_backtest())
        if rep is None:
            raise HTTPException(404, "No backtest has been run.")
        return {**rep, "details": camelize(rep.get("details")), "run": camelize(rep.get("run")), "disclaimer": DISCLAIMER}

    @r.post("/backtests/run", status_code=202, dependencies=[AdminDep])
    def run_backtest(body: BacktestRequest) -> dict[str, Any]:
        def work(say: Callable[[str], None]) -> dict[str, Any]:
            rep = svc().backtest(body.start, body.end, body.symbols, body.template, say)
            return {"trades": rep["metrics"]["totalTrades"], "run": rep["run"]["id"]}
        return job("backtest", {"from": str(body.start), "to": str(body.end)}, work)

    return r
