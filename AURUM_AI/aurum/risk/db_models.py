"""Module 06 tables (share Module 01's ``Base``). PostgreSQL DDL: sql/migrations/0008_risk_engine.sql.

Money and volumes are NUMERIC(24, 8) and are read back as Decimal. Audit, lock history, rule evaluations and assessments
are append-only from the application's point of view: only an assessment's ``state`` (active -> consumed | cancelled) and
a lock's release fields are ever updated, and each such change is written to the audit log.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from aurum.database.models import Base, BigIntPK, JsonType, UTCDateTime, utcnow

Money = Numeric(24, 8, asdecimal=True)


class RiskConfigVersion(Base):
    """RiskConfiguration + RiskConfigurationVersion: each row is one complete, immutable configuration; the active
    configuration is the highest version."""
    __tablename__ = "risk_config_versions"
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    profile: Mapped[str] = mapped_column(String(16), nullable=False)
    config: Mapped[dict] = mapped_column(JsonType, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(32), nullable=False)
    created_by: Mapped[str] = mapped_column(String(80), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    weakened: Mapped[list | None] = mapped_column(JsonType)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class RiskAssessment(Base):
    """TradeRiskAssessment. (idempotency_key, attempt) is unique: a repeat inside the validity window returns the stored row,
    a repeat after expiry creates attempt + 1."""
    __tablename__ = "risk_assessments"
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    assessment_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    trade_idea_id: Mapped[str | None] = mapped_column(String(64))
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    asset_class: Mapped[str] = mapped_column(String(32), nullable=False)
    direction: Mapped[str] = mapped_column(String(4), nullable=False)
    strategy: Mapped[str | None] = mapped_column(String(64))
    decision: Mapped[str] = mapped_column(String(8), nullable=False)               # PASS | REVIEW | BLOCK | ERROR
    state: Mapped[str] = mapped_column(String(12), nullable=False, default="active")  # active | consumed | cancelled
    requested_volume: Mapped[Decimal | None] = mapped_column(Money)
    recommended_volume: Mapped[Decimal | None] = mapped_column(Money)
    final_volume: Mapped[Decimal | None] = mapped_column(Money)
    risk_pct: Mapped[Decimal | None] = mapped_column(Money)
    monetary_risk: Mapped[Decimal | None] = mapped_column(Money)
    potential_reward: Mapped[Decimal | None] = mapped_column(Money)
    risk_reward: Mapped[Decimal | None] = mapped_column(Money)
    adjusted_risk_reward: Mapped[Decimal | None] = mapped_column(Money)
    reserved_risk: Mapped[Decimal | None] = mapped_column(Money)
    account_currency: Mapped[str | None] = mapped_column(String(16))
    account_status: Mapped[str] = mapped_column(String(16), nullable=False)
    portfolio_status: Mapped[str] = mapped_column(String(16), nullable=False)
    correlation_status: Mapped[str] = mapped_column(String(16), nullable=False)
    market_status: Mapped[str] = mapped_column(String(16), nullable=False)
    config_version: Mapped[int] = mapped_column(Integer, nullable=False)
    engine_version: Mapped[str] = mapped_column(String(16), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    input_snapshot: Mapped[dict] = mapped_column(JsonType, nullable=False)
    result: Mapped[dict] = mapped_column(JsonType, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    created_by: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    state_changed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    __table_args__ = (UniqueConstraint("idempotency_key", "attempt", name="uq_risk_assessments_key_attempt"),
                      Index("ix_risk_assessments_created", "created_at"),
                      Index("ix_risk_assessments_idea", "trade_idea_id"),
                      Index("ix_risk_assessments_live", "decision", "state", "expires_at"),
                      Index("ix_risk_assessments_symbol_created", "symbol", "created_at"))


class RiskRuleEvaluation(Base):
    __tablename__ = "risk_rule_evaluations"
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    assessment_id: Mapped[str] = mapped_column(ForeignKey("risk_assessments.assessment_id", ondelete="CASCADE"), nullable=False)
    rule_id: Mapped[str] = mapped_column(String(8), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    category: Mapped[str] = mapped_column(String(16), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    outcome: Mapped[str] = mapped_column(String(8), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(48), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[dict | None] = mapped_column(JsonType)
    thresholds: Mapped[dict | None] = mapped_column(JsonType)
    __table_args__ = (Index("ix_risk_rule_evals_assessment", "assessment_id"),
                      Index("ix_risk_rule_evals_rule_outcome", "rule_id", "outcome"))


class RiskAccountSnapshot(Base):
    """AccountRiskSnapshot: account metrics as Module 06 computed them (per assessment, or from GET /risk/account)."""
    __tablename__ = "risk_account_snapshots"
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    assessment_id: Mapped[str | None] = mapped_column(String(36))
    computed_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    source_captured_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    currency: Mapped[str | None] = mapped_column(String(16))
    balance: Mapped[Decimal | None] = mapped_column(Money)
    equity: Mapped[Decimal | None] = mapped_column(Money)
    drawdown_pct: Mapped[Decimal | None] = mapped_column(Money)
    daily_loss_pct: Mapped[Decimal | None] = mapped_column(Money)
    weekly_loss_pct: Mapped[Decimal | None] = mapped_column(Money)
    monthly_loss_pct: Mapped[Decimal | None] = mapped_column(Money)
    margin_utilization_pct: Mapped[Decimal | None] = mapped_column(Money)
    consecutive_losses: Mapped[int | None] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JsonType, nullable=False)
    __table_args__ = (Index("ix_risk_account_snapshots_time", "computed_at"),)


class RiskPortfolioSnapshot(Base):
    __tablename__ = "risk_portfolio_snapshots"
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    assessment_id: Mapped[str | None] = mapped_column(String(36))
    computed_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    total_open_risk: Mapped[Decimal | None] = mapped_column(Money)
    total_open_risk_pct: Mapped[Decimal | None] = mapped_column(Money)
    positions: Mapped[int] = mapped_column(Integer, nullable=False)
    reservations: Mapped[int] = mapped_column(Integer, nullable=False)
    bounded: Mapped[bool] = mapped_column(Boolean, nullable=False)
    payload: Mapped[dict] = mapped_column(JsonType, nullable=False)
    __table_args__ = (Index("ix_risk_portfolio_snapshots_time", "computed_at"),)


class RiskCorrelationSnapshot(Base):
    __tablename__ = "risk_correlation_snapshots"
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    computed_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    symbol_a: Mapped[str] = mapped_column(String(32), nullable=False)
    symbol_b: Mapped[str] = mapped_column(String(32), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(4), nullable=False)
    lookback_bars: Mapped[int] = mapped_column(Integer, nullable=False)
    samples: Mapped[int] = mapped_column(Integer, nullable=False)
    coefficient: Mapped[Decimal | None] = mapped_column(Money)
    last_common_bar: Mapped[datetime | None] = mapped_column(UTCDateTime)
    reliable: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (Index("ix_risk_corr_pair_time", "symbol_a", "symbol_b", "computed_at"),)


class RiskPeriodRecord(Base):
    """DailyRiskRecord / WeeklyRiskRecord / MonthlyRiskRecord (``kind`` = day | week | month). Holds the persisted starting
    value so a period's loss reference never drifts, and when its limit was first reached."""
    __tablename__ = "risk_period_records"
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    label: Mapped[str] = mapped_column(String(16), nullable=False)
    basis: Mapped[str] = mapped_column(String(8), nullable=False)
    start_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    end_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    start_value: Mapped[Decimal | None] = mapped_column(Money)
    start_source: Mapped[str] = mapped_column(Text, nullable=False)
    start_uncertain: Mapped[bool] = mapped_column(Boolean, nullable=False)
    last_loss_pct: Mapped[Decimal | None] = mapped_column(Money)
    limit_reached_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    __table_args__ = (UniqueConstraint("kind", "label", "basis", name="uq_risk_period_records"),)


