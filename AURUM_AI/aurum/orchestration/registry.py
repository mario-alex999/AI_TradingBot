"""Workflow registry and version management.

- A registration describes a workflow AURUM may trigger: n8n workflow id, webhook path, category, trigger types, retry and
  time-out policy. The four WF08 entries are seeded as placeholders and cannot be activated until a real n8n workflow and
  a validated version exist.
- A version carries the input/output contract. Its content is immutable once created (PostgreSQL trigger as well);
  only its status moves: DRAFT -> VALIDATED -> PUBLISHED -> ACTIVE -> DEPRECATED -> RETIRED.
- Activating a version deprecates the previous active one; activating a DEPRECATED version is a rollback.
- Validation checks the schemas, the contract version and, when the n8n API key is configured, that the n8n workflow
  exists and has a Webhook node on the registered path. Without the key the version is marked validated *with* a warning
  that n8n was not checked; nothing claims a check that did not happen.
- AURUM states are AURUM's own. If the n8n API is configured, activating a registration can also activate the workflow in
  n8n (``syncEngine``); otherwise the response says that n8n must be activated in the n8n editor.
"""
from __future__ import annotations

import re
import uuid
from typing import Any

from sqlalchemy import func, select

from aurum.auth import roles as perm
from aurum.orchestration import CONTRACT_VERSION, SCHEMA_VERSION
from aurum.orchestration.catalogue import ENVIRONMENTS, find_sensitive
from aurum.orchestration.common import Actor, OrchError, pick
from aurum.orchestration.db_models import OrchExecution, OrchWorkflow, OrchWorkflowVersion
from aurum.orchestration.engines import EngineError, EngineNotConfigured, check_webhook_path
from aurum.orchestration.repository import stable_hash, utc
from aurum.orchestration.retry import RetryPolicy
from aurum.orchestration.schemas import SchemaError, check_schema
from aurum.orchestration.states import (ACTIVATABLE_VERSIONS, IllegalTransition, check_version, check_workflow)

WORKFLOW_ID = re.compile(r"^[a-z0-9][a-z0-9\-]{2,63}$")
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
CATEGORIES = ("market_monitoring", "market_analysis", "risk_approval", "execution_handoff", "notification", "test", "other")
TRIGGER_TYPES = ("webhook", "event", "api", "schedule", "manual", "admin")


