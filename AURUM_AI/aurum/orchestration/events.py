"""Event infrastructure: PostgreSQL-backed (no new broker; Redis/RabbitMQ/Kafka are not needed at this scale).

- Publish: validate against the catalogue (owner, version, environment, payload, no credentials) -> inbox row (duplicate
  message ids are recognised) -> event row + one delivery row per matching subscription, in ONE transaction (outbox).
- ``publish_internal`` does the same inside a caller's transaction, so Module 08's own state changes and their events
  commit together.
- Bridge: Module 06 (``risk_events``) and Module 07 (``outbox_events``) already have outboxes. The bridge reads them
  read-only by cursor and records each event once (event id = source event id), so n8n workflows can subscribe to risk and
  approval events without those modules changing.
- Delivery (worker): leases due deliveries, then either triggers a workflow (idempotency key ``evt:<event>:<sub>:<replay>``,
  so a re-delivery never creates a second execution) or POSTs the signed envelope to an allow-listed webhook. Failures are
  retried with back-off and dead-lettered after the subscription's limit. Delivery is at-least-once; receivers deduplicate
  by ``eventId``.
- Replay: operators may replay non-critical events (new deliveries, numbered). Risk, approval and execution events are
  never replayable through Module 08.
"""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlparse

import httpx
from sqlalchemy import and_, func, select
from sqlalchemy.exc import IntegrityError

from aurum.auth import roles as perm
from aurum.orchestration import SCHEMA_VERSION, SOURCE_MODULE
from aurum.orchestration.catalogue import (CATALOGUE, Envelope, EventError, catalogue_view, find_sensitive, matches,
                                           normalise_module, validate_envelope)
from aurum.orchestration.common import SYSTEM, Actor, OrchError, pick
from aurum.orchestration.db_models import (OrchDeadLetter, OrchDelivery, OrchDeliveryAttempt, OrchEvent, OrchInbox,
                                           OrchSourceCursor, OrchSubscription)
from aurum.orchestration.repository import jsonable, utc
from aurum.orchestration.retry import PERMANENT, TRANSIENT, RetryPolicy, classify_exception, classify_http
from aurum.orchestration.security import signed_headers, secret_value

DELIVERY_POLICY = RetryPolicy(max_attempts=6, base_seconds=30, factor=2, max_backoff_seconds=1800, jitter=0.1)
TRANSIENT_TRIGGER_CODES = ("WORKFLOW_NOT_ACTIVE", "SCHEMA_NOT_READY")


