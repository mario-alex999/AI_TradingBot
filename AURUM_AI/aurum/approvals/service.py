"""ApprovalService: the Module 07 business rules.

Flow: intake -> Module 06 assessment -> evidence snapshot -> approval request (PENDING) -> a person approves, rejects or asks
for a modification -> final validation -> execution handoff (outbox event + queue for Module 09) -> Module 09 reports the
result. Trade management follows the same pattern for open positions.

Rules that hold everywhere:
- Only a signed-in person can decide; services (n8n, Module 09) cannot. Silence, timeouts and AI confidence never approve.
- A Module 06 BLOCK or ERROR can never be approved. REVIEW needs every warning acknowledged (or is refused, per policy).
- Status changes follow ``states.py`` inside one serialised transaction that re-reads the row and checks its version, so
  two people (or a person and the expiry job) cannot both win. Unique constraints back this up in the database.
- Every change writes an audit row and its outbox event in the same transaction. Notifications never affect a decision.
- Module 07 never contacts MT5.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session

from aurum.approvals import MODULE_VERSION, SCHEMA_VERSION, evidence as evidence_mod, intake
from aurum.approvals.db_models import (ApprovalAuditLog, ApprovalDecision, ApprovalEvidenceSnapshot, ApprovalRequest,
                                       ExecutionHandoff, ExecutionResult, ManagedPosition, ModificationRequest,
                                       Notification, NotificationDelivery, ReviewerAssignment, RiskAssessmentSnapshot,
                                       TradeIdea, TradeIdeaVersion, TradeManagementDecision, TradeManagementRequest)
from aurum.approvals.gateways import EvidenceSource, GatewayUnavailable, IdeaSource, PositionSource, RiskGateway
from aurum.approvals.policy import Policy, load_seed
from aurum.approvals.repository import ApprovalRepository, SchemaNotReady, utc
from aurum.approvals.states import (ACTIVE, OPEN, ApprovalStatus as S, IllegalTransition, ManagementStatus as MS, check,
                                    check_management)
from aurum.auth import roles as perm
from aurum.auth.db_models import User
from aurum.auth.service import Principal
from aurum.risk.repository import jsonable, stable_hash

log = logging.getLogger(__name__)

DISCLAIMER = ("Approval authorises an execution request for Module 09, which validates it again before any order. Module 07 "
              "places no orders. AI output and confidence scores are not predictions of profit.")
MANAGEMENT_ACTIONS = ("MOVE_STOP_LOSS", "MOVE_TAKE_PROFIT", "BREAKEVEN", "TRAILING_STOP", "CLOSE", "PARTIAL_CLOSE",
                      "CANCEL_PENDING_ORDER", "MODIFY_PENDING_ORDER", "EMERGENCY_EXIT")
MODIFICATION_KINDS = ("change_entry", "change_stop_loss", "change_take_profit", "change_position_size", "change_direction",
                      "request_additional_analysis", "request_additional_risk_assessment", "request_updated_market_information")
HANDOFF_GRACE = timedelta(minutes=2)


class ApprovalError(Exception):
    def __init__(self, status: int, code: str, message: str, details: Any = None):
        super().__init__(message)
        self.status, self.code, self.message, self.details = status, code, message, details


@dataclass(frozen=True)
class Actor:
    name: str
    kind: str                      # user | service | system
    user_id: str | None = None

    @classmethod
    def of(cls, p: Principal | None) -> "Actor":
        if p is None:
            return SYSTEM
        return cls(p.username, p.kind, p.id if p.kind == "user" else None)


SYSTEM = Actor("system", "system")


def D(v: Any) -> Decimal | None:
    if v is None or v == "":
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None


def _s(v: Any) -> str | None:
    return None if v is None else str(v)


class ApprovalService:
    def __init__(self, engine: Any, settings: Any, risk: RiskGateway, ideas: IdeaSource | None = None,
                 evidence: EvidenceSource | None = None, positions: PositionSource | None = None,
                 clock: Callable[[], datetime] | None = None, seed_policy: Policy | None = None):
        self.engine = engine
        self.settings = settings
        self.repo = ApprovalRepository(engine)
        self.risk, self.ideas, self.evidence_source, self.position_source = risk, ideas, evidence, positions
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._seed = seed_policy

    def now(self) -> datetime:
        return self.clock()

    # ==== policy ===============================================================================================================
    def _policy(self, s: Session) -> tuple[Policy, int]:
        got = self.repo.active_policy(s)
        if got is not None:
            return got
        seed = self._seed or load_seed(getattr(self.settings, "approvals_config", None) or _default_seed_path())
        version = self.repo.add_policy(s, seed, "Seed configuration (config/approvals.yaml).", "system", self.now())
        self.repo.audit(s, actor="system", actor_kind="system", action="policy.seeded", entity_type="approval_policy",
                        entity_id=str(version), at=self.now(), details={"policy_hash": seed.fingerprint()})
        return seed, version

    def policy(self) -> dict[str, Any]:
        with self.repo.write() as s:
            p, v = self._policy(s)
        return {"version": v, "policy": p.dump(), "history": self.repo.policy_history()}

    def update_policy(self, body: dict[str, Any], who: Principal, correlation_id: str | None) -> dict[str, Any]:
        reason = str(body.get("reason") or "").strip()
        if len(reason) < 5:
            raise ApprovalError(422, "REASON_REQUIRED", "Give a reason of at least 5 characters for the policy change.")
        try:
            new = Policy.model_validate(body.get("policy") or {})
        except Exception as exc:  # noqa: BLE001 - pydantic validation error
            raise ApprovalError(422, "INVALID_POLICY", "The policy is not valid.", str(exc)[:2000]) from exc
        with self.repo.write() as s:
            current, version = self._policy(s)
            expected = body.get("expected_version")
            if expected is not None and int(expected) != version:
                raise ApprovalError(409, "STALE_POLICY", f"The policy changed (now v{version}); reload and try again.")
            now = self.now()
            nv = self.repo.add_policy(s, new, reason, who.username, now)
            self.repo.audit(s, actor=who.username, actor_kind=who.kind, action="policy.updated", entity_type="approval_policy",
                            entity_id=str(nv), at=now, reason=reason, correlation_id=correlation_id,
                            previous_state=f"v{version}", new_state=f"v{nv}",
                            details={"before": current.dump(), "after": new.dump()})
            self.repo.emit(s, "approval.policy.updated", {"version": nv, "previous_version": version, "reason": reason,
                                                          "changed_by": who.username}, at=now,
                           correlation_id=correlation_id or str(uuid.uuid4()), idempotency_key=f"policy:{nv}")
        return self.policy()

    # ==== users and assignment =================================================================================================
    def _recipients(self, s: Session, account_login: str | None = None) -> list[str]:
        users = s.execute(select(User).where(User.is_active.is_(True))).scalars().all()
        return [u.id for u in users if u.account_scope is None or account_login is None or account_login in u.account_scope]

    def _assignee(self, s: Session, symbol: str, strategy: str, timeframe: str) -> str | None:
        rows = s.execute(select(ReviewerAssignment).join(User, User.id == ReviewerAssignment.user_id)
                         .where(ReviewerAssignment.active.is_(True), User.is_active.is_(True))
                         .order_by(ReviewerAssignment.created_at)).scalars().all()
        for a in rows:
            if (a.symbol in (None, symbol)) and (a.strategy in (None, strategy)) and (a.timeframe in (None, timeframe)):
                return a.user_id
        return None

    def assignments(self) -> list[dict[str, Any]]:
        with self.repo.read() as s:
            rows = s.execute(select(ReviewerAssignment, User.username).join(User, User.id == ReviewerAssignment.user_id)
                             .order_by(ReviewerAssignment.created_at)).all()
            return [{"id": a.id, "username": name, "symbol": a.symbol, "strategy": a.strategy, "timeframe": a.timeframe,
                     "active": a.active, "created_by": a.created_by, "created_at": a.created_at} for a, name in rows]

    def set_assignment(self, body: dict[str, Any], who: Principal, correlation_id: str | None) -> dict[str, Any]:
        with self.repo.write() as s:
            now = self.now()
            if body.get("id"):
                a = s.get(ReviewerAssignment, str(body["id"]))
                if a is None:
                    raise ApprovalError(404, "NOT_FOUND", "Assignment not found.")
                a.active = bool(body.get("active", False))
                action = "assignment.updated"
            else:
                u = s.execute(select(User).where(User.username == str(body.get("username") or "").lower())).scalar_one_or_none()
                if u is None or u.role not in ("admin", "trader", "reviewer"):
                    raise ApprovalError(422, "INVALID_REVIEWER", "Assign an active admin, trader or reviewer.")
                a = ReviewerAssignment(id=str(uuid.uuid4()), user_id=u.id, symbol=(body.get("symbol") or None),
                                       strategy=(body.get("strategy") or None), timeframe=(body.get("timeframe") or None),
                                       active=True, created_by=who.username, created_at=now)
                s.add(a)
                action = "assignment.created"
            s.flush()
            self.repo.audit(s, actor=who.username, actor_kind=who.kind, action=action, entity_type="reviewer_assignment",
                            entity_id=a.id, at=now, correlation_id=correlation_id,
                            details={"symbol": a.symbol, "strategy": a.strategy, "timeframe": a.timeframe, "active": a.active})
        return {"assignments": self.assignments()}

    # ==== intake ===============================================================================================================
    def _symbols(self) -> dict[str, str]:
        enabled = {s.upper() for s in getattr(self.settings, "enabled_instruments", []) or []}
        try:
            known = self.risk.symbols()
        except GatewayUnavailable:
            known = {}
        return {k: v for k, v in known.items() if not enabled or k in enabled}

    def ingest(self, raw: dict[str, Any], *, source: str, who: Actor, correlation_id: str | None = None,
               source_ref: str | None = None, update: bool = False) -> dict[str, Any]:
        """Validates and stores a trade idea (or a new version of it), then opens its approval request."""
        now = self.now()
        payload = intake.validate(raw, supported_symbols=self._symbols(), now=now, source=source, source_ref=source_ref)
        with self.repo.write() as s:
            idea = s.get(TradeIdea, payload.idea_id)
            if idea is not None:
                cur = self._version(s, idea.idea_id, idea.current_version)
                if cur.payload_hash == payload.payload_hash():
                    req = self._active_request(s, idea.idea_id)
                    return {"idea_id": idea.idea_id, "version": idea.current_version, "duplicate": True,
                            "approval_id": req.approval_id if req else None}
                if not update:
                    raise ApprovalError(409, "DUPLICATE_IDEA", "This idea id already exists with different content; send "
                                                               "it as trade_idea.updated.")
                if cur.analysis_version == payload.analysis_version:
                    raise ApprovalError(422, "INCONSISTENT_ANALYSIS_VERSION", "The parameters changed but the analysis "
                                                                              "version did not.")
                version = idea.current_version + 1
                self._supersede(s, idea.idea_id, now, who, "The trade idea was updated; the new version needs its own approval.")
            else:
                if update:
                    raise ApprovalError(404, "IDEA_NOT_FOUND", "There is no idea with this id to update.")
                version = 1
                idea = TradeIdea(idea_id=payload.idea_id, source=source, source_ref=source_ref, symbol=payload.symbol,
                                 market=payload.market, timeframe=payload.timeframe, strategy=payload.strategy, status="active",
                                 current_version=0, correlation_id=correlation_id or payload.correlation_id, created_at=now,
                                 expires_at=payload.expires_at, updated_at=now)
                s.add(idea)
                s.flush()
            self._add_version(s, idea, version, payload, created_by=who.name, reason=None, now=now)
            self.repo.audit(s, actor=who.name, actor_kind=who.kind, action="idea.received" if version == 1 else "idea.updated",
                            entity_type="trade_idea", entity_id=idea.idea_id, idea_id=idea.idea_id, at=now,
                            correlation_id=idea.correlation_id, details={"version": version, "source": source,
                                                                         "payload_hash": payload.payload_hash()})
        approval = self._open_request(idea.idea_id, version, who, parent=None)
        return {"idea_id": idea.idea_id, "version": version, "duplicate": False, "approval_id": approval.get("approval_id"),
                "status": approval.get("status")}

    def _add_version(self, s: Session, idea: TradeIdea, version: int, p: intake.IdeaPayload, *, created_by: str,
                     reason: str | None, now: datetime, requested_volume: Decimal | None = None) -> TradeIdeaVersion:
        v = TradeIdeaVersion(idea_id=idea.idea_id, version=version, direction=p.direction, entry_low=p.entry_low,
                             entry_high=p.entry_high, entry=p.entry, stop_loss=p.stop_loss,
                             take_profits=[str(t) for t in p.take_profits], risk_reward=p.risk_reward,
                             requested_volume=requested_volume, higher_timeframe_context=jsonable(p.higher_timeframe_context),
                             ai_summary=p.ai_summary, ai_confidence=p.ai_confidence, evidence=jsonable(list(p.evidence)),
                             market_conditions=jsonable(p.market_conditions), analysis_version=p.analysis_version,
                             source_refs=jsonable(list(p.source_refs)), schema_version=p.schema_version,
                             payload=jsonable(p.raw), payload_hash=p.payload_hash(), created_by=created_by, reason=reason,
                             created_at=now, expires_at=p.expires_at)
        s.add(v)
        idea.current_version, idea.updated_at, idea.expires_at, idea.status = version, now, p.expires_at, "active"
        s.flush()
        return v

    def _version(self, s: Session, idea_id: str, version: int) -> TradeIdeaVersion:
        return s.execute(select(TradeIdeaVersion).where(TradeIdeaVersion.idea_id == idea_id,
                                                        TradeIdeaVersion.version == version)).scalar_one()

    def _active_request(self, s: Session, idea_id: str) -> ApprovalRequest | None:
        return s.execute(select(ApprovalRequest).where(ApprovalRequest.idea_id == idea_id,
                                                       ApprovalRequest.status.in_([x.value for x in ACTIVE]))).scalar_one_or_none()

    def _supersede(self, s: Session, idea_id: str, now: datetime, who: Actor, reason: str) -> None:
        req = self._active_request(s, idea_id)
        if req is None:
            return
        if req.status in (S.EXECUTION_PENDING.value, S.EXECUTION_SUBMITTED.value):
            raise ApprovalError(409, "ALREADY_HANDED_OFF", "This idea's approval was already handed to execution; it cannot be "
                                                           "changed. Use trade management for the resulting position.")
        target = S.INVALIDATED.value
        self._move(s, req, target, who, reason, now, event="approval.invalidated")

    # ---- opening a request -----------------------------------------------------------------------------------------------
    def _proposal(self, idea: TradeIdea, v: TradeIdeaVersion) -> dict[str, Any]:
        p = {"symbol": idea.symbol, "direction": v.direction, "entry": str(v.entry), "stop_loss": str(v.stop_loss),
             "take_profit": v.take_profits[0], "strategy": idea.strategy, "timeframe": idea.timeframe,
             "trade_idea_id": idea.idea_id, "source": "module_07"}
        if v.requested_volume is not None:
            p["requested_volume"] = str(v.requested_volume)
        if v.ai_confidence is not None:
            p["ai_confidence"] = float(v.ai_confidence)
        return p

    def _assess(self, idea: TradeIdea, v: TradeIdeaVersion, purpose: str, correlation_id: str) -> dict[str, Any] | None:
        key = f"m07:{idea.idea_id}:v{v.version}:{purpose}:{uuid.uuid4().hex[:12]}"
        try:
            return self.risk.assess(self._proposal(idea, v), "module_07", correlation_id, key)
        except GatewayUnavailable as exc:
            log.warning("Module 06 unavailable during %s", purpose, extra={"idea": idea.idea_id, "error": str(exc)})
            return None
        except Exception as exc:  # noqa: BLE001 - e.g. 422 from Module 06: recorded as ERROR, never as a pass
            log.warning("Module 06 rejected the proposal", extra={"idea": idea.idea_id, "error": str(exc)[:200]})
            return {"decision": "ERROR", "errors": [{"rule_id": "M07", "message": f"Module 06 could not assess: {exc}"[:500]}],
                    "timestamp": self.now()}

    def _version_dict(self, idea: TradeIdea, v: TradeIdeaVersion) -> dict[str, Any]:
        return {"idea_id": idea.idea_id, "version": v.version, "symbol": idea.symbol, "market": idea.market,
                "direction": v.direction, "entry": str(v.entry), "entry_low": str(v.entry_low), "entry_high": str(v.entry_high),
                "stop_loss": str(v.stop_loss), "take_profits": v.take_profits, "evidence": v.evidence,
                "higher_timeframe_context": v.higher_timeframe_context, "ai_summary": v.ai_summary,
                "ai_confidence": _s(v.ai_confidence), "analysis_version": v.analysis_version}

    def _gather(self, idea: TradeIdea, v: TradeIdeaVersion, risk: dict[str, Any] | None) -> dict[str, Any]:
        """Collects evidence from the other modules (slow; called outside the write lock)."""
        now = self.now()
        candidate = review = None
        gates: list[dict[str, Any]] = []
        if idea.source == "module05" and self.ideas is not None and idea.source_ref:
            candidate = self.ideas.candidate(idea.source_ref)
            review = self.ideas.ai_review(idea.source_ref)
            gates = self.ideas.gates(idea.source_ref)
        market = self.evidence_source.market_context(idea.symbol, now) if self.evidence_source is not None else None
        return evidence_mod.build(self._version_dict(idea, v), risk, candidate, review, gates, market, now)

    def _open_request(self, idea_id: str, version: int, who: Actor, parent: str | None) -> dict[str, Any]:
        with self.repo.read() as s:
            idea = s.get(TradeIdea, idea_id)
            v = self._version(s, idea_id, version)
            s.expunge_all()
        cid = idea.correlation_id
        risk = self._assess(idea, v, "intake", cid)
        ev = self._gather(idea, v, risk)
        now = self.now()
        with self.repo.write() as s:
            policy, pv = self._policy(s)
            key = f"{idea_id}:v{version}"
            existing = s.execute(select(ApprovalRequest).where(ApprovalRequest.idempotency_key == key)).scalar_one_or_none()
            current = s.get(TradeIdea, idea_id)
            if existing is not None or current.current_version != version or current.status != "active":
                if risk and risk.get("assessment_id"):
                    self._cancel_assessment_later(risk["assessment_id"], "Superseded before the request opened.")
                return self.request_view(existing) if existing is not None else {"approval_id": None, "status": "superseded"}
            high_vol = str((((risk or {}).get("market_status") or {}).get("volatility_regime")) or "").upper() in ("HIGH", "VERY_HIGH", "EXTREME")
            expires, expiry_reason = policy.expires_at(now, idea.timeframe, idea.symbol, idea.strategy, v.expires_at, high_vol)
            decision = (risk or {}).get("decision") or "ERROR"
            account = self._account_login()
            approval_id = str(uuid.uuid4())
            snap = ApprovalEvidenceSnapshot(id=str(uuid.uuid4()), approval_id=approval_id, idea_id=idea_id, idea_version=version,
                                            content=ev["content"], content_hash=ev["hash"], completeness=ev["completeness"],
                                            created_at=now)        # insert-only: the snapshot is never updated afterwards
            s.add(snap)
            req = ApprovalRequest(
                approval_id=approval_id, idea_id=idea_id, idea_version=version, parent_approval_id=parent,
                status=S.PENDING.value, version=1, assigned_user_id=self._assignee(s, idea.symbol, idea.strategy, idea.timeframe),
                symbol=idea.symbol, direction=v.direction, timeframe=idea.timeframe, strategy=idea.strategy, entry=v.entry,
                stop_loss=v.stop_loss, take_profit=D(v.take_profits[0]), risk_decision=decision,
                analysis_version=v.analysis_version, policy_version=pv, account_login=account, ai_confidence=v.ai_confidence,
                approvals_required=self._approvals_required(policy, risk), idempotency_key=key, correlation_id=cid,
                created_at=now, expires_at=expires, status_changed_at=now, status_reason=f"Opened; expires by {expiry_reason}.",
                evidence_snapshot_id=snap.id, evidence_hash=ev["hash"])
            self._apply_risk(req, risk)
            s.add(req)
            s.flush()
            self._store_risk(s, req.approval_id, idea_id, version, "intake", risk, now)
            self.repo.audit(s, actor=who.name, actor_kind=who.kind, action="approval.requested", entity_type="approval_request",
                            entity_id=req.approval_id, approval_id=req.approval_id, idea_id=idea_id, new_state=req.status,
                            at=now, correlation_id=cid, evidence_snapshot_id=snap.id,
                            details={"risk_decision": decision, "expires_at": expires, "parent_approval_id": parent})
            self.repo.emit(s, "approval.requested", self._event_payload(req), at=now, correlation_id=cid,
                           idempotency_key=f"approval.requested:{req.approval_id}")
            recipients = self._recipients(s, account)
            channels = policy.notifications.channels
            self.repo.notify(s, dedupe_key=f"requested:{req.approval_id}", event_type="approval.requested",
                             priority="normal" if decision in ("PASS", "REVIEW") else "high",
                             title=f"New approval request: {req.direction} {req.symbol}",
                             message=f"{req.strategy} on {req.timeframe}. Risk decision {decision}. Expires "
                                     f"{expires:%H:%M} UTC.", at=now, recipients=recipients, channels=channels,
                             approval_id=req.approval_id)
            if req.assigned_user_id:
                self.repo.notify(s, dedupe_key=f"assigned:{req.approval_id}", event_type="approval.assigned", priority="normal",
                                 title=f"Assigned to you: {req.direction} {req.symbol}", message="You are the assigned reviewer.",
                                 at=now, recipients=[req.assigned_user_id], channels=channels, approval_id=req.approval_id,
                                 recipient_user_id=req.assigned_user_id)
            if decision in ("BLOCK", "ERROR"):
                blockers = [f"{r.get('rule_id')}: {r.get('message')}" for r in (risk or {}).get("rule_violations", []) + (risk or {}).get("errors", [])]
                self.repo.notify(s, dedupe_key=f"risk-blocked:{req.approval_id}", event_type="risk.blocked", priority="high",
                                 title=f"Risk {decision}: {req.direction} {req.symbol} cannot be approved",
                                 message="; ".join(blockers)[:900] or "Module 06 returned no usable assessment.", at=now,
                                 recipients=recipients, channels=channels, approval_id=req.approval_id)
            return self.request_view(req)

    def _approvals_required(self, policy: Policy, risk: dict[str, Any] | None) -> int:
        mp = policy.review.multi_person
        if not mp.enabled:
            return 1
        pct = D((risk or {}).get("risk_pct"))
        if mp.min_risk_pct is None or (pct is not None and pct >= mp.min_risk_pct):
            return mp.required_approvals
        return 1

    def _apply_risk(self, req: ApprovalRequest, risk: dict[str, Any] | None) -> None:
        r = risk or {}
        req.risk_decision = r.get("decision") or "ERROR"
        req.risk_assessment_id = r.get("assessment_id")
        req.risk_assessed_at = _parse_time(r.get("timestamp"))
        req.risk_expires_at = _parse_time(r.get("expires_at"))
        req.risk_config_version = r.get("config_version")
        req.position_size = D(r.get("final_volume"))
        req.risk_amount = D(r.get("monetary_risk"))
        req.risk_pct = D(r.get("risk_pct"))
        req.risk_reward = D(r.get("risk_reward"))
        req.risk_warnings = jsonable([{"rule_id": w.get("rule_id"), "name": w.get("name"), "message": w.get("message")}
                                      for w in r.get("warnings") or []])

    def _store_risk(self, s: Session, approval_id: str | None, idea_id: str | None, version: int | None, purpose: str,
                    risk: dict[str, Any] | None, now: datetime, management_request_id: str | None = None) -> None:
        r = risk or {"decision": "ERROR", "errors": [{"message": "Module 06 unavailable."}]}
        s.add(RiskAssessmentSnapshot(id=str(uuid.uuid4()), approval_id=approval_id, management_request_id=management_request_id,
                                     idea_id=idea_id, idea_version=version, purpose=purpose, assessment_id=r.get("assessment_id"),
                                     decision=r.get("decision") or "ERROR", final_volume=D(r.get("final_volume")),
                                     risk_amount=D(r.get("monetary_risk")), risk_pct=D(r.get("risk_pct")),
                                     config_version=r.get("config_version"), assessed_at=_parse_time(r.get("timestamp")) or now,
                                     expires_at=_parse_time(r.get("expires_at")), payload=jsonable(r), created_at=now))
        s.flush()

    def _account_login(self) -> str | None:
        try:
            return self.risk.account_login()
        except GatewayUnavailable:
            return None

    def _cancel_assessment_later(self, assessment_id: str | None, reason: str) -> None:
        """Frees a Module 06 reservation. Called after the Module 07 transaction commits; a failure is logged, not fatal
        (Module 06 reservations also expire on their own)."""
        if not assessment_id:
            return
        try:
            self.risk.cancel(assessment_id, "module_07", reason, None)
        except Exception as exc:  # noqa: BLE001
            log.warning("Could not cancel Module 06 assessment", extra={"assessment": assessment_id, "error": str(exc)[:200]})

    # ==== status changes ========================================================================================================
    def _move(self, s: Session, req: ApprovalRequest, target: str, who: Actor, reason: str, now: datetime, *,
              event: str | None = None, details: dict[str, Any] | None = None) -> None:
        """The only place an approval's status changes. Checks the state machine, bumps the version, audits and emits."""
        previous = req.status
        check(previous, target)
        res = s.execute(update(ApprovalRequest)
                        .where(ApprovalRequest.approval_id == req.approval_id, ApprovalRequest.version == req.version,
                               ApprovalRequest.status == previous)
                        .values(status=target, version=req.version + 1, status_changed_at=now, status_reason=reason[:2000],
                                decided_at=now if target in (S.APPROVED.value, S.REJECTED.value,
                                                             S.MODIFICATION_REQUESTED.value) else req.decided_at))
        if res.rowcount != 1:
            raise ApprovalError(409, "CONCURRENT_CHANGE", "This request changed while you were acting on it. Reload it.")
        s.refresh(req)
        self.repo.audit(s, actor=who.name, actor_kind=who.kind, action=f"status.{target.lower()}", entity_type="approval_request",
                        entity_id=req.approval_id, approval_id=req.approval_id, idea_id=req.idea_id, previous_state=previous,
                        new_state=target, reason=reason, correlation_id=req.correlation_id, at=now,
                        evidence_snapshot_id=req.evidence_snapshot_id, details=details)
        if event:
            self.repo.emit(s, event, {**self._event_payload(req), "previous_status": previous, "reason": reason},
                           at=now, correlation_id=req.correlation_id, idempotency_key=f"{event}:{req.approval_id}:{req.version}")

    def _load(self, s: Session, approval_id: str, lock: bool = True) -> ApprovalRequest:
        q = select(ApprovalRequest).where(ApprovalRequest.approval_id == approval_id)
        if lock and self.engine.dialect.name == "postgresql":
            q = q.with_for_update()
        req = s.execute(q).scalar_one_or_none()
        if req is None:
            raise ApprovalError(404, "NOT_FOUND", "Approval request not found.")
        return req

    def _visible(self, s: Session, req: ApprovalRequest, who: Principal) -> None:
        if not who.may_access_account(req.account_login):
            raise ApprovalError(404, "NOT_FOUND", "Approval request not found.")      # do not reveal other accounts' requests

    def _replay(self, s: Session, scope: str, key: str | None, body: dict[str, Any]) -> dict[str, Any] | None:
        if not key:
            raise ApprovalError(422, "CLIENT_REQUEST_ID_REQUIRED", "clientRequestId is required for this action.")
        rec = self.repo.idempotent(s, scope, key)
        if rec is None:
            return None
        if rec.request_hash != stable_hash(body):
            raise ApprovalError(409, "IDEMPOTENCY_CONFLICT", "This clientRequestId was already used for a different request.")
        return {**(rec.response or {}), "replayed": True}

    def start_review(self, approval_id: str, who: Principal) -> dict[str, Any]:
        """Marks a request as being reviewed. Opening a request never approves it."""
        with self.repo.write() as s:
            req = self._load(s, approval_id)
            self._visible(s, req, who)
            if req.status == S.PENDING.value:
                now = self.now()
                self._move(s, req, S.UNDER_REVIEW.value, Actor.of(who), f"Review started by {who.username}.", now,
                           event="approval.under_review")
                req.review_started_by = who.id
            return self.request_view(req)

    # ---- approve -----------------------------------------------------------------------------------------------------------
    def approve(self, approval_id: str, who: Principal, body: dict[str, Any], method: str = "web") -> dict[str, Any]:
        """Explicit human approval after every check in section 8 of the specification. See docs/module07/APPROVAL_STATE_MACHINE.md."""
        if not who.is_human or not who.can(perm.DECIDE):
            raise ApprovalError(403, "FORBIDDEN", "Only a signed-in person with approval permission can approve.")
        key = body.get("client_request_id")
        with self.repo.read() as s:
            req = self._load(s, approval_id, lock=False)
            self._visible(s, req, who)
            rec = self._replay(s, f"approve:{approval_id}", key, body)
            if rec is not None:
                return rec
            idea = s.get(TradeIdea, req.idea_id)
            s.expunge_all()
        # Slow checks against other modules, outside the write lock. Results are re-checked for staleness inside it.
        problems: list[str] = []
        risk_check = None
        if req.risk_assessment_id:
            try:
                risk_check = self.risk.validate(req.risk_assessment_id)
            except GatewayUnavailable:
                problems.append("Module 06 is unavailable, so the risk assessment cannot be confirmed. Try again shortly.")
        quote_at = self._quote_time(req.symbol)
        locks = self._locks(req.symbol, req.strategy)
        idea_ok, idea_reason = self._idea_still_valid(idea)

        now = self.now()
        with self.repo.write() as s:
            policy, pv = self._policy(s)
            req = self._load(s, approval_id)
            failure: ApprovalError | None = None
            # 3/4: still open and not expired (an expired request is moved to EXPIRED here, then refused)
            if req.status not in (S.PENDING.value, S.UNDER_REVIEW.value):
                raise ApprovalError(409, "NOT_PENDING", f"This request is {req.status}; it can no longer be approved.")
            if req.expires_at <= now:
                self._move(s, req, S.EXPIRED.value, SYSTEM, "Expired before approval.", now, event="approval.expired")
                failure = ApprovalError(409, "EXPIRED", "This request has expired and cannot be approved.")
            elif int(body.get("request_version") or 0) != req.version:
                raise ApprovalError(409, "STALE_VERSION", "The request changed since you loaded it (for example a risk "
                                                          "revalidation). Review it again before approving.")
            if failure is None:
                errs = self._approval_problems(s, req, idea_ok, idea_reason, risk_check, quote_at, locks, policy, who, body, now)
                errs = problems + errs
                if errs:
                    self.repo.audit(s, actor=who.username, actor_kind="user", action="approve.refused",
                                    entity_type="approval_request", entity_id=req.approval_id, approval_id=req.approval_id,
                                    idea_id=req.idea_id, at=now, reason="; ".join(errs)[:2000], correlation_id=req.correlation_id)
                    failure = ApprovalError(409, "APPROVAL_CHECKS_FAILED", "The trade cannot be approved.", errs)
            if failure is None:
                # 12: one decision per person; the final decision is unique in the database
                if s.execute(select(ApprovalDecision.id).where(ApprovalDecision.approval_id == approval_id,
                                                               ApprovalDecision.user_id == who.id)).first():
                    raise ApprovalError(409, "ALREADY_DECIDED", "You have already recorded a decision on this request.")
                prior = s.execute(select(func.count()).select_from(ApprovalDecision).where(
                    ApprovalDecision.approval_id == approval_id, ApprovalDecision.decision == "APPROVED")).scalar_one()
                final = prior + 1 >= req.approvals_required
                dec = self._decision(req, who, "APPROVED", final, body, method, now, pv)
                s.add(dec)
                s.flush()
                if final:
                    self._move(s, req, S.APPROVED.value, Actor.of(who), body.get("comment") or "Approved.", now,
                               event="approval.approved", details={"decision_id": dec.id, "approvers": prior + 1})
                    self.repo.emit(s, "module05.feedback", {"idea_id": req.idea_id, "decision": "APPROVED",
                                                            "approval_id": req.approval_id, "by": who.username},
                                   at=now, correlation_id=req.correlation_id, idempotency_key=f"m05fb:{req.approval_id}")
                    self._notify(s, policy, req, "approval.approved", "normal", f"Approved: {req.direction} {req.symbol}",
                                 f"Approved by {who.display_name}. Final validation and handoff to execution follow.", now)
                else:
                    self.repo.audit(s, actor=who.username, actor_kind="user", action="approve.partial",
                                    entity_type="approval_request", entity_id=req.approval_id, approval_id=req.approval_id,
                                    idea_id=req.idea_id, at=now, correlation_id=req.correlation_id,
                                    details={"approvals": prior + 1, "required": req.approvals_required})
                response = self.request_view(req)
                self.repo.remember(s, f"approve:{approval_id}", key, stable_hash(body), 200, response, now)
        if failure is not None:
            raise failure
        if response["status"] == S.APPROVED.value:
            try:
                response = self.handoff(approval_id)
            except Exception as exc:  # noqa: BLE001 - the approval stands; the worker retries the handoff
                log.warning("Handoff deferred to the worker", extra={"approval": approval_id, "error": str(exc)[:200]})
        return response

    def _approval_problems(self, s: Session, req: ApprovalRequest, idea_ok: bool, idea_reason: str | None,
                           risk_check: dict[str, Any] | None, quote_at: datetime | None, locks: list[dict[str, Any]] | None,
                           policy: Policy, who: Principal, body: dict[str, Any], now: datetime) -> list[str]:
        errs: list[str] = []
        rv = policy.review
        # 5/6: idea still valid and not cancelled
        if not idea_ok:
            errs.append(idea_reason or "The trade idea is no longer valid.")
        idea = s.get(TradeIdea, req.idea_id)
        if idea.current_version != req.idea_version:
            errs.append("The trade idea has a newer version; this request is out of date.")
        # 7: evidence available; the person saw the current snapshot
        snap = s.get(ApprovalEvidenceSnapshot, req.evidence_snapshot_id) if req.evidence_snapshot_id else None
        if snap is None:
            errs.append("No evidence snapshot is available.")
        else:
            for sec in rv.required_evidence:
                if (snap.completeness or {}).get(sec) == "missing":
                    errs.append(f"Required evidence is missing: {sec}.")
            if body.get("evidence_hash") != snap.content_hash:
                errs.append("The evidence changed since you viewed it. Review the current evidence before approving.")
        # 8/9: risk decision and REVIEW policy
        if req.risk_decision in ("BLOCK", "ERROR"):
            errs.append(f"Module 06 decision is {req.risk_decision}. It can never be approved, by anyone.")
        elif req.risk_decision == "REVIEW":
            if rv.risk_review_policy == "block":
                errs.append("Policy does not allow approving a Module 06 REVIEW decision.")
            else:
                need = {w.get("rule_id") for w in req.risk_warnings or []}
                have = set(body.get("acknowledged_warnings") or [])
                if not need <= have:
                    errs.append("Acknowledge every risk warning before approving: " + ", ".join(sorted(need - have)) + ".")
        if risk_check is not None and not risk_check.get("valid"):
            errs.append("Module 06 says the assessment is no longer valid: " + "; ".join(risk_check.get("reasons") or []))
        elif risk_check is None and req.risk_assessment_id is None and req.risk_decision not in ("BLOCK", "ERROR"):
            errs.append("There is no Module 06 assessment to rely on.")
        if req.risk_assessed_at is None or (now - req.risk_assessed_at).total_seconds() > rv.risk_max_age_seconds:
            errs.append(f"The risk assessment is older than {rv.risk_max_age_seconds} s. Revalidate the risk first.")
        # 10: market data freshness
        if quote_at is None:
            errs.append("No current quote is available for this instrument.")
        elif (now - quote_at).total_seconds() > rv.market_data_max_age_seconds:
            errs.append(f"Market data is stale: the last quote is {int((now - quote_at).total_seconds())} s old "
                        f"(limit {rv.market_data_max_age_seconds} s).")
        # 11: the person confirmed exactly the current parameters and maximum risk
        errs += self._confirmation_problems(req, body.get("confirmation") or {})
        # 13: account permissions
        if not who.may_access_account(req.account_login):
            errs.append("You are not permitted to act on this trading account.")
        allowed = policy.handoff.allowed_accounts
        if allowed and (req.account_login or "") not in allowed:
            errs.append("This trading account is not allowed for execution by policy.")
        # 14: emergency locks
        if locks is None:
            errs.append("Module 06 locks cannot be read, so approval is not possible right now.")
        elif locks:
            errs.append("A risk lock is active: " + "; ".join(str(lk.get("reason")) for lk in locks))
        if rv.require_comment_on_approve and not str(body.get("comment") or "").strip():
            errs.append("A comment is required to approve.")
        if rv.reviewers_see_assigned_only and who.role.value == "reviewer" and req.assigned_user_id not in (None, who.id):
            errs.append("This request is assigned to another reviewer.")
        return errs

    @staticmethod
    def _confirmation_problems(req: ApprovalRequest, c: dict[str, Any]) -> list[str]:
        expected = {"symbol": req.symbol, "direction": req.direction, "entry": req.entry, "stop_loss": req.stop_loss,
                    "take_profit": req.take_profit, "position_size": req.position_size, "max_risk_amount": req.risk_amount}
        bad = []
        for k, v in expected.items():
            got = c.get(k)
            if isinstance(v, Decimal) or k in ("position_size", "max_risk_amount"):
                if (v is None) != (got is None) or (v is not None and D(got) != v):
                    bad.append(k)
            elif str(got or "").upper() != str(v).upper():
                bad.append(k)
        if bad:
            return ["The confirmed values do not match the current request (" + ", ".join(bad) + "). Nothing was approved."]
        return []

    def _decision(self, req: ApprovalRequest, who: Principal, decision: str, final: bool, body: dict[str, Any], method: str,
                  now: datetime, policy_version: int) -> ApprovalDecision:
        record = {"approval_id": req.approval_id, "decision": decision, "user_id": who.id, "at": now,
                  "evidence_hash": req.evidence_hash, "risk_assessment_id": req.risk_assessment_id,
                  "analysis_version": req.analysis_version, "policy_version": policy_version,
                  "client_request_id": body.get("client_request_id"), "params": {"entry": req.entry, "stop_loss": req.stop_loss,
                                                                                 "take_profit": req.take_profit,
                                                                                 "volume": req.position_size}}
        return ApprovalDecision(
            id=str(uuid.uuid4()), approval_id=req.approval_id, decision=decision, is_final=final, user_id=who.id,
            username=who.username, role=who.role.value, method=method[:16], decided_at=now,
            evidence_snapshot_id=req.evidence_snapshot_id, evidence_hash=req.evidence_hash,
            risk_assessment_id=req.risk_assessment_id, risk_decision=req.risk_decision, analysis_version=req.analysis_version,
            policy_version=policy_version, client_request_id=str(body.get("client_request_id"))[:128],
            reason_code=(str(body["reason_code"])[:48] if body.get("reason_code") else None),
            comment=(str(body["comment"])[:2000] if body.get("comment") else None),
            acknowledged_warnings=jsonable(body.get("acknowledged_warnings")), confirmation=jsonable(body.get("confirmation")),
            record_hash=stable_hash(record))

    def _quote_time(self, symbol: str) -> datetime | None:
        try:
            return self.risk.quote_time(symbol, self.now())
        except GatewayUnavailable:
            return None

    def _locks(self, symbol: str, strategy: str) -> list[dict[str, Any]] | None:
        try:
            return self.risk.active_locks(symbol, strategy)
        except GatewayUnavailable:
            return None

    def _idea_still_valid(self, idea: TradeIdea) -> tuple[bool, str | None]:
        if idea.status != "active":
            return False, f"The trade idea is {idea.status}."
        if idea.expires_at <= self.now():
            return False, "The trade idea itself has expired."
        if idea.source == "module05" and self.ideas is not None and idea.source_ref:
            c = self.ideas.candidate(idea.source_ref)
            if c is None:
                return False, "Module 05 no longer has this trade idea."
            if c["status"] not in ("new", "under_review", "approved"):
                return False, f"Module 05 marked the idea {c['status']}."
        return True, None

    # ---- reject / modify / cancel ------------------------------------------------------------------------------------------
    def reject(self, approval_id: str, who: Principal, body: dict[str, Any]) -> dict[str, Any]:
        if not who.is_human or not who.can(perm.DECIDE):
            raise ApprovalError(403, "FORBIDDEN", "Only a signed-in person with approval permission can reject.")
        key = body.get("client_request_id")
        now = self.now()
        with self.repo.write() as s:
            policy, pv = self._policy(s)
            req = self._load(s, approval_id)
            self._visible(s, req, who)
            rec = self._replay(s, f"reject:{approval_id}", key, body)
            if rec is not None:
                return rec
            if req.status not in (S.PENDING.value, S.UNDER_REVIEW.value):
                raise ApprovalError(409, "NOT_PENDING", f"This request is {req.status}; it can no longer be rejected.")
            if int(body.get("request_version") or 0) != req.version:
                raise ApprovalError(409, "STALE_VERSION", "The request changed since you loaded it. Reload it.")
            reason = str(body.get("reason_code") or "")
            if policy.review.rejection_reason_required and reason not in policy.review.rejection_reasons:
                raise ApprovalError(422, "REASON_REQUIRED", "Choose a rejection reason.", policy.review.rejection_reasons)
            if reason == "other" and not str(body.get("comment") or "").strip():
                raise ApprovalError(422, "COMMENT_REQUIRED", "Explain the reason when choosing 'other'.")
            dec = self._decision(req, who, "REJECTED", True, body, "web", now, pv)
            s.add(dec)
            s.flush()
            text_reason = f"Rejected: {reason.replace('_', ' ')}" + (f". {body['comment']}" if body.get("comment") else "")
            self._move(s, req, S.REJECTED.value, Actor.of(who), text_reason, now, event="approval.rejected",
                       details={"decision_id": dec.id, "reason_code": reason})
            self.repo.emit(s, "module05.feedback", {"idea_id": req.idea_id, "decision": "REJECTED", "reason_code": reason,
                                                    "comment": body.get("comment"), "approval_id": req.approval_id,
                                                    "by": who.username},
                           at=now, correlation_id=req.correlation_id, idempotency_key=f"m05fb:{req.approval_id}")
            self._notify(s, policy, req, "approval.rejected", "normal", f"Rejected: {req.direction} {req.symbol}", text_reason, now)
            response = self.request_view(req)
            self.repo.remember(s, f"reject:{approval_id}", key, stable_hash(body), 200, response, now)
            assessment = req.risk_assessment_id
        self._cancel_assessment_later(assessment, "Rejected in Module 07.")
        return response

    def request_modification(self, approval_id: str, who: Principal, body: dict[str, Any]) -> dict[str, Any]:
        """Closes this request as MODIFICATION_REQUESTED and records what should change. Parameter changes create a new
        version of the idea (the original is kept) and a new request with a fresh Module 06 assessment. Requests for more
        analysis go back to Module 05 as an event; nothing changes silently."""
        if not who.is_human or not who.can(perm.DECIDE):
            raise ApprovalError(403, "FORBIDDEN", "Only a signed-in person with approval permission can request changes.")
        kinds = [k for k in body.get("kinds") or [] if k in MODIFICATION_KINDS]
        if not kinds:
            raise ApprovalError(422, "KINDS_REQUIRED", "Say what should change.", list(MODIFICATION_KINDS))
        changes = {k: body.get("changes", {}).get(k) for k in ("entry", "stop_loss", "take_profit", "position_size", "direction")
                   if (body.get("changes") or {}).get(k) not in (None, "")}
        key = body.get("client_request_id")
        now = self.now()
        with self.repo.write() as s:
            policy, pv = self._policy(s)
            req = self._load(s, approval_id)
            self._visible(s, req, who)
            rec = self._replay(s, f"modify:{approval_id}", key, body)
            if rec is not None:
                return rec
            if req.status not in (S.PENDING.value, S.UNDER_REVIEW.value):
                raise ApprovalError(409, "NOT_PENDING", f"This request is {req.status}; changes can no longer be requested.")
            if int(body.get("request_version") or 0) != req.version:
                raise ApprovalError(409, "STALE_VERSION", "The request changed since you loaded it. Reload it.")
            idea = s.get(TradeIdea, req.idea_id)
            v = self._version(s, req.idea_id, req.idea_version)
            previous = {"entry": str(v.entry), "stop_loss": str(v.stop_loss), "take_profit": v.take_profits[0],
                        "direction": v.direction, "position_size": _s(req.position_size)}
            new_payload = None
            if changes:
                raw = dict(v.payload)
                raw.update({"direction": changes.get("direction", v.direction),
                            "entry": str(changes.get("entry", v.entry)),
                            "entry_low": str(changes.get("entry", v.entry_low) if "entry" in changes else v.entry_low),
                            "entry_high": str(changes.get("entry", v.entry_high) if "entry" in changes else v.entry_high),
                            "stop_loss": str(changes.get("stop_loss", v.stop_loss)),
                            "take_profits": [str(changes["take_profit"])] if "take_profit" in changes else v.take_profits,
                            "analysis_version": f"{v.analysis_version.split('+m')[0]}+m{req.idea_version + 1}",
                            "expires_at": max(_parse_time(raw.get("expires_at")) or now, now + timedelta(minutes=5)).isoformat()})
                if "entry" in changes:
                    raw["entry_low"] = raw["entry_high"] = raw["entry"]
                try:
                    new_payload = intake.validate(raw, supported_symbols=self._symbols(), now=now, source=idea.source,
                                                  source_ref=idea.source_ref)
                except intake.IntakeError as exc:
                    raise ApprovalError(422, exc.code, f"The requested change is not valid: {exc.message}", exc.details) from exc
                if "position_size" in changes and (D(changes["position_size"]) is None or D(changes["position_size"]) <= 0):
                    raise ApprovalError(422, "INVALID_VOLUME", "position_size must be a positive number.")
            dec = self._decision(req, who, "MODIFICATION_REQUESTED", True, body, "web", now, pv)
            s.add(dec)
            mod = ModificationRequest(id=str(uuid.uuid4()), approval_id=approval_id, requested_by=who.id, kinds=kinds,
                                      changes=jsonable(changes) or None, previous=previous,
                                      comment=(str(body["comment"])[:2000] if body.get("comment") else None),
                                      status="revised" if new_payload else "awaiting_module05", created_at=now)
            s.add(mod)
            s.flush()
            self._move(s, req, S.MODIFICATION_REQUESTED.value, Actor.of(who),
                       f"Modification requested: {', '.join(kinds)}." + (f" {body['comment']}" if body.get("comment") else ""),
                       now, event="approval.modification_requested",
                       details={"modification_id": mod.id, "changes": jsonable(changes)})
            new_version = None
            if new_payload is not None:
                new_version = req.idea_version + 1
                self._add_version(s, idea, new_version, new_payload, created_by=who.username, now=now,
                                  reason=f"Modification {mod.id} requested on approval {approval_id}.",
                                  requested_volume=D(changes.get("position_size")))
                mod.new_idea_version = new_version
            self._notify(s, policy, req, "approval.modification_requested", "normal",
                         f"Changes requested: {req.direction} {req.symbol}", f"{who.display_name} asked for: {', '.join(kinds)}.",
                         now)
            assessment = req.risk_assessment_id
        self._cancel_assessment_later(assessment, "Modification requested in Module 07.")
        out: dict[str, Any] = {"modification_id": mod.id, "previous_approval_id": approval_id, "status": mod.status}
        if new_version is not None:
            new_req = self._open_request(req.idea_id, new_version, Actor.of(who), parent=approval_id)
            with self.repo.write() as s:
                m = s.get(ModificationRequest, mod.id)
                m.new_approval_id = new_req.get("approval_id")
            out["new_approval"] = new_req
        return out

    def cancel(self, approval_id: str, who: Actor | Principal, reason: str) -> dict[str, Any]:
        actor = who if isinstance(who, Actor) else Actor.of(who)
        if not reason.strip():
            raise ApprovalError(422, "REASON_REQUIRED", "Give a reason for cancelling.")
        now = self.now()
        with self.repo.write() as s:
            policy, _ = self._policy(s)
            req = self._load(s, approval_id)
            if isinstance(who, Principal):
                self._visible(s, req, who)
            if req.status not in (S.PENDING.value, S.UNDER_REVIEW.value, S.APPROVED.value):
                raise ApprovalError(409, "NOT_CANCELLABLE", f"A {req.status} request cannot be cancelled.")
            self._move(s, req, S.CANCELLED.value, actor, f"Cancelled: {reason}", now, event="approval.cancelled")
            self._notify(s, policy, req, "approval.cancelled", "normal", f"Cancelled: {req.direction} {req.symbol}", reason, now)
            view, assessment = self.request_view(req), req.risk_assessment_id
        self._cancel_assessment_later(assessment, "Cancelled in Module 07.")
        return view

    def revalidate_risk(self, approval_id: str, who: Principal | Actor) -> dict[str, Any]:
        """Fresh Module 06 assessment and evidence for an open request. The request version increases, so anyone holding the
        old view must review again before approving."""
        actor = who if isinstance(who, Actor) else Actor.of(who)
        with self.repo.read() as s:
            req = self._load(s, approval_id, lock=False)
            if isinstance(who, Principal):
                self._visible(s, req, who)
            if req.status not in (S.PENDING.value, S.UNDER_REVIEW.value):
                raise ApprovalError(409, "NOT_PENDING", f"Only an open request can be revalidated (this one is {req.status}).")
            idea, v = s.get(TradeIdea, req.idea_id), self._version(s, req.idea_id, req.idea_version)
            old = req.risk_assessment_id
            s.expunge_all()
        self._cancel_assessment_later(old, "Replaced by a revalidation in Module 07.")    # frees R047 for the new one
        risk = self._assess(idea, v, "revalidate", req.correlation_id)
        ev = self._gather(idea, v, risk)
        now = self.now()
        with self.repo.write() as s:
            policy, _ = self._policy(s)
            req = self._load(s, approval_id)
            if req.status not in (S.PENDING.value, S.UNDER_REVIEW.value):
                stale = (risk or {}).get("assessment_id")
                raise_after = ApprovalError(409, "NOT_PENDING", f"The request became {req.status} during revalidation.")
            else:
                stale, raise_after = None, None
                before = req.risk_decision
                snap = ApprovalEvidenceSnapshot(id=str(uuid.uuid4()), approval_id=approval_id, idea_id=req.idea_id,
                                                idea_version=req.idea_version, content=ev["content"], content_hash=ev["hash"],
                                                completeness=ev["completeness"], created_at=now)
                s.add(snap)
                s.flush()
                self._apply_risk(req, risk)
                req.evidence_snapshot_id, req.evidence_hash = snap.id, ev["hash"]
                req.version += 1
                self._store_risk(s, approval_id, req.idea_id, req.idea_version, "revalidate", risk, now)
                self.repo.audit(s, actor=actor.name, actor_kind=actor.kind, action="risk.revalidated",
                                entity_type="approval_request", entity_id=approval_id, approval_id=approval_id,
                                idea_id=req.idea_id, at=now, correlation_id=req.correlation_id, evidence_snapshot_id=snap.id,
                                details={"before": before, "after": req.risk_decision,
                                         "assessment_id": req.risk_assessment_id})
                self.repo.emit(s, "approval.risk_revalidated", {**self._event_payload(req), "previous_risk_decision": before},
                               at=now, correlation_id=req.correlation_id,
                               idempotency_key=f"approval.risk_revalidated:{approval_id}:{req.version}")
                if req.risk_decision in ("BLOCK", "ERROR") and before not in ("BLOCK", "ERROR"):
                    self._notify(s, policy, req, "risk.blocked", "high", f"Risk {req.risk_decision}: {req.direction} {req.symbol}",
                                 "The fresh Module 06 assessment no longer allows approval.", now)
                view = self.request_view(req)
        if raise_after is not None:
            self._cancel_assessment_later(stale, "Request closed during revalidation.")
            raise raise_after
        return view

    # ==== expiry ================================================================================================================
    def expire_due(self) -> dict[str, int]:
        """Moves open requests past their expiry to EXPIRED. Safe to run repeatedly and concurrently: each row changes only if
        it is still open and unchanged, and an approved request is never touched."""
        now = self.now()
        expired: list[str | None] = []
        warned = 0
        with self.repo.write() as s:
            policy, _ = self._policy(s)
            due = s.execute(select(ApprovalRequest).where(ApprovalRequest.status.in_([x.value for x in OPEN]),
                                                          ApprovalRequest.expires_at <= now)).scalars().all()
            for req in due:
                self._move(s, req, S.EXPIRED.value, SYSTEM, f"Expired at {utc(req.expires_at).isoformat()} without a decision.",
                           now, event="approval.expired")
                self._notify(s, policy, req, "approval.expired", "normal", f"Expired: {req.direction} {req.symbol}",
                             "No decision was made before the request expired. Nothing will be executed.", now)
                expired.append(req.risk_assessment_id)
            soon = policy.expiry.expiring_soon_minutes
            if soon:
                upcoming = s.execute(select(ApprovalRequest).where(
                    ApprovalRequest.status.in_([x.value for x in OPEN]), ApprovalRequest.expires_at > now,
                    ApprovalRequest.expires_at <= now + timedelta(minutes=soon),
                    ApprovalRequest.expiring_notified_at.is_(None))).scalars().all()
                for req in upcoming:
                    req.expiring_notified_at = now
                    self._notify(s, policy, req, "approval.expiring_soon", "high", f"Expiring soon: {req.direction} {req.symbol}",
                                 f"Expires at {utc(req.expires_at):%H:%M:%S} UTC.", now)
                    warned += 1
            m = self._expire_management(s, now, policy)
        for a in expired:
            self._cancel_assessment_later(a, "Approval request expired in Module 07.")
        return {"expired": len(expired), "expiring_soon": warned, "management_expired": m}

    # ==== final validation and handoff =========================================================================================
    def handoff(self, approval_id: str) -> dict[str, Any]:
        """Final validation, then the execution request for Module 09 (status EXECUTION_PENDING, a unique execution request
        id, and ``trade.approved_for_execution`` in the outbox, all in one transaction). On a failed check the approval is
        INVALIDATED and nothing is published. If Module 06 is unavailable the approval stays APPROVED and the worker retries
        until the handoff deadline, after which it is invalidated."""
        with self.repo.read() as s:
            req = self._load(s, approval_id, lock=False)
            if req.status != S.APPROVED.value:
                return self.request_view(req)
            idea, v = s.get(TradeIdea, req.idea_id), self._version(s, req.idea_id, req.idea_version)
            s.expunge_all()
        policy = self.policy_obj()
        now = self.now()
        deadline = max(req.expires_at, (req.decided_at or now) + HANDOFF_GRACE)
        problems: list[str] = []
        fresh = None
        idea_ok, idea_reason = self._idea_still_valid(idea)
        if not idea_ok:
            problems.append(idea_reason or "Trade idea no longer valid.")
        if idea.current_version != req.idea_version:
            problems.append("The trade idea changed after approval.")
        if now > deadline:
            problems.append("The approval was not handed off before its deadline.")
        unavailable = False
        if not problems:
            age = (now - req.risk_assessed_at).total_seconds() if req.risk_assessed_at else 1e9
            full = policy.handoff.revalidate_risk == "always" or age > policy.handoff.revalidate_if_older_than_seconds
            try:
                check_old = self.risk.validate(req.risk_assessment_id) if req.risk_assessment_id else {"valid": False, "reasons": ["No assessment."]}
                if not check_old.get("valid"):
                    problems += ["Module 06: " + r for r in check_old.get("reasons") or []]
                elif full:
                    self.risk.cancel(req.risk_assessment_id, "module_07", "Replaced by the handoff revalidation.", req.correlation_id)
                    fresh = self._assess(idea, v, "handoff", req.correlation_id)
                    if fresh is None:
                        unavailable = True
                    else:
                        problems += self._handoff_risk_problems(req, fresh)
                quote_at = self.risk.quote_time(req.symbol, now)
                if quote_at is None or (now - quote_at).total_seconds() > policy.review.market_data_max_age_seconds:
                    problems.append("Market data is stale at handoff.")
                account = self.risk.account_login()
                if account != req.account_login:
                    problems.append("The trading account changed after approval.")
                if policy.handoff.allowed_accounts and (account or "") not in policy.handoff.allowed_accounts:
                    problems.append("The trading account is not allowed for execution by policy.")
            except GatewayUnavailable:
                unavailable = True
        if unavailable and not problems:
            if now <= deadline:
                with self.repo.write() as s:
                    self.repo.audit(s, actor="system", actor_kind="system", action="handoff.deferred",
                                    entity_type="approval_request", entity_id=approval_id, approval_id=approval_id,
                                    idea_id=req.idea_id, at=now, reason="Module 06 unavailable; will retry.",
                                    correlation_id=req.correlation_id)
                with self.repo.read() as s:
                    return self.request_view(self._load(s, approval_id, lock=False))
            problems.append("Module 06 stayed unavailable until the handoff deadline.")
        with self.repo.write() as s:
            req = self._load(s, approval_id)
            if req.status != S.APPROVED.value:          # someone else (another worker) handled it
                if fresh and fresh.get("assessment_id"):
                    self._cancel_assessment_later(fresh["assessment_id"], "Duplicate handoff attempt.")
                return self.request_view(req)
            if problems:
                self._store_risk(s, approval_id, req.idea_id, req.idea_version, "handoff", fresh, now) if fresh else None
                self._move(s, req, S.INVALIDATED.value, SYSTEM, "Final validation failed: " + "; ".join(problems), now,
                           event="approval.invalidated", details={"problems": problems})
                self._notify(s, policy, req, "approval.invalidated", "high", f"Invalidated: {req.direction} {req.symbol}",
                             "Final validation failed, so nothing was sent to execution: " + "; ".join(problems)[:600], now)
                view = self.request_view(req)
                stale = (fresh or {}).get("assessment_id")
            else:
                stale = None
                if fresh:
                    self._apply_risk(req, fresh)
                    self._store_risk(s, approval_id, req.idea_id, req.idea_version, "handoff", fresh, now)
                exec_id = str(uuid.uuid4())
                payload = self._execution_payload(req, exec_id, s)
                s.add(ExecutionHandoff(execution_request_id=exec_id, kind="trade", approval_id=approval_id, status="pending",
                                       payload=jsonable(payload), payload_hash=stable_hash(payload),
                                       account_login=req.account_login, idempotency_key=f"exec:{approval_id}",
                                       correlation_id=req.correlation_id, created_at=now, updated_at=now))
                s.flush()
                self._move(s, req, S.EXECUTION_PENDING.value, SYSTEM, "Final validation passed; handed to Module 09.", now,
                           details={"execution_request_id": exec_id})
                self.repo.emit(s, "trade.approved_for_execution", payload, at=now, correlation_id=req.correlation_id,
                               idempotency_key=f"trade.approved_for_execution:{approval_id}", causation_id=approval_id)
                self._notify(s, policy, req, "execution.handoff_submitted", "normal",
                             f"Sent to execution: {req.direction} {req.symbol}",
                             f"Execution request {exec_id[:8]} is waiting for Module 09.", now)
                view = self.request_view(req)
        if stale:
            self._cancel_assessment_later(stale, "Approval invalidated at handoff.")
        return view

    def _handoff_risk_problems(self, req: ApprovalRequest, fresh: dict[str, Any]) -> list[str]:
        d = fresh.get("decision")
        if d not in ("PASS", "REVIEW"):
            why = [f"{r.get('rule_id')}: {r.get('message')}" for r in (fresh.get("rule_violations") or []) + (fresh.get("errors") or [])]
            return [f"The fresh Module 06 decision is {d}: " + "; ".join(why)]
        out = []
        approved_warnings = {w.get("rule_id") for w in req.risk_warnings or []}
        new = {w.get("rule_id") for w in fresh.get("warnings") or []} - approved_warnings
        if new:
            out.append("New risk warnings appeared after approval: " + ", ".join(sorted(new)))
        if D(fresh.get("final_volume")) != req.position_size:
            out.append(f"The position size changed after approval ({req.position_size} -> {fresh.get('final_volume')}).")
        return out

    def _execution_payload(self, req: ApprovalRequest, exec_id: str, s: Session) -> dict[str, Any]:
        approver = s.execute(select(ApprovalDecision).where(ApprovalDecision.approval_id == req.approval_id,
                                                            ApprovalDecision.decision == "APPROVED")
                             .order_by(ApprovalDecision.decided_at)).scalars().all()
        snapshot = {"approval_id": req.approval_id, "evidence_hash": req.evidence_hash, "risk_assessment_id": req.risk_assessment_id,
                    "decisions": [d.record_hash for d in approver]}
        return {"approval_id": req.approval_id, "trade_idea_id": req.idea_id, "idea_version": req.idea_version,
                "execution_request_id": exec_id, "symbol": req.symbol, "direction": req.direction,
                "entry": {"type": "price", "price": str(req.entry)}, "stop_loss": str(req.stop_loss),
                "take_profit": str(req.take_profit), "approved_volume": _s(req.position_size),
                "risk_assessment_id": req.risk_assessment_id, "risk_decision": req.risk_decision,
                "approval_snapshot_hash": stable_hash(snapshot),
                "approver_ids": [d.user_id for d in approver], "approved_at": req.decided_at,
                "account_reference": req.account_login, "correlation_id": req.correlation_id,
                "idempotency_key": f"exec:{req.approval_id}", "schema_version": SCHEMA_VERSION,
                "instructions": "Module 09 must validate this request with Module 06 (POST /risk/assessments/{id}/validate) "
                                "immediately before any order, never exceed approved_volume, never trade without the stop-loss, "
                                "and report the outcome to /api/v1/approvals/execution/results."}

    def handoff_due(self) -> int:
        with self.repo.read() as s:
            ids = list(s.execute(select(ApprovalRequest.approval_id).where(ApprovalRequest.status == S.APPROVED.value)).scalars())
        n = 0
        for a in ids:
            try:
                self.handoff(a)
                n += 1
            except Exception as exc:  # noqa: BLE001 - one bad row must not stop the others
                log.warning("Handoff attempt failed", extra={"approval": a, "error": str(exc)[:200]})
        return n

    # ==== Module 09 contract ====================================================================================================
    def execution_queue(self, who: Principal, limit: int = 50) -> list[dict[str, Any]]:
        now = self.now()
        with self.repo.write() as s:
            rows = s.execute(select(ExecutionHandoff).where(ExecutionHandoff.status == "pending")
                             .order_by(ExecutionHandoff.created_at).limit(limit)).scalars().all()
            for h in rows:
                if h.picked_up_at is None:
                    h.picked_up_at = now
            return [{"execution_request_id": h.execution_request_id, "kind": h.kind, "payload": h.payload,
                     "payload_hash": h.payload_hash, "created_at": h.created_at} for h in rows]

    def report_execution(self, who: Principal, body: dict[str, Any]) -> dict[str, Any]:
        """Module 09 reports an outcome. Deduplicated by eventId. Only moves statuses forward along the state machine."""
        statuses = ("accepted", "rejected", "submitted", "filled", "partial", "failed")
        event_id, exec_id, status = str(body.get("event_id") or ""), str(body.get("execution_request_id") or ""), body.get("status")
        if not event_id or not exec_id or status not in statuses:
            raise ApprovalError(422, "INVALID_RESULT", f"eventId, executionRequestId and status ({', '.join(statuses)}) are required.")
        now = self.now()
        with self.repo.write() as s:
            policy, _ = self._policy(s)
            if s.execute(select(ExecutionResult.id).where(ExecutionResult.event_id == event_id)).first():
                return {"duplicate": True, "event_id": event_id}
            h = s.get(ExecutionHandoff, exec_id)
            if h is None:
                raise ApprovalError(404, "NOT_FOUND", "Unknown execution request.")
            s.add(ExecutionResult(event_id=event_id[:64], execution_request_id=exec_id, status=status,
                                  broker_ticket=_s(body.get("broker_ticket")), position_id=_s(body.get("position_id")),
                                  filled_volume=D(body.get("filled_volume")), fill_price=D(body.get("fill_price")),
                                  message=(str(body.get("message"))[:2000] if body.get("message") else None),
                                  payload=jsonable(body), reported_by=who.username, reported_at=now))
            h.status, h.updated_at = status, now
            s.flush()
            if h.kind == "trade":
                self._apply_trade_result(s, policy, h, status, body, now, who)
            else:
                self._apply_management_result(s, policy, h, status, body, now, who)
            return {"duplicate": False, "event_id": event_id, "execution_request_id": exec_id, "handoff_status": status}

    def _apply_trade_result(self, s: Session, policy: Policy, h: ExecutionHandoff, status: str, body: dict[str, Any],
                            now: datetime, who: Principal) -> None:
        req = self._load(s, h.approval_id)
        actor = Actor.of(who)
        steps = {"accepted": [], "submitted": [S.EXECUTION_SUBMITTED.value],
                 "filled": [S.EXECUTION_SUBMITTED.value, S.EXECUTED.value], "partial": [S.EXECUTION_SUBMITTED.value, S.EXECUTED.value],
                 "rejected": [S.EXECUTION_FAILED.value], "failed": [S.EXECUTION_FAILED.value]}[status]
        for target in steps:
            if req.status == target:
                continue
            try:
                check(req.status, target)
            except IllegalTransition:
                continue                      # e.g. a late "submitted" after "filled": ignored, already recorded
            event = {S.EXECUTED.value: "execution.completed", S.EXECUTION_FAILED.value: "execution.failed"}.get(target)
            self._move(s, req, target, actor, f"Module 09 reported {status}." + (f" {body.get('message')}" if body.get("message") else ""),
                       now, event=event, details={"execution_request_id": h.execution_request_id})
        if req.status == S.EXECUTED.value:
            self._notify(s, policy, req, "execution.completed", "normal", f"Executed: {req.direction} {req.symbol}",
                         f"Module 09 reported {status}" + (f", ticket {body.get('broker_ticket')}" if body.get("broker_ticket") else "") + ".", now)
            if body.get("position_id"):
                self._upsert_position(s, {"position_id": str(body["position_id"]), "account_login": req.account_login,
                                          "symbol": req.symbol, "side": req.direction,
                                          "volume": D(body.get("filled_volume")) or req.position_size or Decimal(0),
                                          "entry_price": D(body.get("fill_price")) or req.entry, "stop_loss": req.stop_loss,
                                          "take_profit": req.take_profit, "status": "OPEN" if status == "filled" else "OPEN",
                                          "opened_at": now}, "module09", now, req.approval_id, h.execution_request_id)
        elif req.status == S.EXECUTION_FAILED.value:
            self._notify(s, policy, req, "execution.failed", "critical", f"Execution failed: {req.direction} {req.symbol}",
                         str(body.get("message") or "Module 09 reported a failure.")[:600], now)

    # ==== positions =============================================================================================================
    def _upsert_position(self, s: Session, p: dict[str, Any], source: str, now: datetime, approval_id: str | None = None,
                         exec_id: str | None = None) -> None:
        row = s.get(ManagedPosition, p["position_id"])
        if row is None:
            row = ManagedPosition(position_id=p["position_id"], symbol=p["symbol"], side=p["side"], volume=p["volume"],
                                  entry_price=p["entry_price"], status=p.get("status", "UNKNOWN"), source=source, updated_at=now)
            s.add(row)
        for k in ("account_login", "symbol", "side", "volume", "entry_price", "current_price", "stop_loss", "take_profit",
                  "unrealized_pnl", "realized_pnl", "status", "opened_at"):
            if k in p:
                setattr(row, k, p[k])
        row.source, row.source_updated_at, row.updated_at = source, p.get("source_updated_at") or now, now
        if approval_id:
            row.approval_id = approval_id
        if exec_id:
            row.execution_request_id = exec_id
        s.flush()

    def positions(self, who: Principal | None = None) -> dict[str, Any]:
        """Positions with their source and age. Module 09 positions win when it has reported any; otherwise the read-only
        account mirror is used and labelled as such. Nothing is fabricated: missing prices are shown as unavailable."""
        now = self.now()
        with self.repo.read() as s:
            m09 = s.execute(select(ManagedPosition).where(ManagedPosition.source == "module09",
                                                          ManagedPosition.status != "CLOSED")).scalars().all()
        source, as_of, error = "module09", None, None
        rows: list[dict[str, Any]]
        if m09:
            rows = [self._position_dict(p) for p in m09]
            as_of = max((p.source_updated_at for p in m09 if p.source_updated_at), default=None)
        elif self.position_source is not None:
            source = self.position_source.name
            try:
                raw, as_of = self.position_source.positions(now)
                with self.repo.write() as s:
                    seen = set()
                    for p in raw:
                        self._upsert_position(s, {**p, "source_updated_at": as_of}, source, now)
                        seen.add(p["position_id"])
                    for gone in s.execute(select(ManagedPosition).where(ManagedPosition.source == source,
                                                                        ManagedPosition.status != "CLOSED")).scalars():
                        if gone.position_id not in seen:
                            gone.status, gone.updated_at = "CLOSED", now
                    rows = [self._position_dict(p) for p in s.execute(select(ManagedPosition).where(
                        ManagedPosition.source == source, ManagedPosition.status != "CLOSED")).scalars()]
            except Exception as exc:  # noqa: BLE001 - shown as unavailable, never guessed
                rows, error = [], f"Position source unavailable: {type(exc).__name__}"
        else:
            rows, source, error = [], "none", "No position source is configured."
        if who is not None:
            rows = [r for r in rows if who.may_access_account(r.get("account_login"))]
        age = (now - as_of).total_seconds() if as_of else None
        return {"source": source, "as_of": as_of, "age_seconds": age, "stale": age is None or age > 300, "error": error,
                "note": None if source == "module09" else
                "Module 09 is not built yet. Positions come from the read-only account mirror (sync-account) and are only "
                "as fresh as its last run.", "positions": rows}

    @staticmethod
    def _position_dict(p: ManagedPosition) -> dict[str, Any]:
        return {"position_id": p.position_id, "account_login": p.account_login, "symbol": p.symbol, "side": p.side,
                "volume": p.volume, "entry_price": p.entry_price, "current_price": p.current_price, "stop_loss": p.stop_loss,
                "take_profit": p.take_profit, "unrealized_pnl": p.unrealized_pnl, "realized_pnl": p.realized_pnl,
                "status": p.status, "opened_at": p.opened_at, "source": p.source, "last_updated": p.source_updated_at,
                "approval_id": p.approval_id}

    def position(self, position_id: str, who: Principal) -> dict[str, Any]:
        data = self.positions(who)
        for p in data["positions"]:
            if p["position_id"] == position_id:
                with self.repo.read() as s:
                    reqs = s.execute(select(TradeManagementRequest).where(TradeManagementRequest.position_id == position_id)
                                     .order_by(TradeManagementRequest.created_at.desc()).limit(50)).scalars().all()
                    return {**p, "source_info": {k: data[k] for k in ("source", "as_of", "stale", "note")},
                            "management_requests": [self._mgmt_view(r) for r in reqs]}
        raise ApprovalError(404, "NOT_FOUND", "Position not found (or no longer open).")

    # ==== trade management ======================================================================================================
    def create_management_request(self, who: Principal, body: dict[str, Any]) -> dict[str, Any]:
        if not who.is_human or not who.can(perm.MANAGE_REQUEST):
            raise ApprovalError(403, "FORBIDDEN", "Only a signed-in person with trade-management permission can request this.")
        action = str(body.get("action") or "").upper()
        if action not in MANAGEMENT_ACTIONS:
            raise ApprovalError(422, "INVALID_ACTION", "Unknown management action.", list(MANAGEMENT_ACTIONS))
        if action == "EMERGENCY_EXIT" and not who.can(perm.EMERGENCY):
            raise ApprovalError(403, "FORBIDDEN", "Your role cannot request an emergency exit.")
        reason = str(body.get("reason") or "").strip()
        if len(reason) < 3:
            raise ApprovalError(422, "REASON_REQUIRED", "Give a reason for the change.")
        key = str(body.get("client_request_id") or "")
        if not key:
            raise ApprovalError(422, "CLIENT_REQUEST_ID_REQUIRED", "clientRequestId is required.")
        pos = self.position(str(body.get("position_id") or ""), who)
        if pos["status"] not in ("OPEN", "PARTIALLY_CLOSED"):
            raise ApprovalError(409, "POSITION_NOT_OPEN", f"The position is {pos['status']}.")
        policy = self.policy_obj()
        params, implications = self._validate_management(action, body.get("params") or {}, pos, policy)
        now = self.now()
        emergency = action == "EMERGENCY_EXIT"
        minutes = policy.management.emergency.expiry_minutes if emergency else policy.management.expiry_minutes
        with self.repo.write() as s:
            existing = s.execute(select(TradeManagementRequest).where(TradeManagementRequest.idempotency_key == key)).scalar_one_or_none()
            if existing is not None:
                return {**self._mgmt_view(existing), "replayed": True}
            active = s.execute(select(TradeManagementRequest).where(
                TradeManagementRequest.position_id == pos["position_id"],
                TradeManagementRequest.status.in_([x.value for x in (MS.PENDING, MS.APPROVED, MS.EXECUTION_PENDING,
                                                                      MS.EXECUTION_SUBMITTED)]))).scalar_one_or_none()
            if active is not None and not emergency:
                raise ApprovalError(409, "REQUEST_ACTIVE", "This position already has an active management request.",
                                    {"request_id": active.request_id})
            if active is not None and emergency:
                if active.status in (MS.EXECUTION_PENDING.value, MS.EXECUTION_SUBMITTED.value):
                    raise ApprovalError(409, "REQUEST_ACTIVE", "A management action for this position is already with Module 09.")
                self._move_mgmt(s, active, MS.CANCELLED.value, Actor.of(who), "Superseded by an emergency exit request.", now)
            r = TradeManagementRequest(
                request_id=str(uuid.uuid4()), position_id=pos["position_id"], account_login=pos.get("account_login"),
                symbol=pos["symbol"], action=action, params=jsonable(params),
                position_snapshot=jsonable({k: pos.get(k) for k in ("status", "side", "entry_price", "stop_loss", "take_profit",
                                                                     "volume", "current_price", "source", "last_updated")}),
                reason=reason[:2000], evidence=jsonable(body.get("evidence")), risk_implications=jsonable(implications),
                priority="emergency" if emergency else "normal", requested_by=who.id, status=MS.PENDING.value, version=1,
                idempotency_key=key[:128], correlation_id=str(body.get("correlation_id") or uuid.uuid4())[:64], created_at=now,
                expires_at=now + timedelta(minutes=minutes), status_changed_at=now)
            s.add(r)
            s.flush()
            self.repo.audit(s, actor=who.username, actor_kind="user", action="management.requested",
                            entity_type="trade_management_request", entity_id=r.request_id, at=now, new_state=r.status,
                            reason=reason, correlation_id=r.correlation_id,
                            details={"action": action, "params": jsonable(params), "position_id": r.position_id})
            self.repo.emit(s, "trade_management.requested", self._mgmt_event(r), at=now, correlation_id=r.correlation_id,
                           idempotency_key=f"trade_management.requested:{r.request_id}")
            title = (f"EMERGENCY EXIT requested: {r.symbol} position {r.position_id}" if emergency
                     else f"Management request: {action.replace('_', ' ').lower()} on {r.symbol}")
            self.repo.notify(s, dedupe_key=f"mgmt-requested:{r.request_id}",
                             event_type="management.emergency_requested" if emergency else "management.requested",
                             priority="critical" if emergency else "normal", title=title,
                             message=f"{who.display_name}: {reason}"[:900], at=now,
                             recipients=self._recipients(s, r.account_login), channels=policy.notifications.channels,
                             management_request_id=r.request_id, position_id=r.position_id)
            return self._mgmt_view(r)

    def _validate_management(self, action: str, params: dict[str, Any], pos: dict[str, Any],
                             policy: Policy) -> tuple[dict[str, Any], dict[str, Any]]:
        """Checks that the requested change is coherent for the position. It never widens a stop-loss unless policy allows it
        and a valid Module 06 assessment at the new stop is supplied."""
        side = pos["side"]
        sign = 1 if side == "BUY" else -1
        entry, cur_sl, cur_tp, price = D(pos["entry_price"]), D(pos.get("stop_loss")), D(pos.get("take_profit")), D(pos.get("current_price"))
        out: dict[str, Any] = {}
        impl: dict[str, Any] = {"current_stop_loss": _s(cur_sl), "current_take_profit": _s(cur_tp), "current_price": _s(price)}

        def stop_check(new_sl: Decimal) -> None:
            if new_sl <= 0:
                raise ApprovalError(422, "INVALID_PRICE", "The stop-loss must be positive.")
            if price is not None and sign * (price - new_sl) <= 0:
                raise ApprovalError(422, "STOP_THROUGH_PRICE", "That stop-loss is beyond the current price; it would close "
                                                               "the position immediately. Request a close instead.")
            widening = cur_sl is None or sign * (new_sl - cur_sl) < 0
            impl["stop_change"] = "removes no stop" if cur_sl is None else ("widens risk" if widening else "reduces risk")
            if widening:
                if not policy.management.allow_stop_widening:
                    raise ApprovalError(422, "STOP_WIDENING_NOT_ALLOWED", "This change widens the stop-loss and increases "
                                                                          "risk. Policy does not allow widening stops.")
                aid = params.get("risk_assessment_id")
                if not aid:
                    raise ApprovalError(422, "RISK_ASSESSMENT_REQUIRED", "Widening a stop needs a Module 06 assessment at the "
                                                                         "new stop (riskAssessmentId).")
                chk = self.risk.validate(str(aid))
                if not chk.get("valid") or chk.get("decision") != "PASS":
                    raise ApprovalError(422, "RISK_NOT_PASSED", "The Module 06 assessment for the wider stop is not a valid PASS.",
                                        chk.get("reasons"))
                out["risk_assessment_id"] = str(aid)
            out["stop_loss"] = str(new_sl)

        if action == "MOVE_STOP_LOSS":
            new = D(params.get("stop_loss"))
            if new is None:
                raise ApprovalError(422, "INVALID_PRICE", "stopLoss is required.")
            stop_check(new)
        elif action == "BREAKEVEN":
            if price is not None and sign * (price - entry) <= 0:
                raise ApprovalError(422, "NOT_IN_PROFIT", "The position is not in profit, so a breakeven stop would close it.")
            stop_check(entry)
        elif action == "TRAILING_STOP":
            dist = D(params.get("distance"))
            if dist is None or dist <= 0:
                raise ApprovalError(422, "INVALID_DISTANCE", "distance must be a positive price distance.")
            if price is None:
                raise ApprovalError(422, "NO_PRICE", "No current price is available to place a trailing stop.")
            out["distance"] = str(dist)
            stop_check(price - sign * dist)
            impl["proposed_initial_stop"] = out["stop_loss"]
        elif action == "MOVE_TAKE_PROFIT":
            new = D(params.get("take_profit"))
            if new is None or new <= 0:
                raise ApprovalError(422, "INVALID_PRICE", "takeProfit must be a positive price.")
            if sign * (new - (price or entry)) <= 0:
                raise ApprovalError(422, "INVALID_PRICE", "The take-profit must be beyond the current price in the trade's direction.")
            out["take_profit"] = str(new)
        elif action == "PARTIAL_CLOSE":
            vol = D(pos["volume"])
            frac, v = D(params.get("fraction")), D(params.get("volume"))
            if v is None and frac is not None:
                if not (Decimal(0) < frac <= policy.management.max_partial_close_fraction):
                    raise ApprovalError(422, "INVALID_FRACTION", f"fraction must be above 0 and at most "
                                                                 f"{policy.management.max_partial_close_fraction}.")
                v = vol * frac
            if v is None or v <= 0 or v >= vol:
                raise ApprovalError(422, "INVALID_VOLUME", "Close a volume above 0 and below the open volume (use CLOSE for all).")
            out["volume"] = str(v)
            out["note"] = "Module 09 rounds the volume down to the broker's lot step."
            impl["remaining_volume"] = str(vol - v)
        elif action in ("CLOSE", "EMERGENCY_EXIT"):
            out["volume"] = str(pos["volume"])
            impl["effect"] = "closes the whole position at market"
        elif action in ("CANCEL_PENDING_ORDER", "MODIFY_PENDING_ORDER"):
            raise ApprovalError(422, "NOT_AVAILABLE", "No position source reports pending orders yet (Module 09 is not built), "
                                                      "so pending orders cannot be managed here.")
        return out, impl

    def approve_management(self, request_id: str, who: Principal, body: dict[str, Any]) -> dict[str, Any]:
        return self._decide_management(request_id, who, body, "APPROVED")

    def reject_management(self, request_id: str, who: Principal, body: dict[str, Any]) -> dict[str, Any]:
        return self._decide_management(request_id, who, body, "REJECTED")

    def _decide_management(self, request_id: str, who: Principal, body: dict[str, Any], decision: str) -> dict[str, Any]:
        if not who.is_human or not who.can(perm.MANAGE_DECIDE):
            raise ApprovalError(403, "FORBIDDEN", "Only a signed-in person with trade-management permission can decide this.")
        key = body.get("client_request_id")
        pos = None
        with self.repo.read() as s:
            r0 = s.get(TradeManagementRequest, request_id)
            if r0 is None or not who.may_access_account(r0.account_login):
                raise ApprovalError(404, "NOT_FOUND", "Management request not found.")
            position_id = r0.position_id
        if decision == "APPROVED":
            try:
                pos = self.position(position_id, who)
            except ApprovalError:
                pos = None
        policy = self.policy_obj()
        now = self.now()
        with self.repo.write() as s:
            _, pv = self._policy(s)
            r = s.get(TradeManagementRequest, request_id)
            rec = self._replay(s, f"mgmt-{decision.lower()}:{request_id}", key, body)
            if rec is not None:
                return rec
            if r.status != MS.PENDING.value:
                raise ApprovalError(409, "NOT_PENDING", f"This request is {r.status}.")
            if int(body.get("request_version") or 0) != r.version:
                raise ApprovalError(409, "STALE_VERSION", "The request changed since you loaded it. Reload it.")
            failure = None
            if r.expires_at <= now:
                self._move_mgmt(s, r, MS.EXPIRED.value, SYSTEM, "Expired before a decision.", now)
                failure = ApprovalError(409, "EXPIRED", "This management request has expired.")
            elif decision == "APPROVED":
                problems = self._management_problems(r, pos, who, body, policy)
                if problems:
                    failure = ApprovalError(409, "APPROVAL_CHECKS_FAILED", "The management action cannot be approved.", problems)
            elif decision == "REJECTED" and not str(body.get("comment") or "").strip():
                raise ApprovalError(422, "COMMENT_REQUIRED", "Say why the management request is rejected.")
            if failure is None:
                dec = TradeManagementDecision(id=str(uuid.uuid4()), request_id=request_id, decision=decision, is_final=True,
                                              user_id=who.id, username=who.username, role=who.role.value, decided_at=now,
                                              client_request_id=str(key)[:128],
                                              comment=(str(body["comment"])[:2000] if body.get("comment") else None),
                                              confirmation=jsonable(body.get("confirmation")),
                                              record_hash=stable_hash({"request_id": request_id, "decision": decision,
                                                                       "user_id": who.id, "at": now, "params": r.params,
                                                                       "policy_version": pv}))
                s.add(dec)
                s.flush()
                self._move_mgmt(s, r, decision, Actor.of(who), body.get("comment") or decision.capitalize(), now)
                if decision == "APPROVED":
                    exec_id = str(uuid.uuid4())
                    payload = {**self._mgmt_event(r), "execution_request_id": exec_id, "approved_by": who.id, "approved_at": now,
                               "decision_hash": dec.record_hash, "idempotency_key": f"exec-mgmt:{request_id}",
                               "schema_version": SCHEMA_VERSION,
                               "instructions": "Module 09 must confirm the position is still open and the change is valid "
                                               "before acting, and report the outcome to /api/v1/approvals/execution/results."}
                    s.add(ExecutionHandoff(execution_request_id=exec_id, kind="management", management_request_id=request_id,
                                           status="pending", payload=jsonable(payload), payload_hash=stable_hash(payload),
                                           account_login=r.account_login, idempotency_key=f"exec-mgmt:{request_id}",
                                           correlation_id=r.correlation_id, created_at=now, updated_at=now))
                    s.flush()
                    self._move_mgmt(s, r, MS.EXECUTION_PENDING.value, SYSTEM, "Handed to Module 09.", now)
                    self.repo.emit(s, "trade_management.approved", payload, at=now, correlation_id=r.correlation_id,
                                   idempotency_key=f"trade_management.approved:{request_id}")
                else:
                    self.repo.emit(s, "trade_management.rejected", {**self._mgmt_event(r), "comment": body.get("comment")},
                                   at=now, correlation_id=r.correlation_id, idempotency_key=f"trade_management.rejected:{request_id}")
                self.repo.notify(s, dedupe_key=f"mgmt-{decision.lower()}:{request_id}",
                                 event_type=f"management.{decision.lower()}", priority="high" if r.priority == "emergency" else "normal",
                                 title=f"Management {decision.lower()}: {r.action.replace('_', ' ').lower()} on {r.symbol}",
                                 message=f"By {who.display_name}." + (" Sent to Module 09." if decision == "APPROVED" else ""),
                                 at=now, recipients=self._recipients(s, r.account_login),
                                 channels=policy.notifications.channels, management_request_id=request_id,
                                 position_id=r.position_id)
                view = self._mgmt_view(r)
                self.repo.remember(s, f"mgmt-{decision.lower()}:{request_id}", key, stable_hash(body), 200, view, now)
        if failure is not None:
            raise failure
        return view

    def _management_problems(self, r: TradeManagementRequest, pos: dict[str, Any] | None, who: Principal,
                             body: dict[str, Any], policy: Policy) -> list[str]:
        problems = []
        if pos is None:
            return ["The position no longer exists or is no longer open."]
        if pos["status"] not in ("OPEN", "PARTIALLY_CLOSED"):
            problems.append(f"The position is {pos['status']}.")
        if (pos.get("account_login") or None) != (r.account_login or None):
            problems.append("The position's account does not match the request.")
        if not who.may_access_account(r.account_login):
            problems.append("You are not permitted to act on this trading account.")
        snap = r.position_snapshot or {}
        for k in ("side", "stop_loss", "take_profit"):
            if _s(snap.get(k)) != _s(pos.get(k)) and not (D(snap.get(k)) is not None and D(snap.get(k)) == D(pos.get(k))):
                problems.append(f"The position's {k.replace('_', ' ')} changed since the request; create a new request.")
        try:
            self._validate_management(r.action, {**(r.params or {}), "stop_loss": (r.params or {}).get("stop_loss"),
                                                 "take_profit": (r.params or {}).get("take_profit")}
                                      if r.action not in ("TRAILING_STOP", "BREAKEVEN", "PARTIAL_CLOSE") else r.params or {},
                                      pos, policy)
        except ApprovalError as exc:
            problems.append(f"The change is no longer valid: {exc.message}")
        if r.priority == "emergency":
            phrase = policy.management.emergency.confirmation_phrase.format(symbol=r.symbol)
            if str((body.get("confirmation") or {}).get("phrase") or "").strip() != phrase:
                problems.append(f"Type the confirmation phrase exactly: {phrase}")
            if r.requested_by == who.id and not policy.management.emergency.allow_self_authorisation:
                problems.append("Policy requires a second person to authorise an emergency exit.")
        elif (body.get("confirmation") or {}).get("action") != r.action:
            problems.append("Confirm the action shown before approving.")
        return problems

    def _move_mgmt(self, s: Session, r: TradeManagementRequest, target: str, who: Actor, reason: str, now: datetime) -> None:
        previous = r.status
        check_management(previous, target)
        res = s.execute(update(TradeManagementRequest)
                        .where(TradeManagementRequest.request_id == r.request_id, TradeManagementRequest.version == r.version,
                               TradeManagementRequest.status == previous)
                        .values(status=target, version=r.version + 1, status_changed_at=now, status_reason=reason[:2000]))
        if res.rowcount != 1:
            raise ApprovalError(409, "CONCURRENT_CHANGE", "This request changed while you were acting on it. Reload it.")
        s.refresh(r)
        self.repo.audit(s, actor=who.name, actor_kind=who.kind, action=f"management.{target.lower()}",
                        entity_type="trade_management_request", entity_id=r.request_id, at=now, previous_state=previous,
                        new_state=target, reason=reason, correlation_id=r.correlation_id,
                        details={"position_id": r.position_id, "action": r.action})

    def _expire_management(self, s: Session, now: datetime, policy: Policy) -> int:
        due = s.execute(select(TradeManagementRequest).where(TradeManagementRequest.status == MS.PENDING.value,
                                                             TradeManagementRequest.expires_at <= now)).scalars().all()
        for r in due:
            self._move_mgmt(s, r, MS.EXPIRED.value, SYSTEM, "Expired without a decision.", now)
            self.repo.emit(s, "trade_management.expired", self._mgmt_event(r), at=now, correlation_id=r.correlation_id,
                           idempotency_key=f"trade_management.expired:{r.request_id}")
        return len(due)

    def _apply_management_result(self, s: Session, policy: Policy, h: ExecutionHandoff, status: str, body: dict[str, Any],
                                 now: datetime, who: Principal) -> None:
        r = s.get(TradeManagementRequest, h.management_request_id)
        steps = {"accepted": [], "submitted": [MS.EXECUTION_SUBMITTED.value],
                 "filled": [MS.EXECUTION_SUBMITTED.value, MS.EXECUTED.value],
                 "partial": [MS.EXECUTION_SUBMITTED.value, MS.EXECUTED.value],
                 "rejected": [MS.EXECUTION_FAILED.value], "failed": [MS.EXECUTION_FAILED.value]}[status]
        for target in steps:
            try:
                check_management(r.status, target)
            except IllegalTransition:
                continue
            self._move_mgmt(s, r, target, Actor.of(who), f"Module 09 reported {status}.", now)
        failed = r.status == MS.EXECUTION_FAILED.value
        self.repo.notify(s, dedupe_key=f"mgmt-result:{r.request_id}:{r.status}",
                         event_type="management.failed" if failed else "management.executed",
                         priority="critical" if failed else "normal",
                         title=f"Management {'failed' if failed else 'done'}: {r.action.replace('_', ' ').lower()} on {r.symbol}",
                         message=str(body.get("message") or f"Module 09 reported {status}.")[:600], at=now,
                         recipients=self._recipients(s, r.account_login), channels=policy.notifications.channels,
                         management_request_id=r.request_id, position_id=r.position_id)

    def management_requests(self, who: Principal, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        with self.repo.read() as s:
            q = select(TradeManagementRequest).order_by(TradeManagementRequest.created_at.desc()).limit(limit)
            if status:
                q = q.where(TradeManagementRequest.status == status.upper())
            return [self._mgmt_view(r) for r in s.execute(q).scalars() if who.may_access_account(r.account_login)]

    def management_request(self, request_id: str, who: Principal) -> dict[str, Any]:
        with self.repo.read() as s:
            r = s.get(TradeManagementRequest, request_id)
            if r is None or not who.may_access_account(r.account_login):
                raise ApprovalError(404, "NOT_FOUND", "Management request not found.")
            decisions = s.execute(select(TradeManagementDecision).where(TradeManagementDecision.request_id == request_id)).scalars().all()
            handoff = s.execute(select(ExecutionHandoff).where(ExecutionHandoff.management_request_id == request_id)).scalar_one_or_none()
            return {**self._mgmt_view(r), "decisions": [{"decision": d.decision, "username": d.username, "role": d.role,
                                                          "decided_at": d.decided_at, "comment": d.comment} for d in decisions],
                    "handoff": self._handoff_view(s, handoff) if handoff else None}

    def _mgmt_view(self, r: TradeManagementRequest) -> dict[str, Any]:
        return {"request_id": r.request_id, "position_id": r.position_id, "account_login": r.account_login, "symbol": r.symbol,
                "action": r.action, "params": r.params, "position_snapshot": r.position_snapshot, "reason": r.reason,
                "evidence": r.evidence, "risk_implications": r.risk_implications, "priority": r.priority,
                "requested_by": r.requested_by, "status": r.status, "version": r.version, "created_at": r.created_at,
                "expires_at": r.expires_at, "status_changed_at": r.status_changed_at, "status_reason": r.status_reason}

    def _mgmt_event(self, r: TradeManagementRequest) -> dict[str, Any]:
        return {"management_request_id": r.request_id, "position_id": r.position_id, "account_reference": r.account_login,
                "symbol": r.symbol, "action": r.action, "params": r.params, "priority": r.priority, "status": r.status,
                "reason": r.reason, "position_snapshot": r.position_snapshot}

    # ==== Module 05 sync and inbound events ======================================================================================
    def sync_module05(self) -> dict[str, int]:
        """Takes new Module 05 candidates in, and closes Module 07 requests whose candidate Module 05 has since rejected or
        expired. Errors on one candidate do not stop the others."""
        if self.ideas is None:
            return {"ingested": 0, "closed": 0, "errors": 0}
        ingested = closed = errors = 0
        symbols = self._symbols()
        for c in self.ideas.active_candidates():
            with self.repo.read() as s:
                if s.get(TradeIdea, c["candidate_uid"]) is not None:
                    continue
            try:
                raw = intake.from_module05(c, self.ideas.evidence(c["candidate_uid"]), self.ideas.ai_review(c["candidate_uid"]),
                                           symbols.get(c["symbol"], "unknown"), self.risk.normalised_entry(c["candidate_uid"]))
                self.ingest(raw, source="module05", who=SYSTEM, source_ref=c["candidate_uid"])
                ingested += 1
            except (intake.IntakeError, ApprovalError) as exc:
                errors += 1
                with self.repo.write() as s:
                    self.repo.audit(s, actor="system", actor_kind="system", action="idea.rejected_at_intake",
                                    entity_type="trade_idea", entity_id=c["candidate_uid"], idea_id=c["candidate_uid"],
                                    at=self.now(), reason=getattr(exc, "message", str(exc))[:1000],
                                    details={"code": getattr(exc, "code", None)})
            except Exception as exc:  # noqa: BLE001
                errors += 1
                log.warning("Module 05 intake failed", extra={"candidate": c["candidate_uid"], "error": str(exc)[:200]})
        with self.repo.read() as s:
            open_reqs = s.execute(select(ApprovalRequest, TradeIdea).join(TradeIdea, TradeIdea.idea_id == ApprovalRequest.idea_id)
                                  .where(TradeIdea.source == "module05",
                                         ApprovalRequest.status.in_([S.PENDING.value, S.UNDER_REVIEW.value, S.APPROVED.value]))).all()
            pairs = [(r.approval_id, i.source_ref, r.status) for r, i in open_reqs]
        for approval_id, ref, status in pairs:
            c = self.ideas.candidate(ref) if ref else None
            cstatus = c["status"] if c else "missing"
            if cstatus in ("rejected", "expired", "missing"):
                try:
                    self.close_for_source(approval_id, status, f"Module 05 marked the idea {cstatus}.")
                    closed += 1
                except ApprovalError:
                    pass
        return {"ingested": ingested, "closed": closed, "errors": errors}

    def close_for_source(self, approval_id: str, status: str, reason: str) -> None:
        now = self.now()
        with self.repo.write() as s:
            policy, _ = self._policy(s)
            req = self._load(s, approval_id)
            if req.status in (S.PENDING.value, S.UNDER_REVIEW.value):
                target = S.CANCELLED.value
            elif req.status == S.APPROVED.value:
                target = S.INVALIDATED.value
            else:
                return
            self._move(s, req, target, SYSTEM, reason, now, event=f"approval.{target.lower()}")
            idea = s.get(TradeIdea, req.idea_id)
            idea.status, idea.updated_at = "cancelled", now
            self._notify(s, policy, req, f"approval.{target.lower()}", "normal", f"{target.title()}: {req.direction} {req.symbol}",
                         reason, now)
            assessment = req.risk_assessment_id
        self._cancel_assessment_later(assessment, reason)

    def receive_event(self, who: Principal, event: dict[str, Any]) -> dict[str, Any]:
        """Inbound events (n8n, Module 05, Module 09). Deduplicated by eventId. Business rules stay here: no event can approve."""
        required = ("event_id", "event_type", "event_version", "occurred_at", "source_module", "correlation_id", "payload")
        missing = [k for k in required if event.get(k) in (None, "")]
        if missing:
            raise ApprovalError(422, "INVALID_EVENT", f"Missing envelope fields: {', '.join(missing)}.")
        if not isinstance(event["payload"], dict):
            raise ApprovalError(422, "INVALID_EVENT", "payload must be an object.")
        if str(event["event_version"]).split(".")[0] != SCHEMA_VERSION.split(".")[0]:
            raise ApprovalError(422, "UNSUPPORTED_EVENT_VERSION", f"Event version {event['event_version']} is not compatible with {SCHEMA_VERSION}.")
        etype, eid, payload = str(event["event_type"]), str(event["event_id"])[:128], event["payload"]
        now = self.now()
        with self.repo.write() as s:
            rec = self.repo.idempotent(s, "event", eid)
            if rec is not None:
                return {"duplicate": True, "event_id": eid, "result": rec.response}
        actor = Actor.of(who)
        if etype in ("trade_idea.created", "trade_idea.updated"):
            result = self.ingest(payload, source="event", who=actor, correlation_id=str(event["correlation_id"]),
                                 update=etype.endswith("updated"))
        elif etype == "trade_idea.cancelled":
            idea_id = str(payload.get("idea_id") or "")
            with self.repo.read() as s:
                req = self._active_request(s, idea_id)
            if req is not None:
                self.close_for_source(req.approval_id, req.status, str(payload.get("reason") or "Cancelled by the idea's source."))
            result = {"idea_id": idea_id, "closed": req is not None}
        elif etype in ("execution.completed", "execution.failed", "execution.result"):
            result = self.report_execution(who, {**payload, "event_id": eid})
        elif etype == "position.updated":
            p = payload
            with self.repo.write() as s:
                self._upsert_position(s, {"position_id": str(p["position_id"]), "account_login": _s(p.get("account_login")),
                                          "symbol": str(p["symbol"]).upper(), "side": str(p["side"]).upper(),
                                          "volume": D(p.get("volume")) or Decimal(0), "entry_price": D(p.get("entry_price")) or Decimal(0),
                                          "current_price": D(p.get("current_price")), "stop_loss": D(p.get("stop_loss")),
                                          "take_profit": D(p.get("take_profit")), "unrealized_pnl": D(p.get("unrealized_pnl")),
                                          "realized_pnl": D(p.get("realized_pnl")),
                                          "status": str(p.get("status") or "UNKNOWN").upper(),
                                          "source_updated_at": _parse_time(event["occurred_at"]) or now}, "module09", now)
            result = {"position_id": str(p["position_id"]), "updated": True}
        elif etype in ("risk.assessment.completed", "market.data.updated"):
            result = {"recorded": True, "note": "Open requests are revalidated by a person or at handoff; this event does not "
                                                "change any decision."}
        else:
            result = {"ignored": True, "reason": f"Unknown event type {etype}."}
        with self.repo.write() as s:
            if self.repo.idempotent(s, "event", eid) is None:
                self.repo.remember(s, "event", eid, stable_hash(event), 200, jsonable(result), now)
            self.repo.audit(s, actor=who.username, actor_kind=who.kind, action="event.received", entity_type="event",
                            entity_id=eid, at=now, correlation_id=str(event["correlation_id"])[:64],
                            details={"event_type": etype, "source_module": event["source_module"]})
        return {"duplicate": False, "event_id": eid, "result": result}

    # ==== notifications =========================================================================================================
    def _notify(self, s: Session, policy: Policy, req: ApprovalRequest, event_type: str, priority: str, title: str,
                message: str, now: datetime) -> None:
        self.repo.notify(s, dedupe_key=f"{event_type}:{req.approval_id}:{req.version}", event_type=event_type,
                         priority=priority, title=title, message=message, at=now,
                         recipients=self._recipients(s, req.account_login), channels=policy.notifications.channels,
                         approval_id=req.approval_id)

    def notifications(self, who: Principal, unread_only: bool = False, limit: int = 100) -> list[dict[str, Any]]:
        if not who.is_human:
            return []
        with self.repo.read() as s:
            q = (select(Notification, NotificationDelivery).join(NotificationDelivery, and_(
                NotificationDelivery.notification_id == Notification.id, NotificationDelivery.user_id == who.id,
                NotificationDelivery.channel == "in_app")).order_by(Notification.created_at.desc()).limit(limit))
            if unread_only:
                q = q.where(NotificationDelivery.read_at.is_(None))
            return [{"id": n.id, "event_type": n.event_type, "priority": n.priority, "title": n.title, "message": n.message,
                     "approval_id": n.approval_id, "management_request_id": n.management_request_id,
                     "position_id": n.position_id, "created_at": n.created_at, "read": d.read_at is not None,
                     "read_at": d.read_at} for n, d in s.execute(q).all()]

    def mark_read(self, who: Principal, notification_id: str | None) -> dict[str, Any]:
        now = self.now()
        with self.repo.write() as s:
            q = update(NotificationDelivery).where(NotificationDelivery.user_id == who.id, NotificationDelivery.channel == "in_app",
                                                   NotificationDelivery.read_at.is_(None))
            if notification_id is not None:
                q = q.where(NotificationDelivery.notification_id == notification_id)
            n = s.execute(q.values(read_at=now)).rowcount
        return {"marked": n}

    def unread_count(self, who: Principal) -> int:
        with self.repo.read() as s:
            return int(s.execute(select(func.count()).select_from(NotificationDelivery).where(
                NotificationDelivery.user_id == who.id, NotificationDelivery.channel == "in_app",
                NotificationDelivery.read_at.is_(None))).scalar_one())

    # ==== views =================================================================================================================
    def policy_obj(self) -> Policy:
        with self.repo.write() as s:
            return self._policy(s)[0]

    @staticmethod
    def _event_payload(req: ApprovalRequest) -> dict[str, Any]:
        """The outbox payload for approval events: identifiers and parameters only, no evidence text or personal data."""
        return {"approval_id": req.approval_id, "trade_idea_id": req.idea_id, "idea_version": req.idea_version,
                "status": req.status, "version": req.version, "symbol": req.symbol, "direction": req.direction,
                "timeframe": req.timeframe, "strategy": req.strategy, "entry": _s(req.entry), "stop_loss": _s(req.stop_loss),
                "take_profit": _s(req.take_profit), "position_size": _s(req.position_size), "risk_pct": _s(req.risk_pct),
                "risk_decision": req.risk_decision, "risk_assessment_id": req.risk_assessment_id,
                "evidence_hash": req.evidence_hash, "expires_at": req.expires_at, "account_reference": req.account_login}

    def request_view(self, req: ApprovalRequest) -> dict[str, Any]:
        now = self.now()
        return {"approval_id": req.approval_id, "idea_id": req.idea_id, "idea_version": req.idea_version,
                "parent_approval_id": req.parent_approval_id, "status": req.status, "version": req.version,
                "symbol": req.symbol, "direction": req.direction, "timeframe": req.timeframe, "strategy": req.strategy,
                "entry": req.entry, "stop_loss": req.stop_loss, "take_profit": req.take_profit,
                "position_size": req.position_size, "risk_amount": req.risk_amount, "risk_pct": req.risk_pct,
                "risk_reward": req.risk_reward, "ai_confidence": req.ai_confidence, "risk_decision": req.risk_decision,
                "risk_assessment_id": req.risk_assessment_id, "risk_assessed_at": req.risk_assessed_at,
                "risk_expires_at": req.risk_expires_at, "risk_config_version": req.risk_config_version,
                "risk_warnings": req.risk_warnings or [], "evidence_snapshot_id": req.evidence_snapshot_id,
                "evidence_hash": req.evidence_hash, "analysis_version": req.analysis_version,
                "policy_version": req.policy_version, "account_login": req.account_login,
                "assigned_user_id": req.assigned_user_id, "approvals_required": req.approvals_required,
                "created_at": req.created_at, "expires_at": req.expires_at,
                "seconds_remaining": max(0, int((req.expires_at - now).total_seconds())) if req.status in (S.PENDING.value, S.UNDER_REVIEW.value) else None,
                "status_changed_at": req.status_changed_at, "status_reason": req.status_reason, "decided_at": req.decided_at,
                "can_approve": req.status in (S.PENDING.value, S.UNDER_REVIEW.value) and req.risk_decision in ("PASS", "REVIEW")
                and req.expires_at > now, "correlation_id": req.correlation_id}

    def list_requests(self, who: Principal, *, status: list[str] | None = None, symbol: str | None = None,
                      direction: str | None = None, timeframe: str | None = None, risk: str | None = None,
                      strategy: str | None = None, since: datetime | None = None, until: datetime | None = None,
                      sort: str = "-created_at", page: int = 1, page_size: int = 50) -> dict[str, Any]:
        cols = {"created_at": ApprovalRequest.created_at, "expires_at": ApprovalRequest.expires_at,
                "symbol": ApprovalRequest.symbol, "risk_pct": ApprovalRequest.risk_pct, "status": ApprovalRequest.status}
        desc = sort.startswith("-")
        col = cols.get(sort.lstrip("-"))
        if col is None:
            raise ApprovalError(422, "INVALID_SORT", f"sort must be one of {', '.join(cols)} (prefix - for descending).")
        conds = []
        if status:
            conds.append(ApprovalRequest.status.in_([x.upper() for x in status]))
        if symbol:
            conds.append(ApprovalRequest.symbol == symbol.upper())
        if direction:
            conds.append(ApprovalRequest.direction == direction.upper())
        if timeframe:
            conds.append(ApprovalRequest.timeframe == timeframe.upper())
        if risk:
            conds.append(ApprovalRequest.risk_decision == risk.upper())
        if strategy:
            conds.append(ApprovalRequest.strategy == strategy)
        if since:
            conds.append(ApprovalRequest.created_at >= since)
        if until:
            conds.append(ApprovalRequest.created_at < until)
        if who.account_scope is not None:
            conds.append(or_(ApprovalRequest.account_login.is_(None), ApprovalRequest.account_login.in_(list(who.account_scope))))
        page_size = max(1, min(200, page_size))
        with self.repo.read() as s:
            total = s.execute(select(func.count()).select_from(ApprovalRequest).where(*conds)).scalar_one()
            rows = s.execute(select(ApprovalRequest).where(*conds).order_by(col.desc() if desc else col.asc())
                             .offset((max(1, page) - 1) * page_size).limit(page_size)).scalars().all()
            return {"items": [self.request_view(r) for r in rows], "total": total, "page": page, "page_size": page_size}

    def detail(self, approval_id: str, who: Principal) -> dict[str, Any]:
        with self.repo.read() as s:
            req = self._load(s, approval_id, lock=False)
            self._visible(s, req, who)
            idea = s.get(TradeIdea, req.idea_id)
            versions = s.execute(select(TradeIdeaVersion).where(TradeIdeaVersion.idea_id == req.idea_id)
                                 .order_by(TradeIdeaVersion.version)).scalars().all()
            decisions = s.execute(select(ApprovalDecision).where(ApprovalDecision.approval_id == approval_id)
                                  .order_by(ApprovalDecision.decided_at)).scalars().all()
            risks = s.execute(select(RiskAssessmentSnapshot).where(RiskAssessmentSnapshot.approval_id == approval_id)
                              .order_by(RiskAssessmentSnapshot.created_at)).scalars().all()
            snaps = s.execute(select(ApprovalEvidenceSnapshot.id, ApprovalEvidenceSnapshot.content_hash,
                                     ApprovalEvidenceSnapshot.created_at, ApprovalEvidenceSnapshot.completeness)
                              .where(ApprovalEvidenceSnapshot.approval_id == approval_id)
                              .order_by(ApprovalEvidenceSnapshot.created_at)).all()
            mods = s.execute(select(ModificationRequest).where(or_(ModificationRequest.approval_id == approval_id,
                                                                   ModificationRequest.new_approval_id == approval_id))).scalars().all()
            handoff = s.execute(select(ExecutionHandoff).where(ExecutionHandoff.approval_id == approval_id)).scalar_one_or_none()
            related = s.execute(select(ApprovalRequest.approval_id, ApprovalRequest.status, ApprovalRequest.idea_version,
                                       ApprovalRequest.created_at).where(ApprovalRequest.idea_id == req.idea_id)
                                .order_by(ApprovalRequest.created_at)).all()
            return {
                "request": self.request_view(req),
                "idea": {"idea_id": idea.idea_id, "source": idea.source, "source_ref": idea.source_ref, "market": idea.market,
                         "status": idea.status, "current_version": idea.current_version, "expires_at": idea.expires_at},
                "versions": [{"version": v.version, "direction": v.direction, "entry": v.entry, "entry_low": v.entry_low,
                              "entry_high": v.entry_high, "stop_loss": v.stop_loss, "take_profits": v.take_profits,
                              "requested_volume": v.requested_volume, "analysis_version": v.analysis_version,
                              "created_by": v.created_by, "reason": v.reason, "created_at": v.created_at,
                              "payload_hash": v.payload_hash} for v in versions],
                "decisions": [{"decision": d.decision, "is_final": d.is_final, "username": d.username, "role": d.role,
                               "method": d.method, "decided_at": d.decided_at, "reason_code": d.reason_code, "comment": d.comment,
                               "evidence_hash": d.evidence_hash, "risk_assessment_id": d.risk_assessment_id,
                               "acknowledged_warnings": d.acknowledged_warnings, "record_hash": d.record_hash} for d in decisions],
                "risk_assessments": [{"purpose": r.purpose, "assessment_id": r.assessment_id, "decision": r.decision,
                                      "final_volume": r.final_volume, "risk_pct": r.risk_pct, "risk_amount": r.risk_amount,
                                      "config_version": r.config_version, "assessed_at": r.assessed_at,
                                      "expires_at": r.expires_at} for r in risks],
                "evidence_snapshots": [{"id": i, "hash": h, "created_at": c, "completeness": comp} for i, h, c, comp in snaps],
                "modifications": [{"id": m.id, "kinds": m.kinds, "changes": m.changes, "previous": m.previous, "comment": m.comment,
                                   "status": m.status, "new_approval_id": m.new_approval_id, "approval_id": m.approval_id,
                                   "created_at": m.created_at} for m in mods],
                "handoff": self._handoff_view(s, handoff) if handoff else None,
                "related_requests": [{"approval_id": a, "status": st, "idea_version": iv, "created_at": c} for a, st, iv, c in related],
                "disclaimer": DISCLAIMER}

    def _handoff_view(self, s: Session, h: ExecutionHandoff) -> dict[str, Any]:
        results = s.execute(select(ExecutionResult).where(ExecutionResult.execution_request_id == h.execution_request_id)
                            .order_by(ExecutionResult.reported_at)).scalars().all()
        return {"execution_request_id": h.execution_request_id, "kind": h.kind, "status": h.status, "created_at": h.created_at,
                "picked_up_at": h.picked_up_at, "payload": h.payload,
                "results": [{"event_id": r.event_id, "status": r.status, "broker_ticket": r.broker_ticket,
                             "position_id": r.position_id, "filled_volume": r.filled_volume, "fill_price": r.fill_price,
                             "message": r.message, "reported_by": r.reported_by, "reported_at": r.reported_at} for r in results]}

    def evidence(self, approval_id: str, who: Principal, snapshot_id: str | None = None) -> dict[str, Any]:
        with self.repo.read() as s:
            req = self._load(s, approval_id, lock=False)
            self._visible(s, req, who)
            sid = snapshot_id or req.evidence_snapshot_id
            snap = s.get(ApprovalEvidenceSnapshot, sid) if sid else None
            if snap is None or snap.approval_id not in (approval_id, None):
                raise ApprovalError(404, "NOT_FOUND", "Evidence snapshot not found.")
            return {"snapshot_id": snap.id, "hash": snap.content_hash, "created_at": snap.created_at,
                    "completeness": snap.completeness, "current": snap.id == req.evidence_snapshot_id, **snap.content}

    def idea_evidence(self, idea_id: str, who: Principal) -> dict[str, Any]:
        with self.repo.read() as s:
            req = s.execute(select(ApprovalRequest).where(ApprovalRequest.idea_id == idea_id)
                            .order_by(ApprovalRequest.created_at.desc()).limit(1)).scalar_one_or_none()
        if req is None:
            raise ApprovalError(404, "NOT_FOUND", "No evidence for this idea.")
        return self.evidence(req.approval_id, who)

    def ideas_list(self, who: Principal, limit: int = 100) -> list[dict[str, Any]]:
        with self.repo.read() as s:
            rows = s.execute(select(TradeIdea).order_by(TradeIdea.created_at.desc()).limit(limit)).scalars().all()
            return [{"idea_id": i.idea_id, "source": i.source, "source_ref": i.source_ref, "symbol": i.symbol, "market": i.market,
                     "timeframe": i.timeframe, "strategy": i.strategy, "status": i.status, "current_version": i.current_version,
                     "created_at": i.created_at, "expires_at": i.expires_at} for i in rows]

    def idea(self, idea_id: str, who: Principal) -> dict[str, Any]:
        with self.repo.read() as s:
            i = s.get(TradeIdea, idea_id)
            if i is None:
                raise ApprovalError(404, "NOT_FOUND", "Trade idea not found.")
            versions = s.execute(select(TradeIdeaVersion).where(TradeIdeaVersion.idea_id == idea_id)
                                 .order_by(TradeIdeaVersion.version)).scalars().all()
            reqs = s.execute(select(ApprovalRequest).where(ApprovalRequest.idea_id == idea_id)
                             .order_by(ApprovalRequest.created_at)).scalars().all()
            return {"idea_id": i.idea_id, "source": i.source, "symbol": i.symbol, "status": i.status,
                    "versions": [{"version": v.version, "direction": v.direction, "entry": v.entry, "stop_loss": v.stop_loss,
                                  "take_profits": v.take_profits, "analysis_version": v.analysis_version,
                                  "ai_confidence": v.ai_confidence, "ai_summary": v.ai_summary, "payload": v.payload,
                                  "created_at": v.created_at} for v in versions],
                    "approval_requests": [self.request_view(r) for r in reqs if who.may_access_account(r.account_login)]}

    def history(self, approval_id: str, who: Principal) -> list[dict[str, Any]]:
        with self.repo.read() as s:
            req = self._load(s, approval_id, lock=False)
            self._visible(s, req, who)
            rows = s.execute(select(ApprovalAuditLog).where(ApprovalAuditLog.approval_id == approval_id)
                             .order_by(ApprovalAuditLog.seq)).scalars().all()
            return [_audit_row(r) for r in rows]

    def audit_log(self, who: Principal, *, limit: int = 200, entity_type: str | None = None, approval_id: str | None = None,
                  actor: str | None = None) -> dict[str, Any]:
        with self.repo.read() as s:
            q = select(ApprovalAuditLog).order_by(ApprovalAuditLog.seq.desc()).limit(max(1, min(1000, limit)))
            if entity_type:
                q = q.where(ApprovalAuditLog.entity_type == entity_type)
            if approval_id:
                q = q.where(ApprovalAuditLog.approval_id == approval_id)
            if actor:
                q = q.where(ApprovalAuditLog.actor == actor)
            if not who.can(perm.AUDIT_ALL):
                q = q.where(ApprovalAuditLog.actor == who.username)       # people see their own actions unless admin
            rows = [_audit_row(r) for r in s.execute(q).scalars()]
        ok, n, err = self.repo.verify_audit()
        return {"chain_valid": ok, "rows_checked": n, "error": err, "items": rows}

    def dashboard(self, who: Principal) -> dict[str, Any]:
        now = self.now()
        with self.repo.read() as s:
            counts = dict(s.execute(select(ApprovalRequest.status, func.count()).group_by(ApprovalRequest.status)).all())
            pending = s.execute(select(ApprovalRequest).where(ApprovalRequest.status.in_([x.value for x in OPEN]))
                                .order_by(ApprovalRequest.expires_at).limit(20)).scalars().all()
            recent_handoffs = s.execute(select(ExecutionHandoff).order_by(ExecutionHandoff.created_at.desc()).limit(10)).scalars().all()
            mgmt_pending = s.execute(select(func.count()).select_from(TradeManagementRequest)
                                     .where(TradeManagementRequest.status == MS.PENDING.value)).scalar_one()
            pending_views = [self.request_view(r) for r in pending if who.may_access_account(r.account_login)]
            handoffs = [{"execution_request_id": h.execution_request_id, "kind": h.kind, "status": h.status,
                         "approval_id": h.approval_id, "created_at": h.created_at} for h in recent_handoffs]
        return {"counts": {st.value: int(counts.get(st.value, 0)) for st in S}, "pending": pending_views,
                "management_pending": int(mgmt_pending), "recent_handoffs": handoffs, "statistics": self.statistics(),
                "generated_at": now, "module_version": MODULE_VERSION}

    def statistics(self, since: datetime | None = None) -> dict[str, Any]:
        with self.repo.read() as s:
            q = select(ApprovalRequest.status, ApprovalRequest.created_at, ApprovalRequest.decided_at)
            if since:
                q = q.where(ApprovalRequest.created_at >= since)
            rows = s.execute(q).all()
        decided = [(st, c, d) for st, c, d in rows if d is not None]
        durations = sorted((utc(d) - utc(c)).total_seconds() for _, c, d in decided)
        approved = sum(1 for st, _, _ in rows if st in (S.APPROVED.value, S.EXECUTION_PENDING.value, S.EXECUTION_SUBMITTED.value,
                                                       S.EXECUTED.value, S.EXECUTION_FAILED.value))
        rejected = sum(1 for st, _, _ in rows if st == S.REJECTED.value)
        total_decided = approved + rejected
        return {"total": len(rows), "approved": approved, "rejected": rejected,
                "expired": sum(1 for st, _, _ in rows if st == S.EXPIRED.value),
                "invalidated": sum(1 for st, _, _ in rows if st == S.INVALIDATED.value),
                "executed": sum(1 for st, _, _ in rows if st == S.EXECUTED.value),
                "execution_failed": sum(1 for st, _, _ in rows if st == S.EXECUTION_FAILED.value),
                "approval_rate": round(approved / total_decided, 4) if total_decided else None,
                "average_decision_seconds": round(sum(durations) / len(durations), 1) if durations else None,
                "median_decision_seconds": durations[len(durations) // 2] if durations else None}

    def health(self) -> dict[str, Any]:
        out: dict[str, Any] = {"module_version": MODULE_VERSION, "schema_ready": self.repo.schema_ready()}
        if not out["schema_ready"]:
            out["status"] = "not_ready"
            out["detail"] = "Run `python -m aurum.cli init-db` to create the Module 07 tables."
            return out
        ok, n, err = self.repo.verify_audit()
        deps: dict[str, Any] = {}
        try:
            deps["module_06"] = {"ok": True, "symbols": len(self.risk.symbols()), "account": self.risk.account_login()}
        except Exception as exc:  # noqa: BLE001
            deps["module_06"] = {"ok": False, "detail": type(exc).__name__}
        deps["module_05"] = {"ok": self.ideas is not None}
        deps["module_09"] = {"ok": False, "detail": "Not built. Handoffs wait in the execution queue; nothing is executed."}
        with self.repo.read() as s:
            pending_out = s.execute(select(func.count()).select_from(_outbox()).where(_outbox().status == "pending")).scalar_one()
            dead = s.execute(select(func.count()).select_from(_outbox()).where(_outbox().status == "dead")).scalar_one()
            failed_deliveries = s.execute(select(func.count()).select_from(NotificationDelivery)
                                          .where(NotificationDelivery.status.in_(["failed", "dead"]))).scalar_one()
        out.update({"status": "ok" if ok and deps["module_06"]["ok"] else "degraded", "audit_chain": {"valid": ok, "rows": n,
                    "error": err}, "dependencies": deps, "outbox": {"pending": pending_out, "dead": dead},
                    "notification_failures": failed_deliveries})
        return out


# ---- helpers -------------------------------------------------------------------------------------------------------------------
def _outbox() -> Any:
    from aurum.approvals.db_models import OutboxEvent
    return OutboxEvent


def _audit_row(r: ApprovalAuditLog) -> dict[str, Any]:
    return {"seq": r.seq, "at": utc(r.at), "actor": r.actor, "actor_kind": r.actor_kind, "action": r.action,
            "entity_type": r.entity_type, "entity_id": r.entity_id, "approval_id": r.approval_id, "idea_id": r.idea_id,
            "previous_state": r.previous_state, "new_state": r.new_state, "reason": r.reason,
            "correlation_id": r.correlation_id, "evidence_snapshot_id": r.evidence_snapshot_id, "details": r.details,
            "hash": r.hash}


def _parse_time(v: Any) -> datetime | None:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    try:
        t = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _default_seed_path() -> Any:
    from pathlib import Path
    return Path(__file__).resolve().parents[2] / "config" / "approvals.yaml"
