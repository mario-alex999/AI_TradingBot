"""Module API integration gateway: one HTTP client for calling AURUM modules (and later Module 09 and 10 services).

It only transports, validates and normalises; it holds no module business logic. Features: pooled connections (httpx),
per-call time-outs, correlation ids, a bearer service token for protected endpoints, retries for safe (GET) calls only,
a circuit breaker per module, and errors normalised to ``GatewayError(category, message, status)``.

Health is judged from what each module's own health/status endpoint *says*, never from "the URL answered":
e.g. Module 01 must report MT5 and collection connected, Module 06 every dependency ok, Module 07 its audit chain valid.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

import httpx

from aurum.orchestration.retry import TRANSIENT, classify_exception, classify_http

HEALTHY, DEGRADED, UNAVAILABLE, UNKNOWN = "HEALTHY", "DEGRADED", "UNAVAILABLE", "UNKNOWN"


class GatewayError(RuntimeError):
    def __init__(self, category: str, message: str, status: int | None = None, kind: str | None = None):
        super().__init__(message)
        self.category, self.message, self.status, self.kind = category, message, status, kind


@dataclass
class ModuleEndpoint:
    module_id: str
    name: str
    base_url: str | None              # None = not deployed / not configured
    health_path: str | None
    needs_token: bool = False
    timeout: float = 10.0


@dataclass
class GatewayResponse:
    status: int
    data: Any
    latency_ms: float


@dataclass
class HealthResult:
    status: str
    detail: str
    latency_ms: float | None = None
    http_status: int | None = None
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class _Breaker:
    failures: int = 0
    open_until: float = 0.0


DEFAULT_MODULES: tuple[tuple[str, str, str | None, bool], ...] = (
    ("module_01", "Module 01 - Market Data", "/health", False),
    ("module_02", "Module 02 - COT Intelligence", "/cot/status", False),
    ("module_03", "Module 03 - Technical Analysis", "/analysis/runs?limit=1", False),
    ("module_04", "Module 04 - Market Intelligence", "/intelligence/status", False),
    ("module_05", "Module 05 - AI Analysis and Decision", "/decision/status", False),
    ("module_06", "Module 06 - Risk Management", "/risk/health", False),
    ("module_07", "Module 07 - Human Approval", "/approvals/health", True),
    ("module_09", "Module 09 - MT5 Execution", "/health", True),
    ("module_10", "Module 10 - Monitoring and Analytics", "/health", True),
)


class ModuleGateway:
    def __init__(self, endpoints: dict[str, ModuleEndpoint], *, service_token: str | None = None,
                 transport: httpx.BaseTransport | None = None, failure_threshold: int = 5, open_seconds: float = 30.0,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic):
        self.endpoints = endpoints
        self._token = service_token or None
        self._http = httpx.Client(transport=transport, limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
                                  headers={"User-Agent": "aurum-module08-gateway"}, follow_redirects=False)
        self._breakers: dict[str, _Breaker] = {}
        self.failure_threshold, self.open_seconds = failure_threshold, open_seconds
        self._sleep, self._clock = sleep, clock

    # ---- calls ------------------------------------------------------------------------------------------------------------------
    def breaker_state(self, module_id: str) -> str:
        b = self._breakers.get(module_id)
        if b is None or b.failures < self.failure_threshold:
            return "closed"
        return "open" if self._clock() < b.open_until else "half_open"

    def call(self, module_id: str, method: str, path: str, *, json: Any = None, params: dict[str, Any] | None = None,
             correlation_id: str | None = None, idempotency_key: str | None = None, timeout: float | None = None,
             expect_keys: tuple[str, ...] = ()) -> GatewayResponse:
        ep = self.endpoints.get(module_id)
        if ep is None:
            raise GatewayError("UNSUPPORTED_WORKFLOW", f"Unknown module '{module_id}'.")
        if not ep.base_url:
            raise GatewayError("ENGINE_NOT_CONFIGURED", f"{ep.name} is not deployed or has no URL configured.")
        if self.breaker_state(module_id) == "open":
            raise GatewayError("DEPENDENCY_UNAVAILABLE", f"{ep.name}: circuit open after repeated failures.", kind=TRANSIENT)
        headers = {"Accept": "application/json"}
        if correlation_id:
            headers["X-Correlation-ID"] = correlation_id
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        if ep.needs_token and self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        safe = method.upper() in ("GET", "HEAD")
        attempts = 3 if safe else 1                       # never repeat a non-idempotent call automatically
        last: GatewayError | None = None
        for i in range(attempts):
            t0 = self._clock()
            try:
                r = self._http.request(method.upper(), f"{ep.base_url.rstrip('/')}{path}", json=json, params=params,
                                       headers=headers, timeout=timeout or ep.timeout)
            except httpx.HTTPError as exc:
                c = classify_exception(exc)
                last = GatewayError(c.category, f"{ep.name}: {c.message}", kind=c.kind)
                self._fail(module_id)
                if c.kind == TRANSIENT and i + 1 < attempts:
                    self._sleep(0.2 * (2 ** i))
                    continue
                raise last from None
            latency = (self._clock() - t0) * 1000
            if r.status_code >= 400:
                c = classify_http(r.status_code, r.headers.get("retry-after"))
                last = GatewayError(c.category, f"{ep.name}: {c.message}", r.status_code, c.kind)
                if r.status_code >= 500 or r.status_code == 429:
                    self._fail(module_id)
                    if c.kind == TRANSIENT and i + 1 < attempts:
                        self._sleep(0.2 * (2 ** i))
                        continue
                raise last
            self._ok(module_id)
            try:
                data = r.json() if r.content else None
            except ValueError:
                raise GatewayError("INVALID_PAYLOAD", f"{ep.name} returned a non-JSON body.", r.status_code) from None
            missing = [k for k in expect_keys if not isinstance(data, dict) or k not in data]
            if missing:
                raise GatewayError("INVALID_PAYLOAD", f"{ep.name} response is missing {missing}.", r.status_code)
            return GatewayResponse(r.status_code, data, latency)
        raise last or GatewayError("DEPENDENCY_UNAVAILABLE", f"{ep.name}: no response")

    def _fail(self, module_id: str) -> None:
        b = self._breakers.setdefault(module_id, _Breaker())
        b.failures += 1
        if b.failures >= self.failure_threshold:
            b.open_until = self._clock() + self.open_seconds

    def _ok(self, module_id: str) -> None:
        self._breakers[module_id] = _Breaker()

    # ---- health -----------------------------------------------------------------------------------------------------------------
    def check(self, module_id: str, now: datetime | None = None) -> HealthResult:
        ep = self.endpoints.get(module_id)
        if ep is None:
            return HealthResult(UNKNOWN, "Unknown module.")
        if not ep.base_url or not ep.health_path:
            return HealthResult(UNKNOWN, f"{ep.name} is not deployed yet (no URL configured).")
        if ep.needs_token and not self._token:
            return HealthResult(UNKNOWN, f"{ep.name} needs a service token: set ORCHESTRATION_SERVICE_TOKEN.")
        path, _, query = ep.health_path.partition("?")
        params = dict(p.split("=", 1) for p in query.split("&") if "=" in p) if query else None
        try:
            r = self.call(module_id, "GET", path, params=params, timeout=min(ep.timeout, 15.0))
        except GatewayError as exc:
            status = UNAVAILABLE if exc.status is None or (exc.status and exc.status >= 500) else DEGRADED
            if exc.status in (401, 403):
                return HealthResult(UNKNOWN, f"{ep.name} refused the service token (HTTP {exc.status}).", http_status=exc.status)
            return HealthResult(status, exc.message, http_status=exc.status)
        status, detail = evaluate(module_id, r.data if isinstance(r.data, dict) else {}, now or datetime.now(timezone.utc))
        return HealthResult(status, detail, r.latency_ms, r.status)

    def close(self) -> None:
        self._http.close()


def _age_hours(ts: Any, now: datetime) -> float | None:
    try:
        t = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return (now - t).total_seconds() / 3600
    except (TypeError, ValueError):
        return None


def evaluate(module_id: str, d: dict[str, Any], now: datetime) -> tuple[str, str]:
    """Reads a module's own health/status body and decides HEALTHY / DEGRADED / UNAVAILABLE."""
    if module_id == "module_01":
        if not (d.get("database") or {}).get("ok"):
            return UNAVAILABLE, "Database not reachable from the API."
        mt5, coll = d.get("mt5"), d.get("collection")
        if mt5 == "connected" and coll == "connected":
            return HEALTHY, "Database ok; MT5 feed and collection connected."
        return DEGRADED, f"Database ok; MT5 feed '{mt5}', collection '{coll}'."
    if module_id == "module_02":
        return (HEALTHY, "COT engine ok.") if d.get("engine") == "ok" else (DEGRADED, f"COT engine '{d.get('engine')}'.")
    if module_id == "module_03":
        items = d.get("items")
        if not isinstance(items, list):
            return DEGRADED, "Unexpected response from the analysis runs endpoint."
        if not items:
            return DEGRADED, "Engine reachable but no analysis run is stored yet."
        age = _age_hours(items[0].get("generatedAt") or items[0].get("createdAt"), now)
        bad = items[0].get("status") not in (None, "ok")
        detail = f"Latest analysis run {age:.0f} h ago ({items[0].get('symbol')} {items[0].get('timeframe')})." if age is not None else "Runs readable."
        return (DEGRADED, detail + " Its status was not ok.") if bad else (HEALTHY, detail)
    if module_id == "module_04":
        if not d.get("schemaReady"):
            return DEGRADED, f"Schema not ready: missing {d.get('missingTables')}."
        failing = [p.get("provider") for p in d.get("calendarProviders") or [] if p.get("status") in ("error", "failed", "unavailable")]
        return (DEGRADED, f"Providers failing: {failing}.") if failing else (HEALTHY, "Schema ready; configured providers ok.")
    if module_id == "module_05":
        if not d.get("schemaReady"):
            return DEGRADED, "Decision engine schema not ready."
        errors = [r.get("symbol") for r in d.get("recentRuns") or [] if r.get("status") not in (None, "ok")]
        return (DEGRADED, f"Recent runs with errors: {errors}.") if errors else (HEALTHY, "Decision engine ready; recent runs ok.")
    if module_id == "module_06":
        deps = d.get("dependencies") or {}
        bad = [k for k, v in deps.items() if isinstance(v, dict) and "ok" in v and not v["ok"]]
        bad += [f"instrument {k}" for k, v in (deps.get("instruments") or {}).items() if isinstance(v, dict) and not v.get("ok")]
        return (DEGRADED, f"Dependencies not ok: {bad}.") if bad else (HEALTHY, "Risk engine dependencies ok.")
    if module_id == "module_07":
        st = d.get("status")
        chain = (d.get("auditChain") or {}).get("valid")
        if st == "ok" and chain is not False:
            return HEALTHY, "Approval engine ok; audit chain valid."
        return DEGRADED, f"Approval engine status '{st}', audit chain valid: {chain}."
    st = str(d.get("status", "")).lower()
    if st in ("ok", "healthy", "up"):
        return HEALTHY, "Reports healthy."
    return DEGRADED, f"Reports status '{d.get('status')}'."
