"""WF08-04 Human Approval Notification - the backend half.

WF08-03 hands each open Module 05 idea to Module 06. When Module 06 passes one (PASS or REVIEW), WF08-04 makes sure a
person is asked to decide: it hands the idea to **Module 07**, which opens the approval request and notifies the
reviewers, and it reports what was sent.

* ``config``    - the versioned ``approval_notifications`` section (off switch, batch size).
* ``preflight`` - may this run proceed? (switch on, database reachable, Module 07 schema ready).
* ``pending``   - ideas Module 06 passed for WF08-03 that Module 07 has not been given yet.
* ``status``    - one idea: the idea, its risk assessment, and whether both are still valid.
* ``notify``    - hand the idea to Module 07 (request opened, reviewers notified), deliver the notifications that can be
                  delivered now, and return the notification with its delivery status.

How the two risk assessments relate: WF08-03's assessment is the gate that decides whether a person is asked at all.
Module 07 always assesses again when it opens a request, and attaches that assessment (and its risk reservation) to the
request. Module 06 allows only one live assessment per trade idea (R047), so ``notify`` passes WF08-03's assessment to
Module 07, which releases it just before making its own. The risk is reserved once, and the request's assessment is not
blocked as a duplicate of the screening one.

Approval controls live only in the signed-in AURUM frontend. A notification is a message with a link to the review page:
it cannot approve anything, and nothing here approves, places or executes a trade or talks to MT5.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy import select

from aurum.orchestration.config_model import ApprovalNotificationCfg

WORKFLOW_ID = "wf08-04-approval-notification"
WEBHOOK_PATH = "aurum-wf08-04-approval-notification"
VERSION = "1.0.0"
WORKFLOW_NAME = "WF08-04 Human Approval Notification"
TRIGGER_EVENTS = ("risk.assessment.completed", "risk.assessment.requires_review")
PRESCREEN_PREFIX = "wf08-03:"                    # idempotency keys of the assessments WF08-03 requests
PASSED = ("PASS", "REVIEW")

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "ideaIds": {"type": "array", "items": {"type": "string", "maxLength": 64}, "maxItems": 50},
        "dryRun": {"type": "boolean"},
        "reason": {"type": "string", "maxLength": 200},
        "event": {"type": "object"},
        "batch": {"type": "object"},
    },
}
OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["completed", "partial", "skipped"]},
        "reason": {"type": "string"},
        "notified": {"type": "array"},
        "skipped": {"type": "array"},
        "waitingFor": {"type": "string"},
    },
}


def _utc(t: datetime) -> datetime:
    return t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t.astimezone(timezone.utc)


def _iso(t: datetime | None) -> str | None:
    return _utc(t).isoformat().replace("+00:00", "Z") if t else None


class ApprovalNotifier:
    """Stateless over the database: safe to call from any API worker. ``svc`` is the OrchestrationService."""

    def __init__(self, svc: Any, approvals: Callable[[], Any] | None = None, risk: Callable[[], Any] | None = None,
                 deliver: Callable[[Any], dict[str, int]] | None = None):
        self.svc = svc
        self.engine = svc.repo.engine
        self._approvals_factory, self._risk_factory, self._deliver = approvals, risk, deliver
        self._approvals: Any = None
        self._risk: Any = None

    # ---- wiring -------------------------------------------------------------------------------------------------------------
    def cfg(self) -> ApprovalNotificationCfg:
        return self.svc.config().approval_notifications

    @property
    def risk(self) -> Any:
        if self._risk is None:
            if self._risk_factory is not None:
                self._risk = self._risk_factory()
            else:
                from aurum.risk.service import RiskService
                self._risk = RiskService(self.engine, self.svc.settings)
        return self._risk

    @property
    def approvals(self) -> Any:
        if self._approvals is None:
            if self._approvals_factory is not None:
                self._approvals = self._approvals_factory()
            else:
                from aurum.approvals.wiring import build_service
                self._approvals = build_service(self.engine, self.svc.settings, risk=lambda: self.risk)
        return self._approvals

    def config_view(self) -> dict[str, Any]:
        c = self.cfg()
        return {"workflow_id": WORKFLOW_ID, "enabled": c.enabled, "kill_switch": not c.enabled, "coalesce": c.coalesce,
                "max_ideas_per_run": c.max_ideas_per_run, "trigger_events": list(TRIGGER_EVENTS),
                "dashboard_url": self._dashboard()}

    def _dashboard(self) -> str | None:
        url = getattr(self.svc.settings, "aurum_dashboard_url", None)
        return str(url).rstrip("/") if url else None

    # ---- preflight ------------------------------------------------------------------------------------------------------------
    def preflight(self) -> dict[str, Any]:
        from sqlalchemy import inspect

        from aurum.approvals.db_models import ApprovalRequest
        from aurum.risk.db_models import RiskAssessment
        c = self.cfg()
        checks: dict[str, Any] = {"enabled": c.enabled, "dashboard_url": bool(self._dashboard())}
        reasons: list[str] = []
        if not c.enabled:
            reasons.append("Approval notifications are switched off (approval_notifications.enabled = false).")
        try:
            names = inspect(self.engine)
            checks["module_06_schema"] = names.has_table(RiskAssessment.__tablename__)
            checks["module_07_schema"] = names.has_table(ApprovalRequest.__tablename__)
            checks["database"] = "ok"
            if not checks["module_06_schema"] or not checks["module_07_schema"]:
                reasons.append("Module 06 or Module 07 tables are missing; run the database migrations first.")
        except Exception as exc:  # noqa: BLE001 - any failure here means: do not proceed
            checks["database"] = f"unavailable ({type(exc).__name__})"
            reasons.append("The AURUM database is not reachable.")
        return {"proceed": not reasons, "checks": checks, "reasons": reasons, "checked_at": _iso(self.svc.now())}

    # ---- which ideas need a person ----------------------------------------------------------------------------------------------
    def _prescreened(self, idea_ids: list[str] | None = None) -> dict[str, dict[str, Any]]:
        """The newest WF08-03 assessment per idea."""
        from aurum.database.connection import session_scope
        from aurum.risk.db_models import RiskAssessment as A
        q = (select(A.trade_idea_id, A.assessment_id, A.decision, A.state)
             .where(A.idempotency_key.like(PRESCREEN_PREFIX + "%"), A.trade_idea_id.is_not(None)).order_by(A.id))
        if idea_ids:
            q = q.where(A.trade_idea_id.in_(idea_ids))
        with session_scope(self.engine) as s:
            return {i: {"assessment_id": a, "decision": d, "state": st} for i, a, d, st in s.execute(q).all()}

    def _requests(self, idea_ids: list[str]) -> dict[str, dict[str, Any]]:
        """The newest Module 07 approval request per idea."""
        if not idea_ids:
            return {}
        from aurum.approvals.db_models import ApprovalRequest as R
        from aurum.database.connection import session_scope
        with session_scope(self.engine) as s:
            rows = s.execute(select(R.idea_id, R.approval_id, R.status).where(R.idea_id.in_(idea_ids)).order_by(R.created_at)).all()
        return {i: {"approval_id": a, "status": st} for i, a, st in rows}

    def _candidate(self, idea_id: str) -> dict[str, Any] | None:
        from aurum.database.connection import session_scope
        from aurum.decision.db_models import TradeCandidate
        from aurum.decision.repository import to_dict
        with session_scope(self.engine) as s:
            row = s.execute(select(TradeCandidate).where(TradeCandidate.candidate_uid == idea_id)).scalar_one_or_none()
            return to_dict(row) if row is not None else None

    def _view(self, idea_id: str, pre: dict[str, Any] | None, req: dict[str, Any] | None, validate: bool) -> dict[str, Any]:
        from aurum.decision import lifecycle
        idea = self._candidate(idea_id)
        out: dict[str, Any] = {"idea_id": idea_id, "exists": idea is not None, "ok": False, "reasons": []}
        if idea is None:
            out["reasons"].append("Module 05 has no such trade idea.")
            return out
        now = _utc(self.svc.now())
        expired = _utc(idea["expires_at"]) <= now
        out.update(symbol=idea["symbol"], direction=idea["direction"], timeframe=idea["timeframe"], strategy=idea["template"],
                   status=idea["status"], expired=expired, expires_at=_iso(idea["expires_at"]),
                   assessment_id=pre["assessment_id"] if pre else None, risk_decision=pre["decision"] if pre else None,
                   already_notified=req is not None, approval_id=req["approval_id"] if req else None)
        if idea["status"] not in lifecycle.ACTIVE:
            out["reasons"].append(f"The idea is {idea['status']}, not open.")
        elif expired:
            out["reasons"].append("The setup has expired.")
        if req is not None:
            out["reasons"].append(f"Module 07 already has an approval request for this idea ({req['status']}).")
        if pre is None:
            out["reasons"].append("Module 06 has not assessed this idea for WF08-03.")
        elif pre["decision"] not in PASSED:
            out["reasons"].append(f"Module 06 did not pass this idea ({pre['decision']}).")
        elif pre["state"] != "active":
            out["reasons"].append(f"The risk assessment is {pre['state']}.")
        elif validate and not out["reasons"]:
            try:
                check = self.risk.validate_assessment(pre["assessment_id"])
                if not check.get("valid"):
                    out["reasons"] += [str(r) for r in check.get("reasons") or ["The risk assessment is no longer valid."]]
            except Exception as exc:  # noqa: BLE001 - cannot confirm it is still valid: do not notify
                out["reasons"].append(f"Module 06 could not confirm the assessment ({type(exc).__name__}).")
        out["ok"] = not out["reasons"]
        return out

    def pending(self, idea_ids: list[str] | None = None) -> dict[str, Any]:
        """Ideas Module 06 passed that Module 07 has not been given. With ``idea_ids`` the listed ideas are returned
        whatever their state, so the workflow records why each one is skipped."""
        c = self.cfg()
        pre = self._prescreened(idea_ids)
        ids = list(idea_ids) if idea_ids else [i for i, p in pre.items() if p["decision"] in PASSED and p["state"] == "active"]
        reqs = self._requests(ids)
        views = [self._view(i, pre.get(i), reqs.get(i), validate=False) for i in ids]
        items = views if idea_ids else [v for v in views if v["ok"]]
        deferred = items[c.max_ideas_per_run:]
        items = items[:c.max_ideas_per_run]
        return {"enabled": c.enabled, "items": items if c.enabled else [], "count": len(items) if c.enabled else 0,
                "deferred": len(deferred)}

    def status(self, idea_id: str) -> dict[str, Any]:
        """Retrieve the idea and its risk assessment, and check both are still valid."""
        return self._view(idea_id, self._prescreened([idea_id]).get(idea_id), self._requests([idea_id]).get(idea_id), validate=True)

    # ---- notify -----------------------------------------------------------------------------------------------------------------
    def notify(self, idea_id: str, *, dry_run: bool = False, execution_id: str | None = None) -> dict[str, Any]:
        c = self.cfg()
        base: dict[str, Any] = {"idea_id": idea_id}
        if not c.enabled:
            return {**base, "status": "skipped", "reason": "Approval notifications are switched off."}
        st = self.status(idea_id)
        base.update(symbol=st.get("symbol"), direction=st.get("direction"), timeframe=st.get("timeframe"))
        if not st["ok"]:
            return {**base, "status": "skipped", "reason": " ".join(st["reasons"]), "approval_id": st.get("approval_id")}
        if dry_run:
            return {**base, "status": "dry_run", "reason": "Dry run: would open the Module 07 approval request and notify the reviewers."}
        started = time.monotonic()
        approvals = self.approvals
        candidate = self._candidate(idea_id)
        try:
            # WF08-03's assessment is released inside Module 07, just before the request's own assessment: Module 06
            # allows one live assessment per idea (R047), so releasing it afterwards left every request BLOCKED.
            result = approvals.ingest_candidate(candidate, supersedes=st["assessment_id"])
        except Exception as exc:  # noqa: BLE001 - Module 07 refused or failed: nobody was notified
            return {**base, "status": "failed", "reason": f"Module 07 could not open the approval request: "
                                                          f"{getattr(exc, 'message', None) or exc}"[:300]}
        approval_id = result.get("approval_id")
        if not approval_id:
            return {**base, "status": "failed", "reason": f"Module 07 did not open an approval request ({result.get('status')})."}
        # Normally already released by Module 07 (above). Kept for an idea Module 07 already had a request for.
        try:
            self.risk.set_assessment_state(st["assessment_id"], "cancelled", "wf08-04",
                                           "Superseded by the Module 07 approval request's own assessment.", execution_id)
        except Exception:  # noqa: BLE001 - already final or gone: nothing is reserved any more
            pass
        delivered = self._send(approvals)
        message = self._message(approvals, approval_id, candidate)
        deliveries = self._deliveries(approval_id)
        return {**base, "status": "notified", "approval_id": approval_id, "risk_decision": message.get("risk_decision"),
                "notification": message, "deliveries": deliveries, "delivered_now": delivered,
                "review_url": message.get("review_url"), "waiting_for": "Module 07 approval decision",
                "reason": f"Approval request opened; {sum(deliveries.get('in_app', {}).values())} in-app notification(s), "
                          f"external channels: {_external(deliveries)}.",
                "duration_seconds": round(time.monotonic() - started, 2)}

    def _send(self, approvals: Any) -> dict[str, int]:
        """Deliver the email and Telegram notifications that are due (Module 07's own delivery step)."""
        try:
            if self._deliver is not None:
                return self._deliver(approvals)
            from aurum.approvals.worker import Worker
            return Worker(approvals, self.svc.settings).deliver_notifications()
        except Exception as exc:  # noqa: BLE001 - delivery problems never undo the request; they are reported
            return {"error": f"{type(exc).__name__}: {exc}"[:200]}        # type: ignore[dict-item]

    def _deliveries(self, approval_id: str) -> dict[str, dict[str, int]]:
        """Delivery status per channel for this request's notifications."""
        from aurum.approvals.db_models import Notification as N
        from aurum.approvals.db_models import NotificationDelivery as D
        from aurum.database.connection import session_scope
        out: dict[str, dict[str, int]] = {}
        with session_scope(self.engine) as s:
            for channel, status in s.execute(select(D.channel, D.status).join(N, N.id == D.notification_id)
                                             .where(N.approval_id == approval_id)).all():
                out.setdefault(channel, {})
                out[channel][status] = out[channel].get(status, 0) + 1
        return out

    def _message(self, approvals: Any, approval_id: str, candidate: dict[str, Any]) -> dict[str, Any]:
        """Everything a reviewer needs to see, taken from Module 07's request and Module 05's evidence."""
        from aurum.approvals.db_models import ApprovalRequest
        with approvals.repo.read() as s:
            req = approvals.request_view(s.get(ApprovalRequest, approval_id))
        evidence = approvals.ideas.evidence(candidate["candidate_uid"]) if approvals.ideas is not None else []
        supporting = [e["sentence"] for e in evidence if e.get("max_points") and e["points"] >= 0.5 * e["max_points"]]
        conflicting = [e["sentence"] for e in evidence if e.get("max_points") and e["points"] < 0.25 * e["max_points"]]
        base = self._dashboard()
        return {"approval_id": approval_id, "symbol": req["symbol"], "direction": req["direction"], "strategy": req["strategy"],
                "timeframe": req["timeframe"], "entry_zone": [candidate.get("entry_low"), candidate.get("entry_high")],
                "entry": req["entry"], "stop_loss": req["stop_loss"], "take_profit": req["take_profit"],
                "risk_reward": req.get("risk_reward") or candidate.get("risk_reward"),
                "evidence_score": candidate.get("confidence"), "evidence_grade": candidate.get("grade"),
                "supporting_reasons": supporting[:8], "conflicting_evidence": conflicting[:8],
                "risk_assessment": {"decision": req["risk_decision"], "assessment_id": req.get("risk_assessment_id"),
                                    "risk_pct": req.get("risk_pct"), "position_size": req.get("position_size"),
                                    "warnings": req.get("risk_warnings") or []},
                "risk_decision": req["risk_decision"], "expires_at": _iso(req["expires_at"]),
                "review_url": f"{base}/approvals/{approval_id}" if base else None,
                "note": "Approve or reject only in the signed-in AURUM dashboard. This message cannot approve anything."}


def _external(deliveries: dict[str, dict[str, int]]) -> str:
    parts = [f"{ch} {', '.join(f'{k} {v}' for k, v in sorted(st.items()))}" for ch, st in sorted(deliveries.items()) if ch != "in_app"]
    return "; ".join(parts) or "none configured"


# ---- installation (used by `python -m aurum.cli orchestration setup-wf08-04`) ----------------------------------------------
def install(svc: Any, actor: Any, n8n_workflow_id: str, *, activate: bool = True, subscribe: bool = True) -> dict[str, Any]:
    """Registers WF08-04 (it has no reserved placeholder: the Module 08 core reserved ``wf08-04-execution-handoff`` for the
    later execution hand-off), links it to the n8n workflow, adds version 1.0.0, validates and activates it, and
    subscribes it to Module 06's pass events. Safe to re-run."""
    from aurum.orchestration.common import OrchError
    steps: list[str] = []
    svc.ensure_ready()
    try:
        w = svc.get_workflow(WORKFLOW_ID)
    except OrchError:
        svc.register_workflow(actor, {"workflow_id": WORKFLOW_ID, "name": WORKFLOW_NAME, "category": "notification",
                                      "trigger_types": ["event", "manual", "admin"], "owner": "module_08",
                                      "description": "Ask a person to decide: hand ideas Module 06 passed to Module 07, "
                                                     "which opens the approval request and notifies the reviewers."})
        steps.append("registered")
        w = svc.get_workflow(WORKFLOW_ID)
    if w["state"] in ("ACTIVE", "DEGRADED") and (w.get("n8n_workflow_id") != n8n_workflow_id
                                                  or w.get("webhook_path") != WEBHOOK_PATH):
        svc.set_workflow_state(actor, WORKFLOW_ID, "PAUSED", "re-linking WF08-04 to n8n")
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
            "description": "WF08-04 Human Approval Notification: opens the Module 07 approval request for each idea Module 06 "
                           "passed and reports the notifications sent."})
        if isinstance(version.get("version"), dict):        # tolerate a wrapped response
            version = version["version"]
        steps.append(f"added version {VERSION}")
    if version["status"] == "DRAFT":
        version = svc.validate_version(actor, WORKFLOW_ID, version["version_id"])["version"]
        steps.append(f"validated ({version['status']})")
    if activate:
        if version["status"] != "ACTIVE":
            svc.activate_version(actor, WORKFLOW_ID, version["version_id"], "WF08-04 installation")
            steps.append(f"activated version {VERSION}")
        if svc.get_workflow(WORKFLOW_ID)["state"] != "ACTIVE":
            svc.set_workflow_state(actor, WORKFLOW_ID, "ACTIVE", "WF08-04 installation")
            steps.append("workflow ACTIVE")
    subs: list[dict[str, Any]] = []
    if subscribe:
        have = {x.get("event_pattern"): x for x in svc.list_subscriptions()["items"] if x.get("workflow_id") == WORKFLOW_ID}
        for event in TRIGGER_EVENTS:
            if event in have:
                subs.append(have[event])
                steps.append(f"subscription to {event} already present")
            else:
                subs.append(svc.create_subscription(actor, {"name": f"WF08-04 on {event}", "event_pattern": event,
                                                            "target_kind": "workflow", "workflow_id": WORKFLOW_ID}))
                steps.append(f"subscribed to {event}")
    final = svc.get_workflow(WORKFLOW_ID)
    return {"workflow_id": WORKFLOW_ID, "state": final["state"], "version": VERSION, "validation": version.get("validation"),
            "subscriptions": subs, "steps": steps}
