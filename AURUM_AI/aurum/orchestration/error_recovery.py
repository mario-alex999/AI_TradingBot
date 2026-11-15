"""WF08-08 Error Handling and Recovery - the backend half.

Module 08's core already owns the mechanics of failure: every run is tracked, a failed run is classified, retried
within its workflow's retry policy (attempt limit, back-off) and dead-lettered when it cannot be retried. WF08-08 does
not repeat any of that. It adds what the core cannot know or does not say:

* **Failures the workflow could not report.** When an n8n workflow crashes before its own "failed" callback, AURUM only
  notices at the run's deadline. n8n's Error Trigger reports the crash here at once (``report``); it is classified and
  handed to the core, which retries or dead-letters it by its usual rules.
* **One record per failure, in plain words.** For each failed, timed-out or dead-lettered run: which workflow, which
  error, how it is classified, whether a retry is safe, and what happened next (``status`` / ``record``).
* **Escalation.** A failure that is not being retried - dead-lettered, outcome unknown, or an execution hand-off - is
  announced on the dashboard through Module 07's notifications.
* **Recovery.** A run that succeeds after a retry is recorded as recovered.

**Nothing is retried blindly and nothing about a trade is retried at all.** WF08-08 starts no retry itself: retries are
the core's, inside the limits of the retry policy. A timed-out run is not retried unless its workflow is safe to send
twice, because a time-out can mean the work was done and only the answer was lost; it is escalated for a person to
check. A failure of the execution hand-off is never classified as retryable.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy import select

from aurum.orchestration.config_model import ErrorRecoveryCfg
from aurum.orchestration.retry import BUSINESS_CATEGORIES, PERMANENT, TRANSIENT, UNKNOWN, kind_of

WORKFLOW_ID = "wf08-08-error-recovery"
WEBHOOK_PATH = "aurum-wf08-08-error-recovery"
VERSION = "1.0.0"
WORKFLOW_NAME = "WF08-08 Error Handling and Recovery"
DESCRIPTION = ("Record every failed workflow run, whether a retry is safe and what happened next; escalate the failures "
               "that are not being retried. Starts no retry itself and never retries anything about a trade.")
TRIGGER_EVENTS = ("orchestration.workflow.failed", "orchestration.workflow.timed_out", "orchestration.workflow.dead_lettered")
RECOVERY_EVENT = "orchestration.workflow.completed"
RECOVERY_ATTEMPTS = (2, 3, 4, 5)                 # a run that completed on a later attempt recovered from a failure
AUDIT_ACTION = "wf08-08.error_recorded"
AUDIT_ENGINE = "wf08-08.engine_error"
AUDIT_ENTITY = "error_recovery"
FAILED_STATES = ("FAILED", "TIMED_OUT", "DEAD_LETTERED")
IN_FLIGHT = ("QUEUED", "RETRY_PENDING", "DISPATCHING", "ACCEPTED", "RUNNING")

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "executionIds": {"type": "array", "items": {"type": "string", "maxLength": 64}, "maxItems": 50},
        "dryRun": {"type": "boolean"},
        "reason": {"type": "string", "maxLength": 200},
        "event": {"type": "object"},
    },
}
OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["completed", "partial", "skipped"]},
        "reason": {"type": "string"},
        "recorded": {"type": "array"},
        "skipped": {"type": "array"},
    },
}


def _utc(t: datetime) -> datetime:
    return t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t.astimezone(timezone.utc)


def _iso(t: datetime | None) -> str | None:
    return _utc(t).isoformat().replace("+00:00", "Z") if t else None


def classify_engine_error(message: str | None, handoff: bool = False) -> tuple[str, bool | None, str]:
    """(category, retryable, why) for an error text n8n reports. Deliberately coarse: anything not recognised is a
    plain workflow error, which the core retries only when the workflow's policy allows it."""
    if handoff:
        return "OUTCOME_UNKNOWN", False, "An execution hand-off is never retried automatically: check what happened first."
    m = (message or "").lower()
    if any(k in m for k in ("429", "rate limit", "too many requests")):
        return "RATE_LIMITED", True, "Rate limited: safe to try again after a pause."
    if any(k in m for k in ("401", "403", "unauthor", "forbidden", "invalid token", "permission")):
        return "AUTH_FAILED", False, "Authentication or permission was refused: retrying cannot help."
    if any(k in m for k in ("400", "404", "422", "bad request", "not found", "unprocessable", "invalid json", "validation")):
        return "INVALID_PAYLOAD", False, "The request itself was refused: retrying the same request cannot help."
    if any(k in m for k in ("timeout", "timed out", "etimedout", "econnreset", "econnrefused", "enotfound", "eai_again",
                            "socket hang up", "502", "503", "504", "service unavailable", "bad gateway", "network")):
        return "DEPENDENCY_UNAVAILABLE", True, "A service the workflow calls did not answer: safe to try again."
    return "WORKFLOW_ERROR", None, "The workflow stopped with an error it did not classify."


