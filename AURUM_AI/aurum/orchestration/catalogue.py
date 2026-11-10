"""The central event catalogue and the standard event envelope.

The catalogue is code (reviewed and versioned with the repository), served read-only at ``GET /orchestration/catalogue``.
Each event type states:
- ``owners``: the only source modules allowed to publish it. Risk decisions belong to Module 06, approvals and
  execution authorisation to Module 07, execution results to Module 09. n8n and Module 08 can never publish them, so a
  workflow cannot fabricate an approval or an execution request.
- ``n8n``: whether n8n may publish it through the events webhook (requests and orchestration signals only).
- ``replayable``: whether an operator may replay it through Module 08. Execution and approval events are never
  replayable here: re-sending an old execution request needs independent validation by the owning module.
- ``required`` / ``optional`` payload fields with simple types.

Envelope fields (snake_case in Python and the database, camelCase over HTTP): event_id, event_type, event_version,
occurred_at, published_at, source_module, destination, correlation_id, causation_id, idempotency_key, schema_version,
environment, payload, metadata.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from aurum.orchestration import SCHEMA_VERSION

ENVIRONMENTS = ("development", "test", "demo", "production")
SUPPORTED_SCHEMA_MAJOR = "1"
_TYPES = {"str": str, "int": int, "num": (int, float), "bool": bool, "obj": dict, "list": list, "any": object}
# Keys that must never travel in an event payload, at any depth (credentials and broker secrets).
SENSITIVE_KEYS = re.compile(r"(pass(word|wd)?|secret|api[_-]?key|token|credential|private[_-]?key|authorization|cookie|"
                            r"mt5[_-]?login[_-]?password)$", re.IGNORECASE)
MODULES = ("module_01", "module_02", "module_03", "module_04", "module_05", "module_06", "module_07", "module_08",
           "module_09", "module_10", "n8n")


class EventError(ValueError):
    def __init__(self, code: str, message: str, details: Any = None):
        super().__init__(message)
        self.code, self.message, self.details = code, message, details


@dataclass(frozen=True)
class EventType:
    name: str
    family: str
    owners: tuple[str, ...]
    required: tuple[tuple[str, str], ...] = ()
    optional: tuple[tuple[str, str], ...] = ()
    n8n: bool = False
    replayable: bool = True
    critical: bool = False                    # trading-relevant: owned by its module, never replayed by Module 08
    versions: tuple[str, ...] = ("1.0",)
    description: str = ""

    def public(self) -> dict[str, Any]:
        return {"event_type": self.name, "family": self.family, "owners": list(self.owners), "n8n_may_publish": self.n8n,
                "replayable": self.replayable, "critical": self.critical, "versions": list(self.versions),
                "required": [{"name": n, "type": t} for n, t in self.required],
                "optional": [{"name": n, "type": t} for n, t in self.optional], "description": self.description}


def _f(*names: str) -> tuple[tuple[str, str], ...]:
    out = []
    for n in names:
        name, _, typ = n.partition(":")
        out.append((name, typ or "str"))
    return tuple(out)


M01, M02, M03, M04, M05, M06, M07, M08, M09 = (f"module_0{i}" for i in range(1, 10))
_E: list[EventType] = [
    # market data (Module 01)
    EventType("market.data.requested", "market_data", (M08, "n8n"), _f("symbol", "timeframes:list"), _f("reason"), n8n=True,
              description="Ask Module 01 to refresh candles for a symbol."),
    EventType("market.data.updated", "market_data", (M01,), _f("symbol", "timeframe", "as_of"), _f("candles:int", "run_id"),
              description="New closed candles were stored."),
    EventType("market.data.failed", "market_data", (M01,), _f("symbol", "error"), _f("timeframe", "run_id")),
    EventType("market.data.stale", "market_data", (M01, M08), _f("symbol", "timeframe", "age_seconds:num"), _f("threshold_seconds:num")),
    # COT (Module 02)
    EventType("cot.data.updated", "cot", (M02,), _f("report_type", "report_date"), _f("rows:int")),
    EventType("cot.analysis.completed", "cot", (M02,), _f("report_date"), _f("markets:list")),
    EventType("cot.analysis.failed", "cot", (M02,), _f("error"), _f("report_date")),
    # technical analysis (Module 03)
    EventType("technical.analysis.requested", "technical_analysis", (M08, "n8n"), _f("symbol", "timeframe"), _f("reason"), n8n=True),
    EventType("technical.analysis.completed", "technical_analysis", (M03,), _f("symbol", "timeframe", "run_id"), _f("summary:obj")),
    EventType("technical.analysis.failed", "technical_analysis", (M03,), _f("symbol", "error"), _f("timeframe")),
    # market intelligence (Module 04)
    EventType("market.intelligence.updated", "market_intelligence", (M04,), _f("scope"), _f("symbols:list")),
    EventType("market.intelligence.alert", "market_intelligence", (M04,), _f("severity", "title"), _f("symbols:list", "detail")),
    EventType("market.news.detected", "market_intelligence", (M04,), _f("headline", "source"), _f("symbols:list", "impact", "url")),
    # AI analysis and trade ideas (Module 05; Module 07 also records ideas it receives)
    EventType("ai.analysis.requested", "ai_analysis", (M08, "n8n"), _f("symbol"), _f("timeframes:list", "reason"), n8n=True),
    EventType("ai.analysis.completed", "ai_analysis", (M05,), _f("symbol", "run_id"), _f("candidates:int")),
    EventType("ai.analysis.failed", "ai_analysis", (M05,), _f("symbol", "error"), _f("run_id")),
    EventType("trade_idea.created", "ai_analysis", (M05, M07), _f("idea_id", "symbol"), _f("direction", "version:int", "timeframe")),
    EventType("trade_idea.updated", "ai_analysis", (M05, M07), _f("idea_id"), _f("version:int", "symbol")),
    EventType("trade_idea.cancelled", "ai_analysis", (M05, M07), _f("idea_id"), _f("reason")),
    # risk (Module 06)
    EventType("risk.assessment.requested", "risk", (M08, "n8n", M07), _f("symbol", "direction"), _f("idea_id", "entry", "stop_loss"),
              n8n=True, description="A request only. The decision is always Module 06's own risk.assessment.* event."),
    EventType("risk.assessment.completed", "risk", (M06,), _f("assessment_id", "decision"), _f("symbol", "idea_id"), critical=True,
              replayable=False),
    EventType("risk.assessment.blocked", "risk", (M06,), _f("assessment_id"), _f("symbol", "violations:list"), critical=True,
              replayable=False),
    EventType("risk.assessment.requires_review", "risk", (M06,), _f("assessment_id"), _f("symbol", "warnings:list"), critical=True,
              replayable=False),
    EventType("risk.assessment.failed", "risk", (M06,), _f(), _f("assessment_id", "error", "symbol"), critical=True, replayable=False),
    EventType("risk.lock.activated", "risk", (M06,), _f(), _f("lock_id", "scope", "reason")),
    EventType("risk.lock.released", "risk", (M06,), _f(), _f("lock_id", "scope")),
    EventType("risk.limit.reached", "risk", (M06,), _f(), _f("limit", "value")),
    EventType("risk.configuration.updated", "risk", (M06,), _f(), _f("version:int")),
    EventType("risk.portfolio.exposure.updated", "risk", (M06,), _f(), _f("exposure:obj")),
    # approvals (Module 07)
    *[EventType(f"approval.{s}", "approval", (M07,), _f("approval_id"), _f("idea_id", "symbol", "status", "reason"), critical=True,
                replayable=False)
      for s in ("requested", "under_review", "approved", "rejected", "modification_requested", "expired", "cancelled",
                "invalidated", "risk_revalidated")],
    EventType("approval.policy.updated", "approval", (M07,), _f(), _f("version:int")),
    EventType("trade.approved_for_execution", "approval", (M07,), _f("approval_id"), _f("idea_id", "symbol", "handoff_id"),
              critical=True, replayable=False, description="Module 07's authorisation. Only Module 07 can publish it."),
    # execution (Module 09; Module 07 reports outcomes it receives)
    EventType("execution.requested", "execution", (M07, M09), _f("approval_id"), _f("handoff_id", "symbol"), critical=True, replayable=False),
    EventType("execution.submitted", "execution", (M09,), _f("approval_id"), _f("order_ticket", "symbol"), critical=True, replayable=False),
    EventType("execution.completed", "execution", (M09, M07), _f("approval_id"), _f("order_ticket", "symbol", "fill_price"), critical=True,
              replayable=False),
    EventType("execution.failed", "execution", (M09, M07), _f("approval_id"), _f("error", "symbol"), critical=True, replayable=False),
    EventType("position.updated", "execution", (M09, M07), _f("position_id"), _f("symbol", "volume", "profit")),
    EventType("position.closed", "execution", (M09, M07), _f("position_id"), _f("symbol", "profit")),
    # trade management (Module 07 decides, Module 09 completes)
    *[EventType(f"trade_management.{s}", "trade_management", (M07,) if s != "completed" else (M07, M09), _f("request_id"),
                _f("position_id", "action", "reason"), critical=True, replayable=False)
      for s in ("requested", "approved", "rejected", "expired", "completed")],
    # orchestration (Module 08 only; n8n reports progress through signed callbacks, not by publishing these)
    *[EventType(f"orchestration.workflow.{s}", "orchestration", (M08,), _f("execution_id", "workflow_id"),
                _f("status", "attempt:int", "error_category", "n8n_execution_id", "workflow_version"))
      for s in ("triggered", "started", "completed", "failed", "retrying", "timed_out", "cancelled", "dead_lettered")],
    EventType("orchestration.test.ping", "orchestration", (M08, "n8n"), _f(), _f("message"), n8n=True,
              description="Harmless test event for verifying the pipeline end to end."),
]
CATALOGUE: dict[str, EventType] = {e.name: e for e in _E}
FAMILIES = sorted({e.family for e in _E})


def normalise_module(source: str | None) -> str:
    """'module_06_risk' -> 'module_06'; anything else unchanged (lower-cased)."""
    s = (source or "").strip().lower()
    m = re.match(r"^module_?(\d{1,2})", s)
    return f"module_{int(m.group(1)):02d}" if m else s


def matches(pattern: str, event_type: str) -> bool:
    """Subscription patterns: exact type, 'family.*' prefix, or '*'."""
    if pattern == "*" or pattern == event_type:
        return True
    return pattern.endswith(".*") and event_type.startswith(pattern[:-1])


def find_sensitive(obj: Any, path: str = "payload") -> str | None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if SENSITIVE_KEYS.search(str(k)):
                return f"{path}.{k}"
            hit = find_sensitive(v, f"{path}.{k}")
            if hit:
                return hit
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            hit = find_sensitive(v, f"{path}[{i}]")
            if hit:
                return hit
    return None


def _parse_time(value: Any, name: str) -> datetime:
    if isinstance(value, datetime):
        t = value
    else:
        try:
            t = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            raise EventError("INVALID_EVENT", f"{name} must be an ISO-8601 timestamp.") from None
    if t.tzinfo is None:
        raise EventError("INVALID_EVENT", f"{name} must include a time zone (UTC).")
    return t.astimezone(timezone.utc)


@dataclass
class Envelope:
    event_id: str
    event_type: str
    event_version: str
    occurred_at: datetime
    source_module: str
    correlation_id: str
    idempotency_key: str
    environment: str
    payload: dict[str, Any]
    published_at: datetime | None = None
    destination: str | None = None
    causation_id: str | None = None
    schema_version: str = SCHEMA_VERSION
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"event_id": self.event_id, "event_type": self.event_type, "event_version": self.event_version,
                "occurred_at": self.occurred_at, "published_at": self.published_at, "source_module": self.source_module,
                "destination": self.destination, "correlation_id": self.correlation_id, "causation_id": self.causation_id,
                "idempotency_key": self.idempotency_key, "schema_version": self.schema_version,
                "environment": self.environment, "payload": self.payload, "metadata": self.metadata}


_SNAKE = {"eventId": "event_id", "eventType": "event_type", "eventVersion": "event_version", "occurredAt": "occurred_at",
          "publishedAt": "published_at", "sourceModule": "source_module", "correlationId": "correlation_id",
          "causationId": "causation_id", "idempotencyKey": "idempotency_key", "schemaVersion": "schema_version"}


def validate_envelope(raw: dict[str, Any], *, environment: str, now: datetime, publisher: str | None = None,
                      via_n8n: bool = False) -> Envelope:
    """Validates an incoming envelope against the catalogue. Accepts camelCase or snake_case keys.

    ``environment`` is this deployment's environment: an event for another environment is refused (CROSS_ENVIRONMENT).
    ``publisher`` (when given) must be an owner of the event type. ``via_n8n`` restricts to the types n8n may publish.
    Missing event_id / idempotency_key / correlation_id are generated; everything else is checked strictly.
    """
    if not isinstance(raw, dict):
        raise EventError("INVALID_EVENT", "The event must be a JSON object.")
    d = {_SNAKE.get(k, k): v for k, v in raw.items()}
    etype = str(d.get("event_type") or "")
    spec = CATALOGUE.get(etype)
    if spec is None:
        raise EventError("UNKNOWN_EVENT_TYPE", f"'{etype}' is not in the event catalogue.", {"event_type": etype})
    version = str(d.get("event_version") or spec.versions[-1])
    if version not in spec.versions:
        raise EventError("UNSUPPORTED_EVENT_VERSION", f"{etype} version {version} is not supported.",
                         {"supported": list(spec.versions)})
    schema = str(d.get("schema_version") or SCHEMA_VERSION)
    if schema.split(".")[0] != SUPPORTED_SCHEMA_MAJOR:
        raise EventError("UNSUPPORTED_SCHEMA_VERSION", f"Envelope schema {schema} is not supported.",
                         {"supported_major": SUPPORTED_SCHEMA_MAJOR})
    env = str(d.get("environment") or "").lower()
    if env not in ENVIRONMENTS:
        raise EventError("INVALID_EVENT", "environment is required: development, test, demo or production.")
    if env != environment:
        raise EventError("CROSS_ENVIRONMENT", f"This is the {environment} environment; a {env} event is refused.",
                         {"expected": environment, "received": env})
    source = normalise_module(d.get("source_module"))
    if source not in MODULES:
        raise EventError("INVALID_EVENT", "source_module must be module_01 ... module_10 or n8n.")
    if via_n8n and (source != "n8n" or not spec.n8n):
        raise EventError("EVENT_NOT_ALLOWED_FROM_N8N", f"n8n may not publish {etype}.",
                         {"allowed": sorted(e.name for e in _E if e.n8n)})
    if source not in spec.owners:
        raise EventError("EVENT_OWNER_MISMATCH", f"{etype} can only be published by {', '.join(spec.owners)}.",
                         {"source_module": source})
    if publisher is not None and normalise_module(publisher) != source:
        raise EventError("EVENT_OWNER_MISMATCH", "The caller is not allowed to publish for this source module.")
    payload = d.get("payload")
    if not isinstance(payload, dict):
        raise EventError("INVALID_EVENT", "payload must be a JSON object.")
    problems = []
    for name, typ in spec.required:
        if payload.get(name) in (None, ""):
            problems.append({"field": name, "problem": "required"})
        elif not isinstance(payload[name], _TYPES[typ]) or (typ in ("int", "num") and isinstance(payload[name], bool)):
            problems.append({"field": name, "problem": f"must be {typ}"})
    for name, typ in spec.optional:
        if payload.get(name) is not None and not isinstance(payload[name], _TYPES[typ]):
            problems.append({"field": name, "problem": f"must be {typ}"})
    if problems:
        raise EventError("INVALID_PAYLOAD", f"{etype} payload is invalid.", problems)
    leak = find_sensitive(payload) or find_sensitive(d.get("metadata") or {}, "metadata")
    if leak:
        raise EventError("SENSITIVE_FIELD", f"Events may not carry credentials ({leak}).", {"field": leak})
    occurred = _parse_time(d.get("occurred_at") or now, "occurred_at")
    event_id = str(d.get("event_id") or uuid.uuid4())
    if len(event_id) > 64:
        raise EventError("INVALID_EVENT", "event_id is too long (max 64).")
    idem = str(d.get("idempotency_key") or f"{source}:{etype}:{event_id}")
    if len(idem) > 160:
        raise EventError("INVALID_EVENT", "idempotency_key is too long (max 160).")
    metadata = d.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise EventError("INVALID_EVENT", "metadata must be a JSON object.")
    return Envelope(event_id=event_id, event_type=etype, event_version=version, occurred_at=occurred, source_module=source,
                    correlation_id=str(d.get("correlation_id") or uuid.uuid4())[:64], idempotency_key=idem, environment=env,
                    payload=payload, published_at=now, destination=(str(d["destination"])[:32] if d.get("destination") else None),
                    causation_id=(str(d["causation_id"])[:64] if d.get("causation_id") else None), schema_version=schema,
                    metadata=metadata)


def catalogue_view() -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "families": FAMILIES, "environments": list(ENVIRONMENTS),
            "event_types": [e.public() for e in _E]}
