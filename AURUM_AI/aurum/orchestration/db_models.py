"""Module 08 tables (migration 0010_orchestration.sql). Every table is prefixed ``orch_``.

Safeguards in the schema itself:
- one execution per (workflow, idempotency key) and one event per event id and per idempotency key (unique);
- one delivery per (event, subscription) and one schedule run per (schedule, slot) (unique), so duplicates are impossible;
- one callback per callback id (unique), which is also the replay guard for signed callbacks;
- ``version`` columns for optimistic concurrency on executions and workflows;
- the execution status trigger and append-only audit log on PostgreSQL (see the migration), plus a hash chain on the audit.
Secrets are never stored: ``orch_credentials`` holds metadata only (where the secret lives, a short fingerprint, rotation).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from aurum.database.models import Base, BigIntPK, JsonType, UTCDateTime, utcnow

WF_FK = "orch_workflows.workflow_id"


class OrchInstance(Base):
    """Pins this database to one environment. A process configured for another environment refuses to run against it."""
    __tablename__ = "orch_instance"
    key: Mapped[str] = mapped_column(String(16), primary_key=True)                 # always 'instance'
    environment: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


# ---- workflow registry ------------------------------------------------------------------------------------------------------
class OrchWorkflow(Base):
    __tablename__ = "orch_workflows"
    workflow_id: Mapped[str] = mapped_column(String(64), primary_key=True)       # e.g. wf08-01-market-monitoring
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    purpose: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(32), nullable=False)             # market_monitoring | analysis | risk_approval | execution_handoff | test | other
    owner: Mapped[str] = mapped_column(String(80), nullable=False)
    environment: Mapped[str] = mapped_column(String(16), nullable=False)
    engine: Mapped[str] = mapped_column(String(16), nullable=False, default="n8n")
    n8n_workflow_id: Mapped[str | None] = mapped_column(String(64))
    webhook_path: Mapped[str | None] = mapped_column(String(160))
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    trigger_types: Mapped[list] = mapped_column(JsonType, nullable=False)        # webhook | event | api | schedule | manual | admin
    active_version_id: Mapped[str | None] = mapped_column(String(36))
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=300)
    retry_policy: Mapped[dict] = mapped_column(JsonType, nullable=False)
    idempotent_dispatch: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    placeholder: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)   # reserved WF08 entry, no n8n workflow yet
    health_status: Mapped[str] = mapped_column(String(16), nullable=False, default="UNKNOWN")
    health_detail: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_by: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    last_execution_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class OrchWorkflowVersion(Base):
    """The schemas, n8n reference and compatibility of a version never change after creation; only status and timestamps do."""
    __tablename__ = "orch_workflow_versions"
    __table_args__ = (UniqueConstraint("workflow_id", "version_label", name="uq_orch_workflow_versions"),)
    version_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workflow_id: Mapped[str] = mapped_column(ForeignKey(WF_FK), nullable=False, index=True)
    version_label: Mapped[str] = mapped_column(String(32), nullable=False)       # semantic version, e.g. 1.0.0
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(12), nullable=False)
    input_schema: Mapped[dict] = mapped_column(JsonType, nullable=False)
    output_schema: Mapped[dict] = mapped_column(JsonType, nullable=False)
    n8n_version_id: Mapped[str | None] = mapped_column(String(64))
    contract_version: Mapped[str] = mapped_column(String(8), nullable=False)
    compatibility: Mapped[dict] = mapped_column(JsonType, nullable=False)
    definition_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    validation: Mapped[dict | None] = mapped_column(JsonType)
    created_by: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    validated_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    activated_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    deactivated_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


# ---- executions -------------------------------------------------------------------------------------------------------------
class OrchExecution(Base):
    __tablename__ = "orch_executions"
    __table_args__ = (UniqueConstraint("workflow_id", "idempotency_key", name="uq_orch_executions_idem"),
                      Index("ix_orch_executions_status_due", "status", "next_attempt_at"),
                      Index("ix_orch_executions_created", "created_at"))
    execution_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workflow_id: Mapped[str] = mapped_column(ForeignKey(WF_FK), nullable=False, index=True)
    version_id: Mapped[str] = mapped_column(String(36), nullable=False)
    version_label: Mapped[str] = mapped_column(String(32), nullable=False)
    environment: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    trigger_type: Mapped[str] = mapped_column(String(12), nullable=False)
    trigger_source: Mapped[str] = mapped_column(String(80), nullable=False)       # who or what asked
    requesting_module: Mapped[str | None] = mapped_column(String(16))
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    causation_id: Mapped[str | None] = mapped_column(String(64))
    input: Mapped[dict] = mapped_column(JsonType, nullable=False)                 # validated, credential-free input
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    output: Mapped[dict | None] = mapped_column(JsonType)
    symbol: Mapped[str | None] = mapped_column(String(32), index=True)
    trade_idea_id: Mapped[str | None] = mapped_column(String(64), index=True)
    approval_id: Mapped[str | None] = mapped_column(String(36), index=True)
    n8n_execution_id: Mapped[str | None] = mapped_column(String(64))
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    deadline_at: Mapped[datetime | None] = mapped_column(UTCDateTime)           # time-out for the current attempt
    error_category: Mapped[str | None] = mapped_column(String(32))
    error_message: Mapped[str | None] = mapped_column(Text)
    warnings: Mapped[list | None] = mapped_column(JsonType)
    timeline: Mapped[list] = mapped_column(JsonType, nullable=False)              # every status change: at, from, to, by, reason
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    status_changed_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class OrchExecutionAttempt(Base):
    __tablename__ = "orch_execution_attempts"
    __table_args__ = (UniqueConstraint("execution_id", "attempt", name="uq_orch_execution_attempts"),)
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    execution_id: Mapped[str] = mapped_column(ForeignKey("orch_executions.execution_id", ondelete="CASCADE"), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)              # in_progress | accepted | transient | permanent | unknown
    http_status: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[float | None] = mapped_column(Float)
    error_category: Mapped[str | None] = mapped_column(String(32))
    error_message: Mapped[str | None] = mapped_column(Text)
    response_meta: Mapped[dict | None] = mapped_column(JsonType)
    manual: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class OrchExecutionCallback(Base):
    __tablename__ = "orch_execution_callbacks"
    callback_id: Mapped[str] = mapped_column(String(64), primary_key=True)       # unique: duplicate deliveries are recognised
    execution_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    workflow_id: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)                # started | completed | failed | timed_out | partial | progress
    attempt: Mapped[int | None] = mapped_column(Integer)
    received_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)             # applied | stale | rejected
    detail: Mapped[str | None] = mapped_column(Text)
    previous_status: Mapped[str | None] = mapped_column(String(16))
    new_status: Mapped[str | None] = mapped_column(String(16))
    payload: Mapped[dict | None] = mapped_column(JsonType)                       # credential-free (checked on intake)


# ---- events --------------------------------------------------------------------------------------------------------------------
class OrchEvent(Base):
    """Every event Module 08 knows about. Written in the same transaction as its deliveries (transactional outbox)."""
    __tablename__ = "orch_events"
    seq: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    event_version: Mapped[str] = mapped_column(String(8), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(8), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    published_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, index=True)
    source_module: Mapped[str] = mapped_column(String(16), nullable=False)
    destination: Mapped[str | None] = mapped_column(String(32))
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    causation_id: Mapped[str | None] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    environment: Mapped[str] = mapped_column(String(16), nullable=False)
    payload: Mapped[dict] = mapped_column(JsonType, nullable=False)
    meta: Mapped[dict | None] = mapped_column("metadata", JsonType)
    ingress: Mapped[str] = mapped_column(String(12), nullable=False)             # internal | api | n8n | bridge | replay
    status: Mapped[str] = mapped_column(String(12), nullable=False)              # routed | no_subscribers | delivered | partial | failed
    deliveries_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class OrchSubscription(Base):
    __tablename__ = "orch_event_subscriptions"
    subscription_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    event_pattern: Mapped[str] = mapped_column(String(64), nullable=False)       # exact, 'family.*' or '*'
    target_kind: Mapped[str] = mapped_column(String(12), nullable=False)         # workflow | webhook
    workflow_id: Mapped[str | None] = mapped_column(ForeignKey(WF_FK))
    webhook_url: Mapped[str | None] = mapped_column(String(400))
    environment: Mapped[str] = mapped_column(String(16), nullable=False)
    filter: Mapped[dict | None] = mapped_column(JsonType)                       # payload equality filter, e.g. {"symbol": "EURUSD"}
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=6)
    created_by: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class OrchDelivery(Base):
    __tablename__ = "orch_event_deliveries"
    __table_args__ = (UniqueConstraint("event_id", "subscription_id", "replay", name="uq_orch_event_deliveries"),
                      Index("ix_orch_event_deliveries_due", "status", "next_attempt_at"))
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    subscription_id: Mapped[str] = mapped_column(String(36), nullable=False)
    replay: Mapped[int] = mapped_column(Integer, nullable=False, default=0)      # 0 = original, n = nth replay
    status: Mapped[str] = mapped_column(String(12), nullable=False)              # pending | delivered | failed | dead | skipped
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_error: Mapped[str | None] = mapped_column(Text)
    error_category: Mapped[str | None] = mapped_column(String(32))
    execution_id: Mapped[str | None] = mapped_column(String(36))                 # when the target is a workflow
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    delivered_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    latency_ms: Mapped[float | None] = mapped_column(Float)


class OrchDeliveryAttempt(Base):
    __tablename__ = "orch_event_delivery_attempts"
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    delivery_id: Mapped[int] = mapped_column(ForeignKey("orch_event_deliveries.id", ondelete="CASCADE"), nullable=False, index=True)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    outcome: Mapped[str] = mapped_column(String(12), nullable=False)             # delivered | transient | permanent | unknown
    http_status: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[float | None] = mapped_column(Float)
    error: Mapped[str | None] = mapped_column(Text)


class OrchInbox(Base):
    """Inbound messages (published events and n8n event webhooks): one row per (channel, message id) - duplicates are
    recognised, never processed twice."""
    __tablename__ = "orch_inbox"
    __table_args__ = (UniqueConstraint("channel", "message_id", name="uq_orch_inbox"),)
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)             # api | n8n
    message_id: Mapped[str] = mapped_column(String(64), nullable=False)
    event_type: Mapped[str | None] = mapped_column(String(64))
    received_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False)              # processed | rejected
    caller: Mapped[str | None] = mapped_column(String(80))
    error_code: Mapped[str | None] = mapped_column(String(48))
    duplicates: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class OrchSourceCursor(Base):
    """How far the bridge has read the Module 06 and Module 07 outboxes (read-only on their tables)."""
    __tablename__ = "orch_source_cursors"
    source: Mapped[str] = mapped_column(String(32), primary_key=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


# ---- failure management ---------------------------------------------------------------------------------------------------------
class OrchDeadLetter(Base):
    __tablename__ = "orch_dead_letters"
    __table_args__ = (Index("ix_orch_dead_letters_status", "recovery_status"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    kind: Mapped[str] = mapped_column(String(12), nullable=False)                # execution | delivery
    event_id: Mapped[str | None] = mapped_column(String(64), index=True)
    execution_id: Mapped[str | None] = mapped_column(String(36), index=True)
    delivery_id: Mapped[int | None] = mapped_column(Integer)
    workflow_id: Mapped[str | None] = mapped_column(String(64))
    error_category: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    delivery_count: Mapped[int] = mapped_column(Integer, nullable=False)
    last_attempt_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    failed_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(String(64))
    recovery_status: Mapped[str] = mapped_column(String(12), nullable=False)      # open | retried | resolved
    resolved_by: Mapped[str | None] = mapped_column(String(80))
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    resolution_note: Mapped[str | None] = mapped_column(Text)


class OrchIdempotency(Base):
    __tablename__ = "orch_idempotency"
    __table_args__ = (UniqueConstraint("scope", "key", name="uq_orch_idempotency"),)
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    scope: Mapped[str] = mapped_column(String(64), nullable=False)               # e.g. trigger:<principal>, publish:<principal>
    key: Mapped[str] = mapped_column(String(160), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    response: Mapped[dict | None] = mapped_column(JsonType)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)


class OrchLock(Base):
    """Leases. A lock is held until released or until ``expires_at``; an expired lease can be taken over (stale recovery)."""
    __tablename__ = "orch_locks"
    lock_key: Mapped[str] = mapped_column(String(160), primary_key=True)         # scope:value, e.g. symbol:EURUSD
    scope: Mapped[str] = mapped_column(String(24), nullable=False)
    owner: Mapped[str] = mapped_column(String(80), nullable=False)
    token: Mapped[str] = mapped_column(String(36), nullable=False)
    fencing: Mapped[int] = mapped_column(Integer, nullable=False, default=1)     # increases on every takeover
    acquired_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    renewed_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    released_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


# ---- scheduling -------------------------------------------------------------------------------------------------------------------
class OrchSchedule(Base):
    __tablename__ = "orch_schedules"
    schedule_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    workflow_id: Mapped[str] = mapped_column(ForeignKey(WF_FK), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)                # cron | interval | once
    expression: Mapped[str] = mapped_column(String(64), nullable=False)          # cron expr | seconds | ISO time
    timezone: Mapped[str] = mapped_column(String(40), nullable=False)
    input: Mapped[dict] = mapped_column(JsonType, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    next_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime, index=True)
    last_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_status: Mapped[str | None] = mapped_column(String(16))
    last_execution_id: Mapped[str | None] = mapped_column(String(36))
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_by: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class OrchScheduleRun(Base):
    __tablename__ = "orch_schedule_runs"
    __table_args__ = (UniqueConstraint("schedule_id", "slot_at", name="uq_orch_schedule_runs"),)
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    schedule_id: Mapped[str] = mapped_column(ForeignKey("orch_schedules.schedule_id", ondelete="CASCADE"), nullable=False, index=True)
    slot_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    fired_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)              # triggered | failed | skipped
    execution_id: Mapped[str | None] = mapped_column(String(36))
    detail: Mapped[str | None] = mapped_column(Text)


# ---- integrations and health ------------------------------------------------------------------------------------------------------
class OrchIntegration(Base):
    __tablename__ = "orch_integrations"
    integration_id: Mapped[str] = mapped_column(String(32), primary_key=True)   # n8n | database | event_store | module_01 ...
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)                # engine | database | event_store | module
    base_url: Mapped[str | None] = mapped_column(String(300))
    health_path: Mapped[str | None] = mapped_column(String(160))
    environment: Mapped[str] = mapped_column(String(16), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="UNKNOWN")
    detail: Mapped[str | None] = mapped_column(Text)
    last_checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_healthy_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    latency_ms: Mapped[float | None] = mapped_column(Float)
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class OrchHealthCheck(Base):
    __tablename__ = "orch_health_checks"
    __table_args__ = (Index("ix_orch_health_checks_at", "integration_id", "checked_at"),)
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    integration_id: Mapped[str] = mapped_column(String(32), nullable=False)
    checked_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False)
    latency_ms: Mapped[float | None] = mapped_column(Float)
    http_status: Mapped[int | None] = mapped_column(Integer)
    detail: Mapped[str | None] = mapped_column(Text)


class OrchCredential(Base):
    """Metadata only. The secret itself stays in the environment (.env / Docker secrets) and is never written here."""
    __tablename__ = "orch_credentials"
    name: Mapped[str] = mapped_column(String(48), primary_key=True)             # e.g. n8n_api_key
    source: Mapped[str] = mapped_column(String(64), nullable=False)             # env:N8N_API_KEY
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    configured: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    fingerprint: Mapped[str | None] = mapped_column(String(16))                  # first 12 hex of SHA-256; reveals nothing
    first_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    rotated_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    checked_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class OrchConfigVersion(Base):
    """Append-only versions of the non-secret orchestration configuration. The highest version is in force."""
    __tablename__ = "orch_config_versions"
    version: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    config: Mapped[dict] = mapped_column(JsonType, nullable=False)
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class OrchAuditLog(Base):
    """Append-only and hash-chained, like the Module 06 and 07 audit logs."""
    __tablename__ = "orch_audit_logs"
    seq: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, index=True)
    actor: Mapped[str] = mapped_column(String(80), nullable=False)
    actor_kind: Mapped[str] = mapped_column(String(8), nullable=False)          # user | service | system | n8n
    action: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(24), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome: Mapped[str] = mapped_column(String(12), nullable=False)             # ok | refused | failed
    correlation_id: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict | None] = mapped_column(JsonType)
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    hash: Mapped[str] = mapped_column(String(64), nullable=False)
