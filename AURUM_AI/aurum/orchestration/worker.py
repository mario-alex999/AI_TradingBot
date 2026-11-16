"""Module 08 background worker. One loop; each step is independent (a failing step is logged, the others still run).

Each tick (default every 10 s):
1. bridge   - copy new Module 06 / Module 07 outbox events into the event store (read-only on their tables);
2. deliver  - deliver due events to subscriptions (trigger workflows or POST signed webhooks), with retries;
3. schedule - fire due schedule slots (each slot once, enforced by a unique row);
4. dispatch - send queued and retry-pending executions to n8n within the concurrency limit;
5. timeouts - reconcile or time out executions that passed their deadline;
6. health   - integration checks and workflow health, every ``health.interval_seconds`` (measured from the last stored
              check, so it holds for a fresh worker per tick too).

Optional step 0, ``approval_expiry``: Module 07's own expiry pass, only when ``approval_responses.expire_overdue_requests``
is on (hosts that run no Module 07 worker).

Recovery after a restart needs no special step: queued work is picked up by its status and due time; an execution that
was claimed but whose dispatch result was never recorded reaches its deadline and is reconciled or timed out - never
silently re-sent unless the workflow deduplicates by execution id.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from aurum.orchestration.service import OrchestrationService

log = logging.getLogger(__name__)


class Worker:
    def __init__(self, service: OrchestrationService):
        self.svc = service
        self._last_health = 0.0

    def tick(self, *, force_health: bool = False) -> dict[str, Any]:
        self.svc.ensure_ready()
        out: dict[str, Any] = {}
        steps = [("bridge", self.svc.bridge_sources), ("deliver", self.svc.deliver_due),
                 ("schedule", self.svc.run_due_schedules), ("dispatch", self.svc.dispatch_due),
                 ("timeouts", self.svc.sweep_timeouts)]
        if self.svc.config().approval_responses.expire_overdue_requests:
            steps.insert(0, ("approval_expiry", self._approval_expiry))      # before the bridge, so its events go out now
        if force_health or self._health_due(self.svc.config().health.interval_seconds):
            steps += [("health", self.svc.check_all), ("workflow_health", self.svc.evaluate_workflow_health)]
            self._last_health = time.monotonic()
        for name, fn in steps:
            try:
                out[name] = fn()
            except Exception as exc:  # noqa: BLE001 - one failing step must not stop the others
                log.exception("Module 08 worker step failed", extra={"step": name})
                out[name] = {"error": f"{type(exc).__name__}: {exc}"[:300]}
        return out

    def _approval_expiry(self) -> dict[str, Any]:
        from aurum.orchestration.approval_responses import ApprovalResponseHandler
        return ApprovalResponseHandler(self.svc).expire_overdue()

    def _health_due(self, interval: float) -> bool:
        """Due when the last stored integration check is older than the interval. Read from the database, not from this
        object, so a host that starts a fresh worker for every tick (a serverless function) still keeps the interval."""
        if self._last_health and time.monotonic() - self._last_health < interval:
            return False
        try:
            from sqlalchemy import func, select

            from aurum.orchestration.db_models import OrchHealthCheck
            from aurum.orchestration.repository import utc
            with self.svc.repo.read() as s:
                last = s.execute(select(func.max(OrchHealthCheck.checked_at))).scalar()
            return last is None or (utc(self.svc.now()) - utc(last)).total_seconds() >= interval - 5
        except Exception:  # noqa: BLE001 - when in doubt, check
            return True

    def run_forever(self, interval: float, stop: Any = None) -> None:
        log.info("Module 08 worker started", extra={"interval": interval, "environment": self.svc.env})
        while stop is None or not stop.is_set():
            started = time.monotonic()
            result = self.tick()
            busy = {k: v for k, v in result.items() if v and v != {"claimed": 0}}
            log.info("Module 08 worker tick", extra={"result": busy})
            time.sleep(max(0.5, interval - (time.monotonic() - started)))
