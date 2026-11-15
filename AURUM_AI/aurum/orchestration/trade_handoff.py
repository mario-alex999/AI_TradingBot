"""WF08-06 Approved Trade Handoff - the backend half.

Module 07 authorises a trade: after a person approves and its own final validation passes, it creates the execution
request (a row in its queue for Module 09) and publishes ``trade.approved_for_execution``. WF08-06 follows that event.

**Module 09 is not built.** Nothing in AURUM sends an order, and WF08-06 does not either: it calls no broker, no MT5 and
no execution endpoint, and it creates no execution request. Module 09's contract is that it *reads* Module 07's queue
and reports back, and that it verifies the approval and the risk assessment itself before any order. Until Module 09
exists, every hand-off ends as ``awaiting_module_09``.

For each execution request WF08-06:

* asks Module 07 to verify its own authorisation (``verify_decision``: who approved and whether they could) and its own
  execution request (``verify_handoff``: intact, carries the approved parameters, idea version and risk assessment, idea
  still valid, Module 06 still stands by the assessment);
* checks that this hand-off was not recorded before (one execution request per approval, recorded once per state);
* reads whether Module 09 is deployed and whether it has acknowledged the request;
* records the result once in Module 08's audit log and reports Module 07's dashboard notification.

* ``config``    - the versioned ``trade_handoff`` section (off switch, batch size, look-back).
* ``preflight`` - may this run proceed? (switch on, database reachable, Module 07 schema ready).
* ``pending``   - execution requests whose current state has not been recorded yet.
* ``status``    - one approval: the execution request and both verifications.
* ``record``    - verify again, record the hand-off state, report acknowledgement and notifications.

A request that no longer verifies is recorded as ``not_valid`` (audit outcome ``refused``). WF08-06 cannot withdraw it
from Module 07's queue; Module 09 must refuse it on its own verification, which is why Module 09 may never rely on this
workflow as its security boundary.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy import select

from aurum.orchestration.config_model import TradeHandoffCfg

WORKFLOW_ID = "wf08-04-execution-handoff"        # the id the Module 08 core reserved for the execution hand-off
WEBHOOK_PATH = "aurum-wf08-06-trade-handoff"
VERSION = "1.0.0"
WORKFLOW_NAME = "WF08-06 Approved Trade Handoff"
DESCRIPTION = ("Follow the execution requests Module 07 creates for approved trades: verify and record each hand-off and "
               "report Module 09's acknowledgement. Sends no order. Triggers are refused unless Module 07 shows the approval.")
TRIGGER_EVENTS = ("trade.approved_for_execution",)
AUDIT_ACTION = "wf08-06.handoff_recorded"
AUDIT_ENTITY = "trade_handoff"
MODULE_09 = "module_09"

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "approvalId": {"type": "string", "maxLength": 64},
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


def _key(execution_request_id: str, state: str) -> str:
    return f"{execution_request_id[:36]}:{state}"[:64]


class TradeHandoff:
    """Stateless over the database: safe to call from any API worker. ``svc`` is the OrchestrationService."""

    def __init__(self, svc: Any, approvals: Callable[[], Any] | None = None, deliver: Callable[[Any], dict[str, int]] | None = None,
                 module_09: Callable[[], dict[str, Any]] | None = None):
        self.svc = svc
        self.engine = svc.repo.engine
        self._approvals_factory, self._deliver, self._module_09 = approvals, deliver, module_09
        self._approvals: Any = None

    # ---- wiring -------------------------------------------------------------------------------------------------------------
    def cfg(self) -> TradeHandoffCfg:
        return self.svc.config().trade_handoff

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

    def module_09(self) -> dict[str, Any]:
        """Is Module 09 deployed and healthy, as the Module 08 integration registry last saw it? Read-only."""
        if self._module_09 is not None:
            return self._module_09()
        try:
            row = next((i for i in self.svc.list_integrations()["items"] if i["integration_id"] == MODULE_09), None)
        except Exception as exc:  # noqa: BLE001 - not knowing is reported as not available
            return {"deployed": False, "status": "UNKNOWN", "detail": f"registry unavailable ({type(exc).__name__})"}
        if row is None:
            return {"deployed": False, "status": "UNKNOWN", "detail": "Module 09 is not registered."}
        healthy = row.get("status") == "HEALTHY"
        return {"deployed": bool(row.get("enabled")) and healthy, "status": row.get("status") or "UNKNOWN",
                "detail": row.get("detail"), "last_checked_at": row.get("last_checked_at")}

    def config_view(self) -> dict[str, Any]:
        c = self.cfg()
        return {"workflow_id": WORKFLOW_ID, "enabled": c.enabled, "kill_switch": not c.enabled,
                "max_handoffs_per_run": c.max_handoffs_per_run, "lookback_hours": c.lookback_hours,
                "trigger_events": list(TRIGGER_EVENTS), "module_09": self.module_09()}

    # ---- preflight ------------------------------------------------------------------------------------------------------------
    def preflight(self) -> dict[str, Any]:
        from sqlalchemy import inspect

        from aurum.approvals.db_models import ExecutionHandoff
        c = self.cfg()
        m09 = self.module_09()
        checks: dict[str, Any] = {"enabled": c.enabled, "module_09": m09["status"], "module_09_deployed": m09["deployed"]}
        reasons: list[str] = []
        if not c.enabled:
            reasons.append("The trade hand-off is switched off (trade_handoff.enabled = false).")
        try:
            checks["module_07_schema"] = inspect(self.engine).has_table(ExecutionHandoff.__tablename__)
            checks["database"] = "ok"
            if not checks["module_07_schema"]:
                reasons.append("Module 07 tables are missing; run the database migrations first.")
        except Exception as exc:  # noqa: BLE001 - any failure here means: do not proceed
            checks["database"] = f"unavailable ({type(exc).__name__})"
            reasons.append("The AURUM database is not reachable.")
        # Module 09 being absent does not stop the run: the hand-off is still verified and recorded as waiting.
        return {"proceed": not reasons, "checks": checks, "reasons": reasons, "checked_at": _iso(self.svc.now())}

    # ---- which hand-offs still need recording ----------------------------------------------------------------------------------
    def _recorded(self, keys: list[str]) -> set[str]:
        if not keys:
            return set()
        from aurum.orchestration.db_models import OrchAuditLog as L
        with self.svc.repo.read() as s:
            return set(s.execute(select(L.entity_id).where(L.action == AUDIT_ACTION, L.entity_type == AUDIT_ENTITY,
                                                           L.entity_id.in_(keys))).scalars())

    @staticmethod
    def _state(v: dict[str, Any], m09: dict[str, Any]) -> str:
        """What can be said about this hand-off now."""
        if not v.get("verified"):
            return "not_valid"
        if v.get("request_status") in ("filled", "partial"):
            return "executed"
        if v.get("request_status") in ("failed", "rejected"):
            return "execution_failed"
        if v.get("acknowledged"):
            return "acknowledged"
        return "awaiting_acknowledgement" if m09["deployed"] else "awaiting_module_09"

    def _view(self, approval_id: str) -> dict[str, Any]:
        approvals = self.approvals
        v = approvals.verify_handoff(approval_id)
        out = {**v, "ok": False, "reasons": [], "recorded": False}
        for k in ("decided_at", "risk_expires_at", "request_created_at", "picked_up_at"):
            if isinstance(out.get(k), datetime):
                out[k] = _iso(out[k])
        if not v["exists"] or not v["has_request"]:
            out["reasons"] = list(v["problems"])
            return out
        d = approvals.verify_decision(approval_id)
        out["checks"] = {**v["checks"], "authority": d["checks"].get("authority", "failed")}
        out["decided_by"] = d.get("decided_by") or []
        if d["outcome"] != "approved" or d["checks"].get("authority") != "ok":
            out["problems"] = list(v["problems"]) + [p for p in d["problems"] if p not in v["problems"]] or [
                f"Module 07 does not show an approval for this request ({d.get('status')})."]
            out["verified"] = False
        m09 = self.module_09()
        out["module_09"] = m09
        out["state"] = self._state(out, m09)
        out["recorded"] = bool(self._recorded([_key(v["execution_request_id"], out["state"])]))
        if out["recorded"]:
            out["reasons"].append(f"This hand-off state ({out['state']}) is already recorded.")
        out["ok"] = not out["reasons"]
        return out

    def pending(self, approval_ids: list[str] | None = None) -> dict[str, Any]:
        """Execution requests to look at. With ``approval_ids`` the listed approvals are returned whatever their state,
        so the workflow records why each one is skipped; otherwise the trade requests created inside the look-back
        window whose first record is still missing."""
        c = self.cfg()
        if approval_ids:
            items = [{"approval_id": a} for a in dict.fromkeys(approval_ids)]
        else:
            from aurum.approvals.db_models import ExecutionHandoff as H
            from aurum.database.connection import session_scope
            since = _utc(self.svc.now()) - timedelta(hours=c.lookback_hours)
            with session_scope(self.engine) as s:
                rows = s.execute(select(H.approval_id, H.execution_request_id, H.status).where(
                    H.kind == "trade", H.created_at >= since).order_by(H.created_at)).all()
            from aurum.orchestration.db_models import OrchAuditLog as L
            with self.svc.repo.read() as s:
                seen = {str(e).split(":")[0] for e in s.execute(select(L.entity_id).where(
                    L.action == AUDIT_ACTION, L.entity_type == AUDIT_ENTITY)).scalars()}
            items = [{"approval_id": a, "execution_request_id": x, "request_status": st} for a, x, st in rows if x not in seen]
        deferred = items[c.max_handoffs_per_run:]
        items = items[:c.max_handoffs_per_run]
        return {"enabled": c.enabled, "items": items if c.enabled else [], "count": len(items) if c.enabled else 0,
                "deferred": len(deferred)}

    def status(self, approval_id: str) -> dict[str, Any]:
        """Retrieve the approved trade and verify the approval, the risk approval, the expiry and the execution request
        (Module 07 does the verifying), and check whether this hand-off was recorded before."""
        return self._view(approval_id)

    # ---- record -------------------------------------------------------------------------------------------------------------------
    def record(self, approval_id: str, *, dry_run: bool = False, execution_id: str | None = None) -> dict[str, Any]:
        c = self.cfg()
        base: dict[str, Any] = {"approval_id": approval_id}
        if not c.enabled:
            return {**base, "status": "skipped", "reason": "The trade hand-off is switched off."}
        st = self.status(approval_id)
        base.update(symbol=st.get("symbol"), direction=st.get("direction"), approval_status=st.get("status"),
                    execution_request_id=st.get("execution_request_id"), idea_id=st.get("idea_id"))
        if not st["ok"]:
            return {**base, "status": "skipped", "reason": " ".join(st["reasons"])}
        state = st["state"]
        report = {**base, "result": state, "verified": st["verified"], "checks": st["checks"], "problems": st["problems"],
                  "request_status": st["request_status"], "acknowledged": st["acknowledged"], "module_09": st["module_09"],
                  "idea_version": st.get("idea_version"), "risk_assessment_id": st.get("risk_assessment_id"),
                  "next_step": self._next(state)}
        if dry_run:
            return {**report, "status": "dry_run", "reason": f"Dry run: would record '{state}' for this hand-off."}
        now = self.svc.now()
        key = _key(st["execution_request_id"], state)
        with self.svc.repo.write() as s:
            from aurum.orchestration.db_models import OrchAuditLog as L
            if s.execute(select(L.seq).where(L.action == AUDIT_ACTION, L.entity_type == AUDIT_ENTITY, L.entity_id == key)).first():
                return {**report, "status": "skipped", "reason": f"This hand-off state ({state}) is already recorded."}
            self.svc.repo.audit(s, actor="wf08-06", actor_kind="system", action=AUDIT_ACTION, entity_type=AUDIT_ENTITY,
                                entity_id=key, at=now, outcome="ok" if st["verified"] else "refused",
                                correlation_id=st.get("correlation_id"),
                                details={"approval_id": approval_id, "execution_request_id": st["execution_request_id"],
                                         "result": state, "approval_status": st["status"], "request_status": st["request_status"],
                                         "verified": st["verified"], "checks": st["checks"], "problems": st["problems"],
                                         "acknowledged": st["acknowledged"], "module_09": st["module_09"]["status"],
                                         "idea_id": st.get("idea_id"), "idea_version": st.get("idea_version"),
                                         "risk_assessment_id": st.get("risk_assessment_id"), "execution_id": execution_id})
        delivered = self._send()
        deliveries = self._deliveries(approval_id)
        return {**report, "status": "recorded", "deliveries": deliveries, "delivered_now": delivered, "recorded_at": _iso(now),
                "reason": f"Recorded '{state}'" + (f": {'; '.join(st['problems'])}"[:300] if st["problems"] else ".")}

    @staticmethod
    def _next(state: str) -> str:
        return {
            "awaiting_module_09": "The execution request is valid and waits in Module 07's queue. Module 09 is not deployed, "
                                  "so nothing will be executed.",
            "awaiting_acknowledgement": "The execution request is valid and waits in Module 07's queue for Module 09 to pick "
                                        "it up. Do not send it again: Module 09 reads the queue itself.",
            "acknowledged": "Module 09 has the execution request. It verifies the approval and the risk assessment itself "
                            "before any order, and reports the outcome to Module 07.",
            "executed": "Module 09 reported the execution to Module 07.",
            "execution_failed": "Module 09 reported that the execution failed. Nothing is retried automatically.",
            "not_valid": "This execution request does not verify and must not be executed. WF08-06 cannot withdraw it; "
                         "Module 09 must refuse it on its own verification.",
        }[state]

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
        """Module 07's hand-off and execution notifications for this approval, per channel and delivery status."""
        from aurum.approvals.db_models import Notification as N
        from aurum.approvals.db_models import NotificationDelivery as D
        from aurum.database.connection import session_scope
        kinds = ["execution.handoff_submitted", "execution.completed", "execution.failed"]
        out: dict[str, dict[str, int]] = {}
        with session_scope(self.engine) as s:
            for channel, status in s.execute(select(D.channel, D.status).join(N, N.id == D.notification_id)
                                             .where(N.approval_id == approval_id, N.event_type.in_(kinds))).all():
                out.setdefault(channel, {})
                out[channel][status] = out[channel].get(status, 0) + 1
        return out


