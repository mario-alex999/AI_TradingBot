"""Module 07 persistence helpers: serialised write transactions, hash-chained audit, outbox, idempotency, notifications and
policy versions. Business rules live in ``service.py``; this module only writes what it is told, consistently."""
from __future__ import annotations

import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

from sqlalchemy import func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from aurum.approvals import SCHEMA_VERSION, SOURCE_MODULE
from aurum.approvals.db_models import (ApprovalAuditLog, ApprovalPolicy, IdempotencyRecord, Notification,
                                       NotificationDelivery, OutboxEvent)
from aurum.approvals.policy import Policy
from aurum.database.connection import session_scope
from aurum.risk.repository import GENESIS, jsonable, stable_hash

ADVISORY_KEY = 70_700_707
_PROCESS_LOCK = threading.RLock()


class SchemaNotReady(RuntimeError):
    pass


def utc(t: datetime | None) -> datetime | None:
    if t is None:
        return None
    return t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t.astimezone(timezone.utc)


class ApprovalRepository:
    def __init__(self, engine: Engine):
        self.engine = engine

    # ---- transactions --------------------------------------------------------------------------------------------------
    @contextmanager
    def write(self) -> Iterator[Session]:
        """One transaction that holds Module 07's write lock until commit or rollback (advisory lock on PostgreSQL, so it
        covers every process; a process lock everywhere). This keeps the audit chain linear and makes every status change a
        single-writer step, in addition to the row version checks and unique constraints."""
        with _PROCESS_LOCK, session_scope(self.engine) as s:
            if self.engine.dialect.name == "postgresql":
                s.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": ADVISORY_KEY})
            yield s

    def read(self) -> Any:
        return session_scope(self.engine)

    def schema_ready(self) -> bool:
        try:
            with self.read() as s:
                s.execute(select(func.count()).select_from(OutboxEvent)).scalar_one()
            return True
        except Exception:  # noqa: BLE001
            return False

    # ---- audit -----------------------------------------------------------------------------------------------------------
    def audit(self, s: Session, *, actor: str, actor_kind: str, action: str, entity_type: str, entity_id: str,
              at: datetime, approval_id: str | None = None, idea_id: str | None = None, previous_state: str | None = None,
              new_state: str | None = None, reason: str | None = None, correlation_id: str | None = None,
              evidence_snapshot_id: str | None = None, details: dict[str, Any] | None = None) -> None:
        prev = s.execute(select(ApprovalAuditLog.hash).order_by(ApprovalAuditLog.seq.desc()).limit(1)).scalar() or GENESIS
        body = self._audit_body(at, actor, actor_kind, action, entity_type, entity_id, approval_id, idea_id, previous_state,
                                new_state, reason, correlation_id, evidence_snapshot_id, jsonable(details))
        s.add(ApprovalAuditLog(at=at, actor=actor[:80], actor_kind=actor_kind, action=action, entity_type=entity_type,
                               entity_id=entity_id, approval_id=approval_id, idea_id=idea_id, previous_state=previous_state,
                               new_state=new_state, reason=reason, correlation_id=correlation_id,
                               evidence_snapshot_id=evidence_snapshot_id, details=jsonable(details), prev_hash=prev,
                               hash=stable_hash({"prev": prev, **body})))
        s.flush()

    @staticmethod
    def _audit_body(at: datetime, actor: str, actor_kind: str, action: str, entity_type: str, entity_id: str,
                    approval_id: str | None, idea_id: str | None, previous_state: str | None, new_state: str | None,
                    reason: str | None, correlation_id: str | None, evidence_snapshot_id: str | None,
                    details: Any) -> dict[str, Any]:
        return {"at": utc(at), "actor": actor[:80], "actor_kind": actor_kind, "action": action, "entity_type": entity_type,
                "entity_id": entity_id, "approval_id": approval_id, "idea_id": idea_id, "previous_state": previous_state,
                "new_state": new_state, "reason": reason, "correlation_id": correlation_id,
                "evidence_snapshot_id": evidence_snapshot_id, "details": details}

    def verify_audit(self) -> tuple[bool, int, str | None]:
        with self.read() as s:
            rows = s.execute(select(ApprovalAuditLog).order_by(ApprovalAuditLog.seq)).scalars().all()
        prev = GENESIS
        for r in rows:
            body = self._audit_body(r.at, r.actor, r.actor_kind, r.action, r.entity_type, r.entity_id, r.approval_id, r.idea_id,
                                    r.previous_state, r.new_state, r.reason, r.correlation_id, r.evidence_snapshot_id, r.details)
            if r.prev_hash != prev or stable_hash({"prev": prev, **body}) != r.hash:
                return False, len(rows), f"Audit row {r.seq} does not match the hash chain."
            prev = r.hash
        return True, len(rows), None

    # ---- outbox ----------------------------------------------------------------------------------------------------------
    def emit(self, s: Session, event_type: str, payload: dict[str, Any], *, at: datetime, correlation_id: str,
             idempotency_key: str, causation_id: str | None = None) -> str | None:
        """Adds an event to the outbox in the caller's transaction. The idempotency key is unique, so emitting the same
        business event twice (a retried request, a duplicate job) stores it once. Returns the event id, or None if it existed."""
        if s.execute(select(OutboxEvent.event_id).where(OutboxEvent.idempotency_key == idempotency_key)).first():
            return None
        event_id = str(uuid.uuid4())
        s.add(OutboxEvent(event_id=event_id, event_type=event_type, event_version=SCHEMA_VERSION, occurred_at=at,
                          source_module=SOURCE_MODULE, correlation_id=correlation_id, causation_id=causation_id,
                          idempotency_key=idempotency_key, payload=jsonable(payload), status="pending", attempts=0,
                          next_attempt_at=at))
        s.flush()
        return event_id

    @staticmethod
    def envelope(e: OutboxEvent) -> dict[str, Any]:
        return {"sequence": e.seq, "event_id": e.event_id, "event_type": e.event_type, "event_version": e.event_version,
                "occurred_at": utc(e.occurred_at), "source_module": e.source_module, "correlation_id": e.correlation_id,
                "causation_id": e.causation_id, "idempotency_key": e.idempotency_key, "payload": e.payload,
                "delivery": {"status": e.status, "attempts": e.attempts, "last_error": e.last_error,
                             "delivered_at": utc(e.delivered_at)}}

    def events(self, after: int = 0, limit: int = 100, event_type: str | None = None) -> list[dict[str, Any]]:
        with self.read() as s:
            q = select(OutboxEvent).where(OutboxEvent.seq > after)
            if event_type:
                q = q.where(OutboxEvent.event_type == event_type)
            return [self.envelope(e) for e in s.execute(q.order_by(OutboxEvent.seq).limit(limit)).scalars()]

    def last_event_seq(self) -> int:
        with self.read() as s:
            return int(s.execute(select(func.max(OutboxEvent.seq))).scalar() or 0)

    # ---- idempotency -----------------------------------------------------------------------------------------------------
    def idempotent(self, s: Session, scope: str, key: str) -> IdempotencyRecord | None:
        return s.execute(select(IdempotencyRecord).where(IdempotencyRecord.scope == scope, IdempotencyRecord.key == key)
                         ).scalar_one_or_none()

    def remember(self, s: Session, scope: str, key: str, request_hash: str, status_code: int, response: Any, at: datetime,
                 days: int = 30) -> None:
        s.add(IdempotencyRecord(scope=scope, key=key[:128], request_hash=request_hash, status_code=status_code,
                                response=jsonable(response), created_at=at, expires_at=at + timedelta(days=days)))
        s.flush()

    # ---- notifications ---------------------------------------------------------------------------------------------------
    def notify(self, s: Session, *, dedupe_key: str, event_type: str, priority: str, title: str, message: str, at: datetime,
               recipients: list[str], channels: dict[str, bool], approval_id: str | None = None,
               management_request_id: str | None = None, position_id: str | None = None,
               recipient_user_id: str | None = None) -> str | None:
        """Stores a notification and one delivery row per recipient and enabled channel, in the caller's transaction.
        The in-app delivery is complete on insert; email/telegram rows are sent later by the worker. Deduplicated by key."""
        if s.execute(select(Notification.id).where(Notification.dedupe_key == dedupe_key)).first():
            return None
        nid = str(uuid.uuid4())
        s.add(Notification(id=nid, dedupe_key=dedupe_key[:160], event_type=event_type, priority=priority, title=title[:160],
                           message=message, approval_id=approval_id, management_request_id=management_request_id,
                           position_id=position_id, recipient_user_id=recipient_user_id, created_at=at))
        s.flush()
        for uid in dict.fromkeys(recipients):
            s.add(NotificationDelivery(notification_id=nid, user_id=uid, channel="in_app", status="delivered", attempts=1,
                                       sent_at=at, created_at=at))
            for ch in ("email", "telegram"):
                if channels.get(ch):
                    s.add(NotificationDelivery(notification_id=nid, user_id=uid, channel=ch, status="pending", attempts=0,
                                               next_attempt_at=at, created_at=at))
        s.flush()
        return nid

    # ---- policy ----------------------------------------------------------------------------------------------------------
    def active_policy(self, s: Session) -> tuple[Policy, int] | None:
        row = s.execute(select(ApprovalPolicy).order_by(ApprovalPolicy.version.desc()).limit(1)).scalar_one_or_none()
        return (Policy.model_validate(row.policy), row.version) if row else None

    def add_policy(self, s: Session, policy: Policy, reason: str, actor: str, at: datetime) -> int:
        current = s.execute(select(func.max(ApprovalPolicy.version))).scalar() or 0
        version = int(current) + 1
        s.add(ApprovalPolicy(version=version, policy=policy.dump(), policy_hash=policy.fingerprint(), reason=reason,
                             created_by=actor, created_at=at))
        s.flush()
        return version

    def policy_history(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.read() as s:
            rows = s.execute(select(ApprovalPolicy).order_by(ApprovalPolicy.version.desc()).limit(limit)).scalars().all()
            return [{"version": r.version, "reason": r.reason, "created_by": r.created_by, "created_at": utc(r.created_at),
                     "policy_hash": r.policy_hash} for r in rows]
