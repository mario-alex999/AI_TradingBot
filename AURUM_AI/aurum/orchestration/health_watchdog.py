"""WF08-09 System Health Watchdog - the backend half.

Checks that the whole platform works, service by service, and says *how* each one is: online, offline, degraded,
responding but stale, or unavailable because credentials are missing. An API that answers HTTP 200 is not taken as
proof that its data is current: the market data and the account mirror are judged by the time of their last
successful update, and every result carries that time.

* ``services`` - the services to check, in order (the workflow loops over them).
* ``check``    - one fresh check of one service, recorded under the run's id.
* ``evaluate`` - the overall status from the run's checks, recorded once; compared with the previous evaluation, a
                 service that has become unhealthy is announced on the dashboard (through Module 07), once.

The watchdog reads and reports. It restarts nothing, changes no configuration and touches no trade. It runs from the
Module 08 schedule, so if the worker itself stops, the watchdog stops with it: n8n's execution list then shows that
WF08-09 has not run.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy import func, select, text

from aurum.orchestration.config_model import HealthWatchdogCfg

WORKFLOW_ID = "wf08-09-health-watchdog"
WEBHOOK_PATH = "aurum-wf08-09-health-watchdog"
VERSION = "1.0.0"
WORKFLOW_NAME = "WF08-09 System Health Watchdog"
DESCRIPTION = ("Check n8n, the API, the database, the MT5 data service and Modules 01-07 (and Module 09 once deployed), "
               "record the results and alert when a service becomes unhealthy. Reads only.")
DEFAULT_SCHEDULE = "7 * * * *"                   # hourly
SCHEDULE_NAME = "WF08-09 health watchdog"
AUDIT_CHECK = "wf08-09.service_checked"
AUDIT_EVAL = "wf08-09.health_recorded"
AUDIT_ENTITY = "health_watchdog"

ONLINE, DEGRADED, STALE, OFFLINE, NO_CREDENTIALS = "ONLINE", "DEGRADED", "STALE", "OFFLINE", "NO_CREDENTIALS"
NOT_DEPLOYED, CLOSED, NOT_USED, UNKNOWN = "NOT_DEPLOYED", "MARKET_CLOSED", "NOT_USED", "UNKNOWN"
OK_STATES = (ONLINE, CLOSED, NOT_DEPLOYED, NOT_USED)
SEVERITY = {OFFLINE: 5, NO_CREDENTIALS: 4, STALE: 3, UNKNOWN: 3, DEGRADED: 2, ONLINE: 0, CLOSED: 0, NOT_DEPLOYED: 0, NOT_USED: 0}

MODULES = (("module_01", "Module 01 - Market Data"), ("module_02", "Module 02 - COT Intelligence"),
           ("module_03", "Module 03 - Technical Analysis"), ("module_04", "Module 04 - Market Intelligence"),
           ("module_05", "Module 05 - AI Analysis and Decision"), ("module_06", "Module 06 - Risk Management"),
           ("module_07", "Module 07 - Human Approval"))
SERVICES: tuple[tuple[str, str, bool], ...] = (
    ("n8n", "n8n", True), ("fastapi", "AURUM API (FastAPI)", True), ("database", "Database", True),
    ("mt5_data", "MT5 data service", True),
    *[(m, n, True) for m, n in MODULES],
    ("module_09", "Module 09 - MT5 Execution", False), ("ai_provider", "AI provider (Module 05 review)", False))
NAMES = {k: n for k, n, _ in SERVICES}
REQUIRED = {k for k, _, r in SERVICES if r}

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "properties": {"services": {"type": "array", "items": {"type": "string", "maxLength": 32}, "maxItems": 20},
                   "dryRun": {"type": "boolean"}, "reason": {"type": "string", "maxLength": 200}},
}
OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"status": {"type": "string", "enum": ["completed", "partial", "skipped"]}, "reason": {"type": "string"},
                   "overall": {"type": "string"}, "unhealthy": {"type": "array"}, "newlyUnhealthy": {"type": "array"},
                   "recovered": {"type": "array"}},
}


def _utc(t: datetime) -> datetime:
    return t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t.astimezone(timezone.utc)


def _iso(t: datetime | None) -> str | None:
    return _utc(t).isoformat().replace("+00:00", "Z") if t else None


def _ago(seconds: float | None) -> str:
    if seconds is None:
        return "never"
    m = int(seconds // 60)
    return f"{m} min ago" if m < 120 else f"{m // 60} h ago" if m < 2880 else f"{m // 1440} days ago"


def market_open(now: datetime) -> bool:
    """The FX week: from Sunday 22:00 to Friday 22:00 UTC (a common broker session; holidays are not modelled)."""
    now = _utc(now)
    wd, h = now.weekday(), now.hour
    if wd == 5:
        return False
    if wd == 6:
        return h >= 22
    if wd == 4:
        return h < 22
    return True


class HealthWatchdog:
    """Stateless over the database: safe to call from any API worker. ``svc`` is the OrchestrationService."""

    def __init__(self, svc: Any, approvals: Callable[[], Any] | None = None, deliver: Callable[[Any], dict[str, int]] | None = None):
        self.svc = svc
        self.engine = svc.repo.engine
        self._approvals_factory, self._deliver = approvals, deliver
        self._approvals: Any = None

    def cfg(self) -> HealthWatchdogCfg:
        return self.svc.config().health_watchdog

    @property
    def approvals(self) -> Any:
        if self._approvals is None:
            if self._approvals_factory is not None:
                self._approvals = self._approvals_factory()
            else:
                from aurum.approvals.wiring import build_service
                from aurum.risk.service import RiskService
                engine, settings = self.engine, self.svc.settings
                self._approvals = build_service(engine, settings, risk=lambda: RiskService(engine, settings))
        return self._approvals

    def config_view(self) -> dict[str, Any]:
        c = self.cfg()
        return {"workflow_id": WORKFLOW_ID, "enabled": c.enabled, "kill_switch": not c.enabled,
                "market_data_stale_minutes": c.market_data_stale_minutes, "account_stale_minutes": c.account_stale_minutes,
                "database_slow_ms": c.database_slow_ms, "notify_dashboard": c.notify_dashboard,
                "schedule_hint": DEFAULT_SCHEDULE, "services": [k for k, _, _ in SERVICES]}

    def preflight(self) -> dict[str, Any]:
        c = self.cfg()
        reasons = [] if c.enabled else ["The health watchdog is switched off (health_watchdog.enabled = false)."]
        return {"proceed": not reasons, "checks": {"enabled": c.enabled}, "reasons": reasons, "checked_at": _iso(self.svc.now())}

    def services(self, only: list[str] | None = None) -> dict[str, Any]:
        c = self.cfg()
        items = [{"service": k, "name": n, "required": r} for k, n, r in SERVICES if not only or k in only]
        return {"enabled": c.enabled, "items": items if c.enabled else [], "count": len(items) if c.enabled else 0}

    # ---- the checks ---------------------------------------------------------------------------------------------------------
    def _result(self, service: str, status: str, detail: str, last_success: datetime | None = None,
                **extra: Any) -> dict[str, Any]:
        return {"service": service, "name": NAMES.get(service, service), "required": service in REQUIRED, "status": status,
                "ok": status in OK_STATES, "detail": detail, "last_success_at": _iso(last_success),
                "checked_at": _iso(self.svc.now()), **extra}

    def _n8n(self) -> dict[str, Any]:
        from aurum.orchestration.db_models import OrchExecution as E
        h = self.svc.wf_engine.health()
        with self.svc.repo.read() as s:
            last = s.execute(select(func.max(E.finished_at)).where(E.status == "SUCCEEDED", E.n8n_execution_id.is_not(None))).scalar()
        detail = h.detail or ""
        if h.status == "HEALTHY":
            st = ONLINE
        elif h.status == "DEGRADED":
            st = DEGRADED
            if "key is not configured" in detail:
                detail += " Credentials missing: N8N_API_KEY (workflows still run)."
        else:
            st = OFFLINE
        return self._result("n8n", st, f"{detail} Last successful workflow run: {_iso(last) or 'never'}.", last,
                            latency_ms=h.latency_ms)

    def _fastapi(self) -> dict[str, Any]:
        from aurum.orchestration import CONTRACT_VERSION
        now = self.svc.now()
        return self._result("fastapi", ONLINE, f"Answering (orchestration contract {CONTRACT_VERSION}, environment "
                                                f"{self.svc.env}).", now)

    def _database(self) -> dict[str, Any]:
        t0 = time.perf_counter()
        try:
            with self.svc.repo.read() as s:
                s.execute(text("SELECT 1"))
        except Exception as exc:  # noqa: BLE001
            return self._result("database", OFFLINE, f"The database does not answer ({type(exc).__name__}).")
        ms = round((time.perf_counter() - t0) * 1000, 1)
        slow = ms > self.cfg().database_slow_ms
        return self._result("database", DEGRADED if slow else ONLINE,
                            f"{self.engine.dialect.name} answered in {ms} ms" + (" (slow)." if slow else "."), self.svc.now(),
                            latency_ms=ms)

    def _mt5_data(self) -> dict[str, Any]:
        """What Module 01 has actually stored, not whether something answered: the newest closed candle and the newest
        account snapshot, each against its freshness limit while the market is open."""
        from aurum.database.models import MarketCandle
        c = self.cfg()
        now = _utc(self.svc.now())
        try:
            from aurum.account.db_models import AccountSnapshot
            with self.svc.repo.read() as s:
                candle = s.execute(select(func.max(MarketCandle.ts_utc)).where(MarketCandle.is_complete.is_(True))).scalar()
                snap = s.execute(select(func.max(AccountSnapshot.captured_at))).scalar()
        except Exception as exc:  # noqa: BLE001
            return self._result("mt5_data", OFFLINE, f"The market data tables cannot be read ({type(exc).__name__}).")
        c_age = (now - _utc(candle)).total_seconds() if candle else None
        s_age = (now - _utc(snap)).total_seconds() if snap else None
        facts = f"Last candle stored {_ago(c_age)} ({_iso(candle) or 'none'}); last account snapshot {_ago(s_age)} ({_iso(snap) or 'none'})."
        last = max([t for t in (candle, snap) if t is not None], default=None)
        extra = {"last_candle_at": _iso(candle), "last_account_snapshot_at": _iso(snap)}
        if candle is None:
            return self._result("mt5_data", OFFLINE, "No market data has ever been stored. " + facts, None, **extra)
        if not market_open(now):
            return self._result("mt5_data", CLOSED, "The market is closed. " + facts, last, **extra)
        stale = c_age > c.market_data_stale_minutes * 60 or (s_age is not None and s_age > c.account_stale_minutes * 60)
        if stale:
            return self._result("mt5_data", STALE, "Responding but stale: the MT5 collector or its connection is not "
                                                   "delivering. " + facts, last, **extra)
        return self._result("mt5_data", ONLINE, facts, last, **extra)

    def _module(self, mid: str) -> dict[str, Any]:
        r = self.svc.check_integration(mid)
        with self.svc.repo.read() as s:
            from aurum.orchestration.db_models import OrchIntegration
            row = s.get(OrchIntegration, mid)
            last = row.last_healthy_at if row is not None else None
            base = row.base_url if row is not None else None
        detail, st = r.get("detail") or "", r["status"]
        if st == "HEALTHY":
            status = ONLINE
        elif st == "DEGRADED":
            status = DEGRADED
        elif st == "UNAVAILABLE":
            status = OFFLINE
            from aurum.database.connection import serverless
            if base and any(h in base for h in ("127.0.0.1", "localhost")) and serverless():
                detail += (" The internal API address points at this machine, which a serverless host does not serve: "
                           "set AURUM_INTERNAL_BASE_URL to the public API address.")
        elif "not deployed" in detail:
            status = NOT_DEPLOYED
        elif "token" in detail.lower() or "credential" in detail.lower():
            status = NO_CREDENTIALS
        else:
            status = UNKNOWN
        return self._result(mid, status, detail, last, latency_ms=r.get("latency_ms"), http_status=r.get("http_status"))

    def _ai_provider(self) -> dict[str, Any]:
        st = self.svc.settings
        provider = getattr(st, "ai_provider", "anthropic")
        if not getattr(st, "ai_review_enabled", False):
            return self._result("ai_provider", NOT_USED, "The optional AI review is switched off (AI_REVIEW_ENABLED is not "
                                                         "true); Module 05 runs its rule-based analysis only.")
        key = getattr(st, "openai_api_key" if provider == "openai" else "anthropic_api_key", None)
        if provider != "local" and not key:
            return self._result("ai_provider", NO_CREDENTIALS, f"The AI review is switched on but the {provider} API key "
                                                               "is not set, so no review can run.")
        return self._result("ai_provider", ONLINE, f"Configured for {provider}. The watchdog makes no paid API call to test it.")

    def _run_check(self, service: str) -> dict[str, Any]:
        try:
            if service == "n8n":
                return self._n8n()
            if service == "fastapi":
                return self._fastapi()
            if service == "database":
                return self._database()
            if service == "mt5_data":
                return self._mt5_data()
            if service == "ai_provider":
                return self._ai_provider()
            if service.startswith("module_"):
                return self._module(service)
        except Exception as exc:  # noqa: BLE001 - a failing check is a finding, not a crash
            return self._result(service, UNKNOWN, f"The check itself failed ({type(exc).__name__}: {str(exc)[:200]}).")
        return self._result(service, UNKNOWN, "Unknown service.")

    def check(self, service: str, *, run_id: str | None = None, dry_run: bool = False) -> dict[str, Any]:
        if service not in NAMES:
            return {"service": service, "status": "skipped", "ok": False, "detail": "Unknown service."}
        if not self.cfg().enabled:
            return {"service": service, "status": "skipped", "ok": False, "detail": "The health watchdog is switched off."}
        res = self._run_check(service)
        if not dry_run:
            with self.svc.repo.write() as s:
                self.svc.repo.audit(s, actor="wf08-09", actor_kind="system", action=AUDIT_CHECK, entity_type=AUDIT_ENTITY,
                                    entity_id=service, at=self.svc.now(), outcome="ok" if res["ok"] else "failed",
                                    details={"run_id": run_id, **res})
        return res

    # ---- evaluate ---------------------------------------------------------------------------------------------------------------
    def _run_checks(self, run_id: str) -> dict[str, dict[str, Any]]:
        from aurum.orchestration.db_models import OrchAuditLog as L
        since = _utc(self.svc.now()) - timedelta(hours=6)
        out: dict[str, dict[str, Any]] = {}
        with self.svc.repo.read() as s:
            for details in s.execute(select(L.details).where(L.action == AUDIT_CHECK, L.entity_type == AUDIT_ENTITY,
                                                             L.at >= since).order_by(L.seq)).scalars():
                if (details or {}).get("run_id") == run_id:
                    out[details["service"]] = details
        return out

    def _previous(self) -> dict[str, Any] | None:
        from aurum.orchestration.db_models import OrchAuditLog as L
        with self.svc.repo.read() as s:
            return s.execute(select(L.details).where(L.action == AUDIT_EVAL, L.entity_type == AUDIT_ENTITY)
                             .order_by(L.seq.desc()).limit(1)).scalar()

    def evaluate(self, *, run_id: str | None = None, dry_run: bool = False, only: list[str] | None = None) -> dict[str, Any]:
        """Evaluate service status from this run's checks (a service the run did not check is checked now), record the
        result once and alert on services that have become unhealthy since the previous evaluation."""
        c = self.cfg()
        if not c.enabled:
            return {"status": "skipped", "reason": "The health watchdog is switched off."}
        results = self._run_checks(run_id) if run_id else {}
        for k, _, _ in SERVICES:
            if (not only or k in only) and k not in results:
                results[k] = self._run_check(k)
        ordered = [results[k] for k, _, _ in SERVICES if k in results]
        unhealthy = [r for r in ordered if r["required"] and not r["ok"]]
        worst = max((SEVERITY.get(r["status"], 3) for r in ordered if r["required"]), default=0)
        overall = "HEALTHY" if worst == 0 else "DEGRADED" if worst <= 3 else "UNHEALTHY"
        statuses = {r["service"]: r["status"] for r in ordered}
        prev = (self._previous() or {}).get("statuses") or {}
        newly = [r for r in unhealthy if prev.get(r["service"]) != r["status"]]
        recovered = [k for k, st in prev.items() if st not in OK_STATES and statuses.get(k) in OK_STATES]
        report = {"overall": overall, "unhealthy": [{k: r[k] for k in ("service", "name", "status", "detail", "last_success_at")}
                                                    for r in unhealthy],
                  "newly_unhealthy": [r["service"] for r in newly], "recovered": recovered, "statuses": statuses,
                  "services": ordered, "checked": len(ordered)}
        if dry_run:
            return {**report, "status": "dry_run", "notified": 0,
                    "reason": f"Dry run: overall {overall}; {len(unhealthy)} required service(s) unhealthy."}
        now = self.svc.now()
        with self.svc.repo.write() as s:
            self.svc.repo.audit(s, actor="wf08-09", actor_kind="system", action=AUDIT_EVAL, entity_type=AUDIT_ENTITY,
                                entity_id="platform", at=now, outcome="ok" if overall == "HEALTHY" else "failed",
                                details={"run_id": run_id, "overall": overall, "statuses": statuses,
                                         "unhealthy": report["unhealthy"], "newly_unhealthy": report["newly_unhealthy"],
                                         "recovered": recovered,
                                         "last_success": {r["service"]: r["last_success_at"] for r in ordered}})
        notified, note_error = 0, None
        if newly and c.notify_dashboard:
            lines = [f"{r['name']}: {r['status'].replace('_', ' ').lower()} - {r['detail']}"[:300] for r in newly]
            try:
                nid = self.approvals.notify_system_event(
                    "orchestration.health_alert", "high" if overall == "UNHEALTHY" else "normal",
                    f"Platform {overall.lower()}: {', '.join(NAMES[r['service']] for r in newly)}"[:160],
                    "\n".join(lines), f"wf08-09:{run_id or _iso(now)}")
                notified = 1 if nid else 0
            except Exception as exc:  # noqa: BLE001 - the result is recorded; a failed notification is reported
                note_error = type(exc).__name__
        delivered = self._send() if notified else {}
        return {**report, "status": "recorded", "notified": notified, "notification_error": note_error,
                "delivered_now": delivered, "recorded_at": _iso(now),
                "reason": (f"Overall {overall}: " + (", ".join(f"{r['name']} {r['status'].lower()}" for r in unhealthy)
                                                     or "every required service is working") + ".")[:600]}

    def _send(self) -> dict[str, int]:
        try:
            if self._deliver is not None:
                return self._deliver(self.approvals)
            from aurum.approvals.worker import Worker
            return Worker(self.approvals, self.svc.settings).deliver_notifications()
        except Exception as exc:  # noqa: BLE001
            return {"error": f"{type(exc).__name__}: {exc}"[:200]}        # type: ignore[dict-item]


# ---- installation (used by `python -m aurum.cli orchestration setup-wf08-09`) ----------------------------------------------
def install(svc: Any, actor: Any, n8n_workflow_id: str, *, schedule: str | None = DEFAULT_SCHEDULE,
            schedule_timezone: str = "UTC", activate: bool = True) -> dict[str, Any]:
    """Registers WF08-09, links it to the n8n workflow, adds version 1.0.0, validates and activates it and creates its
    schedule. It follows no events: it runs on its schedule. Safe to re-run."""
    from aurum.orchestration.common import OrchError
    steps: list[str] = []
    svc.ensure_ready()
    try:
        w = svc.get_workflow(WORKFLOW_ID)
    except OrchError:
        svc.register_workflow(actor, {"workflow_id": WORKFLOW_ID, "name": WORKFLOW_NAME, "category": "other",
                                      "trigger_types": ["schedule", "manual", "admin"], "owner": "module_08",
                                      "description": DESCRIPTION})
        steps.append("registered")
        w = svc.get_workflow(WORKFLOW_ID)
    if w["state"] in ("ACTIVE", "DEGRADED") and (w.get("n8n_workflow_id") != n8n_workflow_id
                                                  or w.get("webhook_path") != WEBHOOK_PATH):
        svc.set_workflow_state(actor, WORKFLOW_ID, "PAUSED", "re-linking WF08-09 to n8n")
        steps.append("paused to re-link")
        w = svc.get_workflow(WORKFLOW_ID)
    if w.get("n8n_workflow_id") != n8n_workflow_id or w.get("webhook_path") != WEBHOOK_PATH:
        svc.update_workflow(actor, WORKFLOW_ID, {"n8n_workflow_id": n8n_workflow_id, "webhook_path": WEBHOOK_PATH,
                                                 "row_version": w["row_version"]})
        steps.append(f"linked to n8n workflow {n8n_workflow_id} (webhook {WEBHOOK_PATH})")
        w = svc.get_workflow(WORKFLOW_ID)
    version = next((v for v in w.get("versions", []) if v["version"] == VERSION), None)
    if version is None:
        version = svc.add_version(actor, WORKFLOW_ID, {
            "version": VERSION, "input_schema": INPUT_SCHEMA, "output_schema": OUTPUT_SCHEMA,
            "description": "WF08-09 System Health Watchdog: checks every service, records the results and alerts on new problems."})
        if isinstance(version.get("version"), dict):        # tolerate a wrapped response
            version = version["version"]
        steps.append(f"added version {VERSION}")
    if version["status"] == "DRAFT":
        version = svc.validate_version(actor, WORKFLOW_ID, version["version_id"])["version"]
        steps.append(f"validated ({version['status']})")
    if activate:
        if version["status"] != "ACTIVE":
            svc.activate_version(actor, WORKFLOW_ID, version["version_id"], "WF08-09 installation")
            steps.append(f"activated version {VERSION}")
        if svc.get_workflow(WORKFLOW_ID)["state"] != "ACTIVE":
            svc.set_workflow_state(actor, WORKFLOW_ID, "ACTIVE", "WF08-09 installation")
            steps.append("workflow ACTIVE")
    subs: list[dict[str, Any]] = []
    sched = None
    if schedule:
        existing = [x for x in svc.list_schedules()["items"] if x["workflow_id"] == WORKFLOW_ID]
        if existing:
            sched = existing[0]
            steps.append(f"schedule already present ({sched['expression']} {sched['timezone']})")
        else:
            sched = svc.create_schedule(actor, {"name": SCHEDULE_NAME, "workflow_id": WORKFLOW_ID, "kind": "cron",
                                                "expression": schedule, "timezone": schedule_timezone, "input": {},
                                                "enabled": True})
            steps.append(f"schedule created ({schedule} {schedule_timezone})")
    final = svc.get_workflow(WORKFLOW_ID)
    return {"workflow_id": WORKFLOW_ID, "state": final["state"], "version": VERSION, "validation": version.get("validation"),
            "subscriptions": subs, "schedule": sched, "steps": steps}
