"""Module 06 facade used by the API, the CLI, the event inbox and the simulator."""
from __future__ import annotations

import json
import logging
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from pydantic import ValidationError

from aurum.risk import ENGINE_VERSION, SOURCE_MODULE
from aurum.risk.config import PROFILES, RiskConfig, deep_merge, profile_config, weakened_fields
from aurum.risk.engine import ProposalError, RiskEngine
from aurum.risk.models import BLOCK, ERROR, PASS, REVIEW
from aurum.risk.providers import DatabaseRiskData, RiskData
from aurum.risk.repository import ConfigConflict, RiskRepository, jsonable
from aurum.risk.rules import NOT_DISABLEABLE, RULES

log = logging.getLogger(__name__)
LOCK_SCOPES = ("global", "account", "instrument", "strategy")
INBOUND_TYPES = ("trade_idea.created", "account.snapshot.updated", "position.updated", "market.data.updated",
                 "market.intelligence.updated", "risk.configuration.updated", "risk.lock.activated", "risk.lock.released")
MAX_DELIVERY_ATTEMPTS = 8
MAX_INBOX_ATTEMPTS = 5


class RiskError(Exception):
    """Expected failure with an HTTP status and an error code."""

    def __init__(self, status: int, code: str, message: str, details: Any = None):
        super().__init__(message)
        self.status, self.code, self.message, self.details = status, code, message, details


