"""Module 07 tables (migration 0009_human_approval.sql).

Prices, volumes and money are NUMERIC(24, 8) read back as Decimal. Times are UTC. Safeguards in the schema itself:
- one final decision per approval and per management request (partial unique index on ``is_final``);
- one execution handoff per approval (unique ``approval_id``);
- at most one active approval request per trade idea, and one active management request per position (partial unique);
- ``version`` columns for optimistic concurrency on every status change;
- append-only decisions, evidence snapshots and audit log (PostgreSQL triggers in the migration), and a hash chain on the
  audit log.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from aurum.database.models import Base, BigIntPK, JsonType, UTCDateTime, utcnow
from aurum.risk.db_models import Money

ACTIVE_APPROVAL = ("PENDING", "UNDER_REVIEW", "APPROVED", "EXECUTION_PENDING", "EXECUTION_SUBMITTED")
ACTIVE_MANAGEMENT = ("PENDING", "APPROVED", "EXECUTION_PENDING", "EXECUTION_SUBMITTED")
_ACTIVE_APPROVAL_SQL = "status IN ('PENDING','UNDER_REVIEW','APPROVED','EXECUTION_PENDING','EXECUTION_SUBMITTED')"
_ACTIVE_MANAGEMENT_SQL = "status IN ('PENDING','APPROVED','EXECUTION_PENDING','EXECUTION_SUBMITTED')"
USER_FK = "users.id"


# ---- trade ideas -----------------------------------------------------------------------------------------------------------
class TradeIdea(Base):
    __tablename__ = "trade_ideas"
    idea_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False)              # module05 | event | manual
    source_ref: Mapped[str | None] = mapped_column(String(64))                  # Module 05 candidate_uid
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    market: Mapped[str] = mapped_column(String(16), nullable=False)             # forex | metal | index | crypto | ...
    timeframe: Mapped[str] = mapped_column(String(4), nullable=False)
    strategy: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)             # active | cancelled | expired | closed
    current_version: Mapped[int] = mapped_column(Integer, nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class TradeIdeaVersion(Base):
    """Immutable: a change to a trade's parameters is a new version, never an edit."""
    __tablename__ = "trade_idea_versions"
    __table_args__ = (UniqueConstraint("idea_id", "version", name="uq_trade_idea_versions"),)
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    idea_id: Mapped[str] = mapped_column(ForeignKey("trade_ideas.idea_id", ondelete="CASCADE"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    direction: Mapped[str] = mapped_column(String(4), nullable=False)           # BUY | SELL
    entry_low: Mapped[Decimal] = mapped_column(Money, nullable=False)
    entry_high: Mapped[Decimal] = mapped_column(Money, nullable=False)
    entry: Mapped[Decimal] = mapped_column(Money, nullable=False)               # the price assessed and approved
    stop_loss: Mapped[Decimal] = mapped_column(Money, nullable=False)
    take_profits: Mapped[list] = mapped_column(JsonType, nullable=False)        # decimal strings, nearest first
    risk_reward: Mapped[Decimal | None] = mapped_column(Money)
    requested_volume: Mapped[Decimal | None] = mapped_column(Money)             # only when a person asked for a size
    higher_timeframe_context: Mapped[dict | None] = mapped_column(JsonType)
    ai_summary: Mapped[str | None] = mapped_column(Text)
    ai_confidence: Mapped[Decimal | None] = mapped_column(Money)               # as supplied; not a probability of profit
    evidence: Mapped[list | None] = mapped_column(JsonType)
    market_conditions: Mapped[dict | None] = mapped_column(JsonType)
    analysis_version: Mapped[str] = mapped_column(String(64), nullable=False)
    source_refs: Mapped[list | None] = mapped_column(JsonType)
    schema_version: Mapped[str] = mapped_column(String(8), nullable=False)
    payload: Mapped[dict] = mapped_column(JsonType, nullable=False)             # the original payload, kept for audit
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[str] = mapped_column(String(80), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)


# ---- approvals -------------------------------------------------------------------------------------------------------------
class ApprovalRequest(Base):
    __tablename__ = "approval_requests"
    __table_args__ = (
        Index("uq_approval_requests_active_idea", "idea_id", unique=True,
              postgresql_where=text(_ACTIVE_APPROVAL_SQL), sqlite_where=text(_ACTIVE_APPROVAL_SQL)),
        Index("ix_approval_requests_status_expires", "status", "expires_at"),
    )
    approval_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    idea_id: Mapped[str] = mapped_column(ForeignKey("trade_ideas.idea_id"), nullable=False, index=True)
    idea_version: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_approval_id: Mapped[str | None] = mapped_column(ForeignKey("approval_requests.approval_id"))
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)    # optimistic concurrency
    assigned_user_id: Mapped[str | None] = mapped_column(ForeignKey(USER_FK))
    review_started_by: Mapped[str | None] = mapped_column(ForeignKey(USER_FK))
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    direction: Mapped[str] = mapped_column(String(4), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(4), nullable=False)
    strategy: Mapped[str] = mapped_column(String(64), nullable=False)
    entry: Mapped[Decimal] = mapped_column(Money, nullable=False)
    stop_loss: Mapped[Decimal] = mapped_column(Money, nullable=False)
    take_profit: Mapped[Decimal] = mapped_column(Money, nullable=False)
    position_size: Mapped[Decimal | None] = mapped_column(Money)                # Module 06 final volume
    risk_amount: Mapped[Decimal | None] = mapped_column(Money)
    risk_pct: Mapped[Decimal | None] = mapped_column(Money)
    risk_reward: Mapped[Decimal | None] = mapped_column(Money)
    ai_confidence: Mapped[Decimal | None] = mapped_column(Money)
    risk_decision: Mapped[str] = mapped_column(String(8), nullable=False)      # PASS | REVIEW | BLOCK | ERROR
    risk_assessment_id: Mapped[str | None] = mapped_column(String(36))
    risk_assessed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    risk_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    risk_config_version: Mapped[int | None] = mapped_column(Integer)
    risk_warnings: Mapped[list | None] = mapped_column(JsonType)
    evidence_snapshot_id: Mapped[str | None] = mapped_column(String(36))
    evidence_hash: Mapped[str | None] = mapped_column(String(64))
    analysis_version: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    account_login: Mapped[str | None] = mapped_column(String(32))
    approvals_required: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    expiring_notified_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    status_changed_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    status_reason: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class ApprovalDecision(Base):
    """Append-only. A person decides once per request; only one decision per request can be final."""
    __tablename__ = "approval_decisions"
    __table_args__ = (
        UniqueConstraint("approval_id", "user_id", name="uq_approval_decisions_user"),
        Index("uq_approval_decisions_final", "approval_id", unique=True,
              postgresql_where=text("is_final"), sqlite_where=text("is_final = 1")),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    approval_id: Mapped[str] = mapped_column(ForeignKey("approval_requests.approval_id"), nullable=False, index=True)
    decision: Mapped[str] = mapped_column(String(24), nullable=False)          # APPROVED | REJECTED | MODIFICATION_REQUESTED
    is_final: Mapped[bool] = mapped_column(Boolean, nullable=False)
    user_id: Mapped[str] = mapped_column(ForeignKey(USER_FK), nullable=False)
    username: Mapped[str] = mapped_column(String(64), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    method: Mapped[str] = mapped_column(String(16), nullable=False)            # web | api
    decided_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    evidence_snapshot_id: Mapped[str | None] = mapped_column(String(36))
    evidence_hash: Mapped[str | None] = mapped_column(String(64))
    risk_assessment_id: Mapped[str | None] = mapped_column(String(36))
    risk_decision: Mapped[str | None] = mapped_column(String(8))
    analysis_version: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    client_request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(48))
    comment: Mapped[str | None] = mapped_column(Text)
    acknowledged_warnings: Mapped[list | None] = mapped_column(JsonType)
    confirmation: Mapped[dict | None] = mapped_column(JsonType)                 # what the person confirmed on screen
    record_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class ApprovalEvidenceSnapshot(Base):
    """Append-only. The evidence exactly as presented; decisions reference its id and hash."""
    __tablename__ = "approval_evidence_snapshots"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    approval_id: Mapped[str | None] = mapped_column(String(36), index=True)
    idea_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    idea_version: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[dict] = mapped_column(JsonType, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    completeness: Mapped[dict | None] = mapped_column(JsonType)                 # per section: available | missing | stale
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class RiskAssessmentSnapshot(Base):
    """Module 06 results as received. Module 07 never recomputes risk."""
    __tablename__ = "approval_risk_snapshots"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    approval_id: Mapped[str | None] = mapped_column(String(36), index=True)
    management_request_id: Mapped[str | None] = mapped_column(String(36))
    idea_id: Mapped[str | None] = mapped_column(String(64), index=True)
    idea_version: Mapped[int | None] = mapped_column(Integer)
    purpose: Mapped[str] = mapped_column(String(16), nullable=False)            # intake | revalidate | approval | handoff
    assessment_id: Mapped[str | None] = mapped_column(String(36))
    decision: Mapped[str] = mapped_column(String(8), nullable=False)
    final_volume: Mapped[Decimal | None] = mapped_column(Money)
    risk_amount: Mapped[Decimal | None] = mapped_column(Money)
    risk_pct: Mapped[Decimal | None] = mapped_column(Money)
    config_version: Mapped[int | None] = mapped_column(Integer)
    assessed_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    payload: Mapped[dict] = mapped_column(JsonType, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class ModificationRequest(Base):
    __tablename__ = "modification_requests"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    approval_id: Mapped[str] = mapped_column(ForeignKey("approval_requests.approval_id"), nullable=False, index=True)
    requested_by: Mapped[str] = mapped_column(ForeignKey(USER_FK), nullable=False)
    kinds: Mapped[list] = mapped_column(JsonType, nullable=False)
    changes: Mapped[dict | None] = mapped_column(JsonType)                      # new parameters, as decimal strings
    previous: Mapped[dict] = mapped_column(JsonType, nullable=False)            # the parameters being replaced
    comment: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), nullable=False)             # revised | awaiting_module05 | closed
    new_idea_version: Mapped[int | None] = mapped_column(Integer)
    new_approval_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


# ---- execution handoff (to Module 09) -------------------------------------------------------------------------------------
class ExecutionHandoff(Base):
    __tablename__ = "execution_handoffs"
    execution_request_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)               # trade | management
    approval_id: Mapped[str | None] = mapped_column(ForeignKey("approval_requests.approval_id"), unique=True)
    management_request_id: Mapped[str | None] = mapped_column(String(36), unique=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)             # pending | accepted | submitted | filled | partial | failed | rejected
    payload: Mapped[dict] = mapped_column(JsonType, nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    account_login: Mapped[str | None] = mapped_column(String(32))
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    picked_up_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class ExecutionResult(Base):
    """What Module 09 reported. Deduplicated by its event id."""
    __tablename__ = "execution_results"
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    execution_request_id: Mapped[str] = mapped_column(ForeignKey("execution_handoffs.execution_request_id"), nullable=False,
                                                      index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)             # accepted | rejected | submitted | filled | partial | failed
    broker_ticket: Mapped[str | None] = mapped_column(String(32))
    position_id: Mapped[str | None] = mapped_column(String(32))
    filled_volume: Mapped[Decimal | None] = mapped_column(Money)
    fill_price: Mapped[Decimal | None] = mapped_column(Money)
    message: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JsonType, nullable=False)
    reported_by: Mapped[str] = mapped_column(String(80), nullable=False)
    reported_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)


