"""The Module 08 control plane. ``OrchestrationService`` is assembled from four parts:

- ``registry.RegistryMixin``    workflow registrations, versions, activation, rollback;
- ``executions.ExecutionMixin`` trigger, dispatch, callbacks, time-outs, retries, cancellation;
- ``events.EventMixin``         publish, inbox, routing, deliveries (outbox), Module 06/07 bridge, replay;
- ``operations.OpsMixin``       dead letters, leases, schedules, integrations and health, configuration, audit, metrics.

The database is the source of truth for orchestration records; n8n runs the workflow logic. Every write goes through
``repo.write()`` (one serialised transaction); network calls (n8n, modules, webhooks) are always made outside it.
"""
from __future__ import annotations

import threading
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.engine import Engine

from aurum.auth import roles as perm
from aurum.orchestration import CONTRACT_VERSION, MODULE_VERSION, SCHEMA_VERSION
from aurum.orchestration.common import SYSTEM, Actor, OrchError, pick, utcnow  # noqa: F401
from aurum.orchestration.config_model import OrchestrationConfig, load_file, parse_config
from aurum.orchestration.db_models import (OrchCredential, OrchExecution, OrchIntegration, OrchWorkflow, OrchWorkflowVersion)
from aurum.orchestration.engines import ControlledTestEngine, N8nEngine, WorkflowEngine
from aurum.orchestration.events import EventMixin
from aurum.orchestration.executions import ExecutionMixin
from aurum.orchestration.gateway import DEFAULT_MODULES, ModuleEndpoint, ModuleGateway
from aurum.orchestration.operations import OpsMixin
from aurum.orchestration.registry import RegistryMixin
from aurum.orchestration.repository import OrchRepository, stable_hash, utc
from aurum.orchestration.security import CREDENTIALS, fingerprint, secret_value


TEST_WORKFLOW_ID = "aurum-core-test-echo"
PLACEHOLDERS = (
    ("wf08-01-market-monitoring", "WF08-01 Market Monitoring Scheduler", "market_monitoring", ["schedule", "manual", "admin"],
     "Keep market data fresh: request Module 01 updates on a schedule and react to stale-data events."),
    ("wf08-02-market-analysis", "WF08-02 Market Analysis Orchestrator", "market_analysis", ["event", "manual", "admin"],
     "Run Module 03, 04 and 05 analysis when new data arrives, and collect the results into trade ideas."),
    ("wf08-03-risk-approval", "WF08-03 Risk and Human Approval Orchestrator", "risk_approval", ["event", "manual", "admin"],
     "Request Module 06 assessments for new ideas and follow Module 07 approval requests. Never approves anything."),
    ("wf08-04-execution-handoff", "WF08-04 Execution Handoff Orchestrator", "execution_handoff", ["event", "admin"],
     "Hand trades that Module 07 approved to Module 09. Triggers are refused unless Module 07 shows the approval."),
)
TEST_INPUT_SCHEMA = {"type": "object", "additionalProperties": False,
                     "properties": {"message": {"type": "string", "maxLength": 500},
                                    "simulate": {"type": "string", "enum": ["success", "failure", "slow"]},
                                    # event-triggered runs receive the event envelope (orchestration.test.ping)
                                    "event": {"type": "object"}}}
TEST_OUTPUT_SCHEMA = {"type": "object", "properties": {"echo": {"type": "string"}, "receivedAt": {"type": "string"}}}


def build_engine(settings: Any) -> WorkflowEngine:
    env = settings.orchestration_environment
    return N8nEngine(base_url=settings.n8n_base_url, webhook_base_url=settings.n8n_webhook_base_url,
                     api_key=secret_value(settings, "n8n_api_key"),
                     signing_secret=secret_value(settings, "n8n_webhook_shared_secret"),
                     webhook_token=secret_value(settings, "n8n_webhook_auth_token"),
                     environment=settings.n8n_environment or env, timeout=float(settings.n8n_request_timeout))