class ErrorRecovery:
    """Stateless over the database: safe to call from any API worker. ``svc`` is the OrchestrationService."""

    def __init__(self, svc: Any, approvals: Callable[[], Any] | None = None, deliver: Callable[[Any], dict[str, int]] | None = None):
        self.svc = svc
        self.engine = svc.repo.engine
        self._approvals_factory, self._deliver = approvals, deliver
        self._approvals: Any = None

    # ---- wiring -------------------------------------------------------------------------------------------------------------
    def cfg(self) -> ErrorRecoveryCfg:
        return self.svc.config().error_recovery

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
                "max_errors_per_run": c.max_errors_per_run, "lookback_hours": c.lookback_hours,
                "notify_dashboard": c.notify_dashboard, "trigger_events": list(TRIGGER_EVENTS) + [RECOVERY_EVENT]}

    def preflight(self) -> dict[str, Any]:
        c = self.cfg()
        checks: dict[str, Any] = {"enabled": c.enabled}
        reasons: list[str] = []
        if not c.enabled:
            reasons.append("Error handling is switched off (error_recovery.enabled = false).")
        try:
            from aurum.orchestration.db_models import OrchExecution
            with self.svc.repo.read() as s:
                s.execute(select(OrchExecution.execution_id).limit(1)).first()
            checks["database"] = "ok"
        except Exception as exc:  # noqa: BLE001 - any failure here means: do not proceed
            checks["database"] = f"unavailable ({type(exc).__name__})"
            reasons.append("The AURUM database is not reachable.")
        return {"proceed": not reasons, "checks": checks, "reasons": reasons, "checked_at": _iso(self.svc.now())}

    # ---- what was already recorded ----------------------------------------------------------------------------------------------
    def _recorded(self, execution_ids: list[str]) -> dict[str, set[str]]:
        """execution id -> the record keys ("state:attempt") already written for it."""
        if not execution_ids:
            return {}
        from aurum.orchestration.db_models import OrchAuditLog as L
        out: dict[str, set[str]] = {}
        with self.svc.repo.read() as s:
            for eid, details in s.execute(select(L.entity_id, L.details).where(
                    L.action == AUDIT_ACTION, L.entity_type == AUDIT_ENTITY, L.entity_id.in_(execution_ids))).all():
                out.setdefault(eid, set()).add(str((details or {}).get("key")))
        return out

    # ---- which runs to look at --------------------------------------------------------------------------------------------------
    def pending(self, execution_ids: list[str] | None = None) -> dict[str, Any]:
        """Failed runs whose current state is not recorded yet, and runs that recovered after a recorded failure. With
        ``execution_ids`` the listed runs are returned whatever their state."""
        from aurum.orchestration.db_models import OrchExecution as E
        c = self.cfg()
        if execution_ids:
            items = [{"execution_id": x} for x in dict.fromkeys(execution_ids)]
        else:
            since = _utc(self.svc.now()) - timedelta(hours=c.lookback_hours)
            with self.svc.repo.read() as s:
                rows = s.execute(select(E.execution_id, E.workflow_id, E.status, E.attempt).where(
                    E.workflow_id != WORKFLOW_ID, E.status_changed_at >= since,
                    (E.status.in_(FAILED_STATES)) | ((E.status == "SUCCEEDED") & (E.attempt > 1)))
                    .order_by(E.status_changed_at)).all()
            done = self._recorded([r[0] for r in rows])
            items = []
            for eid, wid, st, attempt in rows:
                keys = done.get(eid, set())
                if st == "SUCCEEDED":
                    if keys and f"recovered:{attempt}" not in keys:      # only a failure this workflow saw can "recover"
                        items.append({"execution_id": eid, "workflow_id": wid, "status": st})
                elif not any(k.split(":")[1:] == [str(attempt)] for k in keys):
                    items.append({"execution_id": eid, "workflow_id": wid, "status": st})
        deferred = items[c.max_errors_per_run:]
        items = items[:c.max_errors_per_run]
        return {"enabled": c.enabled, "items": items if c.enabled else [], "count": len(items) if c.enabled else 0,
                "deferred": len(deferred)}

    # ---- one failed run -------------------------------------------------------------------------------------------------------
    def status(self, execution_id: str) -> dict[str, Any]:
        """Identify the failed workflow, capture the error, classify it, decide whether a retry is safe and say what
        the core has done about it. Reads only."""
        from aurum.orchestration.common import OrchError
        out: dict[str, Any] = {"execution_id": execution_id, "exists": False, "ok": False, "reasons": []}
        try:
            e = self.svc.get_execution(execution_id)
            w = self.svc.get_workflow(e["workflow_id"])
        except OrchError:
            out["reasons"].append("Module 08 has no such execution.")
            return out
        st, cat, attempt = e["status"], e.get("error_category"), int(e.get("attempt") or 0)
        handoff = w.get("category") == "execution_handoff"
        kind = kind_of(cat) if cat else None
        with self.svc.repo.read() as s:
            max_attempts = self.svc.policy_for(self.svc._wf(s, e["workflow_id"])).max_attempts
        open_dl = [d for d in e.get("dead_letters") or [] if d.get("recovery_status") == "open"]
        if st == "SUCCEEDED":
            state, safe, why = "recovered", True, f"The run succeeded on attempt {attempt}."
        elif st in IN_FLIGHT and (cat or attempt > 1):
            state, safe = "retry_scheduled", True
            why = (f"{cat or 'The failure'} is transient; the core retries it" +
                   (f" (attempt {attempt} of at most {max_attempts})." if max_attempts else "."))
        elif st == "DEAD_LETTERED":
            state, safe = "escalated", False
            why = ("Retries are used up." if kind == TRANSIENT else "This kind of error is not retried automatically.") + \
                  " The run is in the dead-letter list for a person to resolve."
        elif st == "TIMED_OUT":
            state, safe = "escalated", False
            why = ("No answer before the deadline. The workflow may still have run, so it is not sent again automatically: "
                   "check n8n first, then retry it by hand if it did not run.")
        elif st == "FAILED":
            if cat in BUSINESS_CATEGORIES:
                state, safe, why = "closed", False, f"{cat} is a business outcome, not a fault: nothing to retry."
            else:
                state, safe, why = "escalated", False, "The run failed and is not being retried."
        elif st == "CANCELLED":
            state, safe, why = "closed", False, "The run was cancelled."
        else:
            state, safe, why = "none", False, f"The run is {st}; there is no failure to handle."
        if handoff and state in ("retry_scheduled", "escalated"):
            why += " This is the execution hand-off: nothing about a trade is retried without a person checking first."
        key = f"{state}:{attempt}"
        recorded = key in self._recorded([execution_id]).get(execution_id, set())
        out.update(exists=True, workflow_id=e["workflow_id"], workflow_name=w.get("name"), workflow_category=w.get("category"),
                   execution_status=st, attempt=attempt, max_attempts=max_attempts, n8n_execution_id=e.get("n8n_execution_id"),
                   error_category=cat, error_message=(e.get("error_message") or "")[:500], error_kind=kind,
                   state=state, retry_safe=safe, why=why, next_attempt_at=_iso(e.get("next_attempt_at")),
                   dead_letter_id=open_dl[0]["id"] if open_dl else None, correlation_id=e.get("correlation_id"),
                   symbol=e.get("symbol"), approval_id=e.get("approval_id"), key=key, recorded=recorded,
                   is_handoff=handoff)
        if e["workflow_id"] == WORKFLOW_ID:
            out["reasons"].append("WF08-08 does not handle its own runs.")
        elif state == "none":
            out["reasons"].append(why)
        elif state == "recovered" and not self._recorded([execution_id]).get(execution_id):
            out["reasons"].append("The run succeeded and no failure was recorded for it.")
        elif recorded:
            out["reasons"].append(f"This state ({state}, attempt {attempt}) is already recorded.")
        out["ok"] = not out["reasons"]
        return out

    # ---- record -------------------------------------------------------------------------------------------------------------------
    def record(self, execution_id: str, *, dry_run: bool = False, run_id: str | None = None) -> dict[str, Any]:
        c = self.cfg()
        base: dict[str, Any] = {"execution_id": execution_id}
        if not c.enabled:
            return {**base, "status": "skipped", "reason": "Error handling is switched off."}
        st = self.status(execution_id)
        if not st["exists"] or not st["ok"]:
            return {**base, "status": "skipped", "reason": " ".join(st["reasons"]), "workflow_id": st.get("workflow_id")}
        escalate = st["state"] == "escalated"
        report = {**base, **{k: st[k] for k in ("workflow_id", "workflow_name", "execution_status", "attempt", "max_attempts",
                                                 "error_category", "error_message", "retry_safe", "why", "next_attempt_at",
                                                 "dead_letter_id", "symbol")},
                  "result": st["state"], "escalated": escalate, "next_step": self._next(st)}
        if dry_run:
            return {**report, "status": "dry_run", "reason": f"Dry run: would record '{st['state']}' for this run."}
        now = self.svc.now()
        with self.svc.repo.write() as s:
            from aurum.orchestration.db_models import OrchAuditLog as L
            for details in s.execute(select(L.details).where(L.action == AUDIT_ACTION, L.entity_type == AUDIT_ENTITY,
                                                             L.entity_id == execution_id)).scalars():
                if (details or {}).get("key") == st["key"]:
                    return {**report, "status": "skipped", "reason": f"This state ({st['state']}) is already recorded."}
            self.svc.repo.audit(s, actor="wf08-08", actor_kind="system", action=AUDIT_ACTION, entity_type=AUDIT_ENTITY,
                                entity_id=execution_id, at=now, outcome="ok" if st["state"] in ("recovered", "retry_scheduled") else "failed",
                                correlation_id=st.get("correlation_id"),
                                details={"key": st["key"], "result": st["state"], "workflow_id": st["workflow_id"],
                                         "execution_status": st["execution_status"], "attempt": st["attempt"],
                                         "error_category": st["error_category"], "error_message": st["error_message"],
                                         "error_kind": st["error_kind"], "retry_safe": st["retry_safe"], "why": st["why"],
                                         "dead_letter_id": st["dead_letter_id"], "run_id": run_id})
        notified, note_error = 0, None
        if escalate and c.notify_dashboard:
            try:
                nid = self.approvals.notify_system_event(
                    "orchestration.error_escalated", "critical" if st["is_handoff"] else "high",
                    f"Workflow failed: {st['workflow_name'] or st['workflow_id']}",
                    f"{st['error_category'] or st['execution_status']}: {st['error_message'] or 'no message'} {st['why']}",
                    f"wf08-08:{execution_id}:{st['key']}")
                notified = 1 if nid else 0
            except Exception as exc:  # noqa: BLE001 - the error is recorded; a failed notification is reported
                note_error = type(exc).__name__
        delivered = self._send() if notified else {}
        return {**report, "status": "recorded", "notified": notified, "notification_error": note_error,
                "delivered_now": delivered, "recorded_at": _iso(now), "reason": st["why"]}

    @staticmethod
    def _next(st: dict[str, Any]) -> str:
        if st["state"] == "retry_scheduled":
            when = f" at {st['next_attempt_at']}" if st.get("next_attempt_at") else ""
            return f"The core tries again{when}, within the retry limit. Nothing to do."
        if st["state"] == "recovered":
            return "Recovered. Nothing to do."
        if st["state"] == "closed":
            return "Nothing to retry."
        if st["execution_status"] == "TIMED_OUT":
            return "Check the run in n8n. If it did not run, retry it on the Orchestration page and confirm the unknown outcome."
        return "Resolve the dead letter on the Orchestration page: fix the cause, then retry it or mark it resolved."

    def _send(self) -> dict[str, int]:
        try:
            if self._deliver is not None:
                return self._deliver(self.approvals)
            from aurum.approvals.worker import Worker
            return Worker(self.approvals, self.svc.settings).deliver_notifications()
        except Exception as exc:  # noqa: BLE001
            return {"error": f"{type(exc).__name__}: {exc}"[:200]}        # type: ignore[dict-item]

    # ---- the n8n Error Trigger ---------------------------------------------------------------------------------------------------
    def report(self, body: dict[str, Any]) -> dict[str, Any]:
        """A crash n8n's Error Trigger reports. The run is found by its n8n execution id, the error is classified and the
        core applies its usual retry rules. A crash that matches no tracked run is recorded and escalated."""
        from aurum.orchestration.db_models import OrchExecution as E
        from aurum.orchestration.db_models import OrchWorkflow as W
        c = self.cfg()
        n8n_wf, n8n_exec = str(body.get("n8n_workflow_id") or "")[:64], str(body.get("n8n_execution_id") or "")[:64]
        message = str(body.get("message") or "")[:1000]
        node = str(body.get("node") or "")[:120]
        base = {"n8n_workflow_id": n8n_wf, "n8n_execution_id": n8n_exec, "node": node}
        if not c.enabled:
            return {**base, "status": "skipped", "matched": False, "retry_safe": False, "reason": "Error handling is switched off."}
        with self.svc.repo.read() as s:
            w = s.execute(select(W.workflow_id, W.name, W.category).where(W.n8n_workflow_id == n8n_wf)).first() if n8n_wf else None
            eid = s.execute(select(E.execution_id).where(E.n8n_execution_id == n8n_exec)).scalar() if n8n_exec else None
        if w is not None and w[0] == WORKFLOW_ID:
            return {**base, "status": "skipped", "matched": False, "retry_safe": False,
                    "reason": "WF08-08 does not handle its own failures."}
        category, retryable, why = classify_engine_error(message, handoff=bool(w and w[2] == "execution_handoff"))
        text = f"n8n stopped at '{node or 'unknown node'}': {message or 'no message'}"
        applied: dict[str, Any] = {"applied": False}
        if eid:
            applied = self.svc.report_engine_failure(eid, category, text, retryable)
        now = self.svc.now()
        with self.svc.repo.write() as s:
            self.svc.repo.audit(s, actor="wf08-08", actor_kind="system", action=AUDIT_ENGINE, entity_type=AUDIT_ENTITY,
                                entity_id=(eid or n8n_exec or "unknown")[:64], at=now, outcome="ok" if eid else "failed",
                                details={**base, "workflow_id": w[0] if w else None, "category": category, "retryable": retryable,
                                         "message": message, "applied": applied})
        if not eid:
            notified = 0
            if c.notify_dashboard:
                try:
                    notified = 1 if self.approvals.notify_system_event(
                        "orchestration.error_escalated", "high", f"n8n workflow failed: {(w[1] if w else None) or n8n_wf or 'unknown'}",
                        f"{text}. It matches no run AURUM is tracking, so nothing was retried.",
                        f"wf08-08:engine:{n8n_exec or now.isoformat()}") else 0
                except Exception:  # noqa: BLE001
                    notified = 0
            return {**base, "status": "recorded", "matched": False, "retry_safe": False, "category": category, "notified": notified,
                    "workflow_id": w[0] if w else None, "result": "escalated",
                    "reason": "No tracked run has this n8n execution id (it may have failed before its first callback)."}
        new = applied.get("status")
        result = "unchanged" if not applied.get("applied") else "retry_scheduled" if new in IN_FLIGHT else "escalated"
        return {**base, "status": "recorded", "matched": True, "execution_id": eid, "workflow_id": w[0] if w else None,
                "category": category, "retry_safe": result == "retry_scheduled", "execution_status": new, "result": result,
                "next_attempt_at": _iso(applied.get("next_attempt_at")), "why": why,
                "reason": why if applied.get("applied") else f"The run was already {new}; nothing was changed."}