class RiskService:
    def __init__(self, engine: Any, settings: Any, data: RiskData | None = None, clock: Callable[[], datetime] | None = None,
                 intelligence_service: Any = None):
        self.repo = RiskRepository(engine)
        self.settings = settings
        self.data = data or DatabaseRiskData(engine, settings, intelligence_service)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.engine = RiskEngine(self.data, self.repo, self.clock)

    # ---- assessments ---------------------------------------------------------------------------------------------------
    def assess(self, body: dict[str, Any], actor: str, correlation_id: str | None, idempotency_key: str | None) -> dict[str, Any]:
        from aurum.risk.engine import IdempotencyConflict
        from aurum.risk.repository import SchemaNotReady
        try:
            return self.engine.assess(body, actor=actor, correlation_id=correlation_id, idempotency_key=idempotency_key)
        except ProposalError as exc:
            raise RiskError(422, "INVALID_PROPOSAL", str(exc)) from exc
        except IdempotencyConflict as exc:
            raise RiskError(409, "IDEMPOTENCY_CONFLICT", str(exc)) from exc
        except SchemaNotReady as exc:
            raise RiskError(503, "SCHEMA_NOT_READY", str(exc)) from exc

    def position_size(self, body: dict[str, Any]) -> dict[str, Any]:
        try:
            return self.engine.position_size(body)
        except ProposalError as exc:
            raise RiskError(422, "INVALID_PROPOSAL", str(exc)) from exc

    def get_assessment(self, assessment_id: str) -> dict[str, Any]:
        a = self.repo.get_assessment(assessment_id)
        if a is None:
            raise RiskError(404, "NOT_FOUND", "Assessment not found.")
        return a

    def validate_assessment(self, assessment_id: str) -> dict[str, Any]:
        """The check Module 07 (and later Module 09) runs before acting on an assessment. Valid only when it was PASS or
        REVIEW, is unexpired, unused, made under the active configuration, and no lock now applies to it."""
        now = self.clock()
        a = self.get_assessment(assessment_id)
        cfg, version, _ = self.repo.active_config()
        reasons = []
        if a["decision"] not in (PASS, REVIEW):
            reasons.append(f"Decision is {a['decision']}; it can never be approved.")
        expires = datetime.fromisoformat(a["expires_at"])
        if expires <= now:
            reasons.append("Assessment has expired; request a new one.")
        if a.get("state") != "active":
            reasons.append(f"Assessment is {a.get('state')}; it cannot be reused.")
        if a["config_version"] != version:
            reasons.append(f"Risk configuration changed (v{a['config_version']} -> v{version}); reassess.")
        with self.repo.serialized() as s:
            self.repo.expire_locks(s, now)
            locks = [lk for lk in self.repo.active_locks(s, now) if lk.applies(a["instrument"], a.get("strategy"), None)]
        if locks:
            reasons.append("Active risk lock: " + "; ".join(lk.reason for lk in locks))
        return jsonable({"assessment_id": assessment_id, "valid": not reasons, "decision": a["decision"], "reasons": reasons,
                         "requires_warning_display": a["decision"] == REVIEW, "final_volume": a.get("final_volume"),
                         "expires_at": a["expires_at"], "config_version": a["config_version"], "active_config_version": version,
                         "checked_at": now})

    def set_assessment_state(self, assessment_id: str, state: str, actor: str, reason: str | None,
                             correlation_id: str | None) -> dict[str, Any]:
        """``consumed`` (Module 07 took it forward) or ``cancelled`` (frees its reserved risk). One-way only."""
        if state not in ("consumed", "cancelled"):
            raise RiskError(422, "INVALID_STATE", "state must be consumed or cancelled.")
        now = self.clock()
        if state == "consumed":
            # Validated before taking the write lock (validation itself takes it briefly to expire locks).
            check = self.validate_assessment(assessment_id)
            if not check["valid"]:
                raise RiskError(409, "ASSESSMENT_NOT_VALID", "Cannot consume this assessment.", check["reasons"])
        with self.repo.serialized() as s:
            from sqlalchemy import select

            from aurum.risk.db_models import RiskAssessment
            row = s.execute(select(RiskAssessment).where(RiskAssessment.assessment_id == assessment_id)).scalar_one_or_none()
            if row is None:
                raise RiskError(404, "NOT_FOUND", "Assessment not found.")
            if row.state != "active":
                raise RiskError(409, "STATE_FINAL", f"Assessment is already {row.state}.")
            row.state, row.state_changed_at = state, now
            self.repo.audit(s, actor, f"assessment.{state}", "risk_assessment", assessment_id, {"reason": reason}, correlation_id, at=now)
        return self.get_assessment(assessment_id)

    def history(self, **filters: Any) -> dict[str, Any]:
        total, rows = self.repo.list_assessments(**filters)
        return jsonable({"total": total, "limit": filters["limit"], "offset": filters["offset"], "items": rows})

    # ---- configuration -------------------------------------------------------------------------------------------------
    def config(self) -> dict[str, Any]:
        cfg, version, meta = self.repo.active_config()
        return jsonable({"version": version, "meta": meta, "config": cfg.model_dump(mode="json"), "profiles": list(PROFILES),
                         "history": self.repo.config_versions(20)})

    def update_config(self, body: dict[str, Any], actor: str, correlation_id: str | None) -> dict[str, Any]:
        """Body: {base_version, reason, profile?, changes?, acknowledge_weakening?}. A profile switch starts from that profile;
        ``changes`` is merged on top. Any loosened limit needs acknowledge_weakening=true. Active locks are never released."""
        reason = (body.get("reason") or "").strip()
        if len(reason) < 5:
            raise RiskError(422, "REASON_REQUIRED", "A reason of at least 5 characters is required for every risk-setting change.")
        base_version = body.get("base_version")
        if not isinstance(base_version, int):
            raise RiskError(422, "BASE_VERSION_REQUIRED", "base_version (the version you are editing) is required.")
        old, version, _ = self.repo.active_config()
        changes = body.get("changes") or {}
        if not isinstance(changes, dict):
            raise RiskError(422, "INVALID_CHANGES", "changes must be an object.")
        for rid, ov in (changes.get("rules") or {}).items():
            if rid in NOT_DISABLEABLE and isinstance(ov, dict) and ov.get("enabled") is False:
                raise RiskError(422, "RULE_NOT_DISABLEABLE", f"{rid} is a safety rule and cannot be disabled.")
        try:
            if body.get("profile") and body["profile"] != old.profile:
                new = profile_config(body["profile"], overrides=changes or None)
            else:
                data = deep_merge(old.model_dump(mode="json"), changes)
                if changes and data.get("profile") != "custom":
                    data["profile"] = "custom"
                new = RiskConfig.model_validate(data)
        except (ValidationError, ValueError) as exc:
            raise RiskError(422, "INVALID_CONFIG", "Configuration is invalid.", _errors(exc)) from exc
        if new.fingerprint() == old.fingerprint():
            raise RiskError(409, "NO_CHANGE", "The new configuration is identical to the active one.")
        weakened = weakened_fields(old, new)
        if weakened and body.get("acknowledge_weakening") is not True:
            raise RiskError(409, "WEAKENING_NOT_ACKNOWLEDGED", f"These changes loosen risk limits ({'; '.join(weakened)}). Resend "
                                                               "with acknowledge_weakening=true to confirm.", weakened)
        now = self.clock()
        try:
            with self.repo.serialized() as s:
                new_version = self.repo.add_config_version(s, new, base_version, actor, reason, weakened)
                self.repo.audit(s, actor, "config.updated", "risk_config", str(new_version),
                                {"from_version": version, "reason": reason, "weakened": weakened, "profile": new.profile,
                                 "fingerprint": new.fingerprint()}, correlation_id, at=now)
                self.repo.add_event(s, "risk.configuration.updated", str(new_version),
                                    {"version": new_version, "previous_version": version, "weakened": weakened, "actor": actor},
                                    correlation_id=correlation_id, idempotency_key=f"config:{new_version}", now=now)
        except ConfigConflict as exc:
            raise RiskError(409, "VERSION_CONFLICT", str(exc)) from exc
        out = self.config()
        out["weakened"] = weakened
        out["note"] = ("Active risk locks are unchanged. Assessments made under earlier versions are no longer valid for "
                       "approval (validate reports the version change).")
        return out

    def rules(self) -> list[dict[str, Any]]:
        cfg, version, _ = self.repo.active_config()
        return jsonable([{"rule_id": r.rule_id, "name": r.name, "category": r.category, "severity": r.severity,
                          "description": r.description, "enabled": r.rule_id in NOT_DISABLEABLE or cfg.rule_override(r.rule_id).get("enabled", True),
                          "can_disable": r.rule_id not in NOT_DISABLEABLE, "applies_to": list(r.asset_classes) or ["all"],
                          "thresholds": r.thresholds(cfg), "config_version": version} for r in RULES])

    # ---- locks ---------------------------------------------------------------------------------------------------------
    def locks(self, include_history: bool) -> dict[str, Any]:
        now = self.clock()
        with self.repo.serialized() as s:
            self.repo.expire_locks(s, now)
        rows = self.repo.locks(include_history=include_history)
        return jsonable({"active": [r for r in rows if r["status"] == "active"],
                         "history": [r for r in rows if r["status"] != "active"] if include_history else []})

    def create_lock(self, body: dict[str, Any], actor: str, correlation_id: str | None) -> dict[str, Any]:
        scope = body.get("scope") or "global"
        if scope not in LOCK_SCOPES:
            raise RiskError(422, "INVALID_SCOPE", f"scope must be one of {', '.join(LOCK_SCOPES)}.")
        value = body.get("scope_value")
        if scope != "global" and not value:
            raise RiskError(422, "SCOPE_VALUE_REQUIRED", f"A {scope} lock needs scope_value.")
        reason = (body.get("reason") or "").strip()
        if len(reason) < 5:
            raise RiskError(422, "REASON_REQUIRED", "A reason of at least 5 characters is required.")
        expires = None
        if body.get("expires_at"):
            try:
                expires = datetime.fromisoformat(str(body["expires_at"]).replace("Z", "+00:00"))
            except ValueError as exc:
                raise RiskError(422, "INVALID_EXPIRY", "expires_at must be an ISO timestamp with a time zone.") from exc
            if expires.tzinfo is None or expires <= self.clock():
                raise RiskError(422, "INVALID_EXPIRY", "expires_at must be a future, time-zone-aware timestamp.")
        now = self.clock()
        with self.repo.serialized() as s:
            lock, _ = self.repo.activate_lock(s, scope=scope, scope_value=str(value) if value else None, reason=reason,
                                              trigger_source=str(body.get("trigger_source") or "manual")[:48], rule_id=None,
                                              actor=actor, now=now, expires_at=expires, details={"manual": True},
                                              dedupe_key=None, correlation_id=correlation_id)
            self.repo.add_event(s, "risk.lock.activated", lock.lock_id, {"lock_id": lock.lock_id, "scope": scope,
                                "scope_value": value, "reason": reason, "actor": actor, "expires_at": expires},
                                correlation_id=correlation_id, idempotency_key=f"lock.activated:{lock.lock_id}", now=now)
        return jsonable(lock.__dict__)

    def release_lock(self, lock_id: str, body: dict[str, Any], actor: str, correlation_id: str | None) -> dict[str, Any]:
        reason = (body.get("reason") or "").strip()
        if len(reason) < 5:
            raise RiskError(422, "REASON_REQUIRED", "A release reason of at least 5 characters is required.")
        now = self.clock()
        with self.repo.serialized() as s:
            was_active = self.repo.lock_status(s, lock_id)
            if was_active is None:
                raise RiskError(404, "NOT_FOUND", "Lock not found.")
            if was_active != "active":
                raise RiskError(409, "LOCK_NOT_ACTIVE", f"Lock is already {was_active}.")
            r = self.repo.release_lock(s, lock_id, actor, reason, now, correlation_id)
            self.repo.add_event(s, "risk.lock.released", lock_id, {"lock_id": lock_id, "reason": reason, "actor": actor,
                                "lock_reason": r.reason}, correlation_id=correlation_id,
                                idempotency_key=f"lock.released:{lock_id}", now=now)
            still = r.rule_id in ("R030", "R031", "R032", "R033")
        out = {"lock_id": lock_id, "status": "released", "released_by": actor, "released_at": now, "release_reason": reason}
        if still:
            out["note"] = ("The loss or drawdown limit itself is still evaluated on every assessment: while it remains reached, "
                           "new trades stay blocked. Releasing the lock records the decision; it does not raise the limit.")
        return jsonable(out)

    # ---- dashboards ----------------------------------------------------------------------------------------------------
    def account(self) -> dict[str, Any]:
        return self.engine.account_status()

    def portfolio(self) -> dict[str, Any]:
        return self.engine.portfolio_status()

    def health(self) -> dict[str, Any]:
        now = self.clock()
        out: dict[str, Any] = {"engine_version": ENGINE_VERSION, "source_module": SOURCE_MODULE, "checked_at": now}
        deps: dict[str, Any] = {}
        ready = self.repo.schema_ready()
        deps["database"] = {"ok": ready, "detail": None if ready else "Module 06 tables missing: run init-db."}
        snap = self.data.account_snapshot()
        deps["account_mirror"] = {"ok": snap is not None and snap.trade_mode == "DEMO",
                                  "captured_at": snap.captured_at if snap else None,
                                  "age_minutes": round((now - snap.captured_at).total_seconds() / 60, 1) if snap else None,
                                  "detail": None if snap else "No account snapshot; run sync-account."}
        specs = {}
        for sym in sorted(self.data.symbols()):
            try:
                sp = self.data.spec(sym)
                specs[sym] = {"ok": True, "age_hours": round((now - sp.captured_at).total_seconds() / 3600, 1)}
            except Exception as exc:  # noqa: BLE001
                specs[sym] = {"ok": False, "detail": str(exc)}
            q = self.data.quote(sym, now)
            specs[sym]["quote_age_minutes"] = round((now - q.at).total_seconds() / 60, 1) if q else None
        deps["instruments"] = specs
        if ready:
            cfg, version, _ = self.repo.active_config()
            out["config_version"] = version
            deps["events"] = self.repo.event_counts()
            ok_audit, n, problem = self.repo.verify_audit()
            deps["audit_chain"] = {"ok": ok_audit, "rows": n, "detail": problem}
        deps["module_04"] = {"ok": None, "detail": "Checked per assessment (it is slow to run); unavailable data is reported there."}
        out["dependencies"] = deps
        critical = [deps["database"]["ok"], deps["account_mirror"]["ok"], deps.get("audit_chain", {}).get("ok", False)]
        out["status"] = "ok" if all(critical) else "degraded"
        out["can_assess"] = bool(deps["database"]["ok"])
        out["note"] = "When a dependency is degraded, assessments still run and return ERROR or REVIEW; they never default to PASS."
        return jsonable(out)

    def audit(self, limit: int, entity_type: str | None) -> list[dict[str, Any]]:
        return jsonable(self.repo.audit_rows(limit, entity_type))

    # ---- events --------------------------------------------------------------------------------------------------------
    def events(self, after: int, limit: int, event_type: str | None) -> list[dict[str, Any]]:
        return jsonable(self.repo.events_after(after, limit, event_type))

    def dispatch_events(self, url: str | None, limit: int = 100, timeout: float = 10.0,
                        sender: Callable[[str, dict[str, Any], float], None] | None = None) -> dict[str, int]:
        """Deliver pending outbox events to a webhook (e.g. an n8n Webhook node). Failures back off exponentially and are
        dead-lettered after MAX_DELIVERY_ATTEMPTS; nothing is ever deleted."""
        if not url and sender is None:
            raise RiskError(422, "NO_WEBHOOK", "Set RISK_EVENTS_WEBHOOK_URL or pass --url.")
        send = sender or _post_json
        now = self.clock()
        stats = {"delivered": 0, "failed": 0, "dead": 0}
        with self.repo.serialized() as s:
            for e in self.repo.due_events(s, now, limit):
                e.attempts += 1
                try:
                    send(url or "", self.repo.event_view(e), timeout)
                    e.status, e.delivered_at, e.last_error = "delivered", now, None
                    stats["delivered"] += 1
                except Exception as exc:  # noqa: BLE001
                    e.last_error = f"{type(exc).__name__}: {exc}"[:500]
                    if e.attempts >= MAX_DELIVERY_ATTEMPTS:
                        e.status = "dead"
                        stats["dead"] += 1
                    else:
                        e.next_attempt_at = now + timedelta(seconds=min(3600, 15 * 2 ** (e.attempts - 1)))
                        stats["failed"] += 1
        return stats

    def receive_event(self, event: dict[str, Any], actor: str) -> dict[str, Any]:
        """Inbound events (from Module 05, the account mirror or n8n). Duplicate event_ids are acknowledged without being
        processed again. Unknown types are stored as ignored. Failures are retried by redelivery up to MAX_INBOX_ATTEMPTS,
        then dead-lettered."""
        event_id, etype = str(event.get("event_id") or ""), str(event.get("event_type") or "")
        if not event_id or not etype:
            raise RiskError(422, "INVALID_EVENT", "event_id and event_type are required.")
        payload = event.get("payload") or {}
        from aurum.risk.db_models import RiskInboxEvent
        with self.repo.read() as s:
            row = self.repo.inbox_get(s, event_id)
            if row is not None and row.status in ("processed", "ignored", "dead"):
                return jsonable({"event_id": event_id, "status": row.status, "duplicate": True, "result": row.result})
            attempts = (row.attempts + 1) if row is not None else 1
        status, result, error = "processed", None, None
        try:
            if etype == "trade_idea.created":
                idea = payload.get("trade_idea_id") or event.get("entity_id")
                if not idea:
                    raise ValueError("trade_idea.created needs payload.trade_idea_id")
                a = self.engine.assess({"trade_idea_id": idea}, actor=f"event:{actor}", correlation_id=event.get("correlation_id"),
                                       idempotency_key=f"event:{event_id}")
                result = {"assessment_id": a["assessment_id"], "decision": a["decision"]}
            elif etype in ("account.snapshot.updated", "position.updated", "market.data.updated", "market.intelligence.updated"):
                acct = self.engine.account_status()
                result = {"overall_status": acct["overall_status"], "note": "Limits are re-evaluated on the next assessment."}
            elif etype in ("risk.configuration.updated", "risk.lock.activated", "risk.lock.released"):
                status, result = "ignored", {"note": "Module 06 is the source of these events; external copies change nothing."}
            else:
                status, result = "ignored", {"note": f"Unsupported event type {etype}."}
        except Exception as exc:  # noqa: BLE001
            error = f"{type(exc).__name__}: {exc}"[:500]
            status = "dead" if attempts >= MAX_INBOX_ATTEMPTS else "failed"
        with self.repo.serialized() as s:
            row = self.repo.inbox_get(s, event_id)
            if row is None:
                s.add(RiskInboxEvent(event_id=event_id, event_type=etype, status=status, attempts=attempts, last_error=error,
                                     payload=jsonable(event), result=jsonable(result)))
            else:
                row.status, row.attempts, row.last_error, row.result = status, attempts, error, jsonable(result)
            self.repo.audit(s, actor, "event.received", "risk_inbox", event_id, {"type": etype, "status": status, "error": error})
        if status in ("failed", "dead"):
            raise RiskError(500 if status == "failed" else 422, "EVENT_FAILED" if status == "failed" else "EVENT_DEAD_LETTERED",
                            error or "Event processing failed.", {"attempts": attempts})
        return jsonable({"event_id": event_id, "status": status, "duplicate": False, "result": result})


def _errors(exc: Exception) -> Any:
    if isinstance(exc, ValidationError):
        return [{"loc": ".".join(map(str, e["loc"])), "msg": e["msg"]} for e in exc.errors()]
    return str(exc)


def _post_json(url: str, body: dict[str, Any], timeout: float) -> None:
    data = json.dumps(jsonable(body)).encode()
    req = urllib.request.Request(url, data=data, method="POST", headers={"Content-Type": "application/json",
                                                                          "X-AURUM-Event-Type": body["event_type"],
                                                                          "X-AURUM-Event-Id": body["event_id"]})
    started = time.monotonic()
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - URL comes from the operator's configuration
        if resp.status >= 300:
            raise RuntimeError(f"HTTP {resp.status} after {time.monotonic() - started:.1f}s")
