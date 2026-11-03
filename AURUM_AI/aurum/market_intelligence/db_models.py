"""Module 04 tables (share Module 01's ``Base``). PostgreSQL DDL: sql/migrations/0004_market_intelligence.sql.
Additive only - no Module 01-03 table is touched."""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from aurum.database.models import Base, BigIntPK, JsonType, UTCDateTime, utcnow

RUN_FK = "market_intelligence_runs.run_uid"


class MiRun(Base):
    __tablename__ = "market_intelligence_runs"
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    run_uid: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    run_type: Mapped[str] = mapped_column(String(24), nullable=False)       # analysis | calendar | macro | news
    symbol: Mapped[str | None] = mapped_column(String(32))
    as_of: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    generated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    module_version: Mapped[str] = mapped_column(String(16), nullable=False)
    methodology_version: Mapped[str] = mapped_column(String(16), nullable=False)
    config_fingerprint: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    summary: Mapped[dict | None] = mapped_column(JsonType)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    __table_args__ = (Index("ix_mi_runs_symbol_asof", "symbol", "as_of"),)


class MiSessionDefinition(Base):
    __tablename__ = "market_session_definitions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(32), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)           # session | kill_zone
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    local_open: Mapped[str] = mapped_column(String(5), nullable=False)
    local_close: Mapped[str] = mapped_column(String(5), nullable=False)
    weekdays: Mapped[str] = mapped_column(String(32), nullable=False)
    holidays: Mapped[list | None] = mapped_column(JsonType)
    pre_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    post_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    config_fingerprint: Mapped[str] = mapped_column(String(32), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    __table_args__ = (UniqueConstraint("name", "kind", name="uq_market_session_definitions"),)


class MiSession(Base):
    __tablename__ = "market_sessions"
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(32), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)           # session | kill_zone | overlap | transition
    start_utc: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    end_utc: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    local_date: Mapped[str | None] = mapped_column(String(10))
    timezone: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    __table_args__ = (UniqueConstraint("name", "kind", "start_utc", name="uq_market_sessions"),
                      Index("ix_market_sessions_start", "start_utc"))


