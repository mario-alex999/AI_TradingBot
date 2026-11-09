"""Module 06 persistence. Every write that can affect a later decision (assessments and their reservations, locks, config
versions, audit rows) runs inside ``serialized()``: a PostgreSQL transaction-scoped advisory lock (or a process lock on
SQLite), so two concurrent assessments can never both spend the same risk budget, and the audit hash chain stays linear.
"""
from __future__ import annotations

import hashlib
import json
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Iterator

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from aurum.database.connection import session_scope
from aurum.risk import SOURCE_MODULE
from aurum.risk.config import RiskConfig, default_config
from aurum.risk.db_models import (RiskAccountSnapshot, RiskAssessment, RiskAuditLog, RiskConfigVersion,
                                  RiskCorrelationSnapshot, RiskEvent, RiskInboxEvent, RiskLock, RiskLockHistory,
                                  RiskLossStreak, RiskPeriodRecord, RiskPortfolioSnapshot, RiskRuleEvaluation)
from aurum.risk.models import Lock, PASS, REVIEW
from aurum.risk.money import fnum
from aurum.risk.portfolio import Reservation

ADVISORY_KEY = 60_600_606
_PROCESS_LOCK = threading.RLock()
GENESIS = "0" * 64


def jsonable(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [jsonable(v) for v in obj]
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    return obj


def stable_hash(obj: Any) -> str:
    return hashlib.sha256(json.dumps(jsonable(obj), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _utc(t: datetime | None) -> datetime | None:
    if t is None:
        return None
    return t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t.astimezone(timezone.utc)


class SchemaNotReady(RuntimeError):
    pass


class RiskRepository:
    def __init__(self, engine: Engine):
        self.engine = engine

    # ---- infrastructure ------------------------------------------------------------------------------------------------
    def schema_ready(self) -> bool:
        try:
            with self.engine.connect() as c:
                if self.engine.dialect.name == "postgresql":
                    return bool(c.execute(text("SELECT to_regclass('public.risk_assessments')")).scalar())
                return bool(c.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='risk_assessments'")).scalar())
        except Exception:  # noqa: BLE001
            return False

    def require_schema(self) -> None:
        if not self.schema_ready():
            raise SchemaNotReady("Module 06 tables are missing: run `python -m aurum.cli init-db`.")

    @contextmanager
    def serialized(self) -> Iterator[Session]:
        """One transaction holding the global risk lock until commit or rollback."""
        with _PROCESS_LOCK, session_scope(self.engine) as s:
            if self.engine.dialect.name == "postgresql":
                s.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": ADVISORY_KEY})
            yield s

    def read(self) -> Any:
        return session_scope(self.engine)

    # ---- audit ---------------------------------------------------------------------------------------------------------
    def audit(self, s: Session, actor: str, action: str, entity_type: str, entity_id: str | None,
              details: dict[str, Any] | None = None, correlation_id: str | None = None, at: datetime | None = None) -> None:
        prev = s.execute(select(RiskAuditLog.hash).order_by(RiskAuditLog.id.desc()).limit(1)).scalar() or GENESIS
        at = at or datetime.now(timezone.utc)
        body = {"at": at, "actor": actor, "action": action, "entity_type": entity_type, "entity_id": entity_id,
                "correlation_id": correlation_id, "details": details}
        s.add(RiskAuditLog(at=at, actor=actor, action=action, entity_type=entity_type, entity_id=entity_id,
                           correlation_id=correlation_id, details=jsonable(details), prev_hash=prev,
                           hash=stable_hash({"prev": prev, **body})))
        s.flush()

    def verify_audit(self) -> tuple[bool, int, str | None]:
        with self.read() as s:
            rows = s.execute(select(RiskAuditLog).order_by(RiskAuditLog.id)).scalars().all()
        prev = GENESIS
        for r in rows:
            body = {"at": _utc(r.at), "actor": r.actor, "action": r.action, "entity_type": r.entity_type,
                    "entity_id": r.entity_id, "correlation_id": r.correlation_id, "details": r.details}
            if r.prev_hash != prev or stable_hash({"prev": prev, **body}) != r.hash:
                return False, len(rows), f"Audit row {r.id} does not match the hash chain."
            prev = r.hash
        return True, len(rows), None

    def audit_rows(self, limit: int = 200, entity_type: str | None = None) -> list[dict[str, Any]]:
        with self.read() as s:
            q = select(RiskAuditLog).order_by(RiskAuditLog.id.desc()).limit(limit)
            if entity_type:
                q = q.where(RiskAuditLog.entity_type == entity_type)
            return [{"id": r.id, "at": _utc(r.at), "actor": r.actor, "action": r.action, "entity_type": r.entity_type,
                     "entity_id": r.entity_id, "correlation_id": r.correlation_id, "details": r.details}
                    for r in s.execute(q).scalars().all()]

    # ---- configuration -------------------------------------------------------------------------------------------------
    def active_config(self) -> tuple[RiskConfig, int, dict[str, Any]]:
        with self.read() as s:
            row = s.execute(select(RiskConfigVersion).order_by(RiskConfigVersion.version.desc()).limit(1)).scalar_one_or_none()
            if row is not None:
                return RiskConfig.model_validate(row.config), row.version, self._config_meta(row)
        cfg = default_config()
        with self.serialized() as s:
            row = s.execute(select(RiskConfigVersion).order_by(RiskConfigVersion.version.desc()).limit(1)).scalar_one_or_none()
            if row is None:
                row = RiskConfigVersion(version=1, profile=cfg.profile, config=cfg.model_dump(mode="json"),
                                        fingerprint=cfg.fingerprint(), created_by="system",
                                        reason="Seeded from config/risk.yaml", weakened=[])
                s.add(row)
                s.flush()
                self.audit(s, "system", "config.seeded", "risk_config", "1", {"profile": cfg.profile, "fingerprint": cfg.fingerprint()})
            return RiskConfig.model_validate(row.config), row.version, self._config_meta(row)

    @staticmethod
    def _config_meta(row: RiskConfigVersion) -> dict[str, Any]:
        return {"version": row.version, "profile": row.profile, "fingerprint": row.fingerprint, "created_by": row.created_by,
                "created_at": _utc(row.created_at), "reason": row.reason, "weakened": row.weakened or []}

    def config_versions(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.read() as s:
            rows = s.execute(select(RiskConfigVersion).order_by(RiskConfigVersion.version.desc()).limit(limit)).scalars().all()
            return [self._config_meta(r) for r in rows]

    def add_config_version(self, s: Session, cfg: RiskConfig, base_version: int, actor: str, reason: str | None,
                           weakened: list[str]) -> int:
        current = s.execute(select(func.max(RiskConfigVersion.version))).scalar() or 0
        if current != base_version:
            raise ConfigConflict(f"The active configuration is version {current}, not {base_version}; reload and retry.")
        version = current + 1
        s.add(RiskConfigVersion(version=version, profile=cfg.profile, config=cfg.model_dump(mode="json"),
                                fingerprint=cfg.fingerprint(), created_by=actor, reason=reason, weakened=weakened))
        s.flush()
        return version

    # ---- period anchors ------------------------------------------------------------------------------------------------
    def period_anchor(self, kind: str, label: str, basis: str, start: datetime, end: datetime,
                      compute: Callable[[], tuple[Decimal | None, str, bool]]) -> tuple[Decimal | None, str, bool]:
        """The persisted starting value for a period. A certain value, once stored, never changes; an uncertain or missing one
        is recomputed until a certain one is found."""
        with self.read() as s:
            row = s.execute(select(RiskPeriodRecord).where(and_(RiskPeriodRecord.kind == kind, RiskPeriodRecord.label == label,
                                                                 RiskPeriodRecord.basis == basis))).scalar_one_or_none()
            if row is not None and not row.start_uncertain and row.start_value is not None:
                return row.start_value, row.start_source, False
        value, source, uncertain = compute()
        try:
            with session_scope(self.engine) as s:
                row = s.execute(select(RiskPeriodRecord).where(and_(RiskPeriodRecord.kind == kind, RiskPeriodRecord.label == label,
                                                                     RiskPeriodRecord.basis == basis))).scalar_one_or_none()
                now = datetime.now(timezone.utc)
                if row is None:
                    s.add(RiskPeriodRecord(kind=kind, label=label, basis=basis, start_at=start, end_at=end, start_value=value,
                                           start_source=source, start_uncertain=uncertain, created_at=now, updated_at=now))
                elif row.start_uncertain or row.start_value is None:
                    row.start_value, row.start_source, row.start_uncertain, row.updated_at = value, source, uncertain, now
        except IntegrityError:
            pass  # a concurrent writer stored it first; its value is equally valid
        return value, source, uncertain

    def record_period_loss(self, s: Session, kind: str, label: str, basis: str, loss_pct: Decimal | None, limit_hit: bool,
                           now: datetime) -> None:
        row = s.execute(select(RiskPeriodRecord).where(and_(RiskPeriodRecord.kind == kind, RiskPeriodRecord.label == label,
                                                             RiskPeriodRecord.basis == basis))).scalar_one_or_none()
        if row is None:
            return
        row.last_loss_pct, row.updated_at = loss_pct, now
        if limit_hit and row.limit_reached_at is None:
            row.limit_reached_at = now

    def period_records(self, limit: int = 60) -> list[dict[str, Any]]:
        with self.read() as s:
            rows = s.execute(select(RiskPeriodRecord).order_by(RiskPeriodRecord.start_at.desc()).limit(limit)).scalars().all()
            return [{"kind": r.kind, "label": r.label, "basis": r.basis, "start_at": _utc(r.start_at), "end_at": _utc(r.end_at),
                     "start_value": r.start_value, "start_source": r.start_source, "start_uncertain": r.start_uncertain,
                     "last_loss_pct": r.last_loss_pct, "limit_reached_at": _utc(r.limit_reached_at)} for r in rows]

    # ---- assessments ---------------------------------------------------------------------------------------------------
    def latest_by_key(self, s: Session, key: str) -> RiskAssessment | None:
        return s.execute(select(RiskAssessment).where(RiskAssessment.idempotency_key == key)
                         .order_by(RiskAssessment.attempt.desc()).limit(1)).scalar_one_or_none()

    def live_reservations(self, s: Session, now: datetime, exclude_key: str | None = None) -> list[Reservation]:
        q = select(RiskAssessment).where(and_(RiskAssessment.decision.in_((PASS, REVIEW)), RiskAssessment.state == "active",
                                              RiskAssessment.expires_at > now, RiskAssessment.reserved_risk.is_not(None)))
        if exclude_key:
            q = q.where(RiskAssessment.idempotency_key != exclude_key)
        return [Reservation(r.assessment_id, r.symbol, r.direction, r.asset_class, r.strategy, r.reserved_risk,
                            _utc(r.expires_at)) for r in s.execute(q).scalars().all()]

    def active_for_idea(self, s: Session, idea_id: str | None, now: datetime, exclude_key: str) -> list[str]:
        if not idea_id:
            return []
        q = select(RiskAssessment.assessment_id).where(and_(
            RiskAssessment.trade_idea_id == idea_id, RiskAssessment.decision.in_((PASS, REVIEW)),
            RiskAssessment.state == "active", RiskAssessment.expires_at > now, RiskAssessment.idempotency_key != exclude_key))
        return [r for (r,) in s.execute(q).all()]

    def get_assessment(self, assessment_id: str) -> dict[str, Any] | None:
        with self.read() as s:
            r = s.execute(select(RiskAssessment).where(RiskAssessment.assessment_id == assessment_id)).scalar_one_or_none()
            return self.assessment_view(r) if r else None

    @staticmethod
    def assessment_view(r: RiskAssessment) -> dict[str, Any]:
        out = dict(r.result)
        out.update(state=r.state, state_changed_at=_utc(r.state_changed_at))
        return out

    def list_assessments(self, *, limit: int, offset: int, decision: str | None, symbol: str | None, rule_id: str | None,
                         strategy: str | None, date_from: datetime | None, date_to: datetime | None) -> tuple[int, list[dict[str, Any]]]:
        conds = []
        if decision:
            conds.append(RiskAssessment.decision == decision)
        if symbol:
            conds.append(RiskAssessment.symbol == symbol)
        if strategy:
            conds.append(RiskAssessment.strategy == strategy)
        if date_from:
            conds.append(RiskAssessment.created_at >= date_from)
        if date_to:
            conds.append(RiskAssessment.created_at <= date_to)
        if rule_id:
            sub = select(RiskRuleEvaluation.assessment_id).where(and_(RiskRuleEvaluation.rule_id == rule_id,
                                                                      RiskRuleEvaluation.outcome.in_(("fail", "error"))))
            conds.append(RiskAssessment.assessment_id.in_(sub))
        where = and_(*conds) if conds else True  # noqa: FBT003
        with self.read() as s:
            total = s.execute(select(func.count()).select_from(RiskAssessment).where(where)).scalar() or 0
            rows = s.execute(select(RiskAssessment).where(where).order_by(RiskAssessment.created_at.desc(), RiskAssessment.id.desc())
                             .limit(limit).offset(offset)).scalars().all()
            return int(total), [self._summary(r) for r in rows]

    @staticmethod
    def _summary(r: RiskAssessment) -> dict[str, Any]:
        res = r.result or {}
        return {"assessment_id": r.assessment_id, "trade_idea_id": r.trade_idea_id, "instrument": r.symbol,
                "direction": r.direction, "strategy": r.strategy, "decision": r.decision, "state": r.state,
                "recommended_volume": fnum(r.recommended_volume), "final_volume": fnum(r.final_volume),
                "monetary_risk": fnum(r.monetary_risk), "risk_pct": fnum(r.risk_pct), "risk_reward": fnum(r.adjusted_risk_reward), "config_version": r.config_version,
                "created_at": _utc(r.created_at), "expires_at": _utc(r.expires_at), "source": r.source,
                "rule_violations": [v["rule_id"] for v in res.get("rule_violations", [])],
                "warnings": [v["rule_id"] for v in res.get("warnings", [])],
                "errors": [v["rule_id"] for v in res.get("errors", [])]}

    def rule_counts(self, since: datetime | None = None) -> list[dict[str, Any]]:
        with self.read() as s:
            q = (select(RiskRuleEvaluation.rule_id, RiskRuleEvaluation.name, RiskRuleEvaluation.outcome, func.count())
                 .join(RiskAssessment, RiskAssessment.assessment_id == RiskRuleEvaluation.assessment_id)
                 .where(RiskRuleEvaluation.outcome.in_(("fail", "error"))))
            if since:
                q = q.where(RiskAssessment.created_at >= since)
            q = q.group_by(RiskRuleEvaluation.rule_id, RiskRuleEvaluation.name, RiskRuleEvaluation.outcome)
            return [{"rule_id": a, "name": b, "outcome": c, "count": int(n)} for a, b, c, n in s.execute(q).all()]

    def set_assessment_state(self, s: Session, assessment_id: str, state: str, now: datetime) -> RiskAssessment | None:
        r = s.execute(select(RiskAssessment).where(RiskAssessment.assessment_id == assessment_id)).scalar_one_or_none()
        if r is None:
            return None
        r.state, r.state_changed_at = state, now
        s.flush()
        return r

    # ---- snapshots -----------------------------------------------------------------------------------------------------
    def add_correlations(self, s: Session, results: list[Any], now: datetime) -> None:
        for c in results:
            s.add(RiskCorrelationSnapshot(computed_at=now, symbol_a=c.a, symbol_b=c.b, timeframe=c.timeframe,
                                          lookback_bars=c.lookback_bars, samples=c.samples,
                                          coefficient=None if c.coefficient is None else Decimal(str(c.coefficient)),
                                          last_common_bar=c.last_common_bar, reliable=c.reliable, reason=c.reason))

    def latest_correlations(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.read() as s:
            rows = s.execute(select(RiskCorrelationSnapshot).order_by(RiskCorrelationSnapshot.computed_at.desc()).limit(limit)).scalars().all()
        seen, out = set(), []
        for r in rows:
            k = tuple(sorted((r.symbol_a, r.symbol_b)))
            if k in seen:
                continue
            seen.add(k)
            out.append({"symbol_a": r.symbol_a, "symbol_b": r.symbol_b, "coefficient": r.coefficient, "samples": r.samples,
                        "lookback_bars": r.lookback_bars, "timeframe": r.timeframe, "last_common_bar": _utc(r.last_common_bar),
                        "reliable": r.reliable, "reason": r.reason, "computed_at": _utc(r.computed_at)})
        return out

    # ---- locks ---------------------------------------------------------------------------------------------------------
    @staticmethod
    def _lock(r: RiskLock) -> Lock:
        return Lock(r.lock_id, r.scope, r.scope_value, r.reason, r.trigger_source, r.rule_id, _utc(r.activated_at),
                    r.activated_by, _utc(r.expires_at), r.details)

    def expire_locks(self, s: Session, now: datetime) -> list[str]:
        """Locks whose configured reset time has passed become ``expired`` (recorded in history and audit). This is the
        documented reset policy, never a side effect of a restart: an expired lock was due to end whether or not the
        application was running."""
        rows = s.execute(select(RiskLock).where(and_(RiskLock.status == "active", RiskLock.expires_at.is_not(None),
                                                     RiskLock.expires_at <= now))).scalars().all()
        for r in rows:
            r.status, r.dedupe_key, r.released_at, r.released_by = "expired", None, now, "system"
            r.release_reason = "Reset policy: the lock's period ended."
            s.add(RiskLockHistory(lock_id=r.lock_id, action="expired", at=now, actor="system", reason=r.release_reason))
            self.audit(s, "system", "lock.expired", "risk_lock", r.lock_id, {"reason": r.reason}, at=now)
        s.flush()
        return [r.lock_id for r in rows]

    def active_locks(self, s: Session, now: datetime) -> list[Lock]:
        rows = s.execute(select(RiskLock).where(and_(RiskLock.status == "active",
                                                     or_(RiskLock.expires_at.is_(None), RiskLock.expires_at > now)))).scalars().all()
        return [self._lock(r) for r in rows]

    def activate_lock(self, s: Session, *, scope: str, scope_value: str | None, reason: str, trigger_source: str,
                      rule_id: str | None, actor: str, now: datetime, expires_at: datetime | None,
                      details: dict[str, Any] | None, dedupe_key: str | None, correlation_id: str | None) -> tuple[Lock, bool]:
        """(lock, created). With a dedupe key, an already-active identical lock is returned instead of a second one."""
        if dedupe_key:
            existing = s.execute(select(RiskLock).where(RiskLock.dedupe_key == dedupe_key)).scalar_one_or_none()
            if existing is not None:
                return self._lock(existing), False
        lock_id = str(uuid.uuid4())
        s.add(RiskLock(lock_id=lock_id, dedupe_key=dedupe_key, scope=scope, scope_value=scope_value, reason=reason,
                       trigger_source=trigger_source, rule_id=rule_id, status="active", activated_at=now, activated_by=actor,
                       expires_at=expires_at, details=jsonable(details)))
        s.flush()
        s.add(RiskLockHistory(lock_id=lock_id, action="activated", at=now, actor=actor, reason=reason, details=jsonable(details)))
        self.audit(s, actor, "lock.activated", "risk_lock", lock_id, {"scope": scope, "scope_value": scope_value,
                   "reason": reason, "trigger_source": trigger_source, "rule_id": rule_id, "expires_at": expires_at},
                   correlation_id, at=now)
        r = s.execute(select(RiskLock).where(RiskLock.lock_id == lock_id)).scalar_one()
        return self._lock(r), True

    def lock_status(self, s: Session, lock_id: str) -> str | None:
        return s.execute(select(RiskLock.status).where(RiskLock.lock_id == lock_id)).scalar_one_or_none()

    def release_lock(self, s: Session, lock_id: str, actor: str, reason: str, now: datetime,
                     correlation_id: str | None) -> RiskLock | None:
        r = s.execute(select(RiskLock).where(RiskLock.lock_id == lock_id)).scalar_one_or_none()
        if r is None or r.status != "active":
            return r
        r.status, r.dedupe_key, r.released_at, r.released_by, r.release_reason = "released", None, now, actor, reason
        s.add(RiskLockHistory(lock_id=lock_id, action="released", at=now, actor=actor, reason=reason))
        self.audit(s, actor, "lock.released", "risk_lock", lock_id, {"reason": reason, "lock_reason": r.reason}, correlation_id, at=now)
        s.flush()
        return r

    def locks(self, include_history: bool = True, limit: int = 100) -> list[dict[str, Any]]:
        with self.read() as s:
            q = select(RiskLock).order_by(RiskLock.activated_at.desc()).limit(limit)
            if not include_history:
                q = q.where(RiskLock.status == "active")
            rows = s.execute(q).scalars().all()
            hist: dict[str, list[dict[str, Any]]] = {}
            if rows:
                for h in s.execute(select(RiskLockHistory).where(RiskLockHistory.lock_id.in_([r.lock_id for r in rows]))
                                   .order_by(RiskLockHistory.at)).scalars().all():
                    hist.setdefault(h.lock_id, []).append({"action": h.action, "at": _utc(h.at), "actor": h.actor, "reason": h.reason})
            return [{"lock_id": r.lock_id, "scope": r.scope, "scope_value": r.scope_value, "reason": r.reason,
                     "trigger_source": r.trigger_source, "rule_id": r.rule_id, "status": r.status,
                     "activated_at": _utc(r.activated_at), "activated_by": r.activated_by, "expires_at": _utc(r.expires_at),
                     "released_at": _utc(r.released_at), "released_by": r.released_by, "release_reason": r.release_reason,
                     "details": r.details, "history": hist.get(r.lock_id, [])} for r in rows]

    def acknowledged_streaks(self, s: Session) -> set[str]:
        """Loss streaks whose lock has been released or has expired: the same streak never re-locks."""
        rows = s.execute(select(RiskLossStreak.fingerprint, RiskLossStreak.lock_id)).all()
        if not rows:
            return set()
        status = dict(s.execute(select(RiskLock.lock_id, RiskLock.status).where(
            RiskLock.lock_id.in_([lid for _, lid in rows if lid]))).all())
        return {fp for fp, lid in rows if lid and status.get(lid) in ("released", "expired")}

    def record_streak(self, s: Session, streak: int, trade_ids: list[str], lock_id: str | None, fingerprint: str, now: datetime) -> None:
        if s.execute(select(RiskLossStreak.id).where(RiskLossStreak.fingerprint == fingerprint)).first() is None:
            s.add(RiskLossStreak(detected_at=now, streak=streak, trade_ids=trade_ids, lock_id=lock_id, fingerprint=fingerprint))

    # ---- events --------------------------------------------------------------------------------------------------------
    def add_event(self, s: Session, event_type: str, entity_id: str | None, payload: dict[str, Any], *,
                  correlation_id: str | None, idempotency_key: str, now: datetime) -> str | None:
        if s.execute(select(RiskEvent.id).where(RiskEvent.idempotency_key == idempotency_key)).first() is not None:
            return None
        event_id = str(uuid.uuid4())
        s.add(RiskEvent(event_id=event_id, event_type=event_type, schema_version=1, occurred_at=now,
                        correlation_id=correlation_id, idempotency_key=idempotency_key, entity_id=entity_id,
                        source=SOURCE_MODULE, payload=jsonable(payload), status="pending", attempts=0, next_attempt_at=now))
        return event_id

    @staticmethod
    def event_view(e: RiskEvent) -> dict[str, Any]:
        return {"event_id": e.event_id, "event_type": e.event_type, "schema_version": e.schema_version,
                "timestamp": _utc(e.occurred_at), "correlation_id": e.correlation_id, "idempotency_key": e.idempotency_key,
                "entity_id": e.entity_id, "source_module": e.source, "payload": e.payload, "sequence": e.id,
                "delivery": {"status": e.status, "attempts": e.attempts, "last_error": e.last_error}}

    def events_after(self, after: int, limit: int, event_type: str | None = None) -> list[dict[str, Any]]:
        with self.read() as s:
            q = select(RiskEvent).where(RiskEvent.id > after).order_by(RiskEvent.id).limit(limit)
            if event_type:
                q = q.where(RiskEvent.event_type == event_type)
            return [self.event_view(e) for e in s.execute(q).scalars().all()]

    def due_events(self, s: Session, now: datetime, limit: int) -> list[RiskEvent]:
        return list(s.execute(select(RiskEvent).where(and_(RiskEvent.status == "pending", or_(
            RiskEvent.next_attempt_at.is_(None), RiskEvent.next_attempt_at <= now))).order_by(RiskEvent.id).limit(limit)).scalars().all())

    def event_counts(self) -> dict[str, int]:
        with self.read() as s:
            return {k: int(v) for k, v in s.execute(select(RiskEvent.status, func.count()).group_by(RiskEvent.status)).all()}

    def inbox_get(self, s: Session, event_id: str) -> RiskInboxEvent | None:
        return s.execute(select(RiskInboxEvent).where(RiskInboxEvent.event_id == event_id)).scalar_one_or_none()

    def inbox_rows(self, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        with self.read() as s:
            q = select(RiskInboxEvent).order_by(RiskInboxEvent.id.desc()).limit(limit)
            if status:
                q = q.where(RiskInboxEvent.status == status)
            return [{"event_id": r.event_id, "event_type": r.event_type, "received_at": _utc(r.received_at), "status": r.status,
                     "attempts": r.attempts, "last_error": r.last_error, "result": r.result} for r in s.execute(q).scalars().all()]

    # ---- per-assessment snapshot rows ----------------------------------------------------------------------------------
    def add_assessment(self, s: Session, row: RiskAssessment, rules: list[RiskRuleEvaluation],
                       account: RiskAccountSnapshot, portfolio: RiskPortfolioSnapshot) -> None:
        s.add(row)
        s.flush()
        for r in rules:
            s.add(r)
        s.add(account)
        s.add(portfolio)
        s.flush()


class ConfigConflict(RuntimeError):
    pass
