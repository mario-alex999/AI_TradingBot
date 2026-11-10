"""Module 08 persistence helpers: serialised write transactions, the hash-chained audit, idempotency records,
configuration versions and the environment pin. Rules live in the service; this module only writes consistently."""
from __future__ import annotations

import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

from sqlalchemy import func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from aurum.database.connection import session_scope
from aurum.orchestration.db_models import OrchAuditLog, OrchConfigVersion, OrchIdempotency, OrchInstance
from aurum.risk.repository import GENESIS, jsonable, stable_hash

ADVISORY_KEY = 80_800_808
_PROCESS_LOCK = threading.RLock()


def utc(t: datetime | None) -> datetime | None:
    if t is None:
        return None
    return t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t.astimezone(timezone.utc)


class OrchRepository:
    def __init__(self, engine: Engine):
        self.engine = engine

    @contextmanager
    def write(self) -> Iterator[Session]:
        """One transaction holding Module 08's write lock (advisory lock on PostgreSQL, so it spans processes). It keeps
        the audit chain linear and every state change single-writer. Never hold it across a network call."""
        with _PROCESS_LOCK, session_scope(self.engine) as s:
            if self.engine.dialect.name == "postgresql":
                s.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": ADVISORY_KEY})
            yield s

    def read(self) -> Any:
        return session_scope(self.engine)

    def schema_ready(self) -> bool:
        try:
            with self.read() as s:
                s.execute(select(func.count()).select_from(OrchInstance)).scalar_one()
            return True
        except Exception:  # noqa: BLE001
            return False

    # ---- environment pin ----------------------------------------------------------------------------------------------------
    def pin_environment(self, environment: str, now: datetime) -> str:
        with self.write() as s:
            row = s.get(OrchInstance, "instance")
            if row is None:
                s.add(OrchInstance(key="instance", environment=environment, created_at=now))
                return environment
            return row.environment

    # ---- audit ------------------------------------------------------------------------------------------------------------------
    def audit(self, s: Session, *, actor: str, actor_kind: str, action: str, entity_type: str, entity_id: str, at: datetime,
              outcome: str = "ok", correlation_id: str | None = None, details: dict[str, Any] | None = None) -> None:
        prev = s.execute(select(OrchAuditLog.hash).order_by(OrchAuditLog.seq.desc()).limit(1)).scalar() or GENESIS
        body = self._body(at, actor, actor_kind, action, entity_type, entity_id, outcome, correlation_id, jsonable(details))
        s.add(OrchAuditLog(at=at, actor=actor[:80], actor_kind=actor_kind, action=action[:48], entity_type=entity_type[:24],
                           entity_id=str(entity_id)[:64], outcome=outcome, correlation_id=correlation_id,
                           details=jsonable(details), prev_hash=prev, hash=stable_hash({"prev": prev, **body})))
        s.flush()

    @staticmethod
    def _body(at: datetime, actor: str, actor_kind: str, action: str, entity_type: str, entity_id: str, outcome: str,
              correlation_id: str | None, details: Any) -> dict[str, Any]:
        return {"at": utc(at), "actor": actor[:80], "actor_kind": actor_kind, "action": action[:48],
                "entity_type": entity_type[:24], "entity_id": str(entity_id)[:64], "outcome": outcome,
                "correlation_id": correlation_id, "details": details}

    def verify_audit(self) -> tuple[bool, int, str | None]:
        with self.read() as s:
            rows = s.execute(select(OrchAuditLog).order_by(OrchAuditLog.seq)).scalars().all()
        prev = GENESIS
        for r in rows:
            body = self._body(r.at, r.actor, r.actor_kind, r.action, r.entity_type, r.entity_id, r.outcome, r.correlation_id,
                              r.details)
            if r.prev_hash != prev or stable_hash({"prev": prev, **body}) != r.hash:
                return False, len(rows), f"Audit row {r.seq} does not match the hash chain."
            prev = r.hash
        return True, len(rows), None

    # ---- idempotency ------------------------------------------------------------------------------------------------------------
    def idem_get(self, s: Session, scope: str, key: str, now: datetime) -> OrchIdempotency | None:
        row = s.execute(select(OrchIdempotency).where(OrchIdempotency.scope == scope[:64], OrchIdempotency.key == key[:160])
                        ).scalar_one_or_none()
        if row is not None and utc(row.expires_at) < now:
            s.delete(row)
            s.flush()
            return None
        return row

    def idem_put(self, s: Session, scope: str, key: str, request_hash: str, status_code: int, response: Any, now: datetime,
                 days: int = 7) -> None:
        s.add(OrchIdempotency(scope=scope[:64], key=key[:160], request_hash=request_hash, status_code=status_code,
                              response=jsonable(response), created_at=now, expires_at=now + timedelta(days=days)))
        s.flush()

    # ---- configuration --------------------------------------------------------------------------------------------------------
    def config_current(self, s: Session) -> OrchConfigVersion | None:
        return s.execute(select(OrchConfigVersion).order_by(OrchConfigVersion.version.desc()).limit(1)).scalar_one_or_none()

    def config_add(self, s: Session, config: dict[str, Any], reason: str, actor: str, now: datetime) -> int:
        current = s.execute(select(func.max(OrchConfigVersion.version))).scalar() or 0
        version = int(current) + 1
        s.add(OrchConfigVersion(version=version, config=jsonable(config), config_hash=stable_hash(config), reason=reason,
                                created_by=actor[:80], created_at=now))
        s.flush()
        return version


__all__ = ["OrchRepository", "utc", "jsonable", "stable_hash"]
