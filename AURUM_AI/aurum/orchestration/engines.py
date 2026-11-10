"""Workflow engine adapters. All communication with n8n goes through ``N8nEngine``; the rest of Module 08 only sees the
``WorkflowEngine`` interface, so another n8n deployment style (or another engine) can be added without touching the core.

n8n interfaces used (n8n 2.x, documented public surfaces only; never n8n's internal database):
- ``GET /healthz`` and ``GET /healthz/readiness``           liveness and database readiness (no key needed);
- ``POST {webhook base}/{path}``                          workflow trigger through a Webhook node (no API key needed);
- public REST API ``/api/v1`` with ``X-N8N-API-KEY``:
  ``GET /workflows/{id}``, ``POST /workflows/{id}/activate|deactivate``, ``GET /executions/{id}``,
  ``GET /executions?workflowId=``, ``POST /executions/{id}/stop``.
Without an API key, triggering still works; metadata, activation, execution status and stop report ``not_configured``.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Protocol

import httpx

from aurum.orchestration import CONTRACT_VERSION
from aurum.orchestration.retry import PERMANENT, TRANSIENT, UNKNOWN, Classified, classify_exception, classify_http
from aurum.orchestration.security import WEBHOOK_TOKEN_HEADER, signed_headers

WEBHOOK_PATH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-/]{0,150}$")


class EngineNotConfigured(RuntimeError):
    """The operation needs configuration that is missing (for example the n8n API key)."""


class EngineError(RuntimeError):
    def __init__(self, classified: Classified):
        super().__init__(classified.message)
        self.classified = classified


@dataclass
class DispatchResult:
    outcome: str                         # accepted | transient | permanent | unknown
    http_status: int | None = None
    engine_execution_id: str | None = None
    error_category: str | None = None
    error: str | None = None
    latency_ms: float | None = None
    retry_after: float | None = None
    response_meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class EngineHealth:
    status: str                          # HEALTHY | DEGRADED | UNAVAILABLE | UNKNOWN
    detail: str
    latency_ms: float | None = None
    checks: dict[str, Any] = field(default_factory=dict)


class WorkflowEngine(Protocol):
    name: str
    label: str
    environment: str

    def api_configured(self) -> bool: ...
    def capabilities(self) -> dict[str, str]: ...
    def health(self) -> EngineHealth: ...
    def dispatch(self, webhook_path: str, request: dict[str, Any], now: datetime) -> DispatchResult: ...
    def get_workflow(self, engine_workflow_id: str) -> dict[str, Any]: ...
    def set_active(self, engine_workflow_id: str, active: bool) -> dict[str, Any]: ...
    def get_execution(self, engine_execution_id: str) -> dict[str, Any]: ...
    def list_executions(self, engine_workflow_id: str, limit: int = 20) -> list[dict[str, Any]]: ...
    def stop_execution(self, engine_execution_id: str) -> dict[str, Any]: ...


def check_webhook_path(path: str | None) -> str:
    p = (path or "").strip().strip("/")
    if not p or not WEBHOOK_PATH.match(p) or ".." in p:
        raise ValueError("webhook_path must be a plain n8n webhook path such as 'aurum-core-test-echo'.")
    return p


class N8nEngine:
    name = "n8n"
    label = "n8n"

    def __init__(self, *, base_url: str, webhook_base_url: str | None, api_key: str | None, signing_secret: str | None,
                 webhook_token: str | None, environment: str, timeout: float = 10.0,
                 transport: httpx.BaseTransport | None = None, clock: Callable[[], float] = time.perf_counter):
        self.base_url = base_url.rstrip("/")
        self.webhook_base_url = (webhook_base_url or f"{self.base_url}/webhook").rstrip("/")
        self._api_key = api_key or None
        self._secret = signing_secret or None
        self._token = webhook_token or None
        self.environment = environment
        self.timeout = timeout
        self._clock = clock
        self._http = httpx.Client(timeout=httpx.Timeout(timeout, connect=min(5.0, timeout)), transport=transport,
                                  limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
                                  headers={"User-Agent": "aurum-module08"}, follow_redirects=False)

    # ---- configuration ----------------------------------------------------------------------------------------------------
    def api_configured(self) -> bool:
        return self._api_key is not None

    def capabilities(self) -> dict[str, str]:
        api = "supported" if self._api_key else "needs N8N_API_KEY"
        return {"health": "supported", "trigger_webhook": "supported", "workflow_metadata": api, "activate_deactivate": api,
                "execution_status": api, "execution_list": api, "execution_stop": api,
                "workflow_registration_in_n8n": "not used: workflows are built and imported in n8n, then registered here",
                "execution_retry_in_n8n": "not used: AURUM retries through its own dispatch so idempotency stays in AURUM"}

    def describe(self) -> dict[str, Any]:
        return {"engine": "n8n", "base_url": self.base_url, "webhook_base_url": self.webhook_base_url,
                "environment": self.environment, "api_configured": self.api_configured(),
                "signing_configured": self._secret is not None, "webhook_token_configured": self._token is not None,
                "capabilities": self.capabilities()}

    # ---- health ---------------------------------------------------------------------------------------------------------------
    def health(self) -> EngineHealth:
        checks: dict[str, Any] = {}
        t0 = self._clock()
        try:
            r = self._http.get(f"{self.base_url}/healthz")
            checks["healthz"] = r.status_code
            rr = self._http.get(f"{self.base_url}/healthz/readiness")
            checks["readiness"] = rr.status_code
        except httpx.HTTPError as exc:
            return EngineHealth("UNAVAILABLE", f"n8n is not reachable at {self.base_url} ({type(exc).__name__}).",
                                checks=checks)
        latency = (self._clock() - t0) * 1000
        if checks["healthz"] != 200:
            return EngineHealth("UNAVAILABLE", f"n8n /healthz answered HTTP {checks['healthz']}.", latency, checks)
        if checks["readiness"] != 200:
            return EngineHealth("DEGRADED", "n8n is up but not ready (its database is not reachable).", latency, checks)
        if not self._api_key:
            return EngineHealth("DEGRADED", "n8n is up; the public API key is not configured, so execution status, "
                                "activation and stop are unavailable (triggering works).", latency, checks)
        try:
            ar = self._http.get(f"{self.base_url}/api/v1/workflows", params={"limit": 1}, headers=self._api_headers())
            checks["api"] = ar.status_code
        except httpx.HTTPError as exc:
            return EngineHealth("DEGRADED", f"n8n public API failed ({type(exc).__name__}).", latency, checks)
        if ar.status_code == 200:
            return EngineHealth("HEALTHY", "n8n is ready and the public API key works.", latency, checks)
        if ar.status_code in (401, 403):
            return EngineHealth("DEGRADED", "n8n refused the API key (HTTP %d): check N8N_API_KEY." % ar.status_code,
                                latency, checks)
        return EngineHealth("DEGRADED", f"n8n public API answered HTTP {ar.status_code}.", latency, checks)

    # ---- trigger ----------------------------------------------------------------------------------------------------------------
    def dispatch(self, webhook_path: str, request: dict[str, Any], now: datetime) -> DispatchResult:
        if request.get("environment") != self.environment:
            return DispatchResult(PERMANENT, error_category="ENVIRONMENT_MISMATCH",
                                  error=f"This n8n connection serves {self.environment}; refused a "
                                        f"{request.get('environment')} execution.")
        url = f"{self.webhook_base_url}/{check_webhook_path(webhook_path)}"
        body = json.dumps(request, default=str, separators=(",", ":")).encode()
        headers = {"Content-Type": "application/json", "X-Correlation-ID": str(request.get("correlationId", "")),
                   "X-Aurum-Execution-Id": str(request.get("executionId", "")), "X-Aurum-Attempt": str(request.get("attempt", "")),
                   "Idempotency-Key": str(request.get("executionId", "")), "X-Aurum-Contract": CONTRACT_VERSION,
                   **signed_headers(self._secret, body, now)}
        if self._token:
            headers[WEBHOOK_TOKEN_HEADER] = self._token
        t0 = self._clock()
        try:
            r = self._http.post(url, content=body, headers=headers)
        except httpx.HTTPError as exc:
            c = classify_exception(exc)
            return DispatchResult(c.kind, error_category=c.category, error=c.message, latency_ms=(self._clock() - t0) * 1000)
        latency = (self._clock() - t0) * 1000
        meta = {"http_status": r.status_code, "content_type": r.headers.get("content-type")}
        if 200 <= r.status_code < 300:
            engine_id = None
            try:
                data = r.json()
                if isinstance(data, dict):
                    engine_id = data.get("executionId") or data.get("n8nExecutionId")
                    meta["accepted"] = data.get("accepted")
            except ValueError:
                pass
            return DispatchResult("accepted", r.status_code, str(engine_id) if engine_id else None, latency_ms=latency,
                                  response_meta=meta)
        hint = None
        try:
            data = r.json()
            hint = str(data.get("message") or data.get("code") or "")[:160] if isinstance(data, dict) else None
        except ValueError:
            pass
        c = classify_http(r.status_code, r.headers.get("retry-after"), hint)
        return DispatchResult(c.kind, r.status_code, error_category=c.category, error=c.message, latency_ms=latency,
                              retry_after=c.retry_after, response_meta=meta)

    # ---- public API ------------------------------------------------------------------------------------------------------------
    def _api_headers(self) -> dict[str, str]:
        if not self._api_key:
            raise EngineNotConfigured("The n8n public API key (N8N_API_KEY) is not configured.")
        return {"X-N8N-API-KEY": self._api_key, "Accept": "application/json"}

    def _api(self, method: str, path: str, **kw: Any) -> Any:
        headers = self._api_headers()
        try:
            r = self._http.request(method, f"{self.base_url}/api/v1{path}", headers=headers, **kw)
        except httpx.HTTPError as exc:
            raise EngineError(classify_exception(exc)) from None
        if r.status_code >= 300:
            raise EngineError(classify_http(r.status_code, r.headers.get("retry-after")))
        return r.json() if r.content else {}

    def get_workflow(self, engine_workflow_id: str) -> dict[str, Any]:
        d = self._api("GET", f"/workflows/{engine_workflow_id}")
        hooks = [str((n.get("parameters") or {}).get("path", "")).strip("/") for n in d.get("nodes") or []
                 if str(n.get("type", "")).endswith(".webhook")]
        return {"id": str(d.get("id")), "name": d.get("name"), "active": bool(d.get("active")),
                "version_id": d.get("versionId"), "active_version_id": d.get("activeVersionId"),
                "updated_at": d.get("updatedAt"), "webhook_paths": hooks, "node_count": len(d.get("nodes") or [])}

    def set_active(self, engine_workflow_id: str, active: bool) -> dict[str, Any]:
        d = self._api("POST", f"/workflows/{engine_workflow_id}/{'activate' if active else 'deactivate'}")
        return {"id": str(d.get("id", engine_workflow_id)), "active": bool(d.get("active", active))}

    def get_execution(self, engine_execution_id: str) -> dict[str, Any]:
        d = self._api("GET", f"/executions/{engine_execution_id}", params={"includeData": "false"})
        return _execution_view(d)

    def list_executions(self, engine_workflow_id: str, limit: int = 20) -> list[dict[str, Any]]:
        d = self._api("GET", "/executions", params={"workflowId": engine_workflow_id, "limit": limit, "includeData": "false"})
        return [_execution_view(x) for x in d.get("data") or []]

    def stop_execution(self, engine_execution_id: str) -> dict[str, Any]:
        d = self._api("POST", f"/executions/{engine_execution_id}/stop")
        return {"id": str(d.get("id", engine_execution_id)), "status": d.get("status")}

    def close(self) -> None:
        self._http.close()


def _execution_view(d: dict[str, Any]) -> dict[str, Any]:
    return {"id": str(d.get("id")), "status": d.get("status"), "finished": bool(d.get("finished")), "mode": d.get("mode"),
            "workflow_id": str(d.get("workflowId")) if d.get("workflowId") is not None else None,
            "started_at": d.get("startedAt"), "stopped_at": d.get("stoppedAt")}


ENGINE_SUCCESS = frozenset({"success"})
ENGINE_FAILED = frozenset({"error", "crashed", "canceled", "cancelled"})
ENGINE_RUNNING = frozenset({"running", "new", "waiting", "unknown"})


class ControlledTestEngine:
    """CONTROLLED TEST ADAPTER - not n8n. Scripted outcomes for automated tests and local development only; Module 08
    refuses to use it in the demo or production environment. Every dispatch is recorded in ``calls``."""
    name = "test"
    label = "controlled test adapter (not n8n)"

    def __init__(self, environment: str = "test"):
        self.environment = environment
        self.calls: list[dict[str, Any]] = []
        self.script: list[DispatchResult | Exception] = []
        self.default = DispatchResult("accepted", 200)
        self.healthy = EngineHealth("HEALTHY", "controlled test adapter")
        self.api = True
        self.workflows: dict[str, dict[str, Any]] = {}
        self.executions: dict[str, dict[str, Any]] = {}
        self.stopped: list[str] = []
        self._n = 0

    def api_configured(self) -> bool:
        return self.api

    def capabilities(self) -> dict[str, str]:
        return {"health": "supported", "trigger_webhook": "supported (simulated)", "workflow_metadata": "supported (simulated)",
                "activate_deactivate": "supported (simulated)", "execution_status": "supported (simulated)",
                "execution_list": "supported (simulated)", "execution_stop": "supported (simulated)"}

    def describe(self) -> dict[str, Any]:
        return {"engine": "test", "label": self.label, "environment": self.environment, "api_configured": self.api,
                "capabilities": self.capabilities()}

    def health(self) -> EngineHealth:
        return self.healthy

    def dispatch(self, webhook_path: str, request: dict[str, Any], now: datetime) -> DispatchResult:
        if request.get("environment") != self.environment:
            return DispatchResult(PERMANENT, error_category="ENVIRONMENT_MISMATCH", error="environment mismatch")
        self.calls.append({"path": webhook_path, "request": request, "at": now})
        nxt = self.script.pop(0) if self.script else self.default
        if isinstance(nxt, Exception):
            c = classify_exception(nxt)
            return DispatchResult(c.kind, error_category=c.category, error=c.message)
        if nxt.outcome == "accepted" and nxt.engine_execution_id is None:
            self._n += 1
            res = DispatchResult("accepted", nxt.http_status or 200, str(1000 + self._n), latency_ms=1.0)
            self.executions[res.engine_execution_id] = {"id": res.engine_execution_id, "status": "running", "finished": False}
            return res
        return nxt

    def _need_api(self) -> None:
        if not self.api:
            raise EngineNotConfigured("API disabled in this test")

    def get_workflow(self, engine_workflow_id: str) -> dict[str, Any]:
        self._need_api()
        if engine_workflow_id not in self.workflows:
            raise EngineError(Classified(PERMANENT, "WORKFLOW_NOT_FOUND", "HTTP 404", 404))
        return self.workflows[engine_workflow_id]

    def set_active(self, engine_workflow_id: str, active: bool) -> dict[str, Any]:
        wf = self.get_workflow(engine_workflow_id)
        wf["active"] = active
        return {"id": engine_workflow_id, "active": active}

    def get_execution(self, engine_execution_id: str) -> dict[str, Any]:
        self._need_api()
        if engine_execution_id not in self.executions:
            raise EngineError(Classified(PERMANENT, "WORKFLOW_NOT_FOUND", "HTTP 404", 404))
        return self.executions[engine_execution_id]

    def list_executions(self, engine_workflow_id: str, limit: int = 20) -> list[dict[str, Any]]:
        self._need_api()
        return list(self.executions.values())[:limit]

    def stop_execution(self, engine_execution_id: str) -> dict[str, Any]:
        self._need_api()
        self.stopped.append(engine_execution_id)
        return {"id": engine_execution_id, "status": "canceled"}


__all__ = ["ControlledTestEngine", "DispatchResult", "EngineError", "EngineHealth", "EngineNotConfigured", "N8nEngine",
           "WorkflowEngine", "check_webhook_path", "ENGINE_SUCCESS", "ENGINE_FAILED", "ENGINE_RUNNING", "TRANSIENT",
           "PERMANENT", "UNKNOWN"]
