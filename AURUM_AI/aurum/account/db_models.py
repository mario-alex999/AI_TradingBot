"""Tables for the account mirror. ``sql/migrations/0005_account_trading.sql`` is the PostgreSQL equivalent."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Float, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from aurum.database.models import Base, BigIntPK, UTCDateTime, utcnow


class AccountSnapshot(Base):
    """What ``account_info()`` reported at one instant. Equity history, drawdowns and the summary come from here."""
    __tablename__ = "account_snapshots"
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    captured_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    login: Mapped[int] = mapped_column(BigInteger, nullable=False)
    server: Mapped[str | None] = mapped_column(String(128))
    currency: Mapped[str] = mapped_column(String(16), nullable=False)
    trade_mode: Mapped[str] = mapped_column(String(16), nullable=False)        # DEMO | CONTEST | REAL
    leverage: Mapped[int | None] = mapped_column(Integer)
    balance: Mapped[float] = mapped_column(Float, nullable=False)
    equity: Mapped[float] = mapped_column(Float, nullable=False)
    margin: Mapped[float | None] = mapped_column(Float)
    margin_free: Mapped[float | None] = mapped_column(Float)
    margin_level: Mapped[float | None] = mapped_column(Float)                  # % ; None when no margin is used
    profit: Mapped[float | None] = mapped_column(Float)                        # floating P/L reported by MT5
    __table_args__ = (Index("ix_account_snapshots_time", "captured_at"),)


class AccountDeal(Base):
    """One raw MT5 deal (trade or balance operation). Positions are always rebuilt from every stored deal of the
    position, so syncing overlapping or late windows never double-counts anything."""
    __tablename__ = "account_deals"
    ticket: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    position_id: Mapped[int | None] = mapped_column(BigInteger)
    ts_utc: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    type: Mapped[int] = mapped_column(Integer, nullable=False)
    entry: Mapped[int | None] = mapped_column(Integer)
    symbol: Mapped[str | None] = mapped_column(String(64))
    volume: Mapped[float | None] = mapped_column(Float)
    price: Mapped[float | None] = mapped_column(Float)
    profit: Mapped[float | None] = mapped_column(Float)
    swap: Mapped[float | None] = mapped_column(Float)
    commission: Mapped[float | None] = mapped_column(Float)
    fee: Mapped[float | None] = mapped_column(Float)
    sl: Mapped[float | None] = mapped_column(Float)
    tp: Mapped[float | None] = mapped_column(Float)
    magic: Mapped[int | None] = mapped_column(BigInteger)
    comment: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (Index("ix_account_deals_position", "position_id"), Index("ix_account_deals_time", "ts_utc"))


class TradeRecord(Base):
    """One position leg rebuilt from its deals (open or closed). ``id`` is the MT5 position id, or
    ``<position_id>-<n>`` for the legs a netting reversal opens."""
    __tablename__ = "trade_records"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    position_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False)            # canonical when mapped, else broker symbol
    broker_symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    side: Mapped[str] = mapped_column(String(4), nullable=False)               # buy | sell
    status: Mapped[str] = mapped_column(String(8), nullable=False)             # open | closed
    volume: Mapped[float] = mapped_column(Float, nullable=False)               # volume still open (open) or opened (closed)
    open_time: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    open_price: Mapped[float] = mapped_column(Float, nullable=False)
    close_time: Mapped[datetime | None] = mapped_column(UTCDateTime)
    close_price: Mapped[float | None] = mapped_column(Float)
    stop_loss: Mapped[float | None] = mapped_column(Float)                     # as set on the entry deal
    take_profit: Mapped[float | None] = mapped_column(Float)
    pnl: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)     # net: profit + swap + commission + fee so far
    strategy: Mapped[str | None] = mapped_column(String(128))
    magic: Mapped[int | None] = mapped_column(BigInteger)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow)
    __table_args__ = (Index("ix_trade_records_status_close", "status", "close_time"),
                      Index("ix_trade_records_position", "position_id"))