class RegistryMixin:
    # ---- views -------------------------------------------------------------------------------------------------------------
    @staticmethod
    def wf_view(w: OrchWorkflow, active: OrchWorkflowVersion | None = None) -> dict[str, Any]:
        return {"workflow_id": w.workflow_id, "name": w.name, "description": w.description, "purpose": w.purpose,
                "category": w.category, "owner": w.owner, "environment": w.environment, "engine": w.engine,
                "n8n_workflow_id": w.n8n_workflow_id, "webhook_path": w.webhook_path, "state": w.state,
                "trigger_types": w.trigger_types, "active_version_id": w.active_version_id,
                "active_version": active.version_label if active else None, "timeout_seconds": w.timeout_seconds,
                "retry_policy": w.retry_policy, "idempotent_dispatch": w.idempotent_dispatch, "placeholder": w.placeholder,
                "health_status": w.health_status, "health_detail": w.health_detail, "row_version": w.version,
                "created_by": w.created_by, "created_at": utc(w.created_at), "updated_at": utc(w.updated_at),
                "last_execution_at": utc(w.last_execution_at)}

    @staticmethod
    def version_view(v: OrchWorkflowVersion) -> dict[str, Any]:
        return {"version_id": v.version_id, "workflow_id": v.workflow_id, "version": v.version_label,
                "description": v.description, "status": v.status, "input_schema": v.input_schema,
                "output_schema": v.output_schema, "n8n_version_id": v.n8n_version_id, "contract_version": v.contract_version,
                "compatibility": v.compatibility, "definition_hash": v.definition_hash, "validation": v.validation,
                "created_by": v.created_by, "created_at": utc(v.created_at), "validated_at": utc(v.validated_at),
                "activated_at": utc(v.activated_at), "deactivated_at": utc(v.deactivated_at)}

    def _wf(self, s: Any, workflow_id: str) -> OrchWorkflow:
        w = s.get(OrchWorkflow, workflow_id)
        if w is None:
            raise OrchError(404, "WORKFLOW_NOT_FOUND", f"No workflow '{workflow_id}' is registered.")
        return w

    def _version(self, s: Any, workflow_id: str, version_id: str) -> OrchWorkflowVersion:
        v = s.get(OrchWorkflowVersion, version_id)
        if v is None or v.workflow_id != workflow_id:
            raise OrchError(404, "VERSION_NOT_FOUND", "No such version for this workflow.")
        return v

    # ---- reads ----------------------------------------------------------------------------------------------------------------
    def list_workflows(self, *, state: str | None = None, category: str | None = None, q: str | None = None) -> dict[str, Any]:
        self.ensure_ready()
        with self.repo.read() as s:
            query = select(OrchWorkflow).order_by(OrchWorkflow.workflow_id)
            if state:
                query = query.where(OrchWorkflow.state == state.upper())
            if category:
                query = query.where(OrchWorkflow.category == category)
            rows = s.execute(query).scalars().all()
            actives = {v.version_id: v for v in s.execute(select(OrchWorkflowVersion).where(
                OrchWorkflowVersion.version_id.in_([w.active_version_id for w in rows if w.active_version_id]))).scalars()}
            counts = dict(s.execute(select(OrchExecution.workflow_id, func.count()).group_by(OrchExecution.workflow_id)).all())
            items = []
            for w in rows:
                if q and q.lower() not in f"{w.workflow_id} {w.name} {w.category}".lower():
                    continue
                items.append({**self.wf_view(w, actives.get(w.active_version_id or "")), "executions": int(counts.get(w.workflow_id, 0))})
        return {"items": items, "total": len(items), "categories": list(CATEGORIES), "trigger_types": list(TRIGGER_TYPES)}

    def get_workflow(self, workflow_id: str) -> dict[str, Any]:
        self.ensure_ready()
        with self.repo.read() as s:
            w = self._wf(s, workflow_id)
            versions = s.execute(select(OrchWorkflowVersion).where(OrchWorkflowVersion.workflow_id == workflow_id)
                                 .order_by(OrchWorkflowVersion.created_at.desc())).scalars().all()
            active = next((v for v in versions if v.version_id == w.active_version_id), None)
            stats = dict(s.execute(select(OrchExecution.status, func.count()).where(OrchExecution.workflow_id == workflow_id)
                                   .group_by(OrchExecution.status)).all())
            return {**self.wf_view(w, active), "versions": [self.version_view(v) for v in versions],
                    "execution_counts": {k: int(v) for k, v in stats.items()},
                    "effective_retry_policy": self.policy_for(w).as_dict()}

    def list_versions(self, workflow_id: str) -> dict[str, Any]:
        d = self.get_workflow(workflow_id)
        return {"workflow_id": workflow_id, "active_version_id": d["active_version_id"], "items": d["versions"]}

    def policy_for(self, w: OrchWorkflow) -> RetryPolicy:
        base = self.config().retry_policy()
        p = RetryPolicy.from_dict(w.retry_policy or {}, base)
        cap = int(getattr(self.settings, "n8n_retry_limit", p.max_attempts))
        return RetryPolicy.from_dict({"max_attempts": min(p.max_attempts, cap)}, p)

    # ---- registration ----------------------------------------------------------------------------------------------------
    def _clean_fields(self, body: dict[str, Any], *, creating: bool) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for name, limit in (("name", 120), ("description", 4000), ("purpose", 4000), ("owner", 80)):
            v = pick(body, name)
            if v is not None:
                v = str(v).strip()
                if not v and name in ("name", "owner"):
                    raise OrchError(422, "INVALID_WORKFLOW", f"{name} cannot be empty.")
                out[name] = v[:limit]
        cat = pick(body, "category")
        if cat is not None:
            if cat not in CATEGORIES:
                raise OrchError(422, "INVALID_WORKFLOW", f"category must be one of {list(CATEGORIES)}.")
            out["category"] = cat
        tt = pick(body, "trigger_types")
        if tt is not None:
            if not isinstance(tt, list) or not tt or any(t not in TRIGGER_TYPES for t in tt):
                raise OrchError(422, "INVALID_WORKFLOW", f"trigger_types must be a non-empty list of {list(TRIGGER_TYPES)}.")
            out["trigger_types"] = sorted(set(tt))
        n8n_id = pick(body, "n8n_workflow_id")
        if n8n_id is not None:
            n8n_id = str(n8n_id).strip()
            if n8n_id and not re.match(r"^[A-Za-z0-9_\-]{1,64}$", n8n_id):
                raise OrchError(422, "INVALID_WORKFLOW", "n8n_workflow_id must be the id shown in the n8n workflow URL.")
            out["n8n_workflow_id"] = n8n_id or None
        path = pick(body, "webhook_path")
        if path is not None:
            try:
                out["webhook_path"] = check_webhook_path(path) if str(path).strip() else None
            except ValueError as exc:
                raise OrchError(422, "INVALID_WORKFLOW", str(exc)) from None
        to = pick(body, "timeout_seconds")
        if to is not None:
            if not isinstance(to, int) or isinstance(to, bool) or not 10 <= to <= 86400:
                raise OrchError(422, "INVALID_WORKFLOW", "timeout_seconds must be between 10 and 86400.")
            out["timeout_seconds"] = to
        rp = pick(body, "retry_policy")
        if rp is not None:
            if not isinstance(rp, dict):
                raise OrchError(422, "INVALID_WORKFLOW", "retry_policy must be an object.")
            try:
                RetryPolicy.from_dict(rp)
            except (TypeError, ValueError) as exc:
                raise OrchError(422, "INVALID_WORKFLOW", f"retry_policy: {exc}") from None
            out["retry_policy"] = {k: v for k, v in rp.items() if k in RetryPolicy.__dataclass_fields__}
        idem = pick(body, "idempotent_dispatch")
        if idem is not None:
            out["idempotent_dispatch"] = bool(idem)
        if find_sensitive(body, "workflow"):
            raise OrchError(422, "SENSITIVE_FIELD", "Workflow registrations may not contain credentials.")
        return out

    def register_workflow(self, actor: Actor, body: dict[str, Any]) -> dict[str, Any]:
        self.ensure_ready()
        self.perm_check(actor, perm.ORCH_MANAGE, human=True)
        wid = str(pick(body, "workflow_id") or "").strip().lower()
        if not WORKFLOW_ID.match(wid):
            raise OrchError(422, "INVALID_WORKFLOW", "workflow_id: 3-64 lower-case letters, digits and hyphens.")
        env = str(pick(body, "environment") or self.env).lower()
        if env not in ENVIRONMENTS or env != self.env:
            raise OrchError(409, "CROSS_ENVIRONMENT", f"This is the {self.env} environment; register {env} workflows there.")
        fields = self._clean_fields(body, creating=True)
        for req in ("name", "category"):
            if req not in fields:
                raise OrchError(422, "INVALID_WORKFLOW", f"{req} is required.")
        now = self.now()
        with self.repo.write() as s:
            if s.get(OrchWorkflow, wid) is not None:
                raise OrchError(409, "WORKFLOW_EXISTS", f"'{wid}' is already registered.")
            w = OrchWorkflow(workflow_id=wid, environment=env, engine="n8n", state="REGISTERED", owner=actor.name,
                             trigger_types=["api", "manual"], timeout_seconds=self.config().timeouts.default_execution_seconds,
                             retry_policy={}, idempotent_dispatch=False, placeholder=False, created_by=actor.name,
                             created_at=now, updated_at=now, health_status="UNKNOWN", version=1)
            for k, v in fields.items():
                setattr(w, k, v)
            s.add(w)
            s.flush()
            self.repo.audit(s, actor=actor.name, actor_kind=actor.kind, action="workflow.registered", entity_type="workflow",
                            entity_id=wid, at=now, details={"fields": sorted(fields)})
            initial = pick(body, "initial_version")
            if isinstance(initial, dict):
                self._add_version(s, actor, w, initial, now)
        return self.get_workflow(wid)

    def update_workflow(self, actor: Actor, workflow_id: str, body: dict[str, Any]) -> dict[str, Any]:
        self.ensure_ready()
        self.perm_check(actor, perm.ORCH_MANAGE, human=True)
        fields = self._clean_fields(body, creating=False)
        expected = pick(body, "row_version")
        now = self.now()
        with self.repo.write() as s:
            w = self._wf(s, workflow_id)
            if w.state == "RETIRED":
                raise OrchError(409, "WORKFLOW_RETIRED", "A retired workflow cannot be changed.")
            if expected is not None and int(expected) != w.version:
                raise OrchError(409, "STALE_VERSION", "The workflow changed since you loaded it. Reload and try again.",
                                {"current": w.version})
            moving = {k for k in ("n8n_workflow_id", "webhook_path") if k in fields and fields[k] != getattr(w, k)}
            if moving and w.state in ("ACTIVE", "DEGRADED"):
                raise OrchError(409, "WORKFLOW_ACTIVE", "Pause or deactivate the workflow before changing its n8n target.")
            changed = {k: v for k, v in fields.items() if getattr(w, k) != v}
            for k, v in changed.items():
                setattr(w, k, v)
            if changed:
                w.version += 1
                w.updated_at = now
                self.repo.audit(s, actor=actor.name, actor_kind=actor.kind, action="workflow.updated", entity_type="workflow",
                                entity_id=workflow_id, at=now, details={"changed": sorted(changed)})
        return self.get_workflow(workflow_id)

    # ---- versions -------------------------------------------------------------------------------------------------------
    def _add_version(self, s: Any, actor: Actor, w: OrchWorkflow, body: dict[str, Any], now: Any) -> OrchWorkflowVersion:
        label = str(pick(body, "version") or "").strip()
        if not SEMVER.match(label):
            raise OrchError(422, "INVALID_VERSION", "version must be a semantic version such as 1.0.0.")
        if s.execute(select(OrchWorkflowVersion.version_id).where(OrchWorkflowVersion.workflow_id == w.workflow_id,
                                                                  OrchWorkflowVersion.version_label == label)).first():
            raise OrchError(409, "VERSION_EXISTS", f"Version {label} already exists; versions are never overwritten.")
        ins = pick(body, "input_schema") or {"type": "object"}
        outs = pick(body, "output_schema") or {"type": "object"}
        try:
            check_schema(ins)
            check_schema(outs)
        except SchemaError as exc:
            raise OrchError(422, "INVALID_SCHEMA", str(exc)) from None
        if ins.get("type") not in (None, "object"):
            raise OrchError(422, "INVALID_SCHEMA", "The input schema must describe an object.")
        contract = str(pick(body, "contract_version") or CONTRACT_VERSION)
        compat = pick(body, "compatibility") or {}
        if not isinstance(compat, dict):
            raise OrchError(422, "INVALID_VERSION", "compatibility must be an object.")
        compat = {"contract_version": contract, "event_schema": str(compat.get("event_schema") or compat.get("eventSchema")
                                                                       or SCHEMA_VERSION),
                  **{k: v for k, v in compat.items() if k not in ("event_schema", "eventSchema", "contract_version")}}
        n8n_v = pick(body, "n8n_version_id")
        v = OrchWorkflowVersion(version_id=str(uuid.uuid4()), workflow_id=w.workflow_id, version_label=label,
                                description=(str(pick(body, "description") or "")[:4000] or None), status="DRAFT",
                                input_schema=ins, output_schema=outs, n8n_version_id=str(n8n_v)[:64] if n8n_v else None,
                                contract_version=contract, compatibility=compat,
                                definition_hash=stable_hash([ins, outs, n8n_v]), created_by=actor.name, created_at=now)
        s.add(v)
        s.flush()
        self.repo.audit(s, actor=actor.name, actor_kind=actor.kind, action="workflow.version_added", entity_type="workflow",
                        entity_id=w.workflow_id, at=now, details={"version": label, "version_id": v.version_id})
        return v

    def add_version(self, actor: Actor, workflow_id: str, body: dict[str, Any]) -> dict[str, Any]:
        self.ensure_ready()
        self.perm_check(actor, perm.ORCH_MANAGE, human=True)
        now = self.now()
        with self.repo.write() as s:
            w = self._wf(s, workflow_id)
            if w.state == "RETIRED":
                raise OrchError(409, "WORKFLOW_RETIRED", "A retired workflow cannot get new versions.")
            v = self._add_version(s, actor, w, body, now)
            return self.version_view(v)

    def _compat_problems(self, w: OrchWorkflow, v: OrchWorkflowVersion) -> list[str]:
        problems = []
        if v.contract_version.split(".")[0] != CONTRACT_VERSION.split(".")[0]:
            problems.append(f"contract {v.contract_version} is not compatible with {CONTRACT_VERSION}")
        if str(v.compatibility.get("event_schema", SCHEMA_VERSION)).split(".")[0] != SCHEMA_VERSION.split(".")[0]:
            problems.append(f"event schema {v.compatibility.get('event_schema')} is not compatible with {SCHEMA_VERSION}")
        if not w.webhook_path:
            problems.append("the registration has no webhook_path")
        if w.engine == "n8n" and not w.n8n_workflow_id:
            problems.append("the registration has no n8n_workflow_id")
        return problems

    def validate_version(self, actor: Actor, workflow_id: str, version_id: str | None = None) -> dict[str, Any]:
        """Checks a version (default: the newest DRAFT) and moves it to VALIDATED when everything required passes."""
        self.ensure_ready()
        self.perm_check(actor, perm.ORCH_MANAGE, human=True)
        with self.repo.read() as s:
            w = self._wf(s, workflow_id)
            if version_id is None:
                v = s.execute(select(OrchWorkflowVersion).where(OrchWorkflowVersion.workflow_id == workflow_id)
                              .order_by(OrchWorkflowVersion.created_at.desc()).limit(1)).scalar_one_or_none()
                if v is None:
                    raise OrchError(404, "VERSION_NOT_FOUND", "This workflow has no versions yet.")
            else:
                v = self._version(s, workflow_id, version_id)
            vid, problems = v.version_id, self._compat_problems(w, v)
            n8n_id, path, status = w.n8n_workflow_id, w.webhook_path, v.status
            try:
                check_schema(v.input_schema)
                check_schema(v.output_schema)
            except SchemaError as exc:
                problems.append(str(exc))
        checks: dict[str, Any] = {"schemas": "ok" if not any("schema" in p for p in problems) else "failed"}
        warnings: list[str] = []
        engine_info: dict[str, Any] | None = None
        if n8n_id and self.wf_engine.api_configured():                 # network call outside any transaction
            try:
                engine_info = self.wf_engine.get_workflow(n8n_id)
                checks["n8n_workflow"] = "found"
                if path and path not in engine_info.get("webhook_paths", []):
                    problems.append(f"the n8n workflow has no Webhook node on path '{path}'")
                    checks["n8n_webhook_path"] = "missing"
                else:
                    checks["n8n_webhook_path"] = "ok"
                if not engine_info.get("active"):
                    warnings.append("The n8n workflow is not active in n8n; triggers will fail with HTTP 404 until it is.")
            except EngineNotConfigured:
                checks["n8n_workflow"] = "not_checked"
            except EngineError as exc:
                problems.append(f"n8n: {exc.classified.message}")
                checks["n8n_workflow"] = exc.classified.category
        elif n8n_id:
            checks["n8n_workflow"] = "not_checked"
            warnings.append("N8N_API_KEY is not configured, so the n8n workflow and its webhook path were not verified.")
        result = {"ok": not problems, "problems": problems, "warnings": warnings, "checks": checks,
                  "engine": engine_info, "checked_at": self.now()}
        now = self.now()
        with self.repo.write() as s:
            w = self._wf(s, workflow_id)
            v = self._version(s, workflow_id, vid)
            v.validation = {k: (val.isoformat() if hasattr(val, "isoformat") else val) for k, val in result.items()}
            moved = False
            if not problems and v.status == "DRAFT":
                check_version(v.status, "VALIDATED")
                v.status, v.validated_at, moved = "VALIDATED", now, True
                if w.placeholder:
                    w.placeholder, w.updated_at = False, now
                    w.version += 1
            self.repo.audit(s, actor=actor.name, actor_kind=actor.kind, action="workflow.version_validated",
                            entity_type="workflow", entity_id=workflow_id, at=now, outcome="ok" if not problems else "refused",
                            details={"version": v.version_label, "problems": problems, "warnings": warnings})
            return {"version": self.version_view(v), "validation": result, "status_changed": moved, "previous_status": status}

    def publish_version(self, actor: Actor, workflow_id: str, version_id: str) -> dict[str, Any]:
        return self._version_move(actor, workflow_id, version_id, "PUBLISHED", "workflow.version_published")

    def retire_version(self, actor: Actor, workflow_id: str, version_id: str) -> dict[str, Any]:
        return self._version_move(actor, workflow_id, version_id, "RETIRED", "workflow.version_retired")

    def _version_move(self, actor: Actor, workflow_id: str, version_id: str, target: str, action: str) -> dict[str, Any]:
        self.ensure_ready()
        self.perm_check(actor, perm.ORCH_MANAGE, human=True)
        now = self.now()
        with self.repo.write() as s:
            self._wf(s, workflow_id)
            v = self._version(s, workflow_id, version_id)
            try:
                check_version(v.status, target)
            except IllegalTransition as exc:
                raise OrchError(409, "ILLEGAL_TRANSITION", str(exc)) from None
            prev, v.status = v.status, target
            if target == "RETIRED":
                v.deactivated_at = v.deactivated_at or now
            self.repo.audit(s, actor=actor.name, actor_kind=actor.kind, action=action, entity_type="workflow",
                            entity_id=workflow_id, at=now, details={"version": v.version_label, "from": prev, "to": target})
            return self.version_view(v)

    def activate_version(self, actor: Actor, workflow_id: str, version_id: str, reason: str | None = None) -> dict[str, Any]:
        """Makes ``version_id`` the version new executions use. The previous active version becomes DEPRECATED. Running and
        historical executions keep the version they were created with. A DEPRECATED version can be re-activated (rollback)."""
        self.ensure_ready()
        self.perm_check(actor, perm.ORCH_MANAGE, human=True)
        if not reason or not str(reason).strip():
            raise OrchError(422, "REASON_REQUIRED", "Give a reason for changing the active version.")
        now = self.now()
        with self.repo.write() as s:
            w = self._wf(s, workflow_id)
            v = self._version(s, workflow_id, version_id)
            if v.status not in {x.value for x in ACTIVATABLE_VERSIONS}:
                raise OrchError(409, "VERSION_NOT_ACTIVATABLE", f"A {v.status} version cannot be activated; validate it first.")
            problems = self._compat_problems(w, v)
            if problems:
                raise OrchError(409, "INCOMPATIBLE_VERSION", "This version is not compatible: " + "; ".join(problems),
                                {"problems": problems})
            rollback = v.status == "DEPRECATED"
            previous = s.get(OrchWorkflowVersion, w.active_version_id) if w.active_version_id else None
            if previous is not None and previous.version_id != v.version_id and previous.status == "ACTIVE":
                check_version(previous.status, "DEPRECATED")
                previous.status, previous.deactivated_at = "DEPRECATED", now
            check_version(v.status, "ACTIVE")
            v.status, v.activated_at = "ACTIVE", now
            w.active_version_id, w.updated_at = v.version_id, now
            w.version += 1
            self.repo.audit(s, actor=actor.name, actor_kind=actor.kind, action="workflow.rollback" if rollback else
                            "workflow.version_activated", entity_type="workflow", entity_id=workflow_id, at=now,
                            details={"version": v.version_label, "previous": previous.version_label if previous else None,
                                     "reason": str(reason)[:500]})
        return self.get_workflow(workflow_id)

    # ---- registration state -----------------------------------------------------------------------------------------------
    def set_workflow_state(self, actor: Actor, workflow_id: str, target: str, reason: str | None = None,
                           sync_engine: bool = False) -> dict[str, Any]:
        self.ensure_ready()
        self.perm_check(actor, perm.ORCH_MANAGE, human=True)
        target = str(target).upper()
        if not reason or not str(reason).strip():
            raise OrchError(422, "REASON_REQUIRED", "Give a reason for the state change.")
        with self.repo.read() as s:
            w = self._wf(s, workflow_id)
            n8n_id = w.n8n_workflow_id
            if target == "ACTIVE":
                if w.placeholder:
                    raise OrchError(409, "PLACEHOLDER_NOT_IMPLEMENTED", "This is a reserved WF08 registration; its n8n "
                                    "workflow is not built yet. Register the n8n workflow and validate a version first.")
                if not w.active_version_id:
                    raise OrchError(409, "NO_ACTIVE_VERSION", "Activate a validated version before activating the workflow.")
                v = s.get(OrchWorkflowVersion, w.active_version_id)
                problems = self._compat_problems(w, v) if v else ["active version missing"]
                if problems:
                    raise OrchError(409, "INCOMPATIBLE_VERSION", "; ".join(problems), {"problems": problems})
        engine_result: dict[str, Any] = {"synced": False}
        if sync_engine and target in ("ACTIVE", "INACTIVE", "PAUSED") and n8n_id:
            try:
                r = self.wf_engine.set_active(n8n_id, target == "ACTIVE")
                engine_result = {"synced": True, "n8n_active": r.get("active")}
            except EngineNotConfigured:
                engine_result = {"synced": False, "note": "N8N_API_KEY is not configured: change the workflow's active "
                                 "switch in the n8n editor."}
            except EngineError as exc:
                raise OrchError(502, "ENGINE_ERROR", f"n8n refused the change: {exc.classified.message}") from None
        elif target == "ACTIVE":
            engine_result = {"synced": False, "note": "AURUM state only. Make sure the workflow is also active in n8n."}
        now = self.now()
        with self.repo.write() as s:
            w = self._wf(s, workflow_id)
            if w.state == target:
                return {**self.wf_view(w), "engine": engine_result, "changed": False}
            try:
                check_workflow(w.state, target)
            except IllegalTransition as exc:
                raise OrchError(409, "ILLEGAL_TRANSITION", str(exc)) from None
            prev, w.state, w.updated_at = w.state, target, now
            w.version += 1
            if target == "ACTIVE":
                w.health_status, w.health_detail = "HEALTHY" if w.health_status == "UNKNOWN" else w.health_status, w.health_detail
            self.repo.audit(s, actor=actor.name, actor_kind=actor.kind, action=f"workflow.{target.lower()}",
                            entity_type="workflow", entity_id=workflow_id, at=now,
                            details={"from": prev, "reason": str(reason)[:500], "engine": engine_result})
            return {**self.wf_view(w), "engine": engine_result, "changed": True}

    def engine_workflow(self, workflow_id: str) -> dict[str, Any]:
        """What n8n reports about the registered workflow (needs the n8n API key)."""
        with self.repo.read() as s:
            n8n_id = self._wf(s, workflow_id).n8n_workflow_id
        if not n8n_id:
            raise OrchError(409, "NO_ENGINE_WORKFLOW", "This registration has no n8n_workflow_id.")
        try:
            return {"workflow": self.wf_engine.get_workflow(n8n_id),
                    "executions": self.wf_engine.list_executions(n8n_id, 10)}
        except EngineNotConfigured as exc:
            raise OrchError(409, "ENGINE_NOT_CONFIGURED", str(exc)) from None
        except EngineError as exc:
            raise OrchError(502, "ENGINE_ERROR", exc.classified.message) from None
