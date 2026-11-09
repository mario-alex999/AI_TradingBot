"""/api/v1/approvals and /api/v1/trade-management - Module 07 Human Approval and Trade Management.

Every endpoint needs a signed-in person (session cookie + X-CSRF-Token on writes) or, where stated, a service token.
Decisions (approve, reject, request modification, management approve/reject) are human-only: a service token gets 403
whatever its scopes. No endpoint places, modifies or closes an order; approved work waits for Module 09.

Approvals
GET  /approvals                         list (status, symbol, direction, timeframe, risk, strategy, since, until, sort, page)
GET  /approvals/pending                 open requests, soonest expiry first
GET  /approvals/dashboard               counts, pending queue, statistics
GET  /approvals/statistics              decision statistics (?since=)
GET  /approvals/{id}                    full detail: request, versions, decisions, risk snapshots, handoff
GET  /approvals/{id}/evidence           current evidence snapshot (?snapshotId= for an older one)
GET  /approvals/{id}/history            audit trail of this request
POST /approvals/{id}/review             mark under review (opening never approves)
POST /approvals/{id}/approve            human approval with confirmation; see the service for every check
POST /approvals/{id}/reject             reason required
POST /approvals/{id}/request-modification
POST /approvals/{id}/cancel             reason required
POST /approvals/{id}/revalidate-risk    fresh Module 06 assessment and evidence
GET  /approvals/ideas, /approvals/ideas/{id}, /approvals/ideas/{id}/evidence
POST /approvals/ideas                   submit a trade idea (person or service with approvals:create)
POST /approvals/sync                    pull Module 05 candidates now
GET  /approvals/policy                  active policy and history;  PUT (admin) new version with reason
GET  /approvals/assignments             reviewer assignments;       POST (admin) create/deactivate
GET  /approvals/notifications           stored notifications for the signed-in person (?unreadOnly=)
POST /approvals/notifications/read      {id} or {} for all
GET  /approvals/audit                   audit log with hash-chain check
GET  /approvals/events                  outbox (?after=&type=), for n8n and Module 09
GET  /approvals/events/stream           server-sent events: new outbox events as they are written
POST /approvals/events/inbound          service/n8n events (deduplicated; cannot approve)
GET  /approvals/execution/queue         Module 09: pending execution requests (service token, execution:read)
POST /approvals/execution/results       Module 09: report an outcome (service token, execution:report)
GET  /approvals/health

Trade management
GET  /trade-management/positions, /trade-management/positions/{id}
GET  /trade-management/requests (?status=), /trade-management/requests/{id}
POST /trade-management/requests        create (any action; EMERGENCY_EXIT needs management:emergency)
POST /trade-management/requests/{id}/approve | /reject
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime
from typing import Any, Callable

from fastapi import APIRouter, Body, Depends, Header, Query, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from aurum.api.app import AppState, camelize
from aurum.api.routes.risk import RateLimiter, snake
from aurum.api.security import require
from aurum.approvals import intake
from aurum.approvals.service import Actor, ApprovalError, ApprovalService
from aurum.auth import roles as perm
from aurum.auth.service import Principal


def service_getter(state: AppState, service: ApprovalService | None, risk: Callable[[], Any], decision: Callable[[], Any],
                   technical: Any = None, intelligence: Any = None) -> Callable[[], ApprovalService]:
    holder: dict[str, ApprovalService] = {}

    def svc() -> ApprovalService:
        if service is not None:
            return service
        if "s" not in holder:
            from aurum.approvals.wiring import build_service
            holder["s"] = build_service(state.engine, state.settings, risk, decision,
                                        (lambda: intelligence) if intelligence is not None else None)
        return holder["s"]
    return svc


def _error(exc: ApprovalError, cid: str) -> JSONResponse:
    return JSONResponse(status_code=exc.status, headers={"X-Correlation-ID": cid}, content=camelize(
        {"error": {"code": exc.code, "message": exc.message, "details": exc.details, "correlation_id": cid},
         "detail": exc.message}))


def build_routers(state: AppState, svc: Callable[[], ApprovalService]) -> tuple[APIRouter, APIRouter]:
    a, m = APIRouter(), APIRouter()
    decide_limiter = RateLimiter(30)

    def run(response: Response, fn: Callable[[], Any], cid: str | None = None, status: int = 200) -> Any:
        cid = cid or str(uuid.uuid4())
        response.headers["X-Correlation-ID"] = cid
        try:
            out = fn()
        except ApprovalError as exc:
            return _error(exc, cid)
        except intake.IntakeError as exc:
            return _error(ApprovalError(422, exc.code, exc.message, exc.details), cid)
        response.status_code = status
        return camelize(out)

    def limited(request: Request, who: Principal) -> JSONResponse | None:
        if not decide_limiter.allow(who.id):
            return JSONResponse(status_code=429, headers={"Retry-After": "60"},
                                content={"error": {"code": "RATE_LIMITED", "message": "Too many decisions in a minute."},
                                         "detail": "Too many decisions in a minute."})
        return None

    def method_of(who: Principal) -> str:
        return "web" if who.session_id else "api"

    View = Depends(require(perm.VIEW))
    Decide = Depends(require(perm.DECIDE, human=True))
    Cid = Header(default=None, alias="X-Correlation-ID")

    # ---- lists and dashboard (fixed paths before /{approval_id}) -----------------------------------------------------------
    @a.get("")
    def list_requests(response: Response, who: Principal = View, status: list[str] | None = Query(None),
                      symbol: str | None = None, direction: str | None = None, timeframe: str | None = None,
                      risk: str | None = None, strategy: str | None = None, since: datetime | None = None,
                      until: datetime | None = None, sort: str = "-created_at", page: int = Query(1, ge=1),
                      page_size: int = Query(50, ge=1, le=200, alias="pageSize")) -> Any:
        statuses = [x for s in status or [] for x in s.split(",") if x]
        return run(response, lambda: svc().list_requests(who, status=statuses or None, symbol=symbol, direction=direction,
                                                         timeframe=timeframe, risk=risk, strategy=strategy, since=since,
                                                         until=until, sort=_snake_sort(sort), page=page, page_size=page_size))

    @a.get("/pending")
    def pending(response: Response, who: Principal = View) -> Any:
        return run(response, lambda: svc().list_requests(who, status=["PENDING", "UNDER_REVIEW"], sort="expires_at",
                                                         page_size=200))

    @a.get("/dashboard")
    def dashboard(response: Response, who: Principal = View) -> Any:
        return run(response, lambda: svc().dashboard(who))

    @a.get("/statistics")
    def statistics(response: Response, who: Principal = View, since: datetime | None = None) -> Any:
        return run(response, lambda: svc().statistics(since))

    @a.get("/health")
    def health(response: Response, who: Principal = View) -> Any:
        return run(response, lambda: svc().health())

    # ---- ideas ---------------------------------------------------------------------------------------------------------------
    @a.get("/ideas")
    def ideas(response: Response, who: Principal = View, limit: int = Query(100, ge=1, le=500)) -> Any:
        return run(response, lambda: svc().ideas_list(who, limit))

    @a.post("/ideas")
    def submit_idea(response: Response, body: dict[str, Any] = Body(...), cid: str | None = Cid,
                    who: Principal = Depends(require(perm.CREATE_REQUEST))) -> Any:
        return run(response, lambda: svc().ingest(snake(body), source="manual" if who.is_human else "event",
                                                  who=Actor.of(who), correlation_id=cid), cid, 201)

    @a.get("/ideas/{idea_id}")
    def idea(idea_id: str, response: Response, who: Principal = View) -> Any:
        return run(response, lambda: svc().idea(idea_id, who))

    @a.get("/ideas/{idea_id}/evidence")
    def idea_evidence(idea_id: str, response: Response, who: Principal = View) -> Any:
        return run(response, lambda: svc().idea_evidence(idea_id, who))

    @a.post("/sync")
    def sync(response: Response, who: Principal = Depends(require(perm.CREATE_REQUEST))) -> Any:
        return run(response, lambda: svc().sync_module05())

    # ---- policy and assignments -----------------------------------------------------------------------------------------------
    @a.get("/policy")
    def get_policy(response: Response, who: Principal = View) -> Any:
        return run(response, lambda: svc().policy())

    @a.put("/policy")
    def put_policy(response: Response, body: dict[str, Any] = Body(...), cid: str | None = Cid,
                   who: Principal = Depends(require(perm.POLICY, human=True))) -> Any:
        return run(response, lambda: svc().update_policy(snake(body), who, cid), cid)

    @a.get("/assignments")
    def assignments(response: Response, who: Principal = View) -> Any:
        return run(response, lambda: svc().assignments())

    @a.post("/assignments")
    def set_assignment(response: Response, body: dict[str, Any] = Body(...), cid: str | None = Cid,
                       who: Principal = Depends(require(perm.POLICY, human=True))) -> Any:
        return run(response, lambda: svc().set_assignment(snake(body), who, cid), cid)

    # ---- notifications ------------------------------------------------------------------------------------------------------
    @a.get("/notifications")
    def notifications(response: Response, who: Principal = View, unread_only: bool = Query(False, alias="unreadOnly"),
                      limit: int = Query(100, ge=1, le=500)) -> Any:
        return run(response, lambda: {"items": svc().notifications(who, unread_only, limit),
                                      "unread": svc().unread_count(who) if who.is_human else 0})

    @a.post("/notifications/read")
    def read(response: Response, body: dict[str, Any] = Body(default={}), who: Principal = Depends(require(perm.VIEW, human=True))) -> Any:
        return run(response, lambda: svc().mark_read(who, body.get("id")))

    # ---- audit and events ------------------------------------------------------------------------------------------------------
    @a.get("/audit")
    def audit(response: Response, who: Principal = View, limit: int = Query(200, ge=1, le=1000),
              entity_type: str | None = Query(None, alias="entityType"), approval_id: str | None = Query(None, alias="approvalId"),
              actor: str | None = None) -> Any:
        return run(response, lambda: svc().audit_log(who, limit=limit, entity_type=entity_type, approval_id=approval_id,
                                                     actor=actor))

    @a.get("/events")
    def events(response: Response, who: Principal = View, after: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500),
               type: str | None = None) -> Any:  # noqa: A002 - query name
        return run(response, lambda: {"items": svc().repo.events(after, limit, type), "last_sequence": svc().repo.last_event_seq()})

    @a.get("/events/stream")
    async def stream(request: Request, who: Principal = View, after: int | None = Query(None, ge=0)) -> StreamingResponse:
        """Server-sent events. Each message is one outbox event (camelCase envelope). A comment line every 15 s keeps
        proxies from closing the connection. The client reconnects with ?after=<last sequence> to resume."""
        service = svc()
        start = service.repo.last_event_seq() if after is None else after

        async def gen() -> Any:
            cursor, idle = start, 0
            yield "retry: 3000\n\n"
            while not await request.is_disconnected():
                batch = await asyncio.to_thread(service.repo.events, cursor, 100)
                for e in batch:
                    cursor = e["sequence"]
                    yield f"id: {cursor}\nevent: {e['event_type']}\ndata: {json.dumps(camelize(e))}\n\n"
                if not batch:
                    idle += 1
                    if idle >= 15:
                        idle = 0
                        yield ": keep-alive\n\n"
                await asyncio.sleep(1.0)
        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @a.post("/events/inbound")
    def inbound(response: Response, body: dict[str, Any] = Body(...), who: Principal = Depends(require(perm.EVENTS_IN))) -> Any:
        return run(response, lambda: svc().receive_event(who, snake(body)), str(body.get("correlationId") or "") or None)

    # ---- Module 09 contract -------------------------------------------------------------------------------------------------
    @a.get("/execution/queue")
    def queue(response: Response, who: Principal = Depends(require(perm.EXECUTION_READ)),
              limit: int = Query(50, ge=1, le=200)) -> Any:
        return run(response, lambda: {"items": svc().execution_queue(who, limit)})

    @a.post("/execution/results")
    def results(response: Response, body: dict[str, Any] = Body(...),
                who: Principal = Depends(require(perm.EXECUTION_REPORT))) -> Any:
        return run(response, lambda: svc().report_execution(who, snake(body)))

    # ---- one request ---------------------------------------------------------------------------------------------------------
    @a.get("/{approval_id}")
    def detail(approval_id: str, response: Response, who: Principal = View) -> Any:
        return run(response, lambda: svc().detail(approval_id, who))

    @a.get("/{approval_id}/evidence")
    def evidence(approval_id: str, response: Response, who: Principal = View,
                 snapshot_id: str | None = Query(None, alias="snapshotId")) -> Any:
        return run(response, lambda: svc().evidence(approval_id, who, snapshot_id))

    @a.get("/{approval_id}/history")
    def history(approval_id: str, response: Response, who: Principal = View) -> Any:
        return run(response, lambda: {"items": svc().history(approval_id, who)})

    @a.post("/{approval_id}/review")
    def review(approval_id: str, response: Response, who: Principal = Decide) -> Any:
        return run(response, lambda: svc().start_review(approval_id, who))

    @a.post("/{approval_id}/approve")
    def approve(approval_id: str, request: Request, response: Response, body: dict[str, Any] = Body(...),
                cid: str | None = Cid, who: Principal = Decide) -> Any:
        return limited(request, who) or run(response, lambda: svc().approve(approval_id, who, _decision_body(body),
                                                                             method_of(who)), cid)

    @a.post("/{approval_id}/reject")
    def reject(approval_id: str, request: Request, response: Response, body: dict[str, Any] = Body(...),
               cid: str | None = Cid, who: Principal = Decide) -> Any:
        return limited(request, who) or run(response, lambda: svc().reject(approval_id, who, _decision_body(body)), cid)

    @a.post("/{approval_id}/request-modification")
    def modify(approval_id: str, request: Request, response: Response, body: dict[str, Any] = Body(...),
               cid: str | None = Cid, who: Principal = Decide) -> Any:
        return limited(request, who) or run(response, lambda: svc().request_modification(approval_id, who, _decision_body(body)), cid)

    @a.post("/{approval_id}/cancel")
    def cancel(approval_id: str, response: Response, body: dict[str, Any] = Body(...), cid: str | None = Cid,
               who: Principal = Depends(require(perm.CANCEL))) -> Any:
        return run(response, lambda: svc().cancel(approval_id, who, str(body.get("reason") or "")), cid)

    @a.post("/{approval_id}/revalidate-risk")
    def revalidate(approval_id: str, response: Response, cid: str | None = Cid,
                   who: Principal = Depends(require(perm.REVALIDATE))) -> Any:
        return run(response, lambda: svc().revalidate_risk(approval_id, who), cid)

    # ---- trade management ---------------------------------------------------------------------------------------------------
    MView = Depends(require(perm.VIEW))

    @m.get("/positions")
    def positions(response: Response, who: Principal = MView) -> Any:
        return run(response, lambda: svc().positions(who))

    @m.get("/positions/{position_id}")
    def position(position_id: str, response: Response, who: Principal = MView) -> Any:
        return run(response, lambda: svc().position(position_id, who))

    @m.get("/requests")
    def mgmt_requests(response: Response, who: Principal = MView, status: str | None = None,
                      limit: int = Query(100, ge=1, le=500)) -> Any:
        return run(response, lambda: {"items": svc().management_requests(who, status, limit)})

    @m.post("/requests")
    def mgmt_create(request: Request, response: Response, body: dict[str, Any] = Body(...),
                    who: Principal = Depends(require(perm.MANAGE_REQUEST, human=True))) -> Any:
        return limited(request, who) or run(response, lambda: svc().create_management_request(who, snake(body)), None, 201)

    @m.get("/requests/{request_id}")
    def mgmt_request(request_id: str, response: Response, who: Principal = MView) -> Any:
        return run(response, lambda: svc().management_request(request_id, who))

    @m.post("/requests/{request_id}/approve")
    def mgmt_approve(request_id: str, request: Request, response: Response, body: dict[str, Any] = Body(...),
                     who: Principal = Depends(require(perm.MANAGE_DECIDE, human=True))) -> Any:
        return limited(request, who) or run(response, lambda: svc().approve_management(request_id, who, _decision_body(body)))

    @m.post("/requests/{request_id}/reject")
    def mgmt_reject(request_id: str, request: Request, response: Response, body: dict[str, Any] = Body(...),
                    who: Principal = Depends(require(perm.MANAGE_DECIDE, human=True))) -> Any:
        return limited(request, who) or run(response, lambda: svc().reject_management(request_id, who, _decision_body(body)))

    return a, m


def _decision_body(body: dict[str, Any]) -> dict[str, Any]:
    """camelCase -> snake_case, but rule ids in acknowledgedWarnings and confirmation values are kept as sent."""
    out = snake({k: v for k, v in body.items() if k not in ("confirmation", "acknowledgedWarnings")})
    out["confirmation"] = snake(body.get("confirmation") or {})
    out["acknowledged_warnings"] = list(body.get("acknowledgedWarnings") or body.get("acknowledged_warnings") or [])
    return out


def _snake_sort(sort: str) -> str:
    desc = sort.startswith("-")
    name = snake({sort.lstrip("-"): 0})
    return ("-" if desc else "") + next(iter(name))
