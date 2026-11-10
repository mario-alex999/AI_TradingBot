"""Per-person MT5 account links. ``sql/migrations/0011_mt5_accounts.sql`` is the PostgreSQL equivalent.

One row per authorisation: who linked which DEMO account, in which terminal slot, and how it is going. The broker
password is never stored here (or anywhere): it lives only in the connector process's memory.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from aurum.database.models import Base, JsonType, UTCDateTime, utcnow
import aurum.auth.db_models  # noqa: E402,F401 - registers `users` so the user_id foreign key resolves in a standalone connector process

# CONNECTING -> CONNECTED -> DISCONNECTING -> DISCONNECTED ; CONNECTING -> FAILED ; any active -> LOST (no heartbeat)
STATUSES = ("CONNECTING", "CONNECTED", "DISCONNECTING", "DISCONNECTED", "FAILED", "LOST")
ACTIVE = ("CONNECTING", "CONNECTED", "DISCONNECTING")
_ACTIVE_SQL = "status IN ('CONNECTING', 'CONNECTED', 'DISCONNECTING')"


class Mt5Connection(Base):
    __tablename__ = "mt5_connections"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    session_id: Mapped[str | None] = mapped_column(String(36), index=True)   # the sign-in session that authorised it
    login: Mapped[int] = mapped_column(BigInteger, nullable=False)
    server: Mapped[str] = mapped_column(String(128), nullable=False)
    slot: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    pid: Mapped[int | None] = mapped_column(Integer)
    company: Mapped[str | None] = mapped_column(String(128))
    currency: Mapped[str | None] = mapped_column(String(16))
    leverage: Mapped[int | None] = mapped_column(Integer)
    trade_mode: Mapped[str | None] = mapped_column(String(16))
    error_code: Mapped[str | None] = mapped_column(String(48))
    error_message: Mapped[str | None] = mapped_column(String(400))
    last_sync: Mapped[dict | None] = mapped_column(JsonType)              # counts from the last sync (no secrets)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    connected_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    heartbeat_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_sync_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    disconnected_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    disconnect_reason: Mapped[str | None] = mapped_column(String(48))
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow)
    __table_args__ = (
        CheckConstraint("status IN ('CONNECTING', 'CONNECTED', 'DISCONNECTING', 'DISCONNECTED', 'FAILED', 'LOST')",
                        name="ck_mt5_connections_status"),
        # A terminal slot serves one account at a time, and an account is linked through one slot at a time.
        Index("uq_mt5_connections_active_slot", "slot", unique=True,
              postgresql_where=text(_ACTIVE_SQL), sqlite_where=text(_ACTIVE_SQL)),
        Index("uq_mt5_connections_active_login", "login", unique=True,
              postgresql_where=text(_ACTIVE_SQL), sqlite_where=text(_ACTIVE_SQL)),
    )
