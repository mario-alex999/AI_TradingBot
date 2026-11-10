"""Operations: dead letters, leases (locks), schedules, integration health, configuration, audit, overview and metrics."""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, delete, func, select, text
from sqlalchemy.exc import IntegrityError

from aurum.auth import roles as perm
from aurum.orchestration.catalogue import CATALOGUE
from aurum.orchestration.common import SYSTEM, Actor, OrchError, pick
from aurum.orchestration.config_model import parse_config
from aurum.orchestration.db_models import (OrchAuditLog, OrchCredential, OrchDeadLetter, OrchDelivery, OrchEvent,
                                           OrchExecution, OrchExecutionCallback, OrchHealthCheck, OrchIntegration, OrchLock,
                                           OrchSchedule, OrchScheduleRun, OrchSourceCursor, OrchWorkflow, OrchDeliveryAttempt)
from aurum.orchestration.gateway import DEGRADED, HEALTHY, UNAVAILABLE, UNKNOWN, ModuleGateway
from aurum.orchestration.repository import utc
from aurum.orchestration.scheduler import ScheduleError, next_run
from aurum.orchestration.scheduler import validate as validate_schedule
from aurum.orchestration.security import secret_status, secret_value

LOCK_SCOPES = ("workflow", "symbol", "symbol_timeframe", "account", "execution_request", "position")


