"""/api/v1/risk - Module 06 Advanced Risk Management Engine. No endpoint here places, modifies or closes an order.

POST /risk/assess                          admin: full risk decision (PASS | REVIEW | BLOCK | ERROR); Idempotency-Key supported
POST /risk/position-size                   what-if sizing through the backend engine; stores nothing
GET  /risk/assessments/{id}                one stored assessment
POST /risk/assessments/{id}/validate       Module 07/09 check: still valid to act on?
POST /risk/assessments/{id}/consume        admin: Module 07 took it forward (one-way)
POST /risk/assessments/{id}/cancel         admin: frees its reserved risk (one-way)
GET  /risk/account                         account-level risk
GET  /risk/portfolio                       exposure, open risk, concentration, correlation
GET  /risk/config                          active configuration, profiles, version history
PUT  /risk/config                          admin: new configuration version (reason; loosening needs acknowledgement)
GET  /risk/rules                           rule catalog with current thresholds
GET  /risk/locks                           active and historical locks (?history=)
POST /risk/locks                           admin: create a lock
POST /risk/locks/{id}/release              admin: release a lock (reason required)
GET  /risk/history                         assessments, paginated and filtered
GET  /risk/history/rules                   how often each rule failed
GET  /risk/audit                           audit log (?entityType=)
GET  /risk/events                          outbox, for polling consumers such as n8n (?after=&type=)
POST /risk/events/inbound                  admin: receive an event from another module or n8n (deduplicated)
GET  /risk/health                          dependency health and data freshness

Reads are open like the rest of this local API; every write needs X-AURUM-Admin-Key. X-AURUM-Actor names the person or
service for the audit log. X-Correlation-ID is accepted (or generated) and echoed.
"""
from __future__ import annotations

import re
import threading
import time
import uuid
from datetime import datetime
from typing import Any, Callable

from fastapi import APIRouter, Body, Header, Query, Request, Response
from fastapi.responses import JSONResponse

from aurum.api.app import AdminDep, AppState, camelize
from aurum.risk.service import RiskError, RiskService

_SNAKE = re.compile(r"(?<!^)(?=[A-Z])")