# ---- installation (used by `python -m aurum.cli orchestration setup-wf08-06`) ----------------------------------------------
def install(svc: Any, actor: Any, n8n_workflow_id: str, *, activate: bool = True, subscribe: bool = True) -> dict[str, Any]:
    """Turns the reserved execution-handoff registration into WF08-06: names it, links it to the n8n workflow, adds
    version 1.0.0, validates and activates it, and subscribes it to ``trade.approved_for_execution``. The core's approval
    guard for this category stays in force. Safe to re-run."""
    steps: list[str] = []
    svc.ensure_ready()
    w = svc.get_workflow(WORKFLOW_ID)
    if w.get("name") != WORKFLOW_NAME:
        svc.update_workflow(actor, WORKFLOW_ID, {"name": WORKFLOW_NAME, "description": DESCRIPTION, "row_version": w["row_version"]})
        steps.append("renamed the reserved registration")
        w = svc.get_workflow(WORKFLOW_ID)
    if w["state"] in ("ACTIVE", "DEGRADED") and (w.get("n8n_workflow_id") != n8n_workflow_id
                                                  or w.get("webhook_path") != WEBHOOK_PATH):
        svc.set_workflow_state(actor, WORKFLOW_ID, "PAUSED", "re-linking WF08-06 to n8n")
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
            "description": "WF08-06 Approved Trade Handoff: verifies and records each Module 07 execution request."})
        if isinstance(version.get("version"), dict):        # tolerate a wrapped response
            version = version["version"]
        steps.append(f"added version {VERSION}")
    if version["status"] == "DRAFT":
        version = svc.validate_version(actor, WORKFLOW_ID, version["version_id"])["version"]
        steps.append(f"validated ({version['status']})")
    if activate:
        if version["status"] != "ACTIVE":
            svc.activate_version(actor, WORKFLOW_ID, version["version_id"], "WF08-06 installation")
            steps.append(f"activated version {VERSION}")
        if svc.get_workflow(WORKFLOW_ID)["state"] != "ACTIVE":
            svc.set_workflow_state(actor, WORKFLOW_ID, "ACTIVE", "WF08-06 installation")
            steps.append("workflow ACTIVE")
    subs: list[dict[str, Any]] = []
    if subscribe:
        have = {x.get("event_pattern"): x for x in svc.list_subscriptions()["items"] if x.get("workflow_id") == WORKFLOW_ID}
        for event in TRIGGER_EVENTS:
            if event in have:
                subs.append(have[event])
                steps.append(f"subscription to {event} already present")
            else:
                subs.append(svc.create_subscription(actor, {"name": f"WF08-06 on {event}", "event_pattern": event,
                                                            "target_kind": "workflow", "workflow_id": WORKFLOW_ID}))
                steps.append(f"subscribed to {event}")
    final = svc.get_workflow(WORKFLOW_ID)
    return {"workflow_id": WORKFLOW_ID, "state": final["state"], "version": VERSION, "validation": version.get("validation"),
            "subscriptions": subs, "steps": steps}
