"""Module 03 tables (share Module 01's ``Base``). PostgreSQL DDL: sql/migrations/0003_technical_analysis.sql."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Float, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from aurum.database.models import Base, BigIntPK, JsonType, UTCDateTime, utcnow


class TaAnalysisRun(Base):
    __tablename__ = "ta_analysis_runs"
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    run_uid: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    run_type: Mapped[str] = mapped_column(String(16), nullable=False)          # timeframe | confluence
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    timeframe: Mapped[str | None] = mapped_column(String(32))                  # "D1,H4,..." for confluence runs
    as_of: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    generated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    engine_version: Mapped[str] = mapped_column(String(16), nullable=False)
    config_fingerprint: Mapped[str] = mapped_column(String(32), nullable=False)
    provisional: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    candles_used: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    bias: Mapped[str | None] = mapped_column(String(16))
    score: Mapped[float | None] = mapped_column(Float)
    grade: Mapped[str | None] = mapped_column(String(8))
    summary: Mapped[dict | None] = mapped_column(JsonType)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    __table_args__ = (Index("ix_ta_runs_symbol_tf_asof", "symbol", "timeframe", "as_of"),)


class TaStructureEvent(Base):
    __tablename__ = "ta_structure_events"
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(4), nullable=False)
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    event_type: Mapped[str] = mapped_column(String(8), nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    broken_level: Mapped[float] = mapped_column(Float, nullable=False)
    broken_swing_time: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    break_candle_time: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    confirmed_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    displacement_strength: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    detail: Mapped[dict | None] = mapped_column(JsonType)
    first_run_uid: Mapped[str] = mapped_column(String(36), nullable=False)
    last_run_uid: Mapped[str] = mapped_column(String(36), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    __table_args__ = (UniqueConstraint("symbol", "timeframe", "scope", "event_type", "direction", "break_candle_time",
                                       name="uq_ta_structure_events"),
                      Index("ix_ta_events_lookup", "symbol", "timeframe", "confirmed_at"))


class TaZone(Base):
    __tablename__ = "ta_zones"
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    zone_uid: Mapped[str] = mapped_column(String(96), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(4), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)      # order_block | fair_value_gap | ...
    direction: Mapped[str | None] = mapped_column(String(16))
    top: Mapped[float] = mapped_column(Float, nullable=False)
    bottom: Mapped[float] = mapped_column(Float, nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    detected_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    detail: Mapped[dict | None] = mapped_column(JsonType)
    first_run_uid: Mapped[str] = mapped_column(String(36), nullable=False)
    last_run_uid: Mapped[str] = mapped_column(String(36), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    __table_args__ = (UniqueConstraint("symbol", "timeframe", "kind", "zone_uid", name="uq_ta_zones"),
                      Index("ix_ta_zones_lookup", "symbol", "timeframe", "kind", "state"))