# ---- positions and trade management -----------------------------------------------------------------------------------------
class ManagedPosition(Base):
    """Positions as supplied by the position source (Module 09, or the read-only account mirror until it exists)."""
    __tablename__ = "managed_positions"
    position_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    account_login: Mapped[str | None] = mapped_column(String(32))
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    side: Mapped[str] = mapped_column(String(4), nullable=False)               # BUY | SELL
    volume: Mapped[Decimal] = mapped_column(Money, nullable=False)
    entry_price: Mapped[Decimal] = mapped_column(Money, nullable=False)
    current_price: Mapped[Decimal | None] = mapped_column(Money)
    stop_loss: Mapped[Decimal | None] = mapped_column(Money)
    take_profit: Mapped[Decimal | None] = mapped_column(Money)
    unrealized_pnl: Mapped[Decimal | None] = mapped_column(Money)
    realized_pnl: Mapped[Decimal | None] = mapped_column(Money)
    status: Mapped[str] = mapped_column(String(16), nullable=False)             # OPEN | PARTIALLY_CLOSED | PENDING_CLOSE | CLOSED | FAILED | UNKNOWN
    opened_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    source: Mapped[str] = mapped_column(String(16), nullable=False)             # module09 | account_mirror
    source_updated_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    approval_id: Mapped[str | None] = mapped_column(String(36))
    execution_request_id: Mapped[str | None] = mapped_column(String(36))
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class TradeManagementRequest(Base):
    __tablename__ = "trade_management_requests"
    __table_args__ = (
        Index("uq_trade_management_active_position", "position_id", unique=True,
              postgresql_where=text(_ACTIVE_MANAGEMENT_SQL), sqlite_where=text(_ACTIVE_MANAGEMENT_SQL)),
    )
    request_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    position_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    account_login: Mapped[str | None] = mapped_column(String(32))
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    action: Mapped[str] = mapped_column(String(24), nullable=False)
    params: Mapped[dict] = mapped_column(JsonType, nullable=False)
    position_snapshot: Mapped[dict] = mapped_column(JsonType, nullable=False)   # status, entry, SL, TP, volume when requested
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[dict | None] = mapped_column(JsonType)
    risk_implications: Mapped[dict | None] = mapped_column(JsonType)
    priority: Mapped[str] = mapped_column(String(12), nullable=False)          # normal | emergency
    requested_by: Mapped[str] = mapped_column(ForeignKey(USER_FK), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    status_changed_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    status_reason: Mapped[str | None] = mapped_column(Text)


class TradeManagementDecision(Base):
    __tablename__ = "trade_management_decisions"
    __table_args__ = (
        UniqueConstraint("request_id", "user_id", name="uq_trade_management_decisions_user"),
        Index("uq_trade_management_decisions_final", "request_id", unique=True,
              postgresql_where=text("is_final"), sqlite_where=text("is_final = 1")),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    request_id: Mapped[str] = mapped_column(ForeignKey("trade_management_requests.request_id"), nullable=False, index=True)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)          # APPROVED | REJECTED
    is_final: Mapped[bool] = mapped_column(Boolean, nullable=False)
    user_id: Mapped[str] = mapped_column(ForeignKey(USER_FK), nullable=False)
    username: Mapped[str] = mapped_column(String(64), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    client_request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    comment: Mapped[str | None] = mapped_column(Text)
    confirmation: Mapped[dict | None] = mapped_column(JsonType)
    record_hash: Mapped[str] = mapped_column(String(64), nullable=False)


# ---- notifications ---------------------------------------------------------------------------------------------------------
class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    dedupe_key: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    event_type: Mapped[str] = mapped_column(String(48), nullable=False)
    priority: Mapped[str] = mapped_column(String(8), nullable=False)            # low | normal | high | critical
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    approval_id: Mapped[str | None] = mapped_column(String(36), index=True)
    management_request_id: Mapped[str | None] = mapped_column(String(36))
    position_id: Mapped[str | None] = mapped_column(String(32))
    recipient_user_id: Mapped[str | None] = mapped_column(ForeignKey(USER_FK))   # NULL = every user who may view approvals
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, index=True)


class NotificationDelivery(Base):
    """One row per notification, user and channel. The in-app row carries the read state."""
    __tablename__ = "notification_deliveries"
    __table_args__ = (UniqueConstraint("notification_id", "user_id", "channel", name="uq_notification_deliveries"),
                      Index("ix_notification_deliveries_due", "status", "next_attempt_at"))
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    notification_id: Mapped[str] = mapped_column(ForeignKey("notifications.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[str] = mapped_column(ForeignKey(USER_FK, ondelete="CASCADE"), nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)            # in_app | email | telegram
    status: Mapped[str] = mapped_column(String(16), nullable=False)             # delivered | pending | sent | failed | dead | not_configured
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_error: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    read_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


# ---- administration --------------------------------------------------------------------------------------------------------
class ReviewerAssignment(Base):
    __tablename__ = "reviewer_assignments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey(USER_FK), nullable=False, index=True)
    symbol: Mapped[str | None] = mapped_column(String(32))                     # NULL = any
    strategy: Mapped[str | None] = mapped_column(String(64))
    timeframe: Mapped[str | None] = mapped_column(String(4))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class ApprovalPolicy(Base):
    """Append-only versions. The highest version is the active policy."""
    __tablename__ = "approval_policies"
    version: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    policy: Mapped[dict] = mapped_column(JsonType, nullable=False)
    policy_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class ApprovalAuditLog(Base):
    """Append-only and hash-chained: each row's hash covers its content and the previous row's hash."""
    __tablename__ = "approval_audit_logs"
    seq: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    actor: Mapped[str] = mapped_column(String(80), nullable=False)
    actor_kind: Mapped[str] = mapped_column(String(8), nullable=False)          # user | service | system
    action: Mapped[str] = mapped_column(String(48), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False)
    approval_id: Mapped[str | None] = mapped_column(String(36), index=True)
    idea_id: Mapped[str | None] = mapped_column(String(64), index=True)
    previous_state: Mapped[str | None] = mapped_column(String(24))
    new_state: Mapped[str | None] = mapped_column(String(24))
    reason: Mapped[str | None] = mapped_column(Text)
    correlation_id: Mapped[str | None] = mapped_column(String(64))
    evidence_snapshot_id: Mapped[str | None] = mapped_column(String(36))
    details: Mapped[dict | None] = mapped_column(JsonType)
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    hash: Mapped[str] = mapped_column(String(64), nullable=False)


# ---- reliability -----------------------------------------------------------------------------------------------------------
class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (UniqueConstraint("scope", "key", name="uq_idempotency_records"),)
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    scope: Mapped[str] = mapped_column(String(64), nullable=False)              # e.g. approve:<approval_id>, event:inbound
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    response: Mapped[dict | None] = mapped_column(JsonType)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)


class OutboxEvent(Base):
    """Written in the same transaction as the change it describes, so a committed change cannot lose its event."""
    __tablename__ = "outbox_events"
    __table_args__ = (Index("ix_outbox_events_due", "status", "next_attempt_at"),)
    seq: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    event_version: Mapped[str] = mapped_column(String(8), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    source_module: Mapped[str] = mapped_column(String(16), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    causation_id: Mapped[str | None] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    payload: Mapped[dict] = mapped_column(JsonType, nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False)             # pending | delivered | dead
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_error: Mapped[str | None] = mapped_column(Text)
    delivered_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
