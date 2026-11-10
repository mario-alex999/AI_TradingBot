"""Generic workflow trigger, dispatch, callback processing, time-outs, retries and cancellation.

Trigger (one transaction): authorise -> check the registration (environment, state, active version, trigger type) ->
validate the input against the version's schema -> refuse credentials in the input -> category guards (an
execution-handoff workflow needs an approval Module 07 has APPROVED) -> idempotency -> record RECEIVED -> VALIDATING ->
QUEUED and publish ``orchestration.workflow.triggered``.

Dispatch (worker, or right after a trigger): claim QUEUED/RETRY_PENDING rows (QUEUED -> DISPATCHED, attempt + 1) within the
concurrency limit, call n8n outside the transaction, then record the outcome:
- accepted  -> WAITING_CALLBACK (until a callback or the deadline);
- transient -> RETRY_PENDING with back-off, or DEAD_LETTERED when the policy is exhausted;
- unknown   -> stays DISPATCHED until the deadline; never assumed "not sent". Retried automatically only when the workflow
               deduplicates by execution id (``idempotent_dispatch``);
- permanent -> FAILED -> DEAD_LETTERED (an operator must fix the cause).

Callbacks are checked for identity (execution, workflow, version, attempt, environment), deduplicated by callback id, and
applied only if the state machine allows the move: a late "started" can never overwrite SUCCEEDED.
"""
from __future__ import annotations

import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError

from aurum.auth import roles as perm
from aurum.orchestration import CONTRACT_VERSION
from aurum.orchestration.catalogue import find_sensitive
from aurum.orchestration.common import SYSTEM, Actor, OrchError, pick
from aurum.orchestration.db_models import (OrchDeadLetter, OrchExecution, OrchExecutionAttempt, OrchExecutionCallback,
                                           OrchWorkflow, OrchWorkflowVersion)
from aurum.orchestration.engines import (ENGINE_FAILED, ENGINE_RUNNING, ENGINE_SUCCESS, DispatchResult, EngineError,
                                         EngineNotConfigured)
from aurum.orchestration.repository import stable_hash, utc
from aurum.orchestration.retry import PERMANENT, TRANSIENT, UNKNOWN, kind_of
from aurum.orchestration.schemas import validate as validate_schema
from aurum.orchestration.security import SIGNATURE_HEADER, TIMESTAMP_HEADER, SignatureError, secret_value, verify
from aurum.orchestration.states import (CANCELLABLE, DISPATCHABLE, EXECUTION_FINAL, EXECUTION_IN_FLIGHT, IllegalTransition,
                                        check_execution)

APPROVED_FOR_HANDOFF = ("APPROVED", "EXECUTION_PENDING")
CALLBACK_STATUS = {"started": "RUNNING", "progress": "RUNNING", "completed": "SUCCEEDED", "partial": "SUCCEEDED",
                   "failed": "FAILED", "timed_out": "TIMED_OUT"}
INTERNAL_TRIGGERS = ("event", "schedule")
MAX_DEADLINE_EXTENSIONS = 3