class EventMixin:
    def __init__(self) -> None:
        self._webhook_http: httpx.Client | None = None

    # ---- views ------------------------------------------------------------------------------------------------------------------
    @staticmethod
    def event_view(e: OrchEvent) -> dict[str, Any]:
        return {"sequence": e.seq, "event_id": e.event_id, "event_type": e.event_type, "event_version": e.event_version,
                "schema_version": e.schema_version, "occurred_at": utc(e.occurred_at), "published_at": utc(e.published_at),
                "source_module": e.source_module, "destination": e.destination, "correlation_id": e.correlation_id,
                "causation_id": e.causation_id, "idempotency_key": e.idempotency_key, "environment": e.environment,
                "payload": e.payload, "metadata": e.meta or {}, "ingress": e.ingress, "status": e.status,
                "deliveries_total": e.deliveries_total}

    @staticmethod
    def sub_view(x: OrchSubscription) -> dict[str, Any]:
        return {"subscription_id": x.subscription_id, "name": x.name, "event_pattern": x.event_pattern,
                "target_kind": x.target_kind, "workflow_id": x.workflow_id, "webhook_url": x.webhook_url,
                "environment": x.environment, "filter": x.filter, "active": x.active, "max_attempts": x.max_attempts,
                "created_by": x.created_by, "created_at": utc(x.created_at)}

    @staticmethod
    def dl_view(d: OrchDeadLetter) -> dict[str, Any]:
        return {"id": d.id, "kind": d.kind, "event_id": d.event_id, "execution_id": d.execution_id, "delivery_id": d.delivery_id,
                "workflow_id": d.workflow_id, "error_category": d.error_category, "reason": d.reason,
                "delivery_count": d.delivery_count, "last_attempt_at": utc(d.last_attempt_at), "failed_at": utc(d.failed_at),
                "correlation_id": d.correlation_id, "recovery_status": d.recovery_status, "resolved_by": d.resolved_by,
                "resolved_at": utc(d.resolved_at), "resolution_note": d.resolution_note}

    def catalogue(self) -> dict[str, Any]:
        return catalogue_view()

    # ---- writing events -------------------------------------------------------------------------------------------------------
    def _insert_event(self, s: Any, env: Envelope, ingress: str) -> OrchEvent | None:
        if s.execute(select(OrchEvent.seq).where((OrchEvent.event_id == env.event_id) |
                                                 (OrchEvent.idempotency_key == env.idempotency_key))).first():
            return None
        ev = OrchEvent(event_id=env.event_id, event_type=env.event_type, event_version=env.event_version,
                       schema_version=env.schema_version, occurred_at=env.occurred_at, published_at=env.published_at or self.now(),
                       source_module=env.source_module, destination=env.destination, correlation_id=env.correlation_id,
                       causation_id=env.causation_id, idempotency_key=env.idempotency_key, environment=env.environment,
                       payload=jsonable(env.payload), meta=jsonable(env.metadata) or None, ingress=ingress,
                       status="routed", deliveries_total=0)
        s.add(ev)
        s.flush()
        self._route(s, ev)
        self.count(f"events_{ingress}")
        return ev

    def _route(self, s: Any, ev: OrchEvent, replay: int = 0, only: str | None = None) -> int:
        n = 0
        subs = s.execute(select(OrchSubscription).where(OrchSubscription.active.is_(True),
                                                        OrchSubscription.environment == ev.environment)).scalars().all()
        for sub in subs:
            if only and sub.subscription_id != only:
                continue
            if not matches(sub.event_pattern, ev.event_type):
                continue
            if sub.filter and any((ev.payload or {}).get(k) != v for k, v in sub.filter.items()):
                continue
            s.add(OrchDelivery(event_id=ev.event_id, subscription_id=sub.subscription_id, replay=replay, status="pending",
                               attempts=0, next_attempt_at=self.now(), created_at=self.now()))
            n += 1
        ev.deliveries_total += n
        if n:
            ev.status = "routed"
        elif ev.deliveries_total == 0:
            ev.status = "no_subscribers"
        s.flush()
        return n

    def publish_internal(self, s: Any, event_type: str, payload: dict[str, Any], *, correlation_id: str,
                         idempotency_key: str, causation_id: str | None = None) -> str | None:
        spec = CATALOGUE[event_type]
        assert SOURCE_MODULE in spec.owners, f"Module 08 does not own {event_type}"
        env = Envelope(event_id=str(uuid.uuid4()), event_type=event_type, event_version=spec.versions[-1],
                       occurred_at=self.now(), source_module=SOURCE_MODULE, correlation_id=correlation_id[:64],
                       idempotency_key=idempotency_key[:160], environment=self.env, payload=jsonable(payload),
                       published_at=self.now(), causation_id=causation_id, schema_version=SCHEMA_VERSION)
        ev = self._insert_event(s, env, "internal")
        return ev.event_id if ev else None

    def publish(self, actor: Actor, raw: dict[str, Any], *, via_n8n: bool = False) -> tuple[dict[str, Any], bool]:
        """Publishes one event from another module (API) or from n8n (events webhook). Returns (view, created)."""
        self.ensure_ready()
        channel = "n8n" if via_n8n else "api"
        now = self.now()
        try:
            env = validate_envelope(raw, environment=self.env, now=now, via_n8n=via_n8n)
        except EventError as exc:
            self.count("events_rejected")
            message_id = str(pick(raw, "event_id") or "")[:64] if isinstance(raw, dict) else ""
            with self.repo.write() as s:
                if message_id and not s.execute(select(OrchInbox.id).where(OrchInbox.channel == channel,
                                                                           OrchInbox.message_id == message_id)).first():
                    s.add(OrchInbox(channel=channel, message_id=message_id, event_type=str(pick(raw, "event_type") or "")[:64],
                                    received_at=now, status="rejected", caller=actor.name[:80], error_code=exc.code))
                if exc.code in ("CROSS_ENVIRONMENT", "EVENT_OWNER_MISMATCH", "EVENT_NOT_ALLOWED_FROM_N8N", "SENSITIVE_FIELD"):
                    self._security_event_in(s, actor, f"security.event_{exc.code.lower()}"[:48], exc.code)
            status = 409 if exc.code in ("CROSS_ENVIRONMENT", "EVENT_OWNER_MISMATCH", "EVENT_NOT_ALLOWED_FROM_N8N") else 422
            raise OrchError(status, exc.code, exc.message, exc.details) from None
        with self.repo.write() as s:
            inbox = s.execute(select(OrchInbox).where(OrchInbox.channel == channel, OrchInbox.message_id == env.event_id[:64])
                              ).scalar_one_or_none()
            existing = s.execute(select(OrchEvent).where((OrchEvent.event_id == env.event_id) |
                                                         (OrchEvent.idempotency_key == env.idempotency_key))).scalar_one_or_none()
            if existing is not None:
                if inbox is not None:
                    inbox.duplicates += 1
                self.count("events_duplicate")
                return self.event_view(existing), False
            if inbox is None:
                s.add(OrchInbox(channel=channel, message_id=env.event_id[:64], event_type=env.event_type, received_at=now,
                                status="processed", caller=actor.name[:80]))
            ev = self._insert_event(s, env, channel)
            return self.event_view(ev), True

    # ---- subscriptions -------------------------------------------------------------------------------------------------------
    def list_subscriptions(self) -> dict[str, Any]:
        self.ensure_ready()
        with self.repo.read() as s:
            rows = s.execute(select(OrchSubscription).order_by(OrchSubscription.created_at)).scalars().all()
            return {"items": [self.sub_view(x) for x in rows]}

    def create_subscription(self, actor: Actor, body: dict[str, Any]) -> dict[str, Any]:
        self.ensure_ready()
        self.perm_check(actor, perm.ORCH_MANAGE, human=True)
        pattern = str(pick(body, "event_pattern") or "").strip()
        if not pattern or (pattern != "*" and not pattern.endswith(".*") and pattern not in CATALOGUE):
            raise OrchError(422, "INVALID_SUBSCRIPTION", "event_pattern must be a catalogued type, 'family.*' or '*'.")
        kind = str(pick(body, "target_kind") or "workflow")
        wid, url = pick(body, "workflow_id"), pick(body, "webhook_url")
        if kind == "workflow":
            with self.repo.read() as s:
                w = self._wf(s, str(wid or ""))
                if "event" not in (w.trigger_types or []):
                    raise OrchError(422, "INVALID_SUBSCRIPTION", "That workflow does not accept event triggers.")
                if w.category == "execution_handoff" and not pattern.startswith("trade.approved_for_execution"):
                    raise OrchError(422, "INVALID_SUBSCRIPTION", "Execution-handoff workflows may only subscribe to "
                                    "trade.approved_for_execution (Module 07's authorisation).")
            url = None
        elif kind == "webhook":
            self._check_webhook_url(str(url or ""))
            wid = None
        else:
            raise OrchError(422, "INVALID_SUBSCRIPTION", "target_kind must be workflow or webhook.")
        flt = pick(body, "filter")
        if flt is not None and not isinstance(flt, dict):
            raise OrchError(422, "INVALID_SUBSCRIPTION", "filter must be an object of payload field values.")
        max_attempts = int(pick(body, "max_attempts") or self.config().events.delivery_max_attempts)
        now = self.now()
        with self.repo.write() as s:
            sub = OrchSubscription(subscription_id=str(uuid.uuid4()), name=str(pick(body, "name") or pattern)[:120],
                                   event_pattern=pattern, target_kind=kind, workflow_id=wid, webhook_url=url,
                                   environment=self.env, filter=flt or None, active=True,
                                   max_attempts=max(1, min(20, max_attempts)), created_by=actor.name, created_at=now)
            s.add(sub)
            s.flush()
            self.repo.audit(s, actor=actor.name, actor_kind=actor.kind, action="subscription.created",
                            entity_type="subscription", entity_id=sub.subscription_id, at=now,
                            details={"pattern": pattern, "target": wid or urlparse(url or "").netloc})
            return self.sub_view(sub)

    def set_subscription_active(self, actor: Actor, subscription_id: str, active: bool) -> dict[str, Any]:
        self.ensure_ready()
        self.perm_check(actor, perm.ORCH_MANAGE, human=True)
        now = self.now()
        with self.repo.write() as s:
            sub = s.get(OrchSubscription, subscription_id)
            if sub is None:
                raise OrchError(404, "SUBSCRIPTION_NOT_FOUND", "No such subscription.")
            sub.active = bool(active)
            self.repo.audit(s, actor=actor.name, actor_kind=actor.kind, action="subscription." + ("enabled" if active else "disabled"),
                            entity_type="subscription", entity_id=subscription_id, at=now)
            return self.sub_view(sub)

    def _check_webhook_url(self, url: str) -> None:
        p = urlparse(url)
        if p.scheme not in ("http", "https") or not p.hostname:
            raise OrchError(422, "INVALID_SUBSCRIPTION", "webhook_url must be an http(s) URL.")
        if p.hostname not in self.config().events.allowed_webhook_hosts:
            raise OrchError(422, "WEBHOOK_HOST_NOT_ALLOWED", f"{p.hostname} is not in events.allowed_webhook_hosts.")
        if p.username or p.password:
            raise OrchError(422, "SENSITIVE_FIELD", "Do not put credentials in webhook URLs.")

    # ---- delivery ----------------------------------------------------------------------------------------------------------------
    def deliver_due(self, limit: int = 100) -> dict[str, int]:
        self.ensure_ready()
        now = self.now()
        lease = timedelta(seconds=self.config().dispatch.lease_seconds)
        work: list[tuple[int, str, int, OrchSubscription, dict[str, Any]]] = []
        with self.repo.write() as s:
            rows = s.execute(select(OrchDelivery, OrchSubscription, OrchEvent)
                             .join(OrchSubscription, OrchSubscription.subscription_id == OrchDelivery.subscription_id)
                             .join(OrchEvent, OrchEvent.event_id == OrchDelivery.event_id)
                             .where(OrchDelivery.status == "pending", OrchDelivery.next_attempt_at <= now)
                             .order_by(OrchDelivery.next_attempt_at, OrchDelivery.id).limit(limit)).all()
            for d, sub, ev in rows:
                d.attempts += 1
                d.next_attempt_at = now + lease          # lease: a crash re-delivers after it, never sooner
                s.expunge(sub)
                work.append((d.id, ev.event_id, d.replay, sub, self.event_view(ev)))
        out = {"delivered": 0, "retrying": 0, "dead": 0}
        for did, event_id, replay, sub, ev in work:
            t0 = time.perf_counter()
            kind, category, err, http_status, exec_id, retry_after = self._deliver_one(sub, ev, replay)
            latency = (time.perf_counter() - t0) * 1000
            out[self._record_delivery(did, kind, category, err, http_status, exec_id, latency, sub.max_attempts, retry_after)] += 1
        return out

    def _deliver_one(self, sub: OrchSubscription, ev: dict[str, Any], replay: int) -> tuple[str, str | None, str | None,
                                                                                             int | None, str | None, float | None]:
        if sub.target_kind == "workflow":
            payload = {k: v for k, v in ev.items() if k not in ("status", "deliveries_total", "sequence", "ingress")}
            body = {"workflow_id": sub.workflow_id, "trigger_type": "event", "input": {"event": json.loads(json.dumps(payload, default=str))},
                    "idempotency_key": f"evt:{ev['event_id']}:{sub.subscription_id}:{replay}",
                    "correlation_id": ev["correlation_id"], "causation_id": ev["event_id"], "requesting_module": "module_08",
                    "symbol": (ev.get("payload") or {}).get("symbol"),
                    "trade_idea_id": (ev.get("payload") or {}).get("idea_id"),
                    "approval_id": (ev.get("payload") or {}).get("approval_id")}
            try:
                view, _ = self.trigger(SYSTEM, body, internal=True)
                return "delivered", None, None, None, view["execution_id"], None
            except OrchError as exc:
                kind = TRANSIENT if exc.code in TRANSIENT_TRIGGER_CODES else PERMANENT
                return kind, exc.code[:32], exc.message, None, None, None
        url = sub.webhook_url or ""
        try:
            self._check_webhook_url(url)
        except OrchError as exc:
            return PERMANENT, exc.code[:32], exc.message, None, None, None
        from aurum.api.app import camelize
        body = json.dumps(camelize({k: v for k, v in ev.items() if k not in ("status", "deliveries_total", "ingress")}),
                          default=str, separators=(",", ":")).encode()
        headers = {"Content-Type": "application/json", "X-Correlation-ID": ev["correlation_id"],
                   "Idempotency-Key": ev["event_id"], "X-Aurum-Event-Type": ev["event_type"],
                   **signed_headers(secret_value(self.settings, "n8n_webhook_shared_secret"), body, self.now())}
        token = secret_value(self.settings, "n8n_webhook_auth_token")
        if token:
            headers["X-AURUM-Webhook-Token"] = token
        if self._webhook_http is None:
            self._webhook_http = httpx.Client(transport=self._webhook_transport, follow_redirects=False,
                                              timeout=httpx.Timeout(self.config().timeouts.n8n_request_seconds),
                                              headers={"User-Agent": "aurum-module08"})
        try:
            r = self._webhook_http.post(url, content=body, headers=headers)
        except httpx.HTTPError as exc:
            c = classify_exception(exc)
            # At-least-once: an unknown outcome is re-sent; receivers deduplicate by eventId (documented contract).
            return TRANSIENT, c.category, c.message, None, None, None
        c = classify_http(r.status_code, r.headers.get("retry-after"))
        if c.kind == "ok":
            return "delivered", None, None, r.status_code, None, None
        kind = PERMANENT if c.kind == PERMANENT else TRANSIENT
        return kind, c.category, c.message, r.status_code, None, c.retry_after

    def _record_delivery(self, did: int, kind: str, category: str | None, err: str | None, http_status: int | None,
                         exec_id: str | None, latency: float, max_attempts: int, retry_after: float | None) -> str:
        now = self.now()
        with self.repo.write() as s:
            d = s.get(OrchDelivery, did)
            s.add(OrchDeliveryAttempt(delivery_id=did, attempt=d.attempts, at=now, outcome=kind, http_status=http_status,
                                      latency_ms=latency, error=(err or None)))
            result = "delivered"
            if kind == "delivered":
                d.status, d.delivered_at, d.latency_ms, d.execution_id = "delivered", now, latency, exec_id
                d.last_error = d.error_category = None
                self.count("deliveries_ok")
            elif kind == TRANSIENT and d.attempts < max_attempts:
                d.last_error, d.error_category = err, category
                d.next_attempt_at = now + DELIVERY_POLICY.delay(d.attempts, retry_after)
                result = "retrying"
                self.count("deliveries_retry")
            else:
                d.status, d.last_error, d.error_category = "dead", err, category
                ev = s.execute(select(OrchEvent).where(OrchEvent.event_id == d.event_id)).scalar_one()
                s.add(OrchDeadLetter(id=str(uuid.uuid4()), kind="delivery", event_id=d.event_id, delivery_id=d.id,
                                     workflow_id=None, error_category=category or "UNKNOWN", reason=(err or "")[:2000] or
                                     "delivery failed", delivery_count=d.attempts, last_attempt_at=now, failed_at=now,
                                     correlation_id=ev.correlation_id, recovery_status="open"))
                result = "dead"
                self.count("deliveries_dead")
            self._refresh_event_status(s, d.event_id)
            return result

    def _refresh_event_status(self, s: Any, event_id: str) -> None:
        ev = s.execute(select(OrchEvent).where(OrchEvent.event_id == event_id)).scalar_one()
        counts = dict(s.execute(select(OrchDelivery.status, func.count()).where(OrchDelivery.event_id == event_id)
                                .group_by(OrchDelivery.status)).all())
        if not counts:
            ev.status = "no_subscribers"
        elif counts.get("dead"):
            ev.status = "failed" if not counts.get("delivered") else "partial"
        elif counts.get("pending"):
            ev.status = "routed"
        else:
            ev.status = "delivered"

    # ---- bridge (Module 06 and 07 outboxes, read-only) ------------------------------------------------------------------------
    def bridge_sources(self, limit: int = 200) -> dict[str, Any]:
        self.ensure_ready()
        out: dict[str, Any] = {}
        for source in self.config().events.bridge_sources:
            try:
                out[source] = self._bridge(source, limit)
            except Exception as exc:  # noqa: BLE001 - the other source still runs
                out[source] = {"error": type(exc).__name__}
        return out

    def _bridge(self, source: str, limit: int) -> dict[str, int]:
        if source == "module_07":
            from aurum.approvals.db_models import OutboxEvent as Src
            pos_col = Src.seq
        else:
            from aurum.risk.db_models import RiskEvent as Src
            pos_col = Src.id
        now = self.now()
        ingested = skipped = 0
        with self.repo.write() as s:
            cur = s.get(OrchSourceCursor, source)
            if cur is None:
                cur = OrchSourceCursor(source=source, position=0, updated_at=now)
                s.add(cur)
                s.flush()
            rows = s.execute(select(Src).where(pos_col > cur.position).order_by(pos_col).limit(limit)).scalars().all()
            for r in rows:
                pos = r.seq if source == "module_07" else r.id
                cur.position = int(pos)
                etype = r.event_type
                if etype not in CATALOGUE:          # e.g. module05.feedback: internal to Module 07
                    skipped += 1
                    continue
                payload = dict(r.payload or {})
                warnings = []
                spec = CATALOGUE[etype]
                missing = [n for n, _ in spec.required if payload.get(n) in (None, "")]
                if missing:
                    warnings.append(f"missing {missing}")
                leak = find_sensitive(payload)
                if leak:
                    payload = _redact(payload)
                    warnings.append(f"redacted {leak}")
                env = Envelope(event_id=r.event_id, event_type=etype, event_version=spec.versions[-1],
                               occurred_at=utc(r.occurred_at), source_module=normalise_module(getattr(r, "source_module", None)
                                                                                              or getattr(r, "source", source)),
                               correlation_id=(r.correlation_id or r.event_id)[:64],
                               idempotency_key=f"bridge:{source}:{r.event_id}"[:160], environment=self.env, payload=payload,
                               published_at=now, causation_id=getattr(r, "causation_id", None),
                               metadata={"bridged_from": source, "source_sequence": int(pos),
                                         **({"validation_warnings": warnings} if warnings else {})})
                if self._insert_event(s, env, "bridge") is not None:
                    ingested += 1
            cur.updated_at = now
        return {"ingested": ingested, "skipped": skipped}

    # ---- replay ---------------------------------------------------------------------------------------------------------------------
    def replay_event(self, actor: Actor, event_id: str, body: dict[str, Any]) -> dict[str, Any]:
        self.ensure_ready()
        self.perm_check(actor, perm.ORCH_OPERATE, human=True)
        reason = str(pick(body, "reason") or "").strip()
        if not reason:
            raise OrchError(422, "REASON_REQUIRED", "Give a reason for the replay.")
        only = pick(body, "subscription_id")
        now = self.now()
        with self.repo.read() as s:
            etype = s.execute(select(OrchEvent.event_type).where(OrchEvent.event_id == event_id)).scalar_one_or_none()
        if etype is None:
            raise OrchError(404, "EVENT_NOT_FOUND", "No such event.")
        spec = CATALOGUE.get(etype)
        if spec is None or not spec.replayable:
            with self.repo.write() as s:      # its own transaction, so the refusal stays on record
                self.repo.audit(s, actor=actor.name, actor_kind=actor.kind, action="event.replay_refused", entity_type="event",
                                entity_id=event_id, at=now, outcome="refused", details={"event_type": etype})
            raise OrchError(409, "REPLAY_NOT_ALLOWED", f"{etype} is a risk, approval or execution event. It can "
                            "only be re-issued by its owning module, after its own validation.")
        with self.repo.write() as s:
            ev = s.execute(select(OrchEvent).where(OrchEvent.event_id == event_id)).scalar_one()
            if ev.environment != self.env:
                raise OrchError(409, "CROSS_ENVIRONMENT", "Event belongs to another environment.")
            n = (s.execute(select(func.max(OrchDelivery.replay)).where(OrchDelivery.event_id == event_id)).scalar() or 0) + 1
            created = self._route(s, ev, replay=n, only=str(only) if only else None)
            if not created:
                raise OrchError(409, "NO_SUBSCRIBERS", "No active subscription matches this event.")
            self.repo.audit(s, actor=actor.name, actor_kind=actor.kind, action="event.replayed", entity_type="event",
                            entity_id=event_id, at=now, correlation_id=ev.correlation_id,
                            details={"replay": n, "deliveries": created, "reason": reason[:500], "subscription": only})
            self.count("event_replays")
            return {"event_id": event_id, "replay": n, "deliveries": created}

    # ---- reads -----------------------------------------------------------------------------------------------------------------------
    def list_events(self, *, event_type: str | None = None, source: str | None = None, status: str | None = None,
                    correlation_id: str | None = None, since: datetime | None = None, until: datetime | None = None,
                    after: int | None = None, page: int = 1, page_size: int = 50) -> dict[str, Any]:
        self.ensure_ready()
        conds = []
        if event_type:
            conds.append(OrchEvent.event_type.like(event_type[:-1] + "%") if event_type.endswith("*") else OrchEvent.event_type == event_type)
        for col, val in ((OrchEvent.source_module, source), (OrchEvent.status, status),
                         (OrchEvent.correlation_id, correlation_id)):
            if val:
                conds.append(col == val)
        if since:
            conds.append(OrchEvent.published_at >= since)
        if until:
            conds.append(OrchEvent.published_at <= until)
        if after is not None:
            conds.append(OrchEvent.seq > after)
        with self.repo.read() as s:
            where = and_(*conds) if conds else True
            total = s.execute(select(func.count()).select_from(OrchEvent).where(where)).scalar_one()
            order = OrchEvent.seq.asc() if after is not None else OrchEvent.seq.desc()
            rows = s.execute(select(OrchEvent).where(where).order_by(order).offset((page - 1) * page_size)
                             .limit(page_size)).scalars().all()
            ids = [e.event_id for e in rows]
            retries = dict(s.execute(select(OrchDelivery.event_id, func.sum(OrchDelivery.attempts)).where(
                OrchDelivery.event_id.in_(ids)).group_by(OrchDelivery.event_id)).all()) if ids else {}
            return {"items": [{**self.event_view(e), "delivery_attempts": int(retries.get(e.event_id) or 0)} for e in rows],
                    "total": int(total), "page": page, "page_size": page_size}

    def get_event(self, event_id: str) -> dict[str, Any]:
        self.ensure_ready()
        with self.repo.read() as s:
            ev = s.execute(select(OrchEvent).where(OrchEvent.event_id == event_id)).scalar_one_or_none()
            if ev is None:
                raise OrchError(404, "EVENT_NOT_FOUND", "No such event.")
            spec = CATALOGUE.get(ev.event_type)
            return {**self.event_view(ev), "replayable": bool(spec and spec.replayable), "deliveries": self._deliveries(s, event_id)}

    def event_deliveries(self, event_id: str) -> dict[str, Any]:
        self.ensure_ready()
        with self.repo.read() as s:
            return {"event_id": event_id, "items": self._deliveries(s, event_id)}

    def _deliveries(self, s: Any, event_id: str) -> list[dict[str, Any]]:
        rows = s.execute(select(OrchDelivery, OrchSubscription).join(
            OrchSubscription, OrchSubscription.subscription_id == OrchDelivery.subscription_id).where(
            OrchDelivery.event_id == event_id).order_by(OrchDelivery.id)).all()
        out = []
        for d, sub in rows:
            attempts = s.execute(select(OrchDeliveryAttempt).where(OrchDeliveryAttempt.delivery_id == d.id)
                                 .order_by(OrchDeliveryAttempt.attempt)).scalars().all()
            out.append({"delivery_id": d.id, "subscription_id": d.subscription_id, "subscription": sub.name,
                        "target": sub.workflow_id or urlparse(sub.webhook_url or "").netloc, "replay": d.replay,
                        "status": d.status, "attempts": d.attempts, "next_attempt_at": utc(d.next_attempt_at),
                        "last_error": d.last_error, "error_category": d.error_category, "execution_id": d.execution_id,
                        "delivered_at": utc(d.delivered_at), "latency_ms": d.latency_ms,
                        "history": [{"attempt": a.attempt, "at": utc(a.at), "outcome": a.outcome, "http_status": a.http_status,
                                     "latency_ms": a.latency_ms, "error": a.error} for a in attempts]})
        return out


def _redact(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: ("[redacted]" if find_sensitive({k: 1}) else _redact(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact(v) for v in obj]
    return obj


__all__ = ["EventMixin", "IntegrityError"]