class RiskLock(Base):
    """A persisted lock. ``dedupe_key`` is unique while the lock is active (cleared on release) so one condition can never
    create two active locks. Restarts never touch this table."""
    __tablename__ = "risk_locks"
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    lock_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    dedupe_key: Mapped[str | None] = mapped_column(String(160), unique=True)
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    scope_value: Mapped[str | None] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    trigger_source: Mapped[str] = mapped_column(String(48), nullable=False)
    rule_id: Mapped[str | None] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="active")   # active | released | expired
    activated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    activated_by: Mapped[str] = mapped_column(String(80), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    released_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    released_by: Mapped[str | None] = mapped_column(String(80))
    release_reason: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict | None] = mapped_column(JsonType)
    __table_args__ = (Index("ix_risk_locks_status", "status", "activated_at"),)


class RiskLockHistory(Base):
    __tablename__ = "risk_lock_history"
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    lock_id: Mapped[str] = mapped_column(ForeignKey("risk_locks.lock_id", ondelete="CASCADE"), nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)       # activated | released | expired
    at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    actor: Mapped[str] = mapped_column(String(80), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict | None] = mapped_column(JsonType)
    __table_args__ = (Index("ix_risk_lock_history_lock", "lock_id", "at"),)


class RiskLossStreak(Base):
    """ConsecutiveLossRecord: a streak that reached the limit, with the triggering trades."""
    __tablename__ = "risk_loss_streaks"
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    detected_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    streak: Mapped[int] = mapped_column(Integer, nullable=False)
    trade_ids: Mapped[list] = mapped_column(JsonType, nullable=False)
    lock_id: Mapped[str | None] = mapped_column(String(36))
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)


class RiskAuditLog(Base):
    """Append-only, hash-chained: each row stores the hash of the previous row, so an edited or deleted row is detectable
    (``python -m aurum.cli risk audit verify``)."""
    __tablename__ = "risk_audit_log"
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    actor: Mapped[str] = mapped_column(String(80), nullable=False)
    action: Mapped[str] = mapped_column(String(48), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[str | None] = mapped_column(String(64))
    correlation_id: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict | None] = mapped_column(JsonType)
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    hash: Mapped[str] = mapped_column(String(64), nullable=False)
    __table_args__ = (Index("ix_risk_audit_at", "at"), Index("ix_risk_audit_entity", "entity_type", "entity_id"))


class RiskEvent(Base):
    """Outbox: events Module 06 publishes. Written in the same transaction as the decision, so a decision can never exist
    without its event, and delivery failures are retried and finally dead-lettered, never dropped."""
    __tablename__ = "risk_events"
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    event_type: Mapped[str] = mapped_column(String(48), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    correlation_id: Mapped[str | None] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    entity_id: Mapped[str | None] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict] = mapped_column(JsonType, nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="pending")   # pending | delivered | dead
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_error: Mapped[str | None] = mapped_column(Text)
    delivered_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    __table_args__ = (Index("ix_risk_events_status", "status", "next_attempt_at"),)


class RiskInboxEvent(Base):
    """Inbox: events received from other modules / n8n. ``event_id`` is unique, so a duplicate delivery is recognised and
    never processed twice."""
    __tablename__ = "risk_inbox"
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    event_type: Mapped[str] = mapped_column(String(48), nullable=False)
    received_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    status: Mapped[str] = mapped_column(String(10), nullable=False)        # processed | ignored | failed | dead
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    last_error: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JsonType, nullable=False)
    result: Mapped[dict | None] = mapped_column(JsonType)