# ---- installation (used by `python -m aurum.cli orchestration setup-wf08-08`) ----------------------------------------------
def install(svc: Any, actor: Any, n8n_workflow_id: str, *, activate: bool = True, subscribe: bool = True) -> dict[str, Any]:
    """Registers WF08-08, links it to the n8n workflow, adds version 1.0.0, validates and activates it, and subscribes
    it to the failure events of the other workflows and to runs that succeeded after a retry. Safe to re-run."""
    from aurum.orchestration.common import OrchError
    steps: list[str] = []
    svc.ensure_ready()
    try:
        w = svc.get_workflow(WORKFLOW_ID)
    except OrchError:
        svc.register_workflow(actor, {"workflow_id": WORKFLOW_ID, "name": WORKFLOW_NAME, "category": "other",
                                      "trigger_types": ["event", "manual", "admin"], "owner": "module_08",
                                      "description": DESCRIPTION})
        steps.append("registered")
        w = svc.get_workflow(WORKFLOW_ID)
    if w["state"] in ("ACTIVE", "DEGRADED") and (w.get("n8n_workflow_id") != n8n_workflow_id
                                                  or w.get("webhook_path") != WEBHOOK_PATH):
        svc.set_workflow_state(actor, WORKFLOW_ID, "PAUSED", "re-linking WF08-08 to n8n")
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
            "description": "WF08-08 Error Handling and Recovery: records each failed run, whether a retry is safe, and escalates the rest."})
        if isinstance(version.get("version"), dict):        # tolerate a wrapped response
            version = version["version"]
        steps.append(f"added version {VERSION}")
    if version["status"] == "DRAFT":
        version = svc.validate_version(actor, WORKFLOW_ID, version["version_id"])["version"]
        steps.append(f"validated ({version['status']})")
    if activate:
        if version["status"] != "ACTIVE":
            svc.activate_version(actor, WORKFLOW_ID, version["version_id"], "WF08-08 installation")
            steps.append(f"activated version {VERSION}")
        if svc.get_workflow(WORKFLOW_ID)["state"] != "ACTIVE":
            svc.set_workflow_state(actor, WORKFLOW_ID, "ACTIVE", "WF08-08 installation")
            steps.append("workflow ACTIVE")
    subs: list[dict[str, Any]] = []
    if subscribe:
        have = {(x.get("event_pattern"), json.dumps(x.get("filter"), sort_keys=True)): x
                for x in svc.list_subscriptions()["items"] if x.get("workflow_id") == WORKFLOW_ID}
        wanted = [(e, None) for e in TRIGGER_EVENTS] + [(RECOVERY_EVENT, {"attempt": n}) for n in RECOVERY_ATTEMPTS]
        for event, flt in wanted:
            label = event + (f" (attempt {flt['attempt']})" if flt else "")
            key = (event, json.dumps(flt, sort_keys=True))
            if key in have:
                subs.append(have[key])
                steps.append(f"subscription to {label} already present")
            else:
                body = {"name": f"WF08-08 on {label}", "event_pattern": event, "target_kind": "workflow", "workflow_id": WORKFLOW_ID}
                if flt:
                    body["filter"] = flt
                subs.append(svc.create_subscription(actor, body))
                steps.append(f"subscribed to {label}")
    final = svc.get_workflow(WORKFLOW_ID)
    return {"workflow_id": WORKFLOW_ID, "state": final["state"], "version": VERSION, "validation": version.get("validation"),
            "subscriptions": subs, "steps": steps}