class ExecutionMixin:
    # ---- helpers ------------------------------------------------------------------------------------------------------------
    def _move(self, s: Any, e: OrchExecution, target: str, *, by: str = "system", reason: str | None = None) -> None:
        check_execution(e.status, target)
        now = self.now()
        tl = list(e.timeline or [])
        tl.append({"at": now.isoformat(), "from": e.status, "to": target, "by": by[:80], "reason": (reason or "")[:300] or None,
                   "attempt": e.attempt})
        e.timeline = tl[-200:]
        e.status, e.status_changed_at = target, now
        e.version += 1
        if target == "RUNNING" and e.started_at is None:
            e.started_at = now
        if target in ("SUCCEEDED", "FAILED", "CANCELLED", "DEAD_LETTERED", "TIMED_OUT"):
            e.finished_at = now
            if e.started_at is None:
                e.started_at = utc(e.created_at)
        if target in ("RETRY_PENDING", "DISPATCHED"):
            e.finished_at = None
        s.flush()

    def _exec(self, s: Any, execution_id: str) -> OrchExecution:
        e = s.get(OrchExecution, execution_id)
        if e is None:
            raise OrchError(404, "EXECUTION_NOT_FOUND", "No such execution.")
        return e

    def _emit(self, s: Any, kind: str, e: OrchExecution, **extra: Any) -> None:
        payload = {"execution_id": e.execution_id, "workflow_id": e.workflow_id, "status": e.status, "attempt": e.attempt,
                   "workflow_version": e.version_label, **{k: v for k, v in extra.items() if v is not None}}
        if e.n8n_execution_id:
            payload["n8n_execution_id"] = e.n8n_execution_id
        if e.error_category and kind in ("failed", "dead_lettered", "timed_out", "retrying"):
            payload["error_category"] = e.error_category
        self.publish_internal(s, f"orchestration.workflow.{kind}", payload, correlation_id=e.correlation_id,
                              idempotency_key=f"m08:{kind}:{e.execution_id}:{e.attempt}:{e.version}",
                              causation_id=e.execution_id)

    def _dead_letter(self, s: Any, e: OrchExecution, category: str, reason: str) -> None:
        if e.status != "FAILED":
            self._move(s, e, "FAILED", reason=reason)
        self._move(s, e, "DEAD_LETTERED", reason=reason)
        e.error_category, e.error_message, e.next_attempt_at = category, reason[:2000], None
        s.add(OrchDeadLetter(id=str(uuid.uuid4()), kind="execution", execution_id=e.execution_id, workflow_id=e.workflow_id,
                             error_category=category, reason=reason[:2000], delivery_count=e.attempt,
                             last_attempt_at=self.now(), failed_at=self.now(), correlation_id=e.correlation_id,
                             recovery_status="open"))
        self.count("executions_dead_lettered")
        self._emit(s, "dead_lettered", e)

    def _retry_or_dead(self, s: Any, e: OrchExecution, w: OrchWorkflow, category: str, message: str,
                       retry_after: float | None = None) -> str:
        policy = self.policy_for(w)
        first = s.execute(select(func.min(OrchExecutionAttempt.started_at)).where(
            OrchExecutionAttempt.execution_id == e.execution_id)).scalar()
        now = self.now()
        e.error_category, e.error_message = category, message[:2000]
        if policy.may_retry(e.attempt, utc(first), now):
            self._move(s, e, "RETRY_PENDING", reason=message)
            e.retry_count += 1
            e.next_attempt_at = now + policy.delay(e.attempt, retry_after)
            e.deadline_at = None
            self.count("executions_retry_scheduled")
            self._emit(s, "retrying", e, error_category=category)
            return "RETRY_PENDING"
        self._dead_letter(s, e, category, f"Retries exhausted after {e.attempt} attempts: {message}")
        return "DEAD_LETTERED"

    # ---- trigger --------------------------------------------------------------------------------------------------------------
    def trigger(self, actor: Actor, body: dict[str, Any], *, internal: bool = False) -> tuple[dict[str, Any], bool]:
        """Returns (execution view, created). ``created`` is False when the idempotency key matched an earlier trigger."""
        self.ensure_ready()
        workflow_id = str(pick(body, "workflow_id") or "").strip()
        ttype = str(pick(body, "trigger_type") or ("manual" if actor.is_human else "api")).lower()
        if ttype in INTERNAL_TRIGGERS and not internal:
            raise OrchError(403, "TRIGGER_TYPE_NOT_ALLOWED", f"'{ttype}' triggers come only from Module 08 itself.")
        if ttype == "manual":
            self.perm_check(actor, perm.ORCH_OPERATE, human=True)
        elif ttype == "admin":
            self.perm_check(actor, perm.ORCH_MANAGE, human=True)
        elif ttype in ("api", "webhook"):
            self.perm_check(actor, perm.ORCH_TRIGGER)
        elif ttype not in INTERNAL_TRIGGERS:
            raise OrchError(422, "INVALID_TRIGGER", "trigger_type must be api, webhook, manual, admin, event or schedule.")
        inp = pick(body, "input", {})
        if not isinstance(inp, dict):
            raise OrchError(422, "INVALID_INPUT", "input must be a JSON object.")
        leak = find_sensitive(inp, "input")
        if leak:
            raise OrchError(422, "SENSITIVE_FIELD", f"Workflow input may not carry credentials ({leak}).", {"field": leak})
        if len(json.dumps(inp, default=str)) > 256_000:
            raise OrchError(413, "INPUT_TOO_LARGE", "Workflow input is limited to 256 KB; pass references instead.")
        env = str(pick(body, "environment") or self.env).lower()
        if env != self.env:
            self._refused(actor, "trigger.refused", workflow_id, "CROSS_ENVIRONMENT")
            raise OrchError(409, "CROSS_ENVIRONMENT", f"This is the {self.env} environment; a {env} trigger is refused.")
        key = pick(body, "idempotency_key")
        if not key and actor.kind == "service":
            raise OrchError(422, "IDEMPOTENCY_KEY_REQUIRED", "Services must send an idempotency key with every trigger.")
        key = str(key or f"auto:{uuid.uuid4()}")[:160]
        requested_version = pick(body, "workflow_version")
        corr = str(pick(body, "correlation_id") or uuid.uuid4())[:64]
        related = {"symbol": pick(body, "symbol") or inp.get("symbol"),
                   "trade_idea_id": pick(body, "trade_idea_id") or inp.get("tradeIdeaId") or inp.get("idea_id"),
                   "approval_id": pick(body, "approval_id") or inp.get("approvalId") or inp.get("approval_id")}
        input_hash = stable_hash(inp)
        now = self.now()
        # Category guard needs a read of Module 07; do it before taking the write lock.
        with self.repo.read() as s:
            w0 = self._wf(s, workflow_id) if workflow_id else None
        if w0 is None:
            raise OrchError(422, "INVALID_TRIGGER", "workflow_id is required.")
        if w0.category == "execution_handoff":
            self._approval_guard(actor, workflow_id, related.get("approval_id"))
        try:
            with self.repo.write() as s:
                w = self._wf(s, workflow_id)
                existing = s.execute(select(OrchExecution).where(OrchExecution.workflow_id == workflow_id,
                                                                 OrchExecution.idempotency_key == key)).scalar_one_or_none()
                if existing is not None:
                    if existing.input_hash != input_hash:
                        raise OrchError(409, "IDEMPOTENCY_CONFLICT", "This idempotency key was already used with different "
                                        "input.", {"execution_id": existing.execution_id})
                    self.count("triggers_duplicate")
                    return self.exec_view(existing, full=True), False
                problems = self._trigger_problems(s, w, ttype, requested_version)
                if problems:
                    code, msg = problems
                    raise _Refused(OrchError(409, code, msg))
                v = s.get(OrchWorkflowVersion, w.active_version_id)
                bad = validate_schema(inp, v.input_schema)
                if bad:
                    raise _Refused(OrchError(422, "INPUT_INVALID", "The input does not match the workflow's input schema.",
                                             bad[:20]))
                e = OrchExecution(execution_id=str(uuid.uuid4()), workflow_id=workflow_id, version_id=v.version_id,
                                  version_label=v.version_label, environment=self.env, status="RECEIVED", version=1,
                                  trigger_type=ttype, trigger_source=actor.name[:80],
                                  requesting_module=(str(pick(body, "requesting_module") or "")[:16] or None),
                                  idempotency_key=key, correlation_id=corr,
                                  causation_id=(str(pick(body, "causation_id"))[:64] if pick(body, "causation_id") else None),
                                  input=inp, input_hash=input_hash, symbol=(str(related["symbol"])[:32] if related["symbol"] else None),
                                  trade_idea_id=(str(related["trade_idea_id"])[:64] if related["trade_idea_id"] else None),
                                  approval_id=(str(related["approval_id"])[:36] if related["approval_id"] else None),
                                  attempt=0, retry_count=0, created_at=now, status_changed_at=now,
                                  warnings=(["Workflow is DEGRADED"] if w.state == "DEGRADED" else None),
                                  timeline=[{"at": now.isoformat(), "from": None, "to": "RECEIVED", "by": actor.name[:80],
                                             "reason": None, "attempt": 0}])
                s.add(e)
                s.flush()
                self._move(s, e, "VALIDATING", by=actor.name)
                self._move(s, e, "QUEUED", by=actor.name, reason="input valid")
                e.next_attempt_at = now
                w.last_execution_at = now
                self._emit(s, "triggered", e, trigger_type=ttype)
                if ttype in ("manual", "admin"):
                    self.repo.audit(s, actor=actor.name, actor_kind=actor.kind, action=f"execution.{ttype}_trigger",
                                    entity_type="execution", entity_id=e.execution_id, at=now, correlation_id=corr,
                                    details={"workflow_id": workflow_id})
                self.count("triggers_created")
                return self.exec_view(e, full=True), True
        except _Refused as r:
            # Audited in its own transaction: the trigger's transaction is rolled back by the raise.
            self._refused(actor, "trigger.refused", workflow_id, r.error.code)
            raise r.error from None
        except IntegrityError:
            # Another process inserted the same (workflow, key) between our check and insert: return that one.
            with self.repo.read() as s:
                existing = s.execute(select(OrchExecution).where(OrchExecution.workflow_id == workflow_id,
                                                                 OrchExecution.idempotency_key == key)).scalar_one()
                return self.exec_view(existing, full=True), False

    def _trigger_problems(self, s: Any, w: OrchWorkflow, ttype: str, requested_version: Any) -> tuple[str, str] | None:
        if w.environment != self.env:
            return "CROSS_ENVIRONMENT", f"Workflow '{w.workflow_id}' belongs to {w.environment}."
        if w.placeholder:
            return "PLACEHOLDER_NOT_IMPLEMENTED", f"'{w.workflow_id}' is a reserved registration without an n8n workflow yet."
        if w.state not in {x.value for x in DISPATCHABLE}:
            return "WORKFLOW_NOT_ACTIVE", f"Workflow '{w.workflow_id}' is {w.state}; only ACTIVE workflows can be triggered."
        if ttype not in (w.trigger_types or []):
            return "TRIGGER_TYPE_NOT_ALLOWED", f"'{w.workflow_id}' does not accept {ttype} triggers."
        if not w.active_version_id:
            return "NO_ACTIVE_VERSION", "The workflow has no active version."
        v = s.get(OrchWorkflowVersion, w.active_version_id)
        if v is None or v.status != "ACTIVE":
            return "NO_ACTIVE_VERSION", "The workflow's active version is not ACTIVE."
        if requested_version and str(requested_version) != v.version_label:
            return "VERSION_MISMATCH", f"Requested version {requested_version}, active version is {v.version_label}."
        return None

    def _approval_guard(self, actor: Actor, workflow_id: str, approval_id: Any) -> None:
        """Execution-handoff workflows only ever run for a trade Module 07 has approved. Module 08 does not decide this; it
        reads Module 07's record and refuses otherwise."""
        if not approval_id:
            self._refused(actor, "trigger.refused", workflow_id, "APPROVAL_REQUIRED")
            raise OrchError(409, "APPROVAL_REQUIRED", "Execution-handoff workflows need the approvalId of a Module 07 approval.")
        status = self.approvals(str(approval_id))
        if status not in APPROVED_FOR_HANDOFF:
            self._refused(actor, "trigger.refused", workflow_id, "APPROVAL_NOT_APPROVED")
            raise OrchError(409, "APPROVAL_NOT_APPROVED", f"Module 07 shows this approval as {status or 'unknown'}; only "
                            "APPROVED or EXECUTION_PENDING approvals can be handed to execution.", {"approval_status": status})

    def _refused(self, actor: Actor, action: str, entity: str, code: str) -> None:
        with self.repo.write() as s:
            self._refused_in(s, actor, action, entity, code)

    def _refused_in(self, s: Any, actor: Actor, action: str, entity: str, code: str) -> None:
        self.count("triggers_refused")
        self.repo.audit(s, actor=actor.name, actor_kind=actor.kind, action=action, entity_type="workflow",
                        entity_id=entity or "-", at=self.now(), outcome="refused", details={"code": code})

    # ---- dispatch -------------------------------------------------------------------------------------------------------------
    def _request(self, e: OrchExecution, w: OrchWorkflow) -> dict[str, Any]:
        base = self.settings.orchestration_callback_base_url.rstrip("/")
        return {"contractVersion": CONTRACT_VERSION, "executionId": e.execution_id, "attempt": e.attempt,
                "workflowId": e.workflow_id, "workflowVersion": e.version_label, "versionId": e.version_id,
                "n8nWorkflowId": w.n8n_workflow_id, "triggerType": e.trigger_type, "triggerSource": e.trigger_source,
                "environment": e.environment, "correlationId": e.correlation_id, "causationId": e.causation_id,
                "idempotencyKey": e.idempotency_key, "requestedAt": utc(e.created_at).isoformat(),
                "requestingModule": e.requesting_module, "input": e.input,
                "related": {"symbol": e.symbol, "tradeIdeaId": e.trade_idea_id, "approvalId": e.approval_id},
                "callback": {"url": f"{base}/webhooks/n8n/callback", "eventsUrl": f"{base}/webhooks/n8n/events",
                             "deadlineAt": utc(e.deadline_at).isoformat() if e.deadline_at else None}}

    def dispatch_due(self, limit: int | None = None, only: str | None = None) -> dict[str, int]:
        """Claims due executions and sends them to n8n. Safe to run from several processes: a claim is a conditional
        status change under the write lock, so one execution is sent once per attempt."""
        self.ensure_ready()
        cfg = self.config()
        now = self.now()
        claimed: list[tuple[str, int, str, dict[str, Any]]] = []
        with self.repo.write() as s:
            # Concurrency limit: executions sent to n8n and not finished count against max_parallel (backpressure).
            in_flight = s.execute(select(func.count()).select_from(OrchExecution).where(
                OrchExecution.status.in_([x.value for x in EXECUTION_IN_FLIGHT]))).scalar_one()
            room = max(0, cfg.dispatch.max_parallel - int(in_flight))
            if room == 0:
                self.count("dispatch_backpressure")
                return {"claimed": 0, "backpressure": 1}
            q = (select(OrchExecution, OrchWorkflow).join(OrchWorkflow, OrchWorkflow.workflow_id == OrchExecution.workflow_id)
                 .where(OrchExecution.status.in_(["QUEUED", "RETRY_PENDING"]),
                        or_(OrchExecution.next_attempt_at.is_(None), OrchExecution.next_attempt_at <= now),
                        OrchWorkflow.state.in_([x.value for x in DISPATCHABLE]))
                 .order_by(OrchExecution.next_attempt_at, OrchExecution.created_at)
                 .limit(min(room, limit or cfg.dispatch.batch_size)))
            if only:
                q = q.where(OrchExecution.execution_id == only)
            for e, w in s.execute(q).all():
                if not w.webhook_path:
                    self._move(s, e, "DISPATCHED", reason="claim")
                    e.attempt += 1
                    self._dead_letter(s, e, "UNSUPPORTED_WORKFLOW", "The registration has no webhook path.")
                    continue
                self._move(s, e, "DISPATCHED", reason="claim")
                e.attempt += 1
                e.deadline_at = now + timedelta(seconds=w.timeout_seconds)
                e.next_attempt_at = None
                s.add(OrchExecutionAttempt(execution_id=e.execution_id, attempt=e.attempt, started_at=now,
                                           outcome="in_progress"))
                claimed.append((e.execution_id, e.attempt, w.webhook_path, self._request(e, w)))
        if not claimed:
            return {"claimed": 0}
        results: list[tuple[str, int, DispatchResult]] = []
        with ThreadPoolExecutor(max_workers=min(len(claimed), cfg.dispatch.max_parallel)) as pool:
            futs = [(eid, att, pool.submit(self._safe_dispatch, path, req, now)) for eid, att, path, req in claimed]
            for eid, att, f in futs:
                results.append((eid, att, f.result()))
        summary = {"claimed": len(claimed), "accepted": 0, "retrying": 0, "dead_lettered": 0, "unknown": 0}
        for eid, att, r in results:
            outcome = self._record_dispatch(eid, att, r)
            summary[outcome] = summary.get(outcome, 0) + 1
        return summary

    def _safe_dispatch(self, path: str, req: dict[str, Any], now: datetime) -> DispatchResult:
        try:
            return self.wf_engine.dispatch(path, req, now)
        except Exception as exc:  # noqa: BLE001 - an adapter bug must not lose the claim
            return DispatchResult(UNKNOWN, error_category="OUTCOME_UNKNOWN", error=f"adapter error {type(exc).__name__}")

    def _record_dispatch(self, execution_id: str, attempt: int, r: DispatchResult) -> str:
        now = self.now()
        with self.repo.write() as s:
            e = self._exec(s, execution_id)
            w = self._wf(s, e.workflow_id)
            a = s.execute(select(OrchExecutionAttempt).where(OrchExecutionAttempt.execution_id == execution_id,
                                                             OrchExecutionAttempt.attempt == attempt)).scalar_one()
            a.finished_at, a.http_status, a.latency_ms = now, r.http_status, r.latency_ms
            a.outcome = r.outcome
            a.error_category, a.error_message, a.response_meta = r.error_category, (r.error or None), r.response_meta or None
            self.count(f"dispatch_{r.outcome}")
            if r.engine_execution_id and not e.n8n_execution_id:
                e.n8n_execution_id = r.engine_execution_id[:64]
            if e.status != "DISPATCHED" or e.attempt != attempt:
                return "accepted" if r.outcome == "accepted" else "superseded"   # a callback or cancel got there first
            if r.outcome == "accepted":
                self._move(s, e, "WAITING_CALLBACK", reason="n8n accepted the trigger")
                return "accepted"
            if r.outcome == TRANSIENT:
                return "retrying" if self._retry_or_dead(s, e, w, r.error_category or "SERVER_ERROR", r.error or "",
                                                         r.retry_after) == "RETRY_PENDING" else "dead_lettered"
            if r.outcome == UNKNOWN:
                e.error_category, e.error_message = r.error_category, r.error
                if w.idempotent_dispatch:
                    return "retrying" if self._retry_or_dead(s, e, w, r.error_category or "OUTCOME_UNKNOWN",
                                                             f"{r.error} (receiver deduplicates by execution id)") == "RETRY_PENDING" else "dead_lettered"
                warnings = list(e.warnings or [])
                warnings.append(f"Attempt {attempt}: outcome unknown ({r.error}); waiting for a callback until the deadline.")
                e.warnings = warnings[-20:]
                return "unknown"
            self._dead_letter(s, e, r.error_category or "UNSUPPORTED_WORKFLOW", r.error or "Permanent dispatch failure.")
            return "dead_lettered"

    # ---- callbacks -------------------------------------------------------------------------------------------------------------
    def handle_callback(self, actor: Actor, raw: bytes, headers: dict[str, str]) -> tuple[int, dict[str, Any]]:
        """Processes an n8n execution callback. Returns (HTTP status, body)."""
        self.ensure_ready()
        now = self.now()
        cfg = self.config()
        h = {k.lower(): v for k, v in headers.items()}
        sig, ts = h.get(SIGNATURE_HEADER.lower()), h.get(TIMESTAMP_HEADER.lower())
        secrets = [secret_value(self.settings, "n8n_webhook_shared_secret"),
                   secret_value(self.settings, "n8n_webhook_shared_secret_previous")]
        signed = False
        if sig or cfg.callbacks.signature_required:
            try:
                verify(raw, ts, sig, [x for x in secrets if x], now, cfg.callbacks.max_skew_seconds)
                signed = True
            except SignatureError as exc:
                self._security_event(actor, "security.callback_rejected", exc.code)
                raise OrchError(401, exc.code, exc.message) from None
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except (UnicodeDecodeError, ValueError):
            raise OrchError(400, "INVALID_CALLBACK", "The callback body is not JSON.") from None
        if not isinstance(body, dict):
            raise OrchError(400, "INVALID_CALLBACK", "The callback body must be a JSON object.")
        cb_id, eid, wid = (str(pick(body, k) or "")[:64] for k in ("callback_id", "execution_id", "workflow_id"))
        status = str(pick(body, "status") or "").lower()
        if not cb_id or not eid or not wid or status not in CALLBACK_STATUS:
            raise OrchError(422, "INVALID_CALLBACK", "callbackId, executionId, workflowId and status "
                            f"({', '.join(CALLBACK_STATUS)}) are required.")
        env = str(pick(body, "environment") or "").lower()
        if env != self.env:
            self._security_event(actor, "security.callback_cross_environment", env or "missing")
            raise OrchError(409, "CROSS_ENVIRONMENT", f"This is the {self.env} environment; refused a '{env}' callback.")
        sent_at = None
        if pick(body, "sent_at"):
            try:
                sent_at = datetime.fromisoformat(str(pick(body, "sent_at")).replace("Z", "+00:00"))
            except ValueError:
                raise OrchError(422, "INVALID_CALLBACK", "sentAt must be ISO-8601.") from None
        if not signed:
            # Without a signature the body's own timestamp is the replay window (the callback id stops exact replays).
            if sent_at is None or sent_at.tzinfo is None or abs((now - sent_at).total_seconds()) > cfg.callbacks.max_skew_seconds:
                self._security_event(actor, "security.callback_rejected", "TIMESTAMP_EXPIRED")
                raise OrchError(401, "TIMESTAMP_EXPIRED", "sentAt is missing or outside the allowed window.")
        output, error = pick(body, "output"), pick(body, "error") or {}
        leak = find_sensitive(output or {}, "output") or find_sensitive(error, "error")
        if leak:
            raise OrchError(422, "SENSITIVE_FIELD", f"Callbacks may not carry credentials ({leak}).")
        if output is not None and len(json.dumps(output, default=str)) > cfg.callbacks.max_output_bytes:
            raise OrchError(413, "OUTPUT_TOO_LARGE", "Store large outputs elsewhere and pass a reference.")
        attempt = pick(body, "attempt")
        attempt = int(attempt) if isinstance(attempt, int) and not isinstance(attempt, bool) else None
        version_label = pick(body, "workflow_version")
        n8n_exec = pick(body, "n8n_execution_id")
        stored = {"status": status, "attempt": attempt, "workflow_version": version_label, "output": output,
                  "error": error or None, "n8n_execution_id": n8n_exec, "signed": signed}
        with self.repo.write() as s:
            dup = s.get(OrchExecutionCallback, cb_id)
            if dup is not None:
                self.count("callbacks_duplicate")
                return 200, {"duplicate": True, "callback_id": cb_id, "outcome": dup.outcome, "status": dup.new_status}

            def record(outcome: str, detail: str | None, prev: str | None = None, new: str | None = None) -> None:
                s.add(OrchExecutionCallback(callback_id=cb_id, execution_id=eid, workflow_id=wid, kind=status,
                                            attempt=attempt, received_at=now, sent_at=sent_at, outcome=outcome, detail=detail,
                                            previous_status=prev, new_status=new, payload=stored))
                s.flush()
                self.count(f"callbacks_{outcome}")

            e = s.get(OrchExecution, eid)
            if e is None:
                record("rejected", "UNKNOWN_EXECUTION")
                return 404, {"applied": False, "outcome": "rejected", "code": "UNKNOWN_EXECUTION"}
            if e.workflow_id != wid:
                record("rejected", "WORKFLOW_MISMATCH", e.status)
                self._security_event_in(s, actor, "security.callback_workflow_mismatch", eid)
                return 409, {"applied": False, "outcome": "rejected", "code": "WORKFLOW_MISMATCH"}
            if version_label and str(version_label) != e.version_label:
                record("rejected", "VERSION_MISMATCH", e.status)
                return 409, {"applied": False, "outcome": "rejected", "code": "VERSION_MISMATCH",
                             "expected": e.version_label}
            if attempt is not None and attempt > e.attempt:
                record("rejected", "UNKNOWN_ATTEMPT", e.status)
                return 409, {"applied": False, "outcome": "rejected", "code": "UNKNOWN_ATTEMPT"}
            if attempt is not None and attempt < e.attempt:
                record("stale", f"attempt {attempt} is older than the current attempt {e.attempt}", e.status, e.status)
                return 200, {"applied": False, "outcome": "stale", "status": e.status}
            if n8n_exec and not e.n8n_execution_id:
                e.n8n_execution_id = str(n8n_exec)[:64]
            target = CALLBACK_STATUS[status]
            prev = e.status
            if target == prev == "RUNNING":
                record("applied", "progress", prev, prev)
                return 200, {"applied": True, "outcome": "applied", "status": prev}
            try:
                check_execution(prev, target)
            except IllegalTransition:
                record("stale", f"{prev} cannot become {target}", prev, prev)
                return 200, {"applied": False, "outcome": "stale", "status": prev}
            w = self._wf(s, e.workflow_id)
            by = f"n8n:{actor.name}"
            if target == "RUNNING":
                self._move(s, e, "RUNNING", by=by, reason="started")
                e.deadline_at = now + timedelta(seconds=w.timeout_seconds)
                self._emit(s, "started", e)
            elif target == "SUCCEEDED":
                self._move(s, e, "SUCCEEDED", by=by, reason=status)
                e.output, e.deadline_at, e.error_category, e.error_message = output, None, None, None
                if status == "partial":
                    e.warnings = [*(e.warnings or []), "n8n reported partial completion"][-20:]
                v = s.get(OrchWorkflowVersion, e.version_id)
                problems = validate_schema(output if output is not None else {}, v.output_schema) if v else []
                if problems:
                    e.warnings = [*(e.warnings or []), f"Output does not match the output schema: {problems[:5]}"][-20:]
                self._emit(s, "completed", e, status="partial" if status == "partial" else "succeeded")
            elif target == "TIMED_OUT":
                self._move(s, e, "TIMED_OUT", by=by, reason="n8n reported a time-out")
                e.error_category, e.error_message = "TIMED_OUT", str(error.get("message") or "n8n timed out")[:2000]
                self._emit(s, "timed_out", e)
            else:
                category = str(error.get("category") or "WORKFLOW_ERROR").upper()[:32]
                retryable = error.get("retryable") if isinstance(error.get("retryable"), bool) else None
                msg = str(error.get("message") or "n8n reported a failure")[:2000]
                self._move(s, e, "FAILED", by=by, reason=msg)
                e.error_category, e.error_message, e.deadline_at = category, msg, None
                self._emit(s, "failed", e, error_category=category)
                kind = kind_of(category, retryable)
                policy = self.policy_for(w)
                if kind == TRANSIENT and (category != "WORKFLOW_ERROR" or policy.retry_on_workflow_failure):
                    self._retry_or_dead(s, e, w, category, msg)
                elif kind == PERMANENT and category not in _business():
                    self._dead_letter(s, e, category, msg)
            record("applied", None, prev, e.status)
            return 200, {"applied": True, "outcome": "applied", "status": e.status, "execution_id": eid}

    def _security_event(self, actor: Actor, action: str, code: str) -> None:
        with self.repo.write() as s:
            self._security_event_in(s, actor, action, code)

    def _security_event_in(self, s: Any, actor: Actor, action: str, code: str) -> None:
        self.count("security_events")
        self.repo.audit(s, actor=actor.name, actor_kind=actor.kind, action=action, entity_type="security",
                        entity_id=code[:64], at=self.now(), outcome="refused", details={"code": code})

    # ---- time-outs and reconciliation ---------------------------------------------------------------------------------------
    def sweep_timeouts(self, limit: int = 100) -> dict[str, int]:
        """In-flight executions past their deadline. If n8n can be asked, its answer wins (success, failure, still
        running); otherwise the execution is TIMED_OUT - which records that we stopped waiting, not that it did not run."""
        self.ensure_ready()
        now = self.now()
        with self.repo.read() as s:
            rows = s.execute(select(OrchExecution.execution_id, OrchExecution.n8n_execution_id).where(
                OrchExecution.status.in_([x.value for x in EXECUTION_IN_FLIGHT]), OrchExecution.deadline_at.is_not(None),
                OrchExecution.deadline_at < now).limit(limit)).all()
        out = {"timed_out": 0, "reconciled": 0, "extended": 0}
        for eid, n8n_id in rows:
            engine_state = None
            if n8n_id and self.wf_engine.api_configured():
                try:
                    engine_state = self.wf_engine.get_execution(n8n_id)
                except (EngineError, EngineNotConfigured):
                    engine_state = None
            with self.repo.write() as s:
                e = self._exec(s, eid)
                if e.status not in {x.value for x in EXECUTION_IN_FLIGHT} or not e.deadline_at or utc(e.deadline_at) >= now:
                    continue
                w = self._wf(s, e.workflow_id)
                st = str((engine_state or {}).get("status") or "").lower()
                if engine_state and st in ENGINE_SUCCESS:
                    self._move(s, e, "SUCCEEDED", reason="reconciled: n8n reports success (no callback received)")
                    e.warnings = [*(e.warnings or []), "Completed per n8n execution status; no callback received."][-20:]
                    e.deadline_at = None
                    self._emit(s, "completed", e, status="reconciled")
                    out["reconciled"] += 1
                elif engine_state and st in ENGINE_FAILED:
                    self._move(s, e, "FAILED", reason=f"reconciled: n8n reports {st}")
                    e.error_category, e.error_message, e.deadline_at = "WORKFLOW_ERROR", f"n8n execution {st}", None
                    self._emit(s, "failed", e, error_category="WORKFLOW_ERROR")
                    if self.policy_for(w).retry_on_workflow_failure:
                        self._retry_or_dead(s, e, w, "WORKFLOW_ERROR", f"n8n execution {st}")
                    out["reconciled"] += 1
                elif engine_state and st in ENGINE_RUNNING and sum(
                        1 for t in (e.timeline or []) if t.get("reason") == "deadline extended") < MAX_DEADLINE_EXTENSIONS:
                    e.deadline_at = now + timedelta(seconds=w.timeout_seconds)
                    e.timeline = [*(e.timeline or []), {"at": now.isoformat(), "from": e.status, "to": e.status,
                                                        "by": "system", "reason": "deadline extended", "attempt": e.attempt}]
                    out["extended"] += 1
                else:
                    self._move(s, e, "TIMED_OUT", reason="no callback before the deadline")
                    e.error_category = e.error_category if e.error_category == "OUTCOME_UNKNOWN" else "TIMED_OUT"
                    e.error_message = "No callback before the deadline. The workflow may still have run."
                    self._emit(s, "timed_out", e)
                    self.count("executions_timed_out")
                    out["timed_out"] += 1
                    policy = self.policy_for(w)
                    if w.idempotent_dispatch and policy.retry_on_timeout:
                        self._retry_or_dead(s, e, w, "TIMED_OUT", "timed out; receiver deduplicates by execution id")
        return out

    # ---- operator actions ----------------------------------------------------------------------------------------------------
    def retry_execution(self, actor: Actor, execution_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Manual retry from FAILED, TIMED_OUT or DEAD_LETTERED. Before retrying: the workflow must be dispatchable; n8n is
        asked (when possible) whether the execution actually finished; an unknown outcome needs explicit confirmation unless
        the workflow deduplicates; an execution-handoff retry re-checks the Module 07 approval."""
        self.ensure_ready()
        self.perm_check(actor, perm.ORCH_OPERATE, human=True)
        reason = str(pick(body, "reason") or "").strip()
        if not reason:
            raise OrchError(422, "REASON_REQUIRED", "Give a reason for the retry.")
        confirm = bool(pick(body, "confirm_unknown_outcome", False))
        with self.repo.read() as s:
            e = self._exec(s, execution_id)
            w = self._wf(s, e.workflow_id)
            status, n8n_id, cat, approval = e.status, e.n8n_execution_id, e.error_category, e.approval_id
            idem, category = w.idempotent_dispatch, w.category
            problems = self._trigger_problems(s, w, e.trigger_type, None)
        if status not in ("FAILED", "TIMED_OUT", "DEAD_LETTERED"):
            raise OrchError(409, "NOT_RETRYABLE", f"A {status} execution cannot be retried.")
        if problems and problems[0] not in ("TRIGGER_TYPE_NOT_ALLOWED",):
            raise OrchError(409, problems[0], problems[1])
        if cat in _business():
            raise OrchError(409, "BUSINESS_RULE_FAILURE", f"The workflow failed for a business reason ({cat}); retrying "
                            "cannot change that. Start a new request through the owning module.")
        engine_note = None
        if n8n_id and self.wf_engine.api_configured():
            try:
                st = str(self.wf_engine.get_execution(n8n_id).get("status") or "").lower()
                engine_note = st
                if st in ENGINE_SUCCESS or st in ("running", "waiting", "new"):
                    raise OrchError(409, "ALREADY_RUNNING_OR_DONE_IN_ENGINE", f"n8n reports this execution as '{st}'. "
                                    "Retrying could run it twice.", {"engine_status": st})
            except (EngineError, EngineNotConfigured):
                engine_note = "unavailable"
        unknown = status == "TIMED_OUT" or cat in ("OUTCOME_UNKNOWN", "TIMED_OUT")
        if unknown and not idem and not confirm:
            raise OrchError(409, "UNKNOWN_OUTCOME_CONFIRMATION_REQUIRED", "The previous attempt may have run. Check n8n, "
                            "then confirm with confirmUnknownOutcome=true to send it again.", {"engine_status": engine_note})
        if category == "execution_handoff":
            self._approval_guard(actor, e.workflow_id, approval)
        now = self.now()
        with self.repo.write() as s:
            e = self._exec(s, execution_id)
            if e.status != status:
                raise OrchError(409, "STALE_VERSION", "The execution changed meanwhile; reload.")
            self._move(s, e, "RETRY_PENDING", by=actor.name, reason=f"manual retry: {reason}")
            e.retry_count += 1
            e.next_attempt_at, e.deadline_at = now, None
            for dl in s.execute(select(OrchDeadLetter).where(OrchDeadLetter.execution_id == execution_id,
                                                             OrchDeadLetter.recovery_status == "open")).scalars():
                dl.recovery_status, dl.resolved_by, dl.resolved_at, dl.resolution_note = "retried", actor.name, now, reason[:500]
            self._emit(s, "retrying", e, error_category=e.error_category)
            self.repo.audit(s, actor=actor.name, actor_kind=actor.kind, action="execution.manual_retry", entity_type="execution",
                            entity_id=execution_id, at=now, correlation_id=e.correlation_id,
                            details={"from": status, "reason": reason[:500], "confirmed_unknown_outcome": confirm,
                                     "engine_status": engine_note})
            self.count("manual_retries")
        self.dispatch_due(only=execution_id)
        return self.get_execution(execution_id)

    def cancel_execution(self, actor: Actor, execution_id: str, body: dict[str, Any]) -> dict[str, Any]:
        self.ensure_ready()
        self.perm_check(actor, perm.ORCH_OPERATE, human=True)
        reason = str(pick(body, "reason") or "").strip()
        if not reason:
            raise OrchError(422, "REASON_REQUIRED", "Give a reason for the cancellation.")
        with self.repo.read() as s:
            e = self._exec(s, execution_id)
            status, n8n_id = e.status, e.n8n_execution_id
        if status not in {x.value for x in CANCELLABLE}:
            raise OrchError(409, "NOT_CANCELLABLE", f"A {status} execution cannot be cancelled.")
        engine = {"stop_requested": False}
        if status in {x.value for x in EXECUTION_IN_FLIGHT}:
            if n8n_id and self.wf_engine.api_configured():
                try:
                    self.wf_engine.stop_execution(n8n_id)
                    engine = {"stop_requested": True}
                except (EngineError, EngineNotConfigured) as exc:
                    engine = {"stop_requested": False, "note": f"n8n stop failed: {exc}"}
            else:
                engine = {"stop_requested": False, "note": "n8n could not be asked to stop (no execution id or no API key); "
                          "the workflow may still finish in n8n. Its late callback will be recorded as stale."}
        now = self.now()
        with self.repo.write() as s:
            e = self._exec(s, execution_id)
            if e.status not in {x.value for x in CANCELLABLE}:
                raise OrchError(409, "NOT_CANCELLABLE", f"The execution is now {e.status}.")
            self._move(s, e, "CANCELLED", by=actor.name, reason=reason)
            e.next_attempt_at = e.deadline_at = None
            self._emit(s, "cancelled", e)
            self.repo.audit(s, actor=actor.name, actor_kind=actor.kind, action="execution.cancelled", entity_type="execution",
                            entity_id=execution_id, at=now, correlation_id=e.correlation_id,
                            details={"from": status, "reason": reason[:500], "engine": engine})
        return {**self.get_execution(execution_id), "engine": engine}

    # ---- reads -------------------------------------------------------------------------------------------------------------------
    def list_executions(self, *, workflow_id: str | None = None, status: list[str] | None = None, symbol: str | None = None,
                        correlation_id: str | None = None, trade_idea_id: str | None = None, approval_id: str | None = None,
                        execution_id: str | None = None, trigger_type: str | None = None, since: datetime | None = None,
                        until: datetime | None = None, page: int = 1, page_size: int = 50,
                        sort: str = "-created_at") -> dict[str, Any]:
        self.ensure_ready()
        conds = []
        for col, val in ((OrchExecution.workflow_id, workflow_id), (OrchExecution.symbol, symbol),
                         (OrchExecution.correlation_id, correlation_id), (OrchExecution.trade_idea_id, trade_idea_id),
                         (OrchExecution.approval_id, approval_id), (OrchExecution.trigger_type, trigger_type)):
            if val:
                conds.append(col == val)
        if execution_id:
            conds.append(or_(OrchExecution.execution_id == execution_id, OrchExecution.n8n_execution_id == execution_id))
        if status:
            conds.append(OrchExecution.status.in_([x.upper() for x in status]))
        if since:
            conds.append(OrchExecution.created_at >= since)
        if until:
            conds.append(OrchExecution.created_at <= until)
        col = {"created_at": OrchExecution.created_at, "status": OrchExecution.status,
               "status_changed_at": OrchExecution.status_changed_at}.get(sort.lstrip("-"), OrchExecution.created_at)
        order = col.desc() if sort.startswith("-") else col.asc()
        with self.repo.read() as s:
            where = and_(*conds) if conds else True
            total = s.execute(select(func.count()).select_from(OrchExecution).where(where)).scalar_one()
            rows = s.execute(select(OrchExecution).where(where).order_by(order).offset((page - 1) * page_size)
                             .limit(page_size)).scalars().all()
            return {"items": [self.exec_view(e) for e in rows], "total": int(total), "page": page, "page_size": page_size}

    def get_execution(self, execution_id: str) -> dict[str, Any]:
        self.ensure_ready()
        with self.repo.read() as s:
            e = self._exec(s, execution_id)
            attempts = s.execute(select(OrchExecutionAttempt).where(OrchExecutionAttempt.execution_id == execution_id)
                                 .order_by(OrchExecutionAttempt.attempt)).scalars().all()
            callbacks = s.execute(select(OrchExecutionCallback).where(OrchExecutionCallback.execution_id == execution_id)
                                  .order_by(OrchExecutionCallback.received_at)).scalars().all()
            dls = s.execute(select(OrchDeadLetter).where(OrchDeadLetter.execution_id == execution_id)).scalars().all()
            return {**self.exec_view(e, full=True),
                    "attempts": [{"attempt": a.attempt, "started_at": utc(a.started_at), "finished_at": utc(a.finished_at),
                                  "outcome": a.outcome, "http_status": a.http_status, "latency_ms": a.latency_ms,
                                  "error_category": a.error_category, "error_message": a.error_message, "manual": a.manual}
                                 for a in attempts],
                    "callbacks": [{"callback_id": c.callback_id, "kind": c.kind, "attempt": c.attempt,
                                   "received_at": utc(c.received_at), "sent_at": utc(c.sent_at), "outcome": c.outcome,
                                   "detail": c.detail, "previous_status": c.previous_status, "new_status": c.new_status}
                                  for c in callbacks],
                    "dead_letters": [self.dl_view(d) for d in dls]}


class _Refused(Exception):
    def __init__(self, error: OrchError):
        super().__init__(error.code)
        self.error = error


def _business() -> frozenset[str]:
    from aurum.orchestration.retry import BUSINESS_CATEGORIES
    return BUSINESS_CATEGORIES