class OpsMixin:
    # ---- dead letters -----------------------------------------------------------------------------------------------------
    def list_dead_letters(self, *, status: str | None = "open", kind: str | None = None, page: int = 1,
                          page_size: int = 50) -> dict[str, Any]:
        self.ensure_ready()
        conds = []
        if status and status != "all":
            conds.append(OrchDeadLetter.recovery_status == status)
        if kind:
            conds.append(OrchDeadLetter.kind == kind)
        with self.repo.read() as s:
            where = and_(*conds) if conds else True
            total = s.execute(select(func.count()).select_from(OrchDeadLetter).where(where)).scalar_one()
            rows = s.execute(select(OrchDeadLetter).where(where).order_by(OrchDeadLetter.failed_at.desc())
                             .offset((page - 1) * page_size).limit(page_size)).scalars().all()
            return {"items": [self.dl_view(d) for d in rows], "total": int(total), "page": page, "page_size": page_size}

    def _dl(self, s: Any, dl_id: str) -> OrchDeadLetter:
        d = s.get(OrchDeadLetter, dl_id)
        if d is None:
            raise OrchError(404, "DEAD_LETTER_NOT_FOUND", "No such dead letter.")
        return d

    def export_dead_letter(self, dl_id: str) -> dict[str, Any]:
        """Diagnostic bundle (credential-free: inputs and payloads were checked for credentials on intake)."""
        self.ensure_ready()
        with self.repo.read() as s:
            d = self._dl(s, dl_id)
            view, eid, evid = self.dl_view(d), d.execution_id, d.event_id
        out: dict[str, Any] = {"dead_letter": view, "exported_at": self.now(), "environment": self.env}
        if eid:
            out["execution"] = self.get_execution(eid)
        if evid:
            out["event"] = self.get_event(evid)
        return out

    def retry_dead_letter(self, actor: Actor, dl_id: str, body: dict[str, Any]) -> dict[str, Any]:
        self.ensure_ready()
        self.perm_check(actor, perm.ORCH_OPERATE, human=True)
        with self.repo.read() as s:
            d = self._dl(s, dl_id)
            kind, eid, did, status, evid = d.kind, d.execution_id, d.delivery_id, d.recovery_status, d.event_id
        if status != "open":
            raise OrchError(409, "DEAD_LETTER_CLOSED", f"This dead letter is already {status}.")
        if kind == "execution":
            return {"dead_letter_id": dl_id, "execution": self.retry_execution(actor, eid, body)}
        reason = str(pick(body, "reason") or "").strip()
        if not reason:
            raise OrchError(422, "REASON_REQUIRED", "Give a reason for the retry.")
        now = self.now()
        with self.repo.write() as s:
            ev = s.execute(select(OrchEvent).where(OrchEvent.event_id == evid)).scalar_one()
            spec = CATALOGUE.get(ev.event_type)
            if spec is None or spec.critical:
                raise OrchError(409, "CRITICAL_EVENT_NOT_REPLAYABLE", f"{ev.event_type} is owned by its module; resolve this "
                                "entry and let the owning module re-issue the event after its own validation.")
            dl = self._dl(s, dl_id)
            dv = s.get(OrchDelivery, did)
            dv.status, dv.attempts, dv.next_attempt_at, dv.last_error = "pending", 0, now, None
            dl.recovery_status, dl.resolved_by, dl.resolved_at, dl.resolution_note = "retried", actor.name, now, reason[:500]
            self._refresh_event_status(s, evid)
            self.repo.audit(s, actor=actor.name, actor_kind=actor.kind, action="dead_letter.retried", entity_type="dead_letter",
                            entity_id=dl_id, at=now, correlation_id=dl.correlation_id, details={"reason": reason[:500]})
        return {"dead_letter_id": dl_id, "delivery_id": did, "status": "pending"}

    def resolve_dead_letter(self, actor: Actor, dl_id: str, body: dict[str, Any]) -> dict[str, Any]:
        self.ensure_ready()
        self.perm_check(actor, perm.ORCH_OPERATE, human=True)
        note = str(pick(body, "note") or pick(body, "reason") or "").strip()
        if not note:
            raise OrchError(422, "REASON_REQUIRED", "Describe how this was resolved.")
        now = self.now()
        with self.repo.write() as s:
            d = self._dl(s, dl_id)
            if d.recovery_status != "open":
                raise OrchError(409, "DEAD_LETTER_CLOSED", f"Already {d.recovery_status}.")
            d.recovery_status, d.resolved_by, d.resolved_at, d.resolution_note = "resolved", actor.name, now, note[:2000]
            self.repo.audit(s, actor=actor.name, actor_kind=actor.kind, action="dead_letter.resolved", entity_type="dead_letter",
                            entity_id=dl_id, at=now, correlation_id=d.correlation_id, details={"note": note[:500]})
            return self.dl_view(d)

    # ---- leases --------------------------------------------------------------------------------------------------------------
    @staticmethod
    def lock_view(r: OrchLock, now: datetime) -> dict[str, Any]:
        return {"lock_key": r.lock_key, "scope": r.scope, "owner": r.owner, "fencing_token": r.fencing,
                "acquired_at": utc(r.acquired_at), "renewed_at": utc(r.renewed_at), "expires_at": utc(r.expires_at),
                "released_at": utc(r.released_at), "held": r.released_at is None and utc(r.expires_at) > now}

    def acquire_lock(self, actor: Actor, scope: str, value: str, ttl_seconds: int = 60, owner: str | None = None) -> dict[str, Any]:
        """Lease on ``scope:value``. An expired lease is taken over (fencing token + 1). Same owner = renew (re-entrant).
        Module 08 leases coordinate workflows; they never replace Module 06/09's own account and position protections."""
        self.ensure_ready()
        if scope not in LOCK_SCOPES:
            raise OrchError(422, "INVALID_LOCK", f"scope must be one of {list(LOCK_SCOPES)}.")
        if not value or len(value) > 120:
            raise OrchError(422, "INVALID_LOCK", "value is required (max 120 characters).")
        if not 5 <= int(ttl_seconds) <= 3600:
            raise OrchError(422, "INVALID_LOCK", "ttl_seconds must be between 5 and 3600.")
        owner = (owner or actor.name)[:80]
        key, now = f"{scope}:{value}", self.now()
        with self.repo.write() as s:
            r = s.get(OrchLock, key)
            expires = now + timedelta(seconds=int(ttl_seconds))
            if r is None:
                r = OrchLock(lock_key=key, scope=scope, owner=owner, token=str(uuid.uuid4()), fencing=1, acquired_at=now,
                             renewed_at=now, expires_at=expires)
                s.add(r)
            elif r.released_at is None and utc(r.expires_at) > now and r.owner != owner:
                self.count("lock_conflicts")
                raise OrchError(409, "LOCK_HELD", f"{key} is held by {r.owner}.",
                                {"owner": r.owner, "expires_at": utc(r.expires_at).isoformat()})
            elif r.released_at is None and utc(r.expires_at) > now:
                r.renewed_at, r.expires_at = now, expires
            else:
                stale = r.released_at is None
                r.owner, r.token, r.fencing, r.acquired_at, r.renewed_at, r.expires_at, r.released_at = (
                    owner, str(uuid.uuid4()), r.fencing + 1, now, now, expires, None)
                if stale:
                    self.count("locks_recovered_stale")
            s.flush()
            self.count("locks_acquired")
            return {**self.lock_view(r, now), "token": r.token}

    def renew_lock(self, actor: Actor, key: str, token: str, ttl_seconds: int = 60) -> dict[str, Any]:
        self.ensure_ready()
        now = self.now()
        with self.repo.write() as s:
            r = s.get(OrchLock, key)
            if r is None or r.token != token or r.released_at is not None or utc(r.expires_at) <= now:
                self.count("lock_conflicts")
                raise OrchError(409, "LOCK_LOST", "This lease expired or belongs to someone else; stop and re-acquire.")
            r.renewed_at, r.expires_at = now, now + timedelta(seconds=max(5, min(3600, int(ttl_seconds))))
            return {**self.lock_view(r, now), "token": r.token}

    def release_lock(self, actor: Actor, key: str, token: str) -> dict[str, Any]:
        self.ensure_ready()
        now = self.now()
        with self.repo.write() as s:
            r = s.get(OrchLock, key)
            if r is None or r.token != token:
                raise OrchError(409, "LOCK_LOST", "Not the holder of this lease.")
            if r.released_at is None:
                r.released_at = now
            return self.lock_view(r, now)

    def list_locks(self, held_only: bool = True) -> dict[str, Any]:
        self.ensure_ready()
        now = self.now()
        with self.repo.read() as s:
            q = select(OrchLock).order_by(OrchLock.acquired_at.desc()).limit(500)
            if held_only:
                q = q.where(OrchLock.released_at.is_(None), OrchLock.expires_at > now)
            return {"items": [self.lock_view(r, now) for r in s.execute(q).scalars()]}

    # ---- schedules ------------------------------------------------------------------------------------------------------------
    @staticmethod
    def schedule_view(x: OrchSchedule) -> dict[str, Any]:
        return {"schedule_id": x.schedule_id, "name": x.name, "workflow_id": x.workflow_id, "kind": x.kind,
                "expression": x.expression, "timezone": x.timezone, "input": x.input, "enabled": x.enabled,
                "next_run_at": utc(x.next_run_at), "last_run_at": utc(x.last_run_at), "last_status": x.last_status,
                "last_execution_id": x.last_execution_id, "failure_count": x.failure_count, "created_by": x.created_by,
                "created_at": utc(x.created_at), "updated_at": utc(x.updated_at)}

    def list_schedules(self) -> dict[str, Any]:
        self.ensure_ready()
        with self.repo.read() as s:
            return {"items": [self.schedule_view(x) for x in
                              s.execute(select(OrchSchedule).order_by(OrchSchedule.name)).scalars()],
                    "default_timezone": self.config().scheduling.timezone}

    def schedule_history(self, schedule_id: str, limit: int = 100) -> dict[str, Any]:
        self.ensure_ready()
        with self.repo.read() as s:
            rows = s.execute(select(OrchScheduleRun).where(OrchScheduleRun.schedule_id == schedule_id)
                             .order_by(OrchScheduleRun.slot_at.desc()).limit(limit)).scalars().all()
            return {"items": [{"slot_at": utc(r.slot_at), "fired_at": utc(r.fired_at), "status": r.status,
                               "execution_id": r.execution_id, "detail": r.detail} for r in rows]}

    def create_schedule(self, actor: Actor, body: dict[str, Any]) -> dict[str, Any]:
        self.ensure_ready()
        self.perm_check(actor, perm.ORCH_MANAGE, human=True)
        name = str(pick(body, "name") or "").strip()[:120]
        kind = str(pick(body, "kind") or "cron")
        expr = str(pick(body, "expression") or "").strip()
        tz = str(pick(body, "timezone") or self.config().scheduling.timezone)
        inp = pick(body, "input") or {}
        if not name:
            raise OrchError(422, "INVALID_SCHEDULE", "name is required.")
        try:
            validate_schedule(kind, expr, tz)
        except ScheduleError as exc:
            raise OrchError(422, "INVALID_SCHEDULE", str(exc)) from None
        now = self.now()
        with self.repo.write() as s:
            w = self._wf(s, str(pick(body, "workflow_id") or ""))
            if "schedule" not in (w.trigger_types or []):
                raise OrchError(422, "INVALID_SCHEDULE", "That workflow does not accept schedule triggers.")
            if s.execute(select(OrchSchedule.schedule_id).where(OrchSchedule.name == name)).first():
                raise OrchError(409, "SCHEDULE_EXISTS", "A schedule with this name already exists.")
            x = OrchSchedule(schedule_id=str(uuid.uuid4()), name=name, workflow_id=w.workflow_id, kind=kind, expression=expr,
                             timezone=tz, input=inp, enabled=bool(pick(body, "enabled", True)),
                             next_run_at=next_run(kind, expr, tz, now, anchor=now), failure_count=0, created_by=actor.name,
                             created_at=now, updated_at=now)
            s.add(x)
            s.flush()
            self.repo.audit(s, actor=actor.name, actor_kind=actor.kind, action="schedule.created", entity_type="schedule",
                            entity_id=x.schedule_id, at=now, details={"workflow_id": w.workflow_id, "kind": kind, "expression": expr})
            return self.schedule_view(x)

    def update_schedule(self, actor: Actor, schedule_id: str, body: dict[str, Any]) -> dict[str, Any]:
        self.ensure_ready()
        self.perm_check(actor, perm.ORCH_MANAGE, human=True)
        now = self.now()
        with self.repo.write() as s:
            x = s.get(OrchSchedule, schedule_id)
            if x is None:
                raise OrchError(404, "SCHEDULE_NOT_FOUND", "No such schedule.")
            kind = str(pick(body, "kind") or x.kind)
            expr = str(pick(body, "expression") or x.expression)
            tz = str(pick(body, "timezone") or x.timezone)
            try:
                validate_schedule(kind, expr, tz)
            except ScheduleError as exc:
                raise OrchError(422, "INVALID_SCHEDULE", str(exc)) from None
            x.kind, x.expression, x.timezone = kind, expr, tz
            if pick(body, "input") is not None:
                x.input = pick(body, "input")
            x.next_run_at = next_run(kind, expr, tz, now, anchor=utc(x.created_at)) if x.enabled else x.next_run_at
            x.updated_at = now
            self.repo.audit(s, actor=actor.name, actor_kind=actor.kind, action="schedule.updated", entity_type="schedule",
                            entity_id=schedule_id, at=now, details={"kind": kind, "expression": expr, "timezone": tz})
            return self.schedule_view(x)

    def set_schedule_enabled(self, actor: Actor, schedule_id: str, enabled: bool) -> dict[str, Any]:
        self.ensure_ready()
        self.perm_check(actor, perm.ORCH_OPERATE, human=True)
        now = self.now()
        with self.repo.write() as s:
            x = s.get(OrchSchedule, schedule_id)
            if x is None:
                raise OrchError(404, "SCHEDULE_NOT_FOUND", "No such schedule.")
            x.enabled, x.updated_at = enabled, now
            if enabled:
                x.next_run_at = next_run(x.kind, x.expression, x.timezone, now, anchor=utc(x.created_at))
            self.repo.audit(s, actor=actor.name, actor_kind=actor.kind, action="schedule." + ("resumed" if enabled else "paused"),
                            entity_type="schedule", entity_id=schedule_id, at=now)
            return self.schedule_view(x)

    def run_due_schedules(self) -> dict[str, int]:
        """Fires each due slot once. The unique (schedule, slot) row is claimed first, so two workers cannot both fire it."""
        self.ensure_ready()
        now = self.now()
        catch_up = self.config().scheduling.catch_up
        with self.repo.read() as s:
            due = s.execute(select(OrchSchedule.schedule_id).where(OrchSchedule.enabled.is_(True),
                                                                   OrchSchedule.next_run_at <= now)).scalars().all()
        out = {"fired": 0, "skipped": 0, "failed": 0}
        for sid in due:
            with self.repo.write() as s:
                x = s.get(OrchSchedule, sid)
                if x is None or not x.enabled or x.next_run_at is None or utc(x.next_run_at) > now:
                    continue
                slot, missed = utc(x.next_run_at), 0
                nxt = next_run(x.kind, x.expression, x.timezone, slot, anchor=utc(x.created_at))
                while nxt is not None and nxt <= now:        # downtime: collapse missed slots
                    missed += 1
                    slot = nxt
                    nxt = next_run(x.kind, x.expression, x.timezone, slot, anchor=utc(x.created_at))
                skip = catch_up == "skip" and missed > 0
                try:
                    with s.begin_nested():
                        s.add(OrchScheduleRun(schedule_id=sid, slot_at=slot, fired_at=now, status="skipped" if skip else "triggered",
                                              detail=f"{missed} missed slot(s) collapsed" if missed else None))
                        s.flush()
                except IntegrityError:
                    continue
                x.next_run_at, x.last_run_at, x.updated_at = nxt, now, now
                if nxt is None:
                    x.enabled = False
                wid, inp, name = x.workflow_id, x.input, x.name
            if skip:
                out["skipped"] += 1
                continue
            try:
                view, _ = self.trigger(SYSTEM, {"workflow_id": wid, "trigger_type": "schedule", "input": inp,
                                                "idempotency_key": f"schedule:{sid}:{slot.isoformat()}",
                                                "requesting_module": "module_08", "correlation_id": str(uuid.uuid4())},
                                       internal=True)
                status, exec_id, detail = "triggered", view["execution_id"], None
                out["fired"] += 1
            except OrchError as exc:
                status, exec_id, detail = "failed", None, f"{exc.code}: {exc.message}"[:500]
                out["failed"] += 1
            with self.repo.write() as s:
                run = s.execute(select(OrchScheduleRun).where(OrchScheduleRun.schedule_id == sid,
                                                              OrchScheduleRun.slot_at == slot)).scalar_one()
                run.status, run.execution_id, run.detail = status, exec_id, detail or run.detail
                x = s.get(OrchSchedule, sid)
                x.last_status, x.last_execution_id = status, exec_id
                x.failure_count = 0 if status == "triggered" else x.failure_count + 1
                if status == "failed":
                    self.repo.audit(s, actor="system", actor_kind="system", action="schedule.failed", entity_type="schedule",
                                    entity_id=sid, at=now, outcome="failed", details={"name": name, "detail": detail})
        return out

    # ---- integrations and health ------------------------------------------------------------------------------------------
    def list_integrations(self) -> dict[str, Any]:
        self.ensure_ready()
        with self.repo.read() as s:
            rows = s.execute(select(OrchIntegration).order_by(OrchIntegration.integration_id)).scalars().all()
            return {"items": [self.integration_view(r) for r in rows]}

    def integration_view(self, r: OrchIntegration) -> dict[str, Any]:
        breaker = self.gateway.breaker_state(r.integration_id) if r.kind == "module" else None
        return {"integration_id": r.integration_id, "name": r.name, "kind": r.kind, "base_url": r.base_url,
                "health_path": r.health_path, "environment": r.environment, "enabled": r.enabled, "status": r.status,
                "detail": r.detail, "last_checked_at": utc(r.last_checked_at), "last_healthy_at": utc(r.last_healthy_at),
                "latency_ms": r.latency_ms, "consecutive_failures": r.consecutive_failures, "circuit": breaker}

    def integration_health(self, integration_id: str, limit: int = 50) -> dict[str, Any]:
        self.ensure_ready()
        with self.repo.read() as s:
            r = s.get(OrchIntegration, integration_id)
            if r is None:
                raise OrchError(404, "INTEGRATION_NOT_FOUND", "No such integration.")
            hist = s.execute(select(OrchHealthCheck).where(OrchHealthCheck.integration_id == integration_id)
                             .order_by(OrchHealthCheck.checked_at.desc()).limit(limit)).scalars().all()
            return {**self.integration_view(r), "history": [
                {"checked_at": utc(h.checked_at), "status": h.status, "latency_ms": h.latency_ms, "http_status": h.http_status,
                 "detail": h.detail} for h in hist]}

    def check_integration(self, integration_id: str, actor: Actor | None = None) -> dict[str, Any]:
        self.ensure_ready()
        if integration_id == "n8n":
            h = self.wf_engine.health()
            status, detail, latency, code = h.status, h.detail, h.latency_ms, None
        elif integration_id == "database":
            t0 = time.perf_counter()
            try:
                with self.repo.read() as s:
                    s.execute(text("SELECT 1"))
                status, detail = HEALTHY, f"{self.repo.engine.dialect.name} reachable and answering queries."
            except Exception as exc:  # noqa: BLE001
                status, detail = UNAVAILABLE, f"Database query failed ({type(exc).__name__})."
            latency, code = (time.perf_counter() - t0) * 1000, None
        elif integration_id == "event_store":
            status, detail, latency, code = *self._event_store_health(), None, None
        else:
            if integration_id not in self.gateway.endpoints:
                raise OrchError(404, "INTEGRATION_NOT_FOUND", "No such integration.")
            self._gateway = ModuleGateway(self._endpoints(), service_token=secret_value(self.settings, "orchestration_service_token")) \
                if self._gateway is not None and not getattr(self._gateway, "_pinned", False) else self._gateway
            r = self.gateway.check(integration_id, self.now())
            status, detail, latency, code = r.status, r.detail, r.latency_ms, r.http_status
        now = self.now()
        with self.repo.write() as s:
            row = s.get(OrchIntegration, integration_id)
            if row is not None:
                row.status, row.detail, row.last_checked_at, row.latency_ms, row.updated_at = status, detail, now, latency, now
                if status == HEALTHY:
                    row.last_healthy_at, row.consecutive_failures = now, 0
                elif status in (UNAVAILABLE, DEGRADED):
                    row.consecutive_failures += 1
                if integration_id.startswith("module_"):
                    row.base_url = self.gateway.endpoints[integration_id].base_url
            s.add(OrchHealthCheck(integration_id=integration_id, checked_at=now, status=status, latency_ms=latency,
                                  http_status=code, detail=(detail or "")[:1000]))
            if actor is not None:
                self.repo.audit(s, actor=actor.name, actor_kind=actor.kind, action="integration.tested",
                                entity_type="integration", entity_id=integration_id, at=now, details={"status": status})
        return {"integration_id": integration_id, "status": status, "detail": detail, "latency_ms": latency,
                "http_status": code, "checked_at": now}

    def check_all(self) -> dict[str, str]:
        out = {}
        with self.repo.read() as s:
            ids = s.execute(select(OrchIntegration.integration_id).where(OrchIntegration.enabled.is_(True))).scalars().all()
        for iid in ids:
            try:
                out[iid] = self.check_integration(iid)["status"]
            except Exception as exc:  # noqa: BLE001
                out[iid] = f"error {type(exc).__name__}"
        return out

    def _event_store_health(self) -> tuple[str, str]:
        now = self.now()
        lag_warn = self.config().health.event_lag_warn_seconds
        with self.repo.read() as s:
            oldest = s.execute(select(func.min(OrchDelivery.created_at)).where(OrchDelivery.status == "pending")).scalar()
            pending = s.execute(select(func.count()).select_from(OrchDelivery).where(OrchDelivery.status == "pending")).scalar_one()
            cursors = {r.source: r.updated_at for r in s.execute(select(OrchSourceCursor)).scalars()}
        lag = (now - utc(oldest)).total_seconds() if oldest else 0.0
        if lag > lag_warn:
            return DEGRADED, f"{pending} deliveries pending; the oldest has waited {lag:.0f} s (worker stopped or behind?)."
        stale = [k for k, v in cursors.items() if v is not None and (now - utc(v)).total_seconds() > max(lag_warn, 600)]
        if stale:
            return DEGRADED, f"Bridge cursors not advanced recently: {stale} (is the orchestration worker running?)."
        return HEALTHY, f"Writable; {pending} deliveries pending; oldest wait {lag:.0f} s."

    def evaluate_workflow_health(self) -> dict[str, int]:
        """DEGRADED when the recent failure rate crosses the threshold, back to ACTIVE when it recovers."""
        self.ensure_ready()
        cfg = self.config().health
        now = self.now()
        changed = 0
        with self.repo.write() as s:
            for w in s.execute(select(OrchWorkflow).where(OrchWorkflow.state.in_(["ACTIVE", "DEGRADED"]))).scalars():
                rows = s.execute(select(OrchExecution.status).where(
                    OrchExecution.workflow_id == w.workflow_id, OrchExecution.created_at >= now - timedelta(hours=24),
                    OrchExecution.status.in_(["SUCCEEDED", "FAILED", "DEAD_LETTERED", "TIMED_OUT"]))
                    .order_by(OrchExecution.created_at.desc()).limit(20)).scalars().all()
                if len(rows) < cfg.min_executions_for_rate:
                    w.health_status = w.health_status if w.health_status != "UNKNOWN" else "UNKNOWN"
                    continue
                rate = sum(1 for x in rows if x != "SUCCEEDED") / len(rows)
                detail = f"{rate:.0%} of the last {len(rows)} executions failed."
                if rate >= cfg.degraded_failure_rate and w.state == "ACTIVE":
                    w.state, w.health_status, w.health_detail, w.updated_at = "DEGRADED", "DEGRADED", detail, now
                    w.version += 1
                    changed += 1
                    self.repo.audit(s, actor="system", actor_kind="system", action="workflow.degraded", entity_type="workflow",
                                    entity_id=w.workflow_id, at=now, details={"failure_rate": rate})
                elif rate < cfg.degraded_failure_rate and w.state == "DEGRADED":
                    w.state, w.health_status, w.health_detail, w.updated_at = "ACTIVE", "HEALTHY", detail, now
                    w.version += 1
                    changed += 1
                    self.repo.audit(s, actor="system", actor_kind="system", action="workflow.recovered", entity_type="workflow",
                                    entity_id=w.workflow_id, at=now, details={"failure_rate": rate})
                else:
                    w.health_status = "DEGRADED" if rate >= cfg.degraded_failure_rate else "HEALTHY"
                    w.health_detail = detail
        return {"changed": changed}

    # ---- configuration ----------------------------------------------------------------------------------------------------------
    def get_config(self) -> dict[str, Any]:
        self.ensure_ready()
        with self.repo.write() as s:
            self._sync_credentials(s, self.now())
        with self.repo.read() as s:
            row = self.repo.config_current(s)
            creds = {c.name: c for c in s.execute(select(OrchCredential)).scalars()}
            history = s.execute(text("SELECT version, reason, created_by, created_at FROM orch_config_versions "
                                     "ORDER BY version DESC LIMIT 20")).all()
        secrets = []
        for st in secret_status(self.settings):
            c = creds.get(st["name"])
            secrets.append({**st, "rotated_at": utc(c.rotated_at) if c else None, "first_seen_at": utc(c.first_seen_at) if c else None})
        return {"version": row.version, "config": row.config, "reason": row.reason, "created_by": row.created_by,
                "created_at": utc(row.created_at),
                "history": [{"version": h[0], "reason": h[1], "created_by": h[2], "created_at": utc(h[3]) if isinstance(h[3], datetime) else h[3]}
                            for h in history],
                "secrets": secrets,
                "connection": {"environment": self.env, "n8n_base_url": self.settings.n8n_base_url,
                               "n8n_webhook_base_url": self.settings.n8n_webhook_base_url or f"{self.settings.n8n_base_url.rstrip('/')}/webhook",
                               "n8n_public_url": self.settings.n8n_public_url or self.settings.n8n_base_url,
                               "n8n_environment": self.settings.n8n_environment or self.env,
                               "callback_base_url": self.settings.orchestration_callback_base_url,
                               "internal_base_url": self.settings.aurum_internal_base_url,
                               "request_timeout": self.settings.n8n_request_timeout, "retry_limit": self.settings.n8n_retry_limit},
                "note": "Secrets are set in the server environment (.env). Their values are never shown; the fingerprint "
                        "changes when a secret is rotated."}

    def put_config(self, actor: Actor, body: dict[str, Any]) -> dict[str, Any]:
        self.ensure_ready()
        self.perm_check(actor, perm.ORCH_MANAGE, human=True)
        reason = str(pick(body, "reason") or "").strip()
        if not reason:
            raise OrchError(422, "REASON_REQUIRED", "Give a reason for the configuration change.")
        raw = pick(body, "config")
        if not isinstance(raw, dict):
            raise OrchError(422, "INVALID_CONFIG", "config must be an object.")
        try:
            cfg = parse_config(raw)
        except ValueError as exc:
            raise OrchError(422, "INVALID_CONFIG", str(exc)[:1000]) from None
        expected = pick(body, "version")
        now = self.now()
        with self.repo.write() as s:
            cur = self.repo.config_current(s)
            if expected is not None and cur is not None and int(expected) != cur.version:
                raise OrchError(409, "STALE_VERSION", "The configuration changed since you loaded it.", {"current": cur.version})
            old = cur.config if cur else {}
            new = cfg.dump()
            changed = sorted(k for k in set(old) | set(new) if old.get(k) != new.get(k))
            if not changed:
                raise OrchError(409, "NO_CHANGE", "The configuration is unchanged.")
            v = self.repo.config_add(s, new, reason[:1000], actor.name, now)
            self.repo.audit(s, actor=actor.name, actor_kind=actor.kind, action="config.updated", entity_type="config",
                            entity_id=str(v), at=now, details={"changed_sections": changed, "reason": reason[:500]})
        self._config = None
        if not getattr(self, "_gateway_injected", False):
            self._gateway = None                    # rebuilt with the new module endpoints on next use
        return self.get_config()

    # ---- audit ------------------------------------------------------------------------------------------------------------------
    def audit_log(self, *, action: str | None = None, entity_type: str | None = None, entity_id: str | None = None,
                  actor: str | None = None, since: datetime | None = None, limit: int = 200,
                  before: int | None = None) -> dict[str, Any]:
        self.ensure_ready()
        conds = []
        if action:
            conds.append(OrchAuditLog.action.like(action[:-1] + "%") if action.endswith("*") else OrchAuditLog.action == action)
        for col, val in ((OrchAuditLog.entity_type, entity_type), (OrchAuditLog.entity_id, entity_id),
                         (OrchAuditLog.actor, actor)):
            if val:
                conds.append(col == val)
        if since:
            conds.append(OrchAuditLog.at >= since)
        if before:
            conds.append(OrchAuditLog.seq < before)
        with self.repo.read() as s:
            rows = s.execute(select(OrchAuditLog).where(and_(*conds) if conds else True).order_by(OrchAuditLog.seq.desc())
                             .limit(limit)).scalars().all()
            items = [{"seq": r.seq, "at": utc(r.at), "actor": r.actor, "actor_kind": r.actor_kind, "action": r.action,
                      "entity_type": r.entity_type, "entity_id": r.entity_id, "outcome": r.outcome,
                      "correlation_id": r.correlation_id, "details": r.details} for r in rows]
        ok, n, err = self.repo.verify_audit()
        return {"items": items, "chain": {"valid": ok, "rows": n, "error": err}}

    # ---- overview and metrics ---------------------------------------------------------------------------------------------------
    def overview(self) -> dict[str, Any]:
        self.ensure_ready()
        now = self.now()
        since = now - timedelta(hours=24)
        with self.repo.read() as s:
            wf = dict(s.execute(select(OrchWorkflow.state, func.count()).group_by(OrchWorkflow.state)).all())
            ex = dict(s.execute(select(OrchExecution.status, func.count()).group_by(OrchExecution.status)).all())
            ex24 = dict(s.execute(select(OrchExecution.status, func.count()).where(OrchExecution.created_at >= since)
                                  .group_by(OrchExecution.status)).all())
            dead_open = s.execute(select(func.count()).select_from(OrchDeadLetter).where(
                OrchDeadLetter.recovery_status == "open")).scalar_one()
            events24 = s.execute(select(func.count()).select_from(OrchEvent).where(OrchEvent.published_at >= since)).scalar_one()
            pending = s.execute(select(func.count()).select_from(OrchDelivery).where(OrchDelivery.status == "pending")).scalar_one()
            durations = s.execute(select(OrchExecution.started_at, OrchExecution.finished_at).where(
                OrchExecution.status == "SUCCEEDED", OrchExecution.finished_at >= since)).all()
            lat = s.execute(select(func.avg(OrchDelivery.latency_ms)).where(OrchDelivery.delivered_at >= since)).scalar()
            cb_rejected = s.execute(select(func.count()).select_from(OrchExecutionCallback).where(
                OrchExecutionCallback.outcome == "rejected", OrchExecutionCallback.received_at >= since)).scalar_one()
            integ = {r.integration_id: {"status": r.status, "detail": r.detail, "checked_at": utc(r.last_checked_at)}
                     for r in s.execute(select(OrchIntegration)).scalars()}
            recent = s.execute(select(OrchExecution).order_by(OrchExecution.created_at.desc()).limit(10)).scalars().all()
            recent_v = [self.exec_view(e) for e in recent]
        finished24 = sum(int(ex24.get(k, 0)) for k in ("SUCCEEDED", "FAILED", "DEAD_LETTERED", "TIMED_OUT"))
        secs = [(utc(b) - utc(a)).total_seconds() for a, b in durations if a and b]
        alerts = []
        cfg = self.config().alerts
        if int(dead_open) >= cfg.dead_letter_warn:
            alerts.append({"level": "warning", "message": f"{dead_open} dead letter(s) need attention."})
        if finished24 and (finished24 - int(ex24.get("SUCCEEDED", 0))) / finished24 >= cfg.failure_rate_warn:
            alerts.append({"level": "warning", "message": "Execution failure rate in the last 24 h is above the alert threshold."})
        if integ.get("n8n", {}).get("status") in (UNAVAILABLE,):
            alerts.append({"level": "critical", "message": "n8n is unavailable; dispatches are queued and will retry."})
        return {**self.info(),
                "workflows": {"total": sum(int(v) for v in wf.values()), "by_state": {k: int(v) for k, v in wf.items()},
                              "active": int(wf.get("ACTIVE", 0)) + int(wf.get("DEGRADED", 0)), "paused": int(wf.get("PAUSED", 0))},
                "executions": {"by_status": {k: int(v) for k, v in ex.items()},
                               "running": sum(int(ex.get(k, 0)) for k in ("DISPATCHED", "RUNNING", "WAITING_CALLBACK")),
                               "queued": int(ex.get("QUEUED", 0)), "retry_pending": int(ex.get("RETRY_PENDING", 0)),
                               "succeeded": int(ex.get("SUCCEEDED", 0)), "failed": int(ex.get("FAILED", 0)) + int(ex.get("TIMED_OUT", 0)),
                               "dead_lettered": int(ex.get("DEAD_LETTERED", 0))},
                "last_24h": {"executions": sum(int(v) for v in ex24.values()), "finished": finished24,
                             "success_rate": (int(ex24.get("SUCCEEDED", 0)) / finished24) if finished24 else None,
                             "avg_duration_seconds": (sum(secs) / len(secs)) if secs else None, "events": int(events24),
                             "avg_delivery_latency_ms": float(lat) if lat is not None else None,
                             "rejected_callbacks": int(cb_rejected)},
                "dead_letters_open": int(dead_open), "deliveries_pending": int(pending), "integrations": integ,
                "recent_executions": recent_v, "alerts": alerts, "counters": dict(self.counters)}

    def metrics_text(self) -> str:
        """Prometheus text exposition (no client library needed)."""
        o = self.overview()
        lines = ["# HELP aurum_orch_executions Executions by status.", "# TYPE aurum_orch_executions gauge"]
        for k, v in sorted(o["executions"]["by_status"].items()):
            lines.append(f'aurum_orch_executions{{status="{k}"}} {v}')
        lines += ["# TYPE aurum_orch_workflows gauge"]
        for k, v in sorted(o["workflows"]["by_state"].items()):
            lines.append(f'aurum_orch_workflows{{state="{k}"}} {v}')
        lines += ["# TYPE aurum_orch_dead_letters_open gauge", f"aurum_orch_dead_letters_open {o['dead_letters_open']}",
                  "# TYPE aurum_orch_deliveries_pending gauge", f"aurum_orch_deliveries_pending {o['deliveries_pending']}"]
        sr = o["last_24h"]["success_rate"]
        if sr is not None:
            lines += ["# TYPE aurum_orch_success_rate_24h gauge", f"aurum_orch_success_rate_24h {sr:.4f}"]
        if o["last_24h"]["avg_duration_seconds"] is not None:
            lines += ["# TYPE aurum_orch_execution_duration_avg_seconds gauge",
                      f"aurum_orch_execution_duration_avg_seconds {o['last_24h']['avg_duration_seconds']:.3f}"]
        if o["last_24h"]["avg_delivery_latency_ms"] is not None:
            lines += ["# TYPE aurum_orch_delivery_latency_avg_ms gauge",
                      f"aurum_orch_delivery_latency_avg_ms {o['last_24h']['avg_delivery_latency_ms']:.1f}"]
        status_num = {HEALTHY: 1, DEGRADED: 0.5, UNAVAILABLE: 0, UNKNOWN: -1}
        lines += ["# HELP aurum_orch_integration_up 1 healthy, 0.5 degraded, 0 unavailable, -1 unknown.",
                  "# TYPE aurum_orch_integration_up gauge"]
        for k, v in sorted(o["integrations"].items()):
            lines.append(f'aurum_orch_integration_up{{integration="{k}"}} {status_num.get(v["status"], -1)}')
        lines += ["# HELP aurum_orch_events_total Process-local counters since start.", "# TYPE aurum_orch_events_total counter"]
        for k, v in sorted(o["counters"].items()):
            lines.append(f'aurum_orch_events_total{{counter="{k}"}} {v}')
        return "\n".join(lines) + "\n"

    def health(self) -> dict[str, Any]:
        try:
            self.ensure_ready()
        except OrchError as exc:
            return {"status": "not_ready", "code": exc.code, "detail": exc.message, "environment": self.env}
        ok, n, err = self.repo.verify_audit()
        with self.repo.read() as s:
            integ = {r.integration_id: r.status for r in s.execute(select(OrchIntegration)).scalars()}
        core = [integ.get("database"), integ.get("event_store")]
        status = "ok" if ok and all(x in (HEALTHY, UNKNOWN, None) for x in core) else "degraded"
        return {**self.info(), "status": status, "audit_chain": {"valid": ok, "rows": n, "error": err}, "integrations": integ}

    # ---- retention ---------------------------------------------------------------------------------------------------------------
    def purge(self, actor: Actor, *, dry_run: bool = True) -> dict[str, int]:
        """Deletes delivered events, finished deliveries and health checks older than the retention period. Executions,
        dead letters and the audit log are kept. Dry run by default."""
        cfg = self.config()
        now = self.now()
        ev_cut = now - timedelta(days=cfg.events.retention_days)
        hc_cut = now - timedelta(days=cfg.health.history_days)
        with self.repo.write() as s:
            old_events = select(OrchEvent.event_id).where(OrchEvent.published_at < ev_cut,
                                                          OrchEvent.status.in_(["delivered", "no_subscribers"]))
            counts = {"events": s.execute(select(func.count()).select_from(old_events.subquery())).scalar_one(),
                      "health_checks": s.execute(select(func.count()).select_from(OrchHealthCheck).where(
                          OrchHealthCheck.checked_at < hc_cut)).scalar_one()}
            if not dry_run:
                ids = list(s.execute(old_events).scalars())
                for i in range(0, len(ids), 500):
                    chunk = ids[i:i + 500]
                    dids = select(OrchDelivery.id).where(OrchDelivery.event_id.in_(chunk))
                    s.execute(delete(OrchDeliveryAttempt).where(OrchDeliveryAttempt.delivery_id.in_(dids)))
                    s.execute(delete(OrchDelivery).where(OrchDelivery.event_id.in_(chunk)))
                    s.execute(delete(OrchEvent).where(OrchEvent.event_id.in_(chunk)))
                s.execute(delete(OrchHealthCheck).where(OrchHealthCheck.checked_at < hc_cut))
                self.repo.audit(s, actor=actor.name, actor_kind=actor.kind, action="retention.purged", entity_type="config",
                                entity_id="retention", at=now, details=counts)
        return {**counts, "dry_run": dry_run}