def snake(obj: Any) -> Any:
    """camelCase request keys -> snake_case, recursively (rule ids and symbol keys inside config tables are left alone)."""
    if isinstance(obj, dict):
        return {(_SNAKE.sub("_", k).lower() if isinstance(k, str) and not k.isupper() and not re.match(r"^R\d{3}$", k) else k):
                snake(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [snake(v) for v in obj]
    return obj


class RateLimiter:
    """Per-client sliding window; protects the expensive assessment endpoints."""

    def __init__(self, limit: int, window_s: float = 60.0):
        self.limit, self.window = limit, window_s
        self.hits: dict[str, list[float]] = {}
        self.lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self.lock:
            hits = [t for t in self.hits.get(key, []) if now - t < self.window]
            if len(hits) >= self.limit:
                self.hits[key] = hits
                return False
            hits.append(now)
            self.hits[key] = hits
            return True


def service_getter(state: AppState, service: RiskService | None, intelligence: Any = None) -> Callable[[], RiskService]:
    holder: dict[str, RiskService] = {}

    def svc() -> RiskService:
        if service is not None:
            return service
        if "s" not in holder:
            holder["s"] = RiskService(state.engine, state.settings, intelligence_service=intelligence)
        return holder["s"]
    return svc


def build_router(state: AppState, svc: Callable[[], RiskService], rate_limit_per_minute: int = 60) -> APIRouter:
    r = APIRouter()
    limiter = RateLimiter(rate_limit_per_minute)

    def run(request: Request, response: Response, fn: Callable[[], Any], cid: str | None, status: int = 200) -> Any:
        cid = cid or str(uuid.uuid4())
        response.headers["X-Correlation-ID"] = cid
        try:
            out = fn() if fn.__code__.co_argcount == 0 else fn(cid)
        except RiskError as exc:
            return JSONResponse(status_code=exc.status, headers={"X-Correlation-ID": cid}, content=camelize(
                {"error": {"code": exc.code, "message": exc.message, "details": exc.details, "correlation_id": cid},
                 "detail": exc.message}))
        response.status_code = status
        return camelize(out)

    def limited(request: Request, cid: str | None) -> JSONResponse | None:
        client = request.client.host if request.client else "unknown"
        if not limiter.allow(client):
            return JSONResponse(status_code=429, headers={"Retry-After": "60", "X-Correlation-ID": cid or ""},
                                content={"error": {"code": "RATE_LIMITED", "message": "Too many risk requests; retry in a minute."},
                                         "detail": "Too many risk requests; retry in a minute."})
        return None

    def actor_of(x_aurum_actor: str | None) -> str:
        a = (x_aurum_actor or "").strip()
        return re.sub(r"[^\w .@-]", "", a)[:60] or "admin-key"

    Cid = Header(default=None, alias="X-Correlation-ID")
    Actor = Header(default=None, alias="X-AURUM-Actor")

    @r.post("/assess", dependencies=[AdminDep])
    def assess(request: Request, response: Response, body: dict[str, Any] = Body(...), cid: str | None = Cid,
               actor: str | None = Actor, idem: str | None = Header(default=None, alias="Idempotency-Key")) -> Any:
        if (blocked := limited(request, cid)) is not None:
            return blocked
        return run(request, response, lambda c: svc().assess(snake(body), actor_of(actor), c, idem), cid, 201)

    @r.post("/position-size")
    def position_size(request: Request, response: Response, body: dict[str, Any] = Body(...), cid: str | None = Cid) -> Any:
        if (blocked := limited(request, cid)) is not None:
            return blocked
        return run(request, response, lambda: svc().position_size(snake(body)), cid)

    @r.get("/assessments/{assessment_id}")
    def get_assessment(assessment_id: str, request: Request, response: Response, cid: str | None = Cid) -> Any:
        return run(request, response, lambda: svc().get_assessment(assessment_id), cid)

    @r.post("/assessments/{assessment_id}/validate")
    def validate(assessment_id: str, request: Request, response: Response, cid: str | None = Cid) -> Any:
        return run(request, response, lambda: svc().validate_assessment(assessment_id), cid)

    @r.post("/assessments/{assessment_id}/consume", dependencies=[AdminDep])
    def consume(assessment_id: str, request: Request, response: Response, body: dict[str, Any] = Body(default={}),
                cid: str | None = Cid, actor: str | None = Actor) -> Any:
        return run(request, response, lambda c: svc().set_assessment_state(assessment_id, "consumed", actor_of(actor),
                                                                          body.get("reason"), c), cid)

    @r.post("/assessments/{assessment_id}/cancel", dependencies=[AdminDep])
    def cancel(assessment_id: str, request: Request, response: Response, body: dict[str, Any] = Body(default={}),
               cid: str | None = Cid, actor: str | None = Actor) -> Any:
        return run(request, response, lambda c: svc().set_assessment_state(assessment_id, "cancelled", actor_of(actor),
                                                                          body.get("reason"), c), cid)

    @r.get("/account")
    def account(request: Request, response: Response, cid: str | None = Cid) -> Any:
        return run(request, response, lambda: svc().account(), cid)

    @r.get("/portfolio")
    def portfolio(request: Request, response: Response, cid: str | None = Cid) -> Any:
        return run(request, response, lambda: svc().portfolio(), cid)

    @r.get("/config")
    def get_config(request: Request, response: Response, cid: str | None = Cid) -> Any:
        return run(request, response, lambda: svc().config(), cid)

    @r.put("/config", dependencies=[AdminDep])
    def put_config(request: Request, response: Response, body: dict[str, Any] = Body(...), cid: str | None = Cid,
                   actor: str | None = Actor) -> Any:
        b = snake({k: v for k, v in body.items() if k != "changes"})
        b["changes"] = _config_keys(body.get("changes") or {})
        return run(request, response, lambda c: svc().update_config(b, actor_of(actor), c), cid)

    @r.get("/rules")
    def rules(request: Request, response: Response, cid: str | None = Cid) -> Any:
        return run(request, response, lambda: svc().rules(), cid)

    @r.get("/locks")
    def locks(request: Request, response: Response, history: bool = Query(True), cid: str | None = Cid) -> Any:
        return run(request, response, lambda: svc().locks(history), cid)

    @r.post("/locks", dependencies=[AdminDep])
    def create_lock(request: Request, response: Response, body: dict[str, Any] = Body(...), cid: str | None = Cid,
                    actor: str | None = Actor) -> Any:
        return run(request, response, lambda c: svc().create_lock(snake(body), actor_of(actor), c), cid, 201)

    @r.post("/locks/{lock_id}/release", dependencies=[AdminDep])
    def release_lock(lock_id: str, request: Request, response: Response, body: dict[str, Any] = Body(...),
                     cid: str | None = Cid, actor: str | None = Actor) -> Any:
        return run(request, response, lambda c: svc().release_lock(lock_id, snake(body), actor_of(actor), c), cid)

    @r.get("/history")
    def history(request: Request, response: Response, limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0),
                decision: str | None = Query(None, pattern="^(PASS|REVIEW|BLOCK|ERROR)$"), instrument: str | None = None,
                rule: str | None = Query(None, pattern=r"^R\d{3}$"), strategy: str | None = None,
                date_from: datetime | None = Query(None, alias="from"), date_to: datetime | None = Query(None, alias="to"),
                cid: str | None = Cid) -> Any:
        return run(request, response, lambda: svc().history(limit=limit, offset=offset, decision=decision,
                                                             symbol=instrument.upper() if instrument else None, rule_id=rule,
                                                             strategy=strategy, date_from=date_from, date_to=date_to), cid)

    @r.get("/history/rules")
    def rule_history(request: Request, response: Response, date_from: datetime | None = Query(None, alias="from"),
                     cid: str | None = Cid) -> Any:
        return run(request, response, lambda: svc().repo.rule_counts(date_from), cid)

    @r.get("/audit")
    def audit(request: Request, response: Response, limit: int = Query(100, ge=1, le=1000),
              entity_type: str | None = Query(None, alias="entityType"), cid: str | None = Cid) -> Any:
        return run(request, response, lambda: svc().audit(limit, entity_type), cid)

    @r.get("/events")
    def events(request: Request, response: Response, after: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=1000),
               event_type: str | None = Query(None, alias="type"), cid: str | None = Cid) -> Any:
        return run(request, response, lambda: svc().events(after, limit, event_type), cid)

    @r.post("/events/inbound", dependencies=[AdminDep])
    def inbound(request: Request, response: Response, body: dict[str, Any] = Body(...), cid: str | None = Cid,
                actor: str | None = Actor) -> Any:
        return run(request, response, lambda: svc().receive_event(snake(body), actor_of(actor)), cid)

    @r.get("/health")
    def health(request: Request, response: Response, cid: str | None = Cid) -> Any:
        return run(request, response, lambda: svc().health(), cid)

    return r


def _config_keys(changes: dict[str, Any]) -> dict[str, Any]:
    """Configuration keys are snake_case; camelCase keys from the browser are converted, but per-symbol tables
    (max_spread_points, ...) and rule ids keep their keys."""
    tables = {"max_slippage_points", "commission_per_lot", "max_spread_points", "blocked_sessions", "rules"}
    out: dict[str, Any] = {}
    for k, v in changes.items():
        key = _SNAKE.sub("_", k).lower()
        if key in tables and isinstance(v, dict):
            out[key] = {kk: (snake(vv) if key == "rules" else vv) for kk, vv in v.items()}
        elif isinstance(v, dict):
            out[key] = _config_keys(v)
        else:
            out[key] = v
    return out
