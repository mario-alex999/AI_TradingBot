"""/api/v1/orchestration - Module 08 Workflow Orchestration and Integration Core.

People sign in (session cookie + X-CSRF-Token on writes); services and n8n use bearer service tokens. Registration,
configuration, subscriptions and schedules need ``orchestration:manage`` (admin, human only); retry, cancel, replay and
dead-letter actions need ``orchestration:operate`` (admin/trader, human only); API triggers need ``orchestration:trigger``;
the n8n webhooks need ``orchestration:callback`` (service tokens only). No endpoint approves, executes or contacts MT5.

Health      GET  /health, /health/integrations, /overview, /metrics (Prometheus text), /catalogue, /info
Workflows   POST /workflows; GET /workflows, /workflows/{id}; PUT /workflows/{id}; GET|POST /workflows/{id}/versions
            POST /workflows/{id}/validate; POST /workflows/{id}/versions/{vid}/publish|activate|retire
            POST /workflows/{id}/state {state, reason, syncEngine}; GET /workflows/{id}/engine
Executions  POST /executions/trigger; GET /executions, /executions/{id}; POST /executions/{id}/retry|cancel
Events      POST /events/publish; GET /events, /events/{id}, /events/{id}/deliveries; POST /events/{id}/replay
            GET /events/stream (server-sent events); GET|POST /subscriptions; POST /subscriptions/{id}/enable|disable
Webhooks    POST /webhooks/n8n/callback, /webhooks/n8n/events
Monitoring  GET /market-monitoring/config|preflight|eligible|candle-status|dispatch-status (WF08-01, read-only)
Integrations GET /integrations, /integrations/{id}/health; POST /integrations/{id}/test
Config      GET|PUT /config
Schedules   GET|POST /schedules; PUT /schedules/{id}; POST /schedules/{id}/pause|resume; GET /schedules/{id}/runs
Failures    GET /dead-letters; GET /dead-letters/{id}/export; POST /dead-letters/{id}/retry|resolve
Leases      GET /locks; POST /locks/acquire|renew|release
Audit       GET /audit-logs
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime
from typing import Any, Callable

from fastapi import APIRouter, Body, Depends, Header, Query, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse

from aurum.api.app import AppState, camelize
from aurum.api.routes.risk import RateLimiter, snake
from aurum.api.security import require
from aurum.auth import roles as perm
from aurum.auth.service import Principal
from aurum.orchestration.common import Actor, OrchError
from aurum.orchestration.repository import stable_hash
from aurum.orchestration.security import SIGNATURE_HEADER, TIMESTAMP_HEADER, SignatureError, secret_value, verify


def service_getter(state: AppState, service: Any = None) -> Callable[[], Any]:
    holder: dict[str, Any] = {}

    def svc() -> Any:
        if service is not None:
            return service
        if "s" not in holder:
            from aurum.orchestration.service import OrchestrationService
            holder["s"] = OrchestrationService(state.engine, state.settings)
        return holder["s"]
    return svc


def _err(exc: OrchError, cid: str) -> JSONResponse:
    return JSONResponse(status_code=exc.status, headers={"X-Correlation-ID": cid}, content=camelize(
        {"error": {"code": exc.code, "message": exc.message, "details": exc.details, "correlation_id": cid},
         "detail": exc.message}))


def build_router(state: AppState, svc: Callable[[], Any]) -> APIRouter:
    r = APIRouter()
    write_limit, hook_limit = RateLimiter(60), RateLimiter(600)

    def run(response: Response, fn: Callable[[], Any], cid: str | None = None, status: int = 200) -> Any:
        cid = cid or str(uuid.uuid4())
        response.headers["X-Correlation-ID"] = cid
        try:
            out = fn()
        except OrchError as exc:
            return _err(exc, cid)
        response.status_code = status
        return camelize(out)

    def limited(limiter: RateLimiter, key: str) -> JSONResponse | None:
        if not limiter.allow(key):
            return JSONResponse(status_code=429, headers={"Retry-After": "60"},
                                content={"error": {"code": "RATE_LIMITED", "message": "Too many requests in a minute."},
                                         "detail": "Too many requests in a minute."})
        return None

    def idempotent(op: str, who: Principal, key: str | None, body: Any, response: Response, fn: Callable[[], tuple[Any, int]],
                   cid: str | None) -> Any:
        """Idempotency-Key header: the same key and body returns the stored response; a different body is a 409."""
        if not key:
            return run(response, lambda: _call(fn, response), cid)
        service = svc()
        scope, rhash = f"{op}:{who.id}"[:64], stable_hash(body)
        with service.repo.read() as s:
            rec = service.repo.idem_get(s, scope, key, service.now())
            if rec is not None:
                if rec.request_hash != rhash:
                    return _err(OrchError(409, "IDEMPOTENCY_KEY_REUSED", "This Idempotency-Key was used with a different "
                                          "request."), cid or str(uuid.uuid4()))
                response.headers["Idempotent-Replay"] = "true"
                response.status_code = rec.status_code
                return rec.response
        out = run(response, lambda: _call(fn, response), cid)
        if isinstance(out, JSONResponse):
            return out
        with service.repo.write() as s:
            if service.repo.idem_get(s, scope, key, service.now()) is None:
                service.repo.idem_put(s, scope, key, rhash, response.status_code, out, service.now())
        return out

    def _call(fn: Callable[[], tuple[Any, int]], response: Response) -> Any:
        out, status = fn()
        response.status_code = status
        return out

    def actor(p: Principal) -> Actor:
        return Actor.from_principal(p)

    View = Depends(require(perm.ORCH_VIEW))
    Manage = Depends(require(perm.ORCH_MANAGE, human=True))
    Operate = Depends(require(perm.ORCH_OPERATE, human=True))
    Trigger = Depends(require(perm.ORCH_TRIGGER))
    Hook = Depends(require(perm.ORCH_CALLBACK))
    Locks = Depends(require(perm.ORCH_LOCKS))
    Cid = Header(default=None, alias="X-Correlation-ID")
    Idem = Header(default=None, alias="Idempotency-Key")

    # ---- health and overview --------------------------------------------------------------------------------------------
    @r.get("/health")
    def health(response: Response, who: Principal = View) -> Any:
        return run(response, lambda: svc().health())

    @r.get("/health/integrations")
    def health_integrations(response: Response, who: Principal = View) -> Any:
        return run(response, lambda: svc().list_integrations())

    @r.get("/overview")
    def overview(response: Response, who: Principal = View) -> Any:
        return run(response, lambda: svc().overview())

    @r.get("/info")
    def info(response: Response, who: Principal = View) -> Any:
        return run(response, lambda: svc().info())

    @r.get("/metrics", response_class=PlainTextResponse)
    def metrics(who: Principal = View) -> Any:
        try:
            return PlainTextResponse(svc().metrics_text(), media_type="text/plain; version=0.0.4")
        except OrchError as exc:
            return _err(exc, str(uuid.uuid4()))

    @r.get("/catalogue")
    def catalogue(response: Response, who: Principal = View) -> Any:
        return run(response, lambda: svc().catalogue())

    # ---- workflows ----------------------------------------------------------------------------------------------------------
    @r.get("/workflows")
    def workflows(response: Response, who: Principal = View, state: str | None = None, category: str | None = None,
                  q: str | None = None) -> Any:
        return run(response, lambda: svc().list_workflows(state=state, category=category, q=q))

    @r.post("/workflows")
    def register(response: Response, body: dict[str, Any] = Body(...), cid: str | None = Cid, who: Principal = Manage) -> Any:
        b = snake({k: v for k, v in body.items() if k not in ("initialVersion", "initial_version")})
        iv = body.get("initialVersion", body.get("initial_version"))
        if isinstance(iv, dict):
            b["initial_version"] = {**snake({k: v for k, v in iv.items() if k not in ("inputSchema", "outputSchema")}),
                                    "input_schema": iv.get("inputSchema", iv.get("input_schema")),
                                    "output_schema": iv.get("outputSchema", iv.get("output_schema"))}
        return limited(write_limit, who.id) or run(response, lambda: svc().register_workflow(actor(who), b), cid, 201)

    @r.get("/workflows/{workflow_id}")
    def workflow(workflow_id: str, response: Response, who: Principal = View) -> Any:
        return run(response, lambda: svc().get_workflow(workflow_id))

    @r.put("/workflows/{workflow_id}")
    def update(workflow_id: str, response: Response, body: dict[str, Any] = Body(...), cid: str | None = Cid,
               who: Principal = Manage) -> Any:
        return limited(write_limit, who.id) or run(response, lambda: svc().update_workflow(actor(who), workflow_id, snake(body)), cid)

    @r.get("/workflows/{workflow_id}/versions")
    def versions(workflow_id: str, response: Response, who: Principal = View) -> Any:
        return run(response, lambda: svc().list_versions(workflow_id))

    @r.post("/workflows/{workflow_id}/versions")
    def add_version(workflow_id: str, response: Response, body: dict[str, Any] = Body(...), cid: str | None = Cid,
                    who: Principal = Manage) -> Any:
        # Schemas are passed through unchanged (their property names are the workflow's own).
        b = {**snake({k: v for k, v in body.items() if k not in ("inputSchema", "outputSchema")}),
             "input_schema": body.get("inputSchema", body.get("input_schema")),
             "output_schema": body.get("outputSchema", body.get("output_schema"))}
        return limited(write_limit, who.id) or run(response, lambda: svc().add_version(actor(who), workflow_id, b), cid, 201)

    @r.post("/workflows/{workflow_id}/validate")
    def validate(workflow_id: str, response: Response, body: dict[str, Any] = Body(default={}), cid: str | None = Cid,
                 who: Principal = Manage) -> Any:
        return run(response, lambda: svc().validate_version(actor(who), workflow_id, body.get("versionId")), cid)

    @r.post("/workflows/{workflow_id}/versions/{version_id}/{action}")
    def version_action(workflow_id: str, version_id: str, action: str, response: Response,
                       body: dict[str, Any] = Body(default={}), cid: str | None = Cid, who: Principal = Manage) -> Any:
        a, s = actor(who), svc()
        fns = {"publish": lambda: s.publish_version(a, workflow_id, version_id),
               "retire": lambda: s.retire_version(a, workflow_id, version_id),
               "activate": lambda: s.activate_version(a, workflow_id, version_id, body.get("reason"))}
        if action not in fns:
            return _err(OrchError(404, "NOT_FOUND", "Unknown version action."), cid or str(uuid.uuid4()))
        return run(response, fns[action], cid)

    @r.post("/workflows/{workflow_id}/state")
    def set_state(workflow_id: str, response: Response, body: dict[str, Any] = Body(...), cid: str | None = Cid,
                  who: Principal = Manage) -> Any:
        return run(response, lambda: svc().set_workflow_state(actor(who), workflow_id, str(body.get("state", "")),
                                                               body.get("reason"), bool(body.get("syncEngine", False))), cid)

    @r.get("/workflows/{workflow_id}/engine")
    def engine_view(workflow_id: str, response: Response, who: Principal = View) -> Any:
        return run(response, lambda: svc().engine_workflow(workflow_id))

    # ---- executions --------------------------------------------------------------------------------------------------------
    @r.post("/executions/trigger")
    def trigger(response: Response, body: dict[str, Any] = Body(...), cid: str | None = Cid, idem: str | None = Idem,
                who: Principal = Trigger) -> Any:
        if (resp := limited(write_limit, who.id)) is not None:
            return resp
        b = {**snake({k: v for k, v in body.items() if k != "input"}), "input": body.get("input", {})}
        if idem and not b.get("idempotency_key"):
            b["idempotency_key"] = idem
        b.setdefault("correlation_id", cid)

        def go() -> tuple[Any, int]:
            view, created = svc().trigger(actor(who), b)
            if created:
                try:
                    svc().dispatch_due(only=view["execution_id"])      # best effort; the worker retries otherwise
                    view = svc().get_execution(view["execution_id"])
                except OrchError:
                    pass
            return {**view, "duplicate": not created}, 201 if created else 200
        return idempotent("trigger", who, idem, b, response, go, cid)

    @r.get("/executions")
    def executions(response: Response, who: Principal = View, workflow_id: str | None = Query(None, alias="workflowId"),
                   status: list[str] | None = Query(None), symbol: str | None = None,
                   correlation_id: str | None = Query(None, alias="correlationId"),
                   trade_idea_id: str | None = Query(None, alias="tradeIdeaId"),
                   approval_id: str | None = Query(None, alias="approvalId"),
                   execution_id: str | None = Query(None, alias="executionId"),
                   trigger_type: str | None = Query(None, alias="triggerType"), since: datetime | None = None,
                   until: datetime | None = None, sort: str = "-created_at", page: int = Query(1, ge=1),
                   page_size: int = Query(50, ge=1, le=200, alias="pageSize")) -> Any:
        statuses = [x for s in status or [] for x in s.split(",") if x]
        return run(response, lambda: svc().list_executions(
            workflow_id=workflow_id, status=statuses or None, symbol=symbol, correlation_id=correlation_id,
            trade_idea_id=trade_idea_id, approval_id=approval_id, execution_id=execution_id, trigger_type=trigger_type,
            since=since, until=until, page=page, page_size=page_size, sort=sort.replace("createdAt", "created_at")))

    @r.get("/executions/{execution_id}")
    def execution(execution_id: str, response: Response, who: Principal = View) -> Any:
        return run(response, lambda: svc().get_execution(execution_id))

    @r.post("/executions/{execution_id}/retry")
    def retry(execution_id: str, response: Response, body: dict[str, Any] = Body(...), cid: str | None = Cid,
              who: Principal = Operate) -> Any:
        return limited(write_limit, who.id) or run(response, lambda: svc().retry_execution(actor(who), execution_id, snake(body)), cid)

    @r.post("/executions/{execution_id}/cancel")
    def cancel(execution_id: str, response: Response, body: dict[str, Any] = Body(...), cid: str | None = Cid,
               who: Principal = Operate) -> Any:
        return limited(write_limit, who.id) or run(response, lambda: svc().cancel_execution(actor(who), execution_id, snake(body)), cid)

    # ---- events ------------------------------------------------------------------------------------------------------------
    @r.post("/events/publish")
    def publish(response: Response, body: dict[str, Any] = Body(...), cid: str | None = Cid, idem: str | None = Idem,
                who: Principal = Depends(require(perm.EVENTS_IN))) -> Any:
        if (resp := limited(hook_limit, who.id)) is not None:
            return resp

        def go() -> tuple[Any, int]:
            view, created = svc().publish(actor(who), body)
            return {**view, "duplicate": not created}, 201 if created else 200
        return idempotent("publish", who, idem, body, response, go, cid)

    @r.get("/events")
    def events(response: Response, who: Principal = View, event_type: str | None = Query(None, alias="eventType"),
               source: str | None = None, status: str | None = None,
               correlation_id: str | None = Query(None, alias="correlationId"), since: datetime | None = None,
               until: datetime | None = None, after: int | None = Query(None, ge=0), page: int = Query(1, ge=1),
               page_size: int = Query(50, ge=1, le=500, alias="pageSize")) -> Any:
        return run(response, lambda: svc().list_events(event_type=event_type, source=source, status=status,
                                                       correlation_id=correlation_id, since=since, until=until, after=after,
                                                       page=page, page_size=page_size))

    @r.get("/events/stream")
    async def stream(request: Request, who: Principal = View, after: int | None = Query(None, ge=0)) -> Any:
        """Server-sent events: new orchestration events as they are stored. Reconnect with ?after=<last id>."""
        service = svc()
        try:
            first = await asyncio.to_thread(service.list_events, page_size=1)
        except OrchError as exc:
            return _err(exc, str(uuid.uuid4()))
        start = (first["items"][0]["sequence"] if first["items"] else 0) if after is None else after

        async def gen() -> Any:
            cursor, idle = start, 0
            yield "retry: 3000\n\n"
            while not await request.is_disconnected():
                batch = (await asyncio.to_thread(service.list_events, after=cursor, page_size=100))["items"]
                for e in batch:
                    cursor = e["sequence"]
                    yield f"id: {cursor}\nevent: {e['event_type']}\ndata: {json.dumps(camelize(e), default=str)}\n\n"
                if not batch:
                    idle += 1
                    if idle >= 15:
                        idle = 0
                        yield ": keep-alive\n\n"
                await asyncio.sleep(1.0)
        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @r.get("/events/{event_id}")
    def event(event_id: str, response: Response, who: Principal = View) -> Any:
        return run(response, lambda: svc().get_event(event_id))

    @r.get("/events/{event_id}/deliveries")
    def deliveries(event_id: str, response: Response, who: Principal = View) -> Any:
        return run(response, lambda: svc().event_deliveries(event_id))

    @r.post("/events/{event_id}/replay")
    def replay(event_id: str, response: Response, body: dict[str, Any] = Body(...), cid: str | None = Cid,
               who: Principal = Operate) -> Any:
        return limited(write_limit, who.id) or run(response, lambda: svc().replay_event(actor(who), event_id, snake(body)), cid)

    @r.get("/subscriptions")
    def subscriptions(response: Response, who: Principal = View) -> Any:
        return run(response, lambda: svc().list_subscriptions())

    @r.post("/subscriptions")
    def subscribe(response: Response, body: dict[str, Any] = Body(...), cid: str | None = Cid, who: Principal = Manage) -> Any:
        b = {**snake({k: v for k, v in body.items() if k != "filter"}), "filter": body.get("filter")}
        return run(response, lambda: svc().create_subscription(actor(who), b), cid, 201)

    @r.post("/subscriptions/{subscription_id}/{action}")
    def sub_action(subscription_id: str, action: str, response: Response, cid: str | None = Cid, who: Principal = Manage) -> Any:
        if action not in ("enable", "disable"):
            return _err(OrchError(404, "NOT_FOUND", "Unknown action."), cid or str(uuid.uuid4()))
        return run(response, lambda: svc().set_subscription_active(actor(who), subscription_id, action == "enable"), cid)

    # ---- n8n webhooks (ingress) ------------------------------------------------------------------------------------------
    @r.post("/webhooks/n8n/callback")
    async def n8n_callback(request: Request, response: Response, who: Principal = Hook) -> Any:
        if (resp := limited(hook_limit, who.id)) is not None:
            return resp
        raw = await request.body()
        cid = request.headers.get("X-Correlation-ID") or str(uuid.uuid4())
        try:
            status, out = await asyncio.to_thread(svc().handle_callback, actor(who), raw, dict(request.headers))
        except OrchError as exc:
            return _err(exc, cid)
        return JSONResponse(status_code=status, content=camelize(out), headers={"X-Correlation-ID": cid})

    @r.post("/webhooks/n8n/events")
    async def n8n_events(request: Request, who: Principal = Hook) -> Any:
        if (resp := limited(hook_limit, who.id)) is not None:
            return resp
        raw = await request.body()
        cid = request.headers.get("X-Correlation-ID") or str(uuid.uuid4())
        service = svc()

        def go() -> tuple[int, Any]:
            cfg = service.config()
            sig, ts = request.headers.get(SIGNATURE_HEADER), request.headers.get(TIMESTAMP_HEADER)
            if sig or cfg.callbacks.signature_required:
                try:
                    verify(raw, ts, sig, [x for x in (secret_value(service.settings, "n8n_webhook_shared_secret"),
                                                      secret_value(service.settings, "n8n_webhook_shared_secret_previous")) if x],
                           service.now(), cfg.callbacks.max_skew_seconds)
                except SignatureError as exc:
                    service._security_event(actor(who), "security.n8n_event_rejected", exc.code)
                    raise OrchError(401, exc.code, exc.message) from None
            try:
                body = json.loads(raw.decode("utf-8") or "{}")
            except (UnicodeDecodeError, ValueError):
                raise OrchError(400, "INVALID_EVENT", "The body is not JSON.") from None
            view, created = service.publish(actor(who), body, via_n8n=True)
            return (201 if created else 200), {**view, "duplicate": not created}
        try:
            status, out = await asyncio.to_thread(go)
        except OrchError as exc:
            return _err(exc, cid)
        return JSONResponse(status_code=status, content=camelize(out), headers={"X-Correlation-ID": cid})

    # ---- integrations, config, schedules -------------------------------------------------------------------------------------
    @r.get("/integrations")
    def integrations(response: Response, who: Principal = View) -> Any:
        return run(response, lambda: svc().list_integrations())

    @r.get("/integrations/{integration_id}/health")
    def integration_health(integration_id: str, response: Response, who: Principal = View) -> Any:
        return run(response, lambda: svc().integration_health(integration_id))

    @r.post("/integrations/{integration_id}/test")
    def integration_test(integration_id: str, response: Response, who: Principal = Operate) -> Any:
        return limited(write_limit, who.id) or run(response, lambda: svc().check_integration(integration_id, actor(who)))

    def _config_view(out: dict[str, Any]) -> dict[str, Any]:
        # The configuration document keeps its snake_case keys so it can be edited and sent back to PUT /config unchanged.
        return {**camelize({k: v for k, v in out.items() if k != "config"}), "config": out.get("config")}

    @r.get("/config")
    def get_config(response: Response, who: Principal = View) -> Any:
        try:
            return _config_view(svc().get_config())
        except OrchError as exc:
            return _err(exc, str(uuid.uuid4()))

    @r.put("/config")
    def put_config(response: Response, body: dict[str, Any] = Body(...), cid: str | None = Cid, who: Principal = Manage) -> Any:
        # The configuration document keeps its snake_case keys (it mirrors config/orchestration.yaml).
        cid = cid or str(uuid.uuid4())
        response.headers["X-Correlation-ID"] = cid
        try:
            return _config_view(svc().put_config(actor(who), body))
        except OrchError as exc:
            return _err(exc, cid)

    @r.get("/schedules")
    def schedules(response: Response, who: Principal = View) -> Any:
        return run(response, lambda: svc().list_schedules())

    @r.post("/schedules")
    def create_schedule(response: Response, body: dict[str, Any] = Body(...), cid: str | None = Cid,
                        who: Principal = Manage) -> Any:
        b = {**snake({k: v for k, v in body.items() if k != "input"}), "input": body.get("input", {})}
        return run(response, lambda: svc().create_schedule(actor(who), b), cid, 201)

    @r.put("/schedules/{schedule_id}")
    def update_schedule(schedule_id: str, response: Response, body: dict[str, Any] = Body(...), cid: str | None = Cid,
                        who: Principal = Manage) -> Any:
        b = {**snake({k: v for k, v in body.items() if k != "input"}), "input": body.get("input")}
        return run(response, lambda: svc().update_schedule(actor(who), schedule_id, b), cid)

    @r.post("/schedules/{schedule_id}/{action}")
    def schedule_action(schedule_id: str, action: str, response: Response, cid: str | None = Cid,
                        who: Principal = Operate) -> Any:
        if action not in ("pause", "resume"):
            return _err(OrchError(404, "NOT_FOUND", "Unknown action."), cid or str(uuid.uuid4()))
        return run(response, lambda: svc().set_schedule_enabled(actor(who), schedule_id, action == "resume"), cid)

    @r.get("/schedules/{schedule_id}/runs")
    def schedule_runs(schedule_id: str, response: Response, who: Principal = View) -> Any:
        return run(response, lambda: svc().schedule_history(schedule_id))

    # ---- dead letters ----------------------------------------------------------------------------------------------------
    @r.get("/dead-letters")
    def dead_letters(response: Response, who: Principal = View, status: str = "open", kind: str | None = None,
                     page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200, alias="pageSize")) -> Any:
        return run(response, lambda: svc().list_dead_letters(status=status, kind=kind, page=page, page_size=page_size))

    @r.get("/dead-letters/{dl_id}/export")
    def export(dl_id: str, response: Response, who: Principal = View) -> Any:
        return run(response, lambda: svc().export_dead_letter(dl_id))

    @r.post("/dead-letters/{dl_id}/retry")
    def dl_retry(dl_id: str, response: Response, body: dict[str, Any] = Body(...), cid: str | None = Cid,
                 who: Principal = Operate) -> Any:
        return limited(write_limit, who.id) or run(response, lambda: svc().retry_dead_letter(actor(who), dl_id, snake(body)), cid)

    @r.post("/dead-letters/{dl_id}/resolve")
    def dl_resolve(dl_id: str, response: Response, body: dict[str, Any] = Body(...), cid: str | None = Cid,
                   who: Principal = Operate) -> Any:
        return run(response, lambda: svc().resolve_dead_letter(actor(who), dl_id, snake(body)), cid)

    # ---- leases ----------------------------------------------------------------------------------------------------------------
    @r.get("/locks")
    def locks(response: Response, who: Principal = View, held_only: bool = Query(True, alias="heldOnly")) -> Any:
        return run(response, lambda: svc().list_locks(held_only))

    @r.post("/locks/{action}")
    def lock_action(action: str, response: Response, body: dict[str, Any] = Body(...), cid: str | None = Cid,
                    who: Principal = Locks) -> Any:
        b, a, s = snake(body), actor(who), svc()
        fns = {"acquire": lambda: s.acquire_lock(a, str(b.get("scope", "")), str(b.get("value", "")),
                                                 int(b.get("ttl_seconds", 60)), b.get("owner")),
               "renew": lambda: s.renew_lock(a, str(b.get("lock_key", "")), str(b.get("token", "")), int(b.get("ttl_seconds", 60))),
               "release": lambda: s.release_lock(a, str(b.get("lock_key", "")), str(b.get("token", "")))}
        if action not in fns:
            return _err(OrchError(404, "NOT_FOUND", "Unknown lock action."), cid or str(uuid.uuid4()))
        return limited(hook_limit, who.id) or run(response, fns[action], cid)

    # ---- audit --------------------------------------------------------------------------------------------------------------
    @r.get("/audit-logs")
    def audit(response: Response, who: Principal = View, action: str | None = None,
              entity_type: str | None = Query(None, alias="entityType"), entity_id: str | None = Query(None, alias="entityId"),
              actor_name: str | None = Query(None, alias="actor"), since: datetime | None = None,
              before: int | None = Query(None, ge=1), limit: int = Query(200, ge=1, le=1000)) -> Any:
        return run(response, lambda: svc().audit_log(action=action, entity_type=entity_type, entity_id=entity_id,
                                                     actor=actor_name, since=since, limit=limit, before=before))

    # ---- WF08-01 market monitoring (read-only checks the n8n workflow branches on) ---------------------------------------
    def monitor() -> Any:
        from aurum.orchestration.market_monitoring import MarketMonitor
        service = svc()
        return MarketMonitor(service.repo.engine, service.config, clock=service.now)

    @r.get("/market-monitoring/config")
    def mm_config(response: Response, who: Principal = View) -> Any:
        return run(response, lambda: monitor().config_view())

    @r.get("/market-monitoring/preflight")
    def mm_preflight(response: Response, who: Principal = View) -> Any:
        return run(response, lambda: monitor().preflight())

    @r.get("/market-monitoring/eligible")
    def mm_eligible(response: Response, symbols: str | None = Query(None, max_length=200),
                    timeframes: str | None = Query(None, max_length=60), who: Principal = View) -> Any:
        split = lambda v: [x.strip() for x in v.split(",") if x.strip()] if v else None  # noqa: E731
        return run(response, lambda: monitor().eligible(split(symbols), split(timeframes)))

    @r.get("/market-monitoring/candle-status")
    def mm_candle(response: Response, symbol: str = Query(..., max_length=16), timeframe: str = Query(..., max_length=4),
                  who: Principal = View) -> Any:
        return run(response, lambda: monitor().candle_status(symbol, timeframe))

    @r.get("/market-monitoring/dispatch-status")
    def mm_dispatch(response: Response, symbol: str = Query(..., max_length=16), timeframe: str = Query(..., max_length=4),
                    candle_open: datetime = Query(..., alias="candleOpen"), who: Principal = View) -> Any:
        return run(response, lambda: monitor().dispatch_status(symbol, timeframe, candle_open))

    return r
