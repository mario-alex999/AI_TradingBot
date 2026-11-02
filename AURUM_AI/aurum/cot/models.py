"""Module 02 ORM models (share Module 01's ``Base`` so ``create_all`` covers both on SQLite).

PostgreSQL DDL equivalent: ``sql/migrations/0002_cot_engine.sql``. Module 01 tables are never touched.
All timestamps are UTC-aware (``UTCDateTime``). Report dates and release dates are stored separately.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (BigInteger, Boolean, Date, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column

from aurum.database.models import Base, BigIntPK, JsonType, UTCDateTime, utcnow


class CotDataset(Base):
    __tablename__ = "cot_datasets"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    report_type: Mapped[str] = mapped_column(String(16), unique=True, nullable=False)   # legacy | tff | disaggregated
    dataset_name: Mapped[str] = mapped_column(String(128), nullable=False)
    dataset_id: Mapped[str] = mapped_column(String(32), nullable=False)                # Socrata 4x4 id
    dataset_id_status: Mapped[str] = mapped_column(String(16), nullable=False, default="unverified")
    endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    report_scope: Mapped[str] = mapped_column(String(32), nullable=False)
    history_start: Mapped[date | None] = mapped_column(Date)
    description: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow)


class CotContractMapping(Base):
    __tablename__ = "cot_contract_mappings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    aurum_symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    report_type: Mapped[str] = mapped_column(String(16), nullable=False)
    cftc_contract_name: Mapped[str] = mapped_column(String(160), nullable=False)
    cftc_contract_code: Mapped[str] = mapped_column(String(16), nullable=False)
    exchange: Mapped[str | None] = mapped_column(String(64))
    contract_description: Mapped[str | None] = mapped_column(Text)
    currency: Mapped[str | None] = mapped_column(String(8))
    mapping_status: Mapped[str] = mapped_column(String(16), nullable=False, default="unverified")
    verification_source: Mapped[str | None] = mapped_column(Text)
    verified_at: Mapped[date | None] = mapped_column(Date)
    primary_category: Mapped[str] = mapped_column(String(32), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow)
    __table_args__ = (UniqueConstraint("aurum_symbol", "report_type", name="uq_cot_mapping_symbol_type"),
                      Index("ix_cot_mapping_code", "report_type", "cftc_contract_code"))


class CotReport(Base):
    __tablename__ = "cot_reports"
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("cot_datasets.id"), nullable=False)
    contract_mapping_id: Mapped[int | None] = mapped_column(ForeignKey("cot_contract_mappings.id"))
    report_type: Mapped[str] = mapped_column(String(16), nullable=False)
    contract_code: Mapped[str] = mapped_column(String(16), nullable=False)
    contract_market_name: Mapped[str | None] = mapped_column(String(160))
    report_date: Mapped[date] = mapped_column(Date, nullable=False)                 # positions "as of" (Tuesday)
    release_date: Mapped[date] = mapped_column(Date, nullable=False)                # public release day
    release_timestamp: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)  # first moment usable
    release_method: Mapped[str] = mapped_column(String(16), nullable=False)        # override | rule | rule_holiday
    release_uncertain: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    retrieval_timestamp: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    processed_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    source: Mapped[str] = mapped_column(String(16), nullable=False)                # socrata | files
    source_file: Mapped[str | None] = mapped_column(String(64))
    source_record_id: Mapped[str | None] = mapped_column(String(96))
    open_interest: Mapped[int | None] = mapped_column(BigInteger)
    source_payload: Mapped[dict] = mapped_column(JsonType, nullable=False)        # verbatim CFTC record
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow)
    __table_args__ = (UniqueConstraint("dataset_id", "contract_code", "report_date", name="uq_cot_report_source"),
                      Index("ix_cot_reports_mapping_date", "contract_mapping_id", "report_date"),
                      Index("ix_cot_reports_release", "release_timestamp"))


class CotReportRevision(Base):
    """Previous source payload whenever the CFTC republishes a report with different values."""
    __tablename__ = "cot_report_revisions"
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    cot_report_id: Mapped[int] = mapped_column(ForeignKey("cot_reports.id", ondelete="CASCADE"), nullable=False)
    previous_payload: Mapped[dict] = mapped_column(JsonType, nullable=False)
    previous_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    new_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    previous_retrieval_timestamp: Mapped[datetime | None] = mapped_column(UTCDateTime)
    detected_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class CotPosition(Base):
    __tablename__ = "cot_positions"
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    cot_report_id: Mapped[int] = mapped_column(ForeignKey("cot_reports.id", ondelete="CASCADE"), nullable=False)
    trader_category: Mapped[str] = mapped_column(String(32), nullable=False)
    long_positions: Mapped[int | None] = mapped_column(BigInteger)
    short_positions: Mapped[int | None] = mapped_column(BigInteger)
    spreading_positions: Mapped[int | None] = mapped_column(BigInteger)   # NULL where the family has no spread field
    net_positions: Mapped[int | None] = mapped_column(BigInteger)
    is_reportable: Mapped[bool] = mapped_column(Boolean, nullable=False)
    open_interest: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow)
    __table_args__ = (UniqueConstraint("cot_report_id", "trader_category", name="uq_cot_position"),)


class CotMetric(Base):
    __tablename__ = "cot_metrics"
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    cot_report_id: Mapped[int] = mapped_column(ForeignKey("cot_reports.id", ondelete="CASCADE"), nullable=False)
    trader_category: Mapped[str] = mapped_column(String(32), nullable=False)
    report_date: Mapped[date] = mapped_column(Date, nullable=False)
    release_timestamp: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    net_position: Mapped[int | None] = mapped_column(BigInteger)
    long_change: Mapped[int | None] = mapped_column(BigInteger)
    short_change: Mapped[int | None] = mapped_column(BigInteger)
    weekly_net_change: Mapped[int | None] = mapped_column(BigInteger)
    net_position_pct_oi: Mapped[float | None] = mapped_column(Float)
    historical_percentile_12m: Mapped[float | None] = mapped_column(Float)
    historical_percentile_24m: Mapped[float | None] = mapped_column(Float)
    historical_percentile_36m: Mapped[float | None] = mapped_column(Float)
    historical_percentile_48m: Mapped[float | None] = mapped_column(Float)
    cot_index: Mapped[float | None] = mapped_column(Float)
    z_score: Mapped[float | None] = mapped_column(Float)
    history_weeks: Mapped[int] = mapped_column(Integer, nullable=False)
    positioning_direction: Mapped[str | None] = mapped_column(String(16))   # net_long | net_short | balanced
    positioning_strength: Mapped[str | None] = mapped_column(String(24))    # extremely_bullish ... extremely_bearish
    classification_score: Mapped[float | None] = mapped_column(Float)
    change_type: Mapped[str | None] = mapped_column(String(32))
    change_driver: Mapped[str | None] = mapped_column(String(24))
    significant_change: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    unusual_positioning: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    calculation_version: Mapped[str] = mapped_column(String(16), nullable=False)
    calculation_timestamp: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    __table_args__ = (UniqueConstraint("cot_report_id", "trader_category", name="uq_cot_metric"),
                      Index("ix_cot_metrics_category_date", "trader_category", "report_date"))


class CotIngestionLog(Base):
    __tablename__ = "cot_ingestion_logs"
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    report_type: Mapped[str | None] = mapped_column(String(16))
    job_type: Mapped[str] = mapped_column(String(16), nullable=False)       # backfill | update | calculate
    period_start: Mapped[date | None] = mapped_column(Date)
    period_end: Mapped[date | None] = mapped_column(Date)
    source: Mapped[str | None] = mapped_column(String(16))
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    records_retrieved: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_inserted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_updated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_unchanged: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_rejected: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False)          # running | success | partial | failed
    error_message: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict | None] = mapped_column(JsonType)
    __table_args__ = (Index("ix_cot_ingestion_started", "started_at"),)


class CotDataQualityIssue(Base):
    __tablename__ = "cot_data_quality_issues"
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    report_type: Mapped[str | None] = mapped_column(String(16))
    contract_code: Mapped[str | None] = mapped_column(String(16))
    aurum_symbol: Mapped[str | None] = mapped_column(String(32))
    report_date: Mapped[date | None] = mapped_column(Date)
    issue_type: Mapped[str] = mapped_column(String(48), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)        # info | warning | error | critical
    description: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[dict | None] = mapped_column(JsonType)
    detected_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    occurrences: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    resolved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    __table_args__ = (Index("ix_cot_quality_lookup", "aurum_symbol", "report_type", "report_date"),
                      Index("ix_cot_quality_severity", "severity", "resolved"))