class MiSessionLevel(Base):
    __tablename__ = "market_session_levels"
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    level_type: Mapped[str] = mapped_column(String(40), nullable=False)     # previous_asian_high, previous_day_low ...
    side: Mapped[str] = mapped_column(String(4), nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    period_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    period_label: Mapped[str | None] = mapped_column(String(32))
    period_start: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    period_end: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    formed_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    source_candle_time: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    timeframe: Mapped[str] = mapped_column(String(4), nullable=False)
    method: Mapped[str] = mapped_column(Text, nullable=False)
    provisional: Mapped[bool] = mapped_column(Boolean, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    swept_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    broken_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    candles: Mapped[int | None] = mapped_column(Integer)
    missing_candles: Mapped[int | None] = mapped_column(Integer)
    detail: Mapped[dict | None] = mapped_column(JsonType)
    first_run_uid: Mapped[str] = mapped_column(ForeignKey(RUN_FK), nullable=False)
    last_run_uid: Mapped[str] = mapped_column(ForeignKey(RUN_FK), nullable=False)
    last_as_of: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    __table_args__ = (UniqueConstraint("symbol", "level_type", "period_start", name="uq_market_session_levels"),
                      Index("ix_market_session_levels_lookup", "symbol", "period_end"))


class MiSessionStatistic(Base):
    __tablename__ = "market_session_statistics"
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    run_uid: Mapped[str] = mapped_column(ForeignKey(RUN_FK), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    session: Mapped[str] = mapped_column(String(32), nullable=False)
    window: Mapped[int] = mapped_column(Integer, nullable=False)
    as_of: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    sample_size: Mapped[int] = mapped_column(Integer, nullable=False)
    sufficient: Mapped[bool] = mapped_column(Boolean, nullable=False)
    metrics: Mapped[dict | None] = mapped_column(JsonType)
    __table_args__ = (UniqueConstraint("symbol", "session", "window", "as_of", name="uq_market_session_statistics"),)


class MiLiquidityEvent(Base):
    __tablename__ = "market_session_liquidity_events"
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    session: Mapped[str | None] = mapped_column(String(32))
    level_type: Mapped[str] = mapped_column(String(40), nullable=False)
    level: Mapped[float | None] = mapped_column(Float)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    direction: Mapped[str | None] = mapped_column(String(16))
    event_time: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    confirmation_time: Mapped[datetime | None] = mapped_column(UTCDateTime)
    source_candle_time: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    timeframe: Mapped[str] = mapped_column(String(4), nullable=False)
    closed_beyond: Mapped[bool | None] = mapped_column(Boolean)
    returned_inside: Mapped[bool | None] = mapped_column(Boolean)
    returned_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    related_structure: Mapped[dict | None] = mapped_column(JsonType)
    detail: Mapped[dict | None] = mapped_column(JsonType)
    first_run_uid: Mapped[str] = mapped_column(ForeignKey(RUN_FK), nullable=False)
    last_run_uid: Mapped[str] = mapped_column(ForeignKey(RUN_FK), nullable=False)
    last_as_of: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    __table_args__ = (UniqueConstraint("symbol", "session", "level_type", "event_time",
                                       name="uq_market_session_liquidity_events"),
                      Index("ix_market_liquidity_lookup", "symbol", "event_time"))


class CalendarSource(Base):
    __tablename__ = "economic_calendar_sources"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    full_calendar: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    requires_key: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)          # ok | error | not_configured
    last_attempt_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_success_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_error: Mapped[str | None] = mapped_column(Text)
    last_event_count: Mapped[int | None] = mapped_column(Integer)
    window_start: Mapped[date | None] = mapped_column(Date)
    window_end: Mapped[date | None] = mapped_column(Date)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class CalendarEvent(Base):
    __tablename__ = "economic_calendar_events"
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_event_id: Mapped[str] = mapped_column(String(96), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    country: Mapped[str | None] = mapped_column(String(64))
    currency: Mapped[str | None] = mapped_column(String(8))
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    subcategory: Mapped[str | None] = mapped_column(String(48))
    importance: Mapped[str | None] = mapped_column(String(8))
    importance_source: Mapped[str | None] = mapped_column(String(48))
    scheduled_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    original_scheduled_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    source_timezone: Mapped[str | None] = mapped_column(String(64))
    time_precision: Mapped[str] = mapped_column(String(16), nullable=False)
    values: Mapped[dict | None] = mapped_column(JsonType)        # actual / consensus / previous / revised_previous
    unit: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text)
    provider_updated_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    first_retrieved_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    actual_first_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    affected_instruments: Mapped[list | None] = mapped_column(JsonType)
    quality_status: Mapped[str] = mapped_column(String(16), nullable=False, default="valid")
    raw: Mapped[dict | None] = mapped_column(JsonType)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    __table_args__ = (UniqueConstraint("provider", "provider_event_id", name="uq_economic_calendar_events"),
                      Index("ix_calendar_events_time", "scheduled_at"),
                      Index("ix_calendar_events_currency", "currency", "importance"))


class CalendarRevision(Base):
    __tablename__ = "economic_calendar_event_revisions"
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("economic_calendar_events.id", ondelete="CASCADE"), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    change_type: Mapped[str] = mapped_column(String(24), nullable=False)      # created | rescheduled | values | status
    changes: Mapped[dict | None] = mapped_column(JsonType)
    snapshot: Mapped[dict] = mapped_column(JsonType, nullable=False)
    ingestion_run_uid: Mapped[str | None] = mapped_column(String(36))
    __table_args__ = (Index("ix_calendar_revisions_event", "event_id", "observed_at"),)


class CalendarIngestionLog(Base):
    __tablename__ = "economic_calendar_ingestion_logs"
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    run_uid: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    status: Mapped[str] = mapped_column(String(16), nullable=False)          # success | failed | skipped
    window_start: Mapped[date | None] = mapped_column(Date)
    window_end: Mapped[date | None] = mapped_column(Date)
    received: Mapped[int | None] = mapped_column(Integer)
    inserted: Mapped[int | None] = mapped_column(Integer)
    updated: Mapped[int | None] = mapped_column(Integer)
    unchanged: Mapped[int | None] = mapped_column(Integer)
    invalid: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict | None] = mapped_column(JsonType)


class MacroObservation(Base):
    __tablename__ = "macro_indicators"
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    series_id: Mapped[str] = mapped_column(String(48), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str | None] = mapped_column(String(32))
    currency: Mapped[str | None] = mapped_column(String(8))
    unit: Mapped[str | None] = mapped_column(String(48))
    period: Mapped[str] = mapped_column(String(16), nullable=False)          # observation period (YYYY-MM[-DD])
    value: Mapped[float] = mapped_column(Float, nullable=False)
    vintage: Mapped[str] = mapped_column(String(32), nullable=False)         # FRED realtime_start | first-retrieval ISO
    known_from: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (UniqueConstraint("series_id", "period", "vintage", name="uq_macro_indicators"),
                      Index("ix_macro_indicators_known", "series_id", "known_from"))


class CentralBankRate(Base):
    __tablename__ = "central_bank_rates"
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    bank: Mapped[str] = mapped_column(String(16), nullable=False)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False)
    rate: Mapped[float] = mapped_column(Float, nullable=False)
    rate_lower: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text)
    known_from: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    detail: Mapped[dict | None] = mapped_column(JsonType)
    __table_args__ = (UniqueConstraint("bank", "effective_date", name="uq_central_bank_rates"),)


