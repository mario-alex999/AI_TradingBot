"""SQLAlchemy ORM models. ``sql/schema.sql`` is the PostgreSQL/TimescaleDB DDL equivalent;
``create_all`` on these models is used for SQLite (dev/tests) and as a fallback."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (JSON, BigInteger, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text,
                        UniqueConstraint, func)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

JsonType = JSON().with_variant(JSONB(), "postgresql")
BigIntPK = BigInteger().with_variant(Integer(), "sqlite")  # SQLite autoincrement needs INTEGER


class UTCDateTime(TypeDecorator):
    """Timezone-aware UTC datetime on every backend (SQLite drops tzinfo; this restores it).
    Naive datetimes are rejected so local times can never sneak in."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Any) -> Any:
        if value is None:
            return None
        if hasattr(value, "to_pydatetime"):
            value = value.to_pydatetime()
        if value.tzinfo is None:
            raise ValueError("Naive datetime passed to the database; AURUM stores UTC-aware datetimes only.")
        value = value.astimezone(timezone.utc)
        return value.replace(tzinfo=None) if dialect.name == "sqlite" else value

    def process_result_value(self, value: Any, dialect: Any) -> Any:
        if value is None:
            return None
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Instrument(Base):
    __tablename__ = "instruments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    broker_symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    asset_class: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
    description: Mapped[str | None] = mapped_column(Text)
    is_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow)


class InstrumentSpec(Base):
    __tablename__ = "instrument_specs"
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id", ondelete="CASCADE"), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    collection_run_id: Mapped[str | None] = mapped_column(String(36))
    broker_symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    digits: Mapped[int | None] = mapped_column(Integer)
    point: Mapped[float | None] = mapped_column(Float)
    contract_size: Mapped[float | None] = mapped_column(Float)
    tick_size: Mapped[float | None] = mapped_column(Float)
    tick_value: Mapped[float | None] = mapped_column(Float)
    volume_min: Mapped[float | None] = mapped_column(Float)
    volume_max: Mapped[float | None] = mapped_column(Float)
    volume_step: Mapped[float | None] = mapped_column(Float)
    spread_points: Mapped[float | None] = mapped_column(Float)
    bid: Mapped[float | None] = mapped_column(Float)
    ask: Mapped[float | None] = mapped_column(Float)
    trade_mode: Mapped[str | None] = mapped_column(String(16))
    trading_available: Mapped[bool | None] = mapped_column(Boolean)
    currency_base: Mapped[str | None] = mapped_column(String(16))
    currency_profit: Mapped[str | None] = mapped_column(String(16))
    raw: Mapped[dict | None] = mapped_column(JsonType)
    __table_args__ = (UniqueConstraint("instrument_id", "captured_at", name="uq_instrument_specs_snapshot"),
                      Index("ix_instrument_specs_instrument_time", "instrument_id", "captured_at"))


class MarketCandle(Base):
    __tablename__ = "market_candles"
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), primary_key=True)
    timeframe: Mapped[str] = mapped_column(String(4), primary_key=True)
    ts_utc: Mapped[datetime] = mapped_column(UTCDateTime, primary_key=True)
    open: Mapped[float | None] = mapped_column(Float)     # nullable: invalid rows are kept, never dropped
    high: Mapped[float | None] = mapped_column(Float)
    low: Mapped[float | None] = mapped_column(Float)
    close: Mapped[float | None] = mapped_column(Float)
    tick_volume: Mapped[int | None] = mapped_column(BigInteger)
    real_volume: Mapped[int | None] = mapped_column(BigInteger)
    spread: Mapped[int | None] = mapped_column(Integer)
    is_complete: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    quality_status: Mapped[str] = mapped_column(String(16), nullable=False, default="valid")
    quality_flags: Mapped[str | None] = mapped_column(Text)
    broker_symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    mt5_time: Mapped[int] = mapped_column(BigInteger, nullable=False)    # original MT5 server timestamp
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="mt5")
    collection_run_id: Mapped[str | None] = mapped_column(String(36))
    ingested_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    __table_args__ = (Index("ix_market_candles_tf_time", "timeframe", "ts_utc"),
                      Index("ix_market_candles_inst_tf_time_desc", "instrument_id", "timeframe", ts_utc.desc()))


class MarketTick(Base):
    __tablename__ = "market_ticks"
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), primary_key=True)
    ts_utc: Mapped[datetime] = mapped_column(UTCDateTime, primary_key=True)
    bid: Mapped[float | None] = mapped_column(Float)
    ask: Mapped[float | None] = mapped_column(Float)
    last: Mapped[float | None] = mapped_column(Float)
    volume: Mapped[float | None] = mapped_column(Float)
    spread_points: Mapped[float | None] = mapped_column(Float)
    broker_symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    mt5_time_msc: Mapped[int] = mapped_column(BigInteger, nullable=False)
    collection_run_id: Mapped[str | None] = mapped_column(String(36))
    ingested_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class DataCollectionLog(Base):
    __tablename__ = "data_collection_logs"
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    event: Mapped[str] = mapped_column(String(32), nullable=False)        # run | candles | ticks | specs
    status: Mapped[str] = mapped_column(String(16), nullable=False)       # success | partial | failed | skipped
    instrument_id: Mapped[int | None] = mapped_column(ForeignKey("instruments.id"))
    symbol: Mapped[str | None] = mapped_column(String(32))
    timeframe: Mapped[str | None] = mapped_column(String(4))
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    rows_received: Mapped[int | None] = mapped_column(Integer)
    rows_stored: Mapped[int | None] = mapped_column(Integer)
    issues_found: Mapped[int | None] = mapped_column(Integer)
    message: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict | None] = mapped_column(JsonType)
    __table_args__ = (Index("ix_collection_logs_started", "started_at"),)


class DataQualityIssue(Base):
    __tablename__ = "data_quality_issues"
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    instrument_id: Mapped[int | None] = mapped_column(ForeignKey("instruments.id"))
    symbol: Mapped[str | None] = mapped_column(String(32))
    timeframe: Mapped[str | None] = mapped_column(String(4))
    ts_utc: Mapped[datetime | None] = mapped_column(UTCDateTime)
    issue_type: Mapped[str] = mapped_column(String(48), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    details: Mapped[dict | None] = mapped_column(JsonType)
    first_run_id: Mapped[str | None] = mapped_column(String(36))
    last_run_id: Mapped[str | None] = mapped_column(String(36))
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    occurrences: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    resolved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    __table_args__ = (Index("ix_quality_issues_lookup", "instrument_id", "timeframe", "ts_utc"),
                      Index("ix_quality_issues_type", "issue_type", "severity"))
