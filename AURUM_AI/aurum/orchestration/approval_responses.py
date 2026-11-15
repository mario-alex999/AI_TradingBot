"""WF08-05 Approval Response Handler - the backend half.

A person decides in the signed-in AURUM dashboard and **Module 07** records the decision (or lets the request expire).
WF08-05 follows Module 07's outcome events and, for each one:

* asks Module 07 to verify its own outcome (``ApprovalService.verify_decision``, read-only): who decided and whether they
  could, and - while an approval has not been handed to execution yet - that it is still tied to the idea version,
  evidence and risk assessment that were approved, that the idea has not expired, and that Module 06 still stands by the
  assessment;
* records the verified outcome once in Module 08's audit log;
* reports Module 07's dashboard notifications for that outcome and what happens next.

* ``config``    - the versioned ``approval_responses`` section (off switch, batch size, look-back).
* ``preflight`` - may this run proceed? (switch on, database reachable, Module 07 schema ready).
* ``pending``   - decided requests whose outcome has not been recorded yet.
* ``status``    - one request: its current status and the verification.
* ``record``    - verify again, record the outcome, report notifications and the next step.

WF08-05 changes no approval and no trade. It does not approve, reject, expire, invalidate or hand off anything: those
are Module 07's, and Module 07's own final validation is what releases an approved trade to execution. An approval
that no longer verifies is recorded as ``approved_not_valid`` and nothing is passed on by this workflow.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy import select

from aurum.orchestration.config_model import ApprovalResponseCfg

WORKFLOW_ID = "wf08-05-approval-response"
WEBHOOK_PATH = "aurum-wf08-05-approval-response"
VERSION = "1.0.0"
WORKFLOW_NAME = "WF08-05 Approval Response Handler"
TRIGGER_EVENTS = ("approval.approved", "approval.rejected", "approval.expired", "approval.invalidated",
                  "approval.cancelled", "approval.modification_requested")
AUDIT_ACTION = "wf08-05.response_recorded"
AUDIT_ENTITY = "approval_response"
OPEN = ("PENDING", "UNDER_REVIEW")
HANDED_ON = ("EXECUTION_PENDING", "EXECUTION_SUBMITTED", "EXECUTED", "EXECUTION_FAILED")

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "approvalIds": {"type": "array", "items": {"type": "string", "maxLength": 64}, "maxItems": 50},
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


def _key(approval_id: str, outcome: str) -> str:
    return f"{approval_id}:{outcome}"[:64]


class ApprovalResponseHandler:
    """Stateless over the database: safe to call from any API worker. ``svc`` is the OrchestrationService."""

    def __init__(self, svc: Any, approvals: Callable[[], Any] | None = None, deliver: Callable[[Any], dict[str, int]] | None = None):
        self.svc = svc
        self.engine = svc.repo.engine
        self._approvals_factory, self._deliver = approvals, deliver
        self._approvals: Any = None

    # ---- wiring -------------------------------------------------------------------------------------------------------------
    def cfg(self) -> ApprovalResponseCfg:
        return self.svc.config().approval_responses

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
                "max_responses_per_run": c.max_responses_per_run, "lookback_hours": c.lookback_hours,
                "expire_overdue_requests": c.expire_overdue_requests, "trigger_events": list(TRIGGER_EVENTS)}

    # ---- preflight ------------------------------------------------------------------------------------------------------------
    def preflight(self) -> dict[str, Any]:
        from sqlalchemy import inspect

        from aurum.approvals.db_models import ApprovalRequest
        c = self.cfg()
        checks: dict[str, Any] = {"enabled": c.enabled}
        reasons: list[str] = []
        if not c.enabled:
            reasons.append("Approval response handling is switched off (approval_responses.enabled = false).")
        try:
            checks["module_07_schema"] = inspect(self.engine).has_table(ApprovalRequest.__tablename__)
            checks["database"] = "ok"
            if not checks["module_07_schema"]:
                reasons.append("Module 07 tables are missing; run the database migrations first.")
        except Exception as exc:  # noqa: BLE001 - any failure here means: do not proceed
            checks["database"] = f"unavailable ({type(exc).__name__})"
            reasons.append("The AURUM database is not reachable.")
        return {"proceed": not reasons, "checks": checks, "reasons": reasons, "checked_at": _iso(self.svc.now())}

    # ---- which outcomes still need recording -----------------------------------------------------------------------------------
    def _recorded(self, keys: list[str]) -> set[str]:
        if not keys:
            return set()
        from aurum.orchestration.db_models import OrchAuditLog as L
        with self.svc.repo.read() as s:
            return set(s.execute(select(L.entity_id).where(L.action == AUDIT_ACTION, L.entity_type == AUDIT_ENTITY,
                                                           L.entity_id.in_(keys))).scalars())

    def _decided(self) -> list[tuple[str, str]]:
        """(approval id, status) of requests that left the open states inside the look-back window, oldest first."""
        from aurum.approvals.db_models import ApprovalRequest as R
        from aurum.database.connection import session_scope
        since = _utc(self.svc.now()) - timedelta(hours=self.cfg().lookback_hours)
        with session_scope(self.engine) as s:
            return [(a, st) for a, st in s.execute(select(R.approval_id, R.status).where(
                R.status.not_in(OPEN), R.status_changed_at >= since).order_by(R.status_changed_at)).all()]

    @staticmethod
    def _outcome(status: str) -> str:
        return "approved" if status == "APPROVED" or status in HANDED_ON else status.lower()

    def _view(self, approval_id: str) -> dict[str, Any]:
        v = self.approvals.verify_decision(approval_id)
        out = {**v, "ok": False, "reasons": [], "recorded": False}
        for k in ("decided_at", "status_changed_at", "expires_at"):
            if isinstance(out.get(k), datetime):
                out[k] = _iso(out[k])
        if not v["exists"]:
            out["reasons"] = list(v["problems"])
            return out
        if not v["decided"]:
            out["reasons"].append(f"The request is still {v['status']}; nobody has decided yet.")
            return out
        out["recorded"] = _key(approval_id, v["outcome"]) in self._recorded([_key(approval_id, v["outcome"])])
        if out["recorded"]:
            out["reasons"].append(f"This outcome ({v['outcome']}) is already recorded.")
        out["ok"] = not out["reasons"]
        return out

    def pending(self, approval_ids: list[str] | None = None) -> dict[str, Any]:
        """Decided requests whose outcome is not recorded yet. With ``approval_ids`` the listed requests are returned
        whatever their state, so the workflow records why each one is skipped."""
        c = self.cfg()
        if approval_ids:
            items = [{"approval_id": a} for a in dict.fromkeys(approval_ids)]
        else:
            rows = self._decided()
            done = self._recorded([_key(a, self._outcome(st)) for a, st in rows])
            items = [{"approval_id": a, "status": st, "outcome": self._outcome(st)} for a, st in rows
                     if _key(a, self._outcome(st)) not in done]
        deferred = items[c.max_responses_per_run:]
        items = items[:c.max_responses_per_run]
        return {"enabled": c.enabled, "items": items if c.enabled else [], "count": len(items) if c.enabled else 0,
                "deferred": len(deferred)}

    def status(self, approval_id: str) -> dict[str, Any]:
        """Validate the approval id, retrieve the current status and verify the outcome (Module 07 does the verifying)."""
        return self._view(approval_id)

    # ---- record -------------------------------------------------------------------------------------------------------------------
    def record(self, approval_id: str, *, dry_run: bool = False, execution_id: str | None = None) -> dict[str, Any]:
        c = self.cfg()
        base: dict[str, Any] = {"approval_id": approval_id}
        if not c.enabled:
            return {**base, "status": "skipped", "reason": "Approval response handling is switched off."}
        st = self.status(approval_id)
        base.update(symbol=st.get("symbol"), direction=st.get("direction"), request_status=st.get("status"),
                    outcome=st.get("outcome"), idea_id=st.get("idea_id"))
        if not st["ok"]:
            return {**base, "status": "skipped", "reason": " ".join(st["reasons"])}
        outcome = st["outcome"]
        result = "approved_not_valid" if outcome == "approved" and not st["verified"] else outcome
        report = {**base, "result": result, "verified": st["verified"], "checks": st["checks"], "problems": st["problems"],
                  "decided_by": st.get("decided_by") or [], "idea_version": st.get("idea_version"),
                  "risk_assessment_id": st.get("risk_assessment_id"), "execution_request_id": st.get("execution_request_id"),
                  "next_step": self._next(st, result)}
        if dry_run:
            return {**report, "status": "dry_run", "reason": f"Dry run: would record '{result}' for this request."}
        now = self.svc.now()
        with self.svc.repo.write() as s:
            from aurum.orchestration.db_models import OrchAuditLog as L
            key = _key(approval_id, outcome)
            if s.execute(select(L.seq).where(L.action == AUDIT_ACTION, L.entity_type == AUDIT_ENTITY, L.entity_id == key)).first():
                return {**report, "status": "skipped", "reason": f"This outcome ({outcome}) is already recorded."}
            self.svc.repo.audit(s, actor="wf08-05", actor_kind="system", action=AUDIT_ACTION, entity_type=AUDIT_ENTITY,
                                entity_id=key, at=now, outcome="ok" if st["verified"] else "refused",
                                correlation_id=st.get("correlation_id"),
                                details={"approval_id": approval_id, "result": result, "request_status": st["status"],
                                         "verified": st["verified"], "checks": st["checks"], "problems": st["problems"],
                                         "idea_id": st.get("idea_id"), "idea_version": st.get("idea_version"),
                                         "risk_assessment_id": st.get("risk_assessment_id"),
                                         "decided_by": st.get("decided_by") or [],
                                         "execution_request_id": st.get("execution_request_id"), "execution_id": execution_id})
        delivered = self._send()
        deliveries = self._deliveries(approval_id)
        return {**report, "status": "recorded", "deliveries": deliveries, "delivered_now": delivered, "recorded_at": _iso(now),
                "reason": f"Recorded '{result}'" + (f": {'; '.join(st['problems'])}"[:300] if st["problems"] else ".")}

    @staticmethod
    def _next(st: dict[str, Any], result: str) -> str:
        if result == "approved":
            if st["status"] in HANDED_ON:
                return (f"Module 07 passed its final validation and created execution request "
                        f"{st.get('execution_request_id') or '(unknown)'} ({st['status']}). WF08-06 takes it from there.")
            return "Approved and still valid. Module 07's final validation releases it to execution (WF08-06)."
        if result == "approved_not_valid":
            return ("This approval does not authorise execution any more. WF08-05 passes nothing on; Module 07's final "
                    "validation decides what happens to the request.")
        return "Nothing will be executed for this request."

    def _send(self) -> dict[str, int]:
        """Deliver the email and Telegram notifications that are due (Module 07's own delivery step)."""
        try:
            if self._deliver is not None:
                return self._deliver(self.approvals)
            from aurum.approvals.worker import Worker
            return Worker(self.approvals, self.svc.settings).deliver_notifications()
        except Exception as exc:  # noqa: BLE001 - delivery problems never undo the record; they are reported
            return {"error": f"{type(exc).__name__}: {exc}"[:200]}        # type: ignore[dict-item]

    def _deliveries(self, approval_id: str) -> dict[str, dict[str, int]]:
        """Module 07's notifications about this request's outcome, per channel and delivery status."""
        from aurum.approvals.db_models import Notification as N
        from aurum.approvals.db_models import NotificationDelivery as D
        from aurum.database.connection import session_scope
        kinds = [e for e in TRIGGER_EVENTS] + ["execution.handoff_submitted"]
        out: dict[str, dict[str, int]] = {}
        with session_scope(self.engine) as s:
            for channel, status in s.execute(select(D.channel, D.status).join(N, N.id == D.notification_id)
                                             .where(N.approval_id == approval_id, N.event_type.in_(kinds))).all():
                out.setdefault(channel, {})
                out[channel][status] = out[channel].get(status, 0) + 1
        return out

    # ---- optional: Module 07's expiry pass, for hosts without a resident Module 07 worker ------------------------------------
    def expire_overdue(self) -> dict[str, Any]:
        """Runs Module 07's own ``expire_due`` when ``approval_responses.expire_overdue_requests`` is on. Module 07 decides
        what expires; this only gives it a turn where no Module 07 worker is running (a serverless deployment)."""
        if not self.cfg().expire_overdue_requests:
            return {"skipped": True}
        return dict(self.approvals.expire_due())


# ---- installation (used by `python -m aurum.cli orchestration setup-wf08-05`) ----------------------------------------------
def install(svc: Any, actor: Any, n8n_workflow_id: str, *, activate: bool = True, subscribe: bool = True) -> dict[str, Any]:
    """Registers WF08-05, links it to the n8n workflow, adds version 1.0.0, validates and activates it, and subscribes
    it to Module 07's outcome events. Safe to re-run."""
    from aurum.orchestration.common import OrchError
    steps: list[str] = []
    svc.ensure_ready()
    try:
        w = svc.get_workflow(WORKFLOW_ID)
    except OrchError:
        svc.register_workflow(actor, {"workflow_id": WORKFLOW_ID, "name": WORKFLOW_NAME, "category": "risk_approval",
                                      "trigger_types": ["event", "manual", "admin"], "owner": "module_08",
                                      "description": "Follow Module 07's decisions: verify each approval, rejection or expiry "
                                                     "and record it. Changes no approval and no trade."})
        steps.append("registered")
        w = svc.get_workflow(WORKFLOW_ID)
    if w["state"] in ("ACTIVE", "DEGRADED") and (w.get("n8n_workflow_id") != n8n_workflow_id
                                                  or w.get("webhook_path") != WEBHOOK_PATH):
        svc.set_workflow_state(actor, WORKFLOW_ID, "PAUSED", "re-linking WF08-05 to n8n")
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
            "description": "WF08-05 Approval Response Handler: verifies and records each Module 07 outcome."})
        if isinstance(version.get("version"), dict):        # tolerate a wrapped response
            version = version["version"]
        steps.append(f"added version {VERSION}")
    if version["status"] == "DRAFT":
        version = svc.validate_version(actor, WORKFLOW_ID, version["version_id"])["version"]
        steps.append(f"validated ({version['status']})")
    if activate:
        if version["status"] != "ACTIVE":
            svc.activate_version(actor, WORKFLOW_ID, version["version_id"], "WF08-05 installation")
            steps.append(f"activated version {VERSION}")
        if svc.get_workflow(WORKFLOW_ID)["state"] != "ACTIVE":
            svc.set_workflow_state(actor, WORKFLOW_ID, "ACTIVE", "WF08-05 installation")
            steps.append("workflow ACTIVE")
    subs: list[dict[str, Any]] = []
    if subscribe:
        have = {x.get("event_pattern"): x for x in svc.list_subscriptions()["items"] if x.get("workflow_id") == WORKFLOW_ID}
        for event in TRIGGER_EVENTS:
            if event in have:
                subs.append(have[event])
                steps.append(f"subscription to {event} already present")
            else:
                subs.append(svc.create_subscription(actor, {"name": f"WF08-05 on {event}", "event_pattern": event,
                                                            "target_kind": "workflow", "workflow_id": WORKFLOW_ID}))
                steps.append(f"subscribed to {event}")
    final = svc.get_workflow(WORKFLOW_ID)
    return {"workflow_id": WORKFLOW_ID, "state": final["state"], "version": VERSION, "validation": version.get("validation"),
            "subscriptions": subs, "steps": steps}
