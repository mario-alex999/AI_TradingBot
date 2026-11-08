"""Module 05 tables (share Module 01's ``Base``). PostgreSQL DDL: sql/migrations/0007_decision_engine.sql."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from aurum.database.models import Base, BigIntPK, JsonType, UTCDateTime, utcnow

CANDIDATE_FK = "trade_candidates.candidate_uid"


class DecisionRun(Base):
    __tablename__ = "decision_runs"
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    run_uid: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    as_of: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)            # live | replay
    engine_version: Mapped[str] = mapped_column(String(16), nullable=False)
    config_fingerprint: Mapped[str] = mapped_column(String(32), nullable=False)
    input_hash: Mapped[str | None] = mapped_column(String(64))
    availability: Mapped[dict | None] = mapped_column(JsonType)
    summary: Mapped[dict | None] = mapped_column(JsonType)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), nullable=False)          # ok | no_setup | failed
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    __table_args__ = (Index("ix_decision_runs_symbol_asof", "symbol", "as_of"),)


class TradeCandidate(Base):
    __tablename__ = "trade_candidates"
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    candidate_uid: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    idempotent_key: Mapped[str] = mapped_column(String(64), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(4), nullable=False)
    template: Mapped[str] = mapped_column(String(48), nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)        # long | short
    as_of: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    entry_low: Mapped[float] = mapped_column(Float, nullable=False)
    entry_high: Mapped[float] = mapped_column(Float, nullable=False)
    stop_loss: Mapped[float] = mapped_column(Float, nullable=False)
    take_profit: Mapped[float] = mapped_column(Float, nullable=False)
    take_profit2: Mapped[float | None] = mapped_column(Float)
    risk_reward: Mapped[float] = mapped_column(Float, nullable=False)
    suggested_volume: Mapped[float | None] = mapped_column(Float)
    volume_reason: Mapped[str | None] = mapped_column(Text)
    deterministic_score: Mapped[float] = mapped_column(Float, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    grade: Mapped[str] = mapped_column(String(1), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    rejected_by: Mapped[str | None] = mapped_column(String(48))
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    invalidation: Mapped[str] = mapped_column(Text, nullable=False)
    market_condition: Mapped[str] = mapped_column(String(64), nullable=False)
    first_seen_run: Mapped[str] = mapped_column(String(36), nullable=False)
    last_seen_run: Mapped[str] = mapped_column(String(36), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    availability: Mapped[dict | None] = mapped_column(JsonType)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    __table_args__ = (UniqueConstraint("idempotent_key", name="uq_trade_candidates_key"),
                      Index("ix_trade_candidates_symbol_status_created", "symbol", "status", "created_at"),)


class CandidateEvidence(Base):
    __tablename__ = "candidate_evidence"
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    candidate_uid: Mapped[str] = mapped_column(ForeignKey(CANDIDATE_FK, ondelete="CASCADE"), nullable=False, index=True)
    component: Mapped[str] = mapped_column(String(48), nullable=False)
    points: Mapped[float] = mapped_column(Float, nullable=False)
    max_points: Mapped[float] = mapped_column(Float, nullable=False)
    sentence: Mapped[str] = mapped_column(Text, nullable=False)
    sources: Mapped[list | None] = mapped_column(JsonType)


class CandidateGateResult(Base):
    __tablename__ = "candidate_gate_results"
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    candidate_uid: Mapped[str] = mapped_column(ForeignKey(CANDIDATE_FK, ondelete="CASCADE"), nullable=False, index=True)
    gate: Mapped[str] = mapped_column(String(32), nullable=False)
    result: Mapped[str] = mapped_column(String(8), nullable=False)           # pass | warn | fail
    evidence: Mapped[dict | None] = mapped_column(JsonType)
    thresholds: Mapped[dict | None] = mapped_column(JsonType)
    checked_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class CandidateAiReview(Base):
    """Named apart from ``ai_reviews``, which holds the instrument-level reviews of aurum/ai_review."""
    __tablename__ = "candidate_ai_reviews"
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    candidate_uid: Mapped[str] = mapped_column(ForeignKey(CANDIDATE_FK, ondelete="CASCADE"), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(16), nullable=False)
    model: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), nullable=False)          # reviewed | unavailable
    reason: Mapped[str | None] = mapped_column(String(32))
    verdict: Mapped[str | None] = mapped_column(String(16))                  # agree | caution | reject
    adjustment: Mapped[float | None] = mapped_column(Float)
    review: Mapped[dict | None] = mapped_column(JsonType)
    payload_hash: Mapped[str | None] = mapped_column(String(64))
    budget_month: Mapped[str | None] = mapped_column(String(7))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    cost_microusd: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    api_called: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    __table_args__ = (Index("ix_candidate_ai_reviews_lookup", "candidate_uid", "input_hash", "prompt_version"),
                      Index("ix_candidate_ai_reviews_created", "created_at"),)


class CandidateStatusHistory(Base):
    __tablename__ = "candidate_status_history"
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    candidate_uid: Mapped[str] = mapped_column(ForeignKey(CANDIDATE_FK, ondelete="CASCADE"), nullable=False, index=True)
    from_status: Mapped[str | None] = mapped_column(String(16))
    to_status: Mapped[str] = mapped_column(String(16), nullable=False)
    actor: Mapped[str] = mapped_column(String(80), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class CandidateOutcome(Base):
    __tablename__ = "candidate_outcomes"
    candidate_uid: Mapped[str] = mapped_column(ForeignKey(CANDIDATE_FK, ondelete="CASCADE"), primary_key=True)
    touched: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    filled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    result: Mapped[str] = mapped_column(String(16), nullable=False)          # tp | sl | expired | ambiguous | open
    r_multiple: Mapped[float | None] = mapped_column(Float)
    mfe_r: Mapped[float | None] = mapped_column(Float)
    mae_r: Mapped[float | None] = mapped_column(Float)
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class BacktestRun(Base):
    __tablename__ = "backtest_runs"
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    run_uid: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    config_fingerprint: Mapped[str] = mapped_column(String(32), nullable=False)
    symbols: Mapped[list] = mapped_column(JsonType, nullable=False)
    period_start: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    period_end: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    split_date: Mapped[datetime | None] = mapped_column(UTCDateTime)
    metrics: Mapped[dict] = mapped_column(JsonType, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class BacktestTrade(Base):
    __tablename__ = "backtest_trades"
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    run_uid: Mapped[str] = mapped_column(ForeignKey("backtest_runs.run_uid", ondelete="CASCADE"), nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    template: Mapped[str] = mapped_column(String(48), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(4), nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    as_of: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    session: Mapped[str | None] = mapped_column(String(32))
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    result: Mapped[str] = mapped_column(String(16), nullable=False)
    r_multiple: Mapped[float] = mapped_column(Float, nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