class OrchestrationService(RegistryMixin, ExecutionMixin, EventMixin, OpsMixin):
    def __init__(self, engine: Engine, settings: Any, *, workflow_engine: WorkflowEngine | None = None,
                 gateway: ModuleGateway | None = None, clock: Callable[[], datetime] | None = None,
                 approvals: Callable[[str], str | None] | None = None, webhook_transport: Any = None):
        self.repo = OrchRepository(engine)
        self.settings = settings
        self.env = settings.orchestration_environment
        self._clock = clock or utcnow
        self.wf_engine = workflow_engine or build_engine(settings)
        if isinstance(self.wf_engine, ControlledTestEngine) and self.env in ("demo", "production"):
            raise ValueError("The controlled test adapter cannot be used in the demo or production environment.")
        self._gateway = gateway
        self._gateway_injected = gateway is not None
        self.approvals = approvals if approvals is not None else self._approval_status
        self.counters: Counter[str] = Counter()
        self._lock = threading.Lock()
        self._ready = False
        self._config: tuple[int, OrchestrationConfig] | None = None
        self._webhook_transport = webhook_transport
        EventMixin.__init__(self)

    # ---- basics -----------------------------------------------------------------------------------------------------------
    def now(self) -> datetime:
        return self._clock()

    def count(self, name: str, n: int = 1) -> None:
        with self._lock:
            self.counters[name] += n

    def ensure_ready(self) -> None:
        if self._ready:
            return
        if not self.repo.schema_ready():
            raise OrchError(503, "SCHEMA_NOT_READY", "Module 08 tables are missing. Run `python -m aurum.cli init-db`.")
        now = self.now()
        pinned = self.repo.pin_environment(self.env, now)
        if pinned != self.env:
            raise OrchError(409, "ENVIRONMENT_MISMATCH",
                            f"This database belongs to the {pinned} environment; this process is configured for {self.env}.",
                            {"database": pinned, "process": self.env})
        self._seed(now)
        self._ready = True

    def _seed(self, now: datetime) -> None:
        with self.repo.write() as s:
            if self.repo.config_current(s) is None:
                cfg = load_file(self._config_path())
                self.repo.config_add(s, cfg.dump(), "Initial configuration from config/orchestration.yaml", "system", now)
                self.repo.audit(s, actor="system", actor_kind="system", action="config.seeded", entity_type="config",
                                entity_id="1", at=now)
            self._seed_integrations(s, now)
            if s.get(OrchWorkflow, TEST_WORKFLOW_ID) is None:
                s.add(OrchWorkflow(workflow_id=TEST_WORKFLOW_ID, name="AURUM Core - Test echo (harmless)",
                                   description="Echoes its input back through the callback. Touches no market data, no "
                                               "module and no account. Used to verify the n8n pipeline end to end.",
                                   purpose="Infrastructure verification", category="test", owner="module_08",
                                   environment=self.env, engine="n8n", webhook_path=TEST_WORKFLOW_ID, state="REGISTERED",
                                   trigger_types=["api", "manual", "admin", "event", "schedule"], timeout_seconds=120,
                                   retry_policy={}, idempotent_dispatch=True, placeholder=False, created_by="system",
                                   created_at=now, updated_at=now))
                s.flush()
                s.add(OrchWorkflowVersion(version_id=str(uuid.uuid4()), workflow_id=TEST_WORKFLOW_ID, version_label="1.0.0",
                                          description="Initial test echo contract.", status="DRAFT",
                                          input_schema=TEST_INPUT_SCHEMA, output_schema=TEST_OUTPUT_SCHEMA,
                                          contract_version=CONTRACT_VERSION,
                                          compatibility={"contract_version": CONTRACT_VERSION, "event_schema": SCHEMA_VERSION},
                                          definition_hash=stable_hash([TEST_INPUT_SCHEMA, TEST_OUTPUT_SCHEMA, None]),
                                          created_by="system", created_at=now))
                self.repo.audit(s, actor="system", actor_kind="system", action="workflow.seeded", entity_type="workflow",
                                entity_id=TEST_WORKFLOW_ID, at=now)
            for wid, name, category, triggers, purpose in PLACEHOLDERS:
                if s.get(OrchWorkflow, wid) is None:
                    s.add(OrchWorkflow(workflow_id=wid, name=name, description="Reserved registration. The n8n workflow is "
                                       "not built yet; this entry cannot be activated until it is.", purpose=purpose,
                                       category=category, owner="module_08", environment=self.env, engine="n8n",
                                       state="REGISTERED", trigger_types=triggers, timeout_seconds=600, retry_policy={},
                                       idempotent_dispatch=False, placeholder=True, created_by="system", created_at=now,
                                       updated_at=now))
                    self.repo.audit(s, actor="system", actor_kind="system", action="workflow.placeholder_registered",
                                    entity_type="workflow", entity_id=wid, at=now)
            self._sync_credentials(s, now)

    def _config_path(self) -> Any:
        from pathlib import Path

        from aurum.config import PROJECT_ROOT
        p = Path(self.settings.orchestration_config)
        return p if p.is_absolute() else PROJECT_ROOT / p

    def config(self) -> OrchestrationConfig:
        with self.repo.read() as s:
            row = self.repo.config_current(s)
            if row is None:
                return OrchestrationConfig()
            if self._config is None or self._config[0] != row.version:
                self._config = (row.version, parse_config(row.config))
            return self._config[1]

    # ---- integrations seed / gateway ----------------------------------------------------------------------------------------
    def _endpoints(self, cfg: OrchestrationConfig | None = None) -> dict[str, ModuleEndpoint]:
        cfg = cfg or self.config()
        base = self.settings.aurum_internal_base_url.rstrip("/")
        out: dict[str, ModuleEndpoint] = {}
        for mid, name, path, token in DEFAULT_MODULES:
            override = cfg.modules.get(mid)
            url = override.base_url if override and override.base_url else (base if mid <= "module_07" else None)
            if override is not None and not override.enabled:
                url = None
            out[mid] = ModuleEndpoint(mid, name, url, path, token,
                                      (override.timeout if override and override.timeout else cfg.timeouts.module_request_seconds))
        return out

    @property
    def gateway(self) -> ModuleGateway:
        if self._gateway is None:
            self._gateway = ModuleGateway(self._endpoints(), service_token=secret_value(self.settings, "orchestration_service_token"))
        return self._gateway

    def _seed_integrations(self, s: Any, now: datetime) -> None:
        rows = [("n8n", "n8n workflow engine", "engine", self.settings.n8n_base_url, "/healthz"),
                ("database", "PostgreSQL / TimescaleDB", "database", None, None),
                ("event_store", "Event store (PostgreSQL outbox and inbox)", "event_store", None, None)]
        for mid, ep in self._endpoints(OrchestrationConfig()).items():
            rows.append((mid, ep.name, "module", ep.base_url, ep.health_path))
        for iid, name, kind, url, path in rows:
            row = s.get(OrchIntegration, iid)
            if row is None:
                s.add(OrchIntegration(integration_id=iid, name=name, kind=kind, base_url=url, health_path=path,
                                      environment=self.env, enabled=True, status="UNKNOWN", updated_at=now))
            elif kind == "engine" and row.base_url != url:
                row.base_url, row.updated_at = url, now

    def _sync_credentials(self, s: Any, now: datetime) -> None:
        for c in CREDENTIALS:
            fp = fingerprint(secret_value(self.settings, c.attr))
            row = s.get(OrchCredential, c.name)
            if row is None:
                s.add(OrchCredential(name=c.name, source=f"env:{c.env}", purpose=c.purpose, configured=fp is not None,
                                     fingerprint=fp, first_seen_at=now if fp else None, checked_at=now))
                continue
            if row.fingerprint != fp:
                if row.fingerprint and fp:
                    row.rotated_at = now
                    self.repo.audit(s, actor="system", actor_kind="system", action="credential.rotated",
                                    entity_type="credential", entity_id=c.name, at=now,
                                    details={"previous_fingerprint": row.fingerprint, "fingerprint": fp})
                elif fp and not row.first_seen_at:
                    row.first_seen_at = now
                row.fingerprint, row.configured = fp, fp is not None
            row.checked_at = now

    # ---- Module 07 read (execution-handoff guard) ---------------------------------------------------------------------------
    def _approval_status(self, approval_id: str) -> str | None:
        """Read-only look at Module 07's approval status. Module 08 never changes it."""
        try:
            from aurum.approvals.db_models import ApprovalRequest
            with self.repo.read() as s:
                return s.execute(select(ApprovalRequest.status).where(ApprovalRequest.approval_id == approval_id)).scalar()
        except Exception:  # noqa: BLE001 - Module 07 tables absent
            return None

    # ---- shared views ---------------------------------------------------------------------------------------------------------
    def info(self) -> dict[str, Any]:
        return {"module_version": MODULE_VERSION, "contract_version": CONTRACT_VERSION, "schema_version": SCHEMA_VERSION,
                "environment": self.env, "engine": self.wf_engine.describe() if hasattr(self.wf_engine, "describe") else {},
                "n8n_public_url": self.settings.n8n_public_url or self.settings.n8n_base_url,
                "callback_base_url": self.settings.orchestration_callback_base_url}

    @staticmethod
    def exec_view(e: OrchExecution, full: bool = False) -> dict[str, Any]:
        dur = None
        if e.started_at and e.finished_at:
            dur = (utc(e.finished_at) - utc(e.started_at)).total_seconds()
        out = {"execution_id": e.execution_id, "workflow_id": e.workflow_id, "workflow_version": e.version_label,
               "version_id": e.version_id, "environment": e.environment, "status": e.status, "row_version": e.version,
               "trigger_type": e.trigger_type, "trigger_source": e.trigger_source, "requesting_module": e.requesting_module,
               "correlation_id": e.correlation_id, "causation_id": e.causation_id, "idempotency_key": e.idempotency_key,
               "symbol": e.symbol, "trade_idea_id": e.trade_idea_id, "approval_id": e.approval_id,
               "n8n_execution_id": e.n8n_execution_id, "attempt": e.attempt, "retry_count": e.retry_count,
               "next_attempt_at": utc(e.next_attempt_at), "deadline_at": utc(e.deadline_at), "error_category": e.error_category,
               "error_message": e.error_message, "warnings": e.warnings or [], "created_at": utc(e.created_at),
               "started_at": utc(e.started_at), "finished_at": utc(e.finished_at), "duration_seconds": dur,
               "status_changed_at": utc(e.status_changed_at)}
        if full:
            out.update({"input": e.input, "input_hash": e.input_hash, "output": e.output, "timeline": e.timeline or []})
        return out

    def perm_check(self, actor: Actor, permission: str, *, human: bool = False) -> None:
        if human and not actor.is_human and actor.kind != "system":
            raise OrchError(403, "HUMAN_REQUIRED", "This action needs a signed-in person; services cannot perform it.")
        if not actor.can(permission):
            raise OrchError(403, "FORBIDDEN", "Your role does not allow this action.")


__all__ = ["Actor", "OrchError", "OrchestrationService", "SYSTEM", "TEST_WORKFLOW_ID", "pick", "perm"]
