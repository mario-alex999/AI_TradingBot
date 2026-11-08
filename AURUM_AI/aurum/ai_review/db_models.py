"""AI review tables (share Module 01's ``Base``). PostgreSQL DDL: sql/migrations/0006_ai_review.sql."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from aurum.database.models import Base, BigIntPK, JsonType, UTCDateTime, utcnow


class AiReviewBudgetMonth(Base):
    """One row per UTC month. ``committed_microusd`` is spent plus in-flight reservations, in millionths of a dollar."""
    __tablename__ = "ai_review_budget_months"
    month: Mapped[str] = mapped_column(String(7), primary_key=True)          # YYYY-MM
    committed_microusd: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class AiReview(Base):
    __tablename__ = "ai_reviews"
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    review_uid: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    budget_month: Mapped[str] = mapped_column(String(7), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    as_of: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)          # pending | completed | failed
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    effort: Mapped[str] = mapped_column(String(16), nullable=False)
    context_chars: Mapped[int] = mapped_column(Integer, nullable=False)
    verdict: Mapped[dict | None] = mapped_column(JsonType)
    error_code: Mapped[str | None] = mapped_column(String(32))
    error_message: Mapped[str | None] = mapped_column(Text)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    reserved_microusd: Mapped[int] = mapped_column(BigInteger, nullable=False)
    cost_microusd: Mapped[int] = mapped_column(BigInteger, nullable=False)
    cost_is_estimate: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    __table_args__ = (Index("ix_ai_reviews_symbol_created", "symbol", "created_at"),
                      Index("ix_ai_reviews_month", "budget_month"),)