class CentralBankDecision(Base):
    __tablename__ = "central_bank_decisions"
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    bank: Mapped[str] = mapped_column(String(16), nullable=False)
    decision_date: Mapped[date] = mapped_column(Date, nullable=False)
    effective_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(16), nullable=False)          # changed | held
    previous_rate: Mapped[float | None] = mapped_column(Float)
    new_rate: Mapped[float | None] = mapped_column(Float)
    change_bps: Mapped[int | None] = mapped_column(Integer)
    known_from: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    matched_meeting: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text)
    retrieved_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    __table_args__ = (UniqueConstraint("bank", "decision_date", name="uq_central_bank_decisions"),)


class MarketNews(Base):
    __tablename__ = "market_news"
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    item_uid: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    headline: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    retrieved_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    category: Mapped[str | None] = mapped_column(String(32))
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    currencies: Mapped[list | None] = mapped_column(JsonType)
    reliability: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (Index("ix_market_news_published", "published_at"),)


class MiVolatilityMetric(Base):
    __tablename__ = "market_volatility_metrics"
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    run_uid: Mapped[str] = mapped_column(ForeignKey(RUN_FK), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(4), nullable=False)
    as_of: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    regime: Mapped[str] = mapped_column(String(16), nullable=False)
    atr: Mapped[float | None] = mapped_column(Float)
    atr_percentile: Mapped[float | None] = mapped_column(Float)
    realised_vol_pct: Mapped[float | None] = mapped_column(Float)
    metrics: Mapped[dict | None] = mapped_column(JsonType)
    __table_args__ = (UniqueConstraint("symbol", "timeframe", "as_of", name="uq_market_volatility_metrics"),)


class MiConditionSnapshot(Base):
    __tablename__ = "market_condition_snapshots"
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    run_uid: Mapped[str] = mapped_column(ForeignKey(RUN_FK), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    as_of: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    tags: Mapped[list | None] = mapped_column(JsonType)
    conditions: Mapped[list | None] = mapped_column(JsonType)
    summary: Mapped[list | None] = mapped_column(JsonType)
    __table_args__ = (UniqueConstraint("symbol", "as_of", name="uq_market_condition_snapshots"),)


class MiScore(Base):
    __tablename__ = "market_intelligence_scores"
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    run_uid: Mapped[str] = mapped_column(ForeignKey(RUN_FK), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    as_of: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    score_type: Mapped[str] = mapped_column(String(32), nullable=False)
    score: Mapped[float | None] = mapped_column(Float)
    methodology_version: Mapped[str] = mapped_column(String(16), nullable=False)
    components: Mapped[dict | None] = mapped_column(JsonType)
    __table_args__ = (UniqueConstraint("symbol", "as_of", "score_type", name="uq_market_intelligence_scores"),)


class MiQualityIssue(Base):
    __tablename__ = "market_intelligence_quality_issues"
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    run_uid: Mapped[str | None] = mapped_column(String(36))
    symbol: Mapped[str | None] = mapped_column(String(32))
    component: Mapped[str] = mapped_column(String(32), nullable=False)
    issue_type: Mapped[str] = mapped_column(String(48), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    message: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict | None] = mapped_column(JsonType)
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    occurrences: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    __table_args__ = (Index("ix_mi_quality_component", "component", "severity"),)
