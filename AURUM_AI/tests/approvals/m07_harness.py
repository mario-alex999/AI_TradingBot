"""Test harness for Module 07.

- A real SQLite database with the full schema (copied from a per-session template).
- Real AuthService users and sessions (low PBKDF2 iterations for speed).
- ``FakeRisk``: a CONTROLLED stand-in for Module 06 (decision, warnings, locks, quote age, availability are set by each
  test). One integration test file uses the real Module 06 instead (test_integration_module06.py).
- ``FakeIdeas``: a controlled Module 05 source. ``FakeExecution``: a Module 09 test adapter that only calls the public
  Module 07 contract (execution queue and results), exactly as Module 09 will.
"""
from __future__ import annotations

import shutil
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from aurum.approvals.gateways import GatewayUnavailable
from aurum.approvals.policy import Policy
from aurum.approvals.service import ApprovalService
from aurum.auth.service import AuthService, Principal
from aurum.database.connection import init_database, make_engine

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)
PASSWORD = "correct-horse-battery-9"
_TEMPLATE: dict[str, Path] = {}


class Clock:
    def __init__(self, t: datetime = NOW):
        self.t = t

    def __call__(self) -> datetime:
        return self.t

    def advance(self, **kw: Any) -> None:
        self.t += timedelta(**kw)


def template(root: Path) -> Path:
    if "p" not in _TEMPLATE:
        p = root / "m07-template.db"
        eng = make_engine(f"sqlite:///{p.as_posix()}")
        init_database(eng)
        eng.dispose()
        _TEMPLATE["p"] = p
    return _TEMPLATE["p"]


class FakeRisk:
    """MOCKED Module 06. Every field a test may want to change is a plain attribute."""

    def __init__(self, clock: Clock):
        self.clock = clock
        self.decision = "PASS"
        self.warnings: list[dict[str, Any]] = []
        self.violations: list[dict[str, Any]] = []
        self.volume = "0.10"
        self.locks: list[dict[str, Any]] = []
        self.quote_age = timedelta(seconds=5)
        self.unavailable = False
        self.account = "1001"
        self.assessments: dict[str, dict[str, Any]] = {}
        self.cancelled: set[str] = set()
        self.invalid: set[str] = set()
        self.calls: list[str] = []

    def _up(self) -> None:
        if self.unavailable:
            raise GatewayUnavailable("Module 06 is unavailable (test)")

    def assess(self, proposal: dict[str, Any], actor: str, correlation_id: str, idempotency_key: str) -> dict[str, Any]:
        self._up()
        self.calls.append("assess")
        aid = str(uuid.uuid4())
        r = {"assessment_id": aid, "decision": self.decision, "final_volume": self.volume if self.decision in ("PASS", "REVIEW") else "0",
             "risk_pct": "0.50", "monetary_risk": "20.00", "potential_reward": "50.00", "risk_reward": "2.50",
             "warnings": list(self.warnings), "rule_violations": list(self.violations), "errors": [],
             "config_version": 1, "timestamp": self.clock(), "expires_at": self.clock() + timedelta(minutes=5),
             "market_status": {"volatility_regime": "NORMAL"}, "proposal": proposal}
        self.assessments[aid] = r
        return r

    def validate(self, assessment_id: str) -> dict[str, Any]:
        self._up()
        bad = assessment_id in self.cancelled or assessment_id in self.invalid
        return {"valid": not bad, "reasons": ["Assessment is no longer valid (test)."] if bad else [],
                "decision": self.assessments.get(assessment_id, {}).get("decision")}

    def cancel(self, assessment_id: str, actor: str, reason: str, correlation_id: str | None) -> None:
        self._up()
        self.cancelled.add(assessment_id)

    def normalised_entry(self, candidate_uid: str) -> Decimal | None:
        return None

    def quote_time(self, symbol: str, now: datetime) -> datetime | None:
        self._up()
        return None if self.quote_age is None else self.clock() - self.quote_age

    def symbols(self) -> dict[str, str]:
        return {"EURUSD": "forex", "XAUUSD": "metal", "GBPUSD": "forex"}

    def account_login(self) -> str | None:
        self._up()
        return self.account

    def active_locks(self, symbol: str | None = None, strategy: str | None = None) -> list[dict[str, Any]]:
        self._up()
        return list(self.locks)


class StaticPositions:
    name = "account_mirror"

    def __init__(self, clock: Clock):
        self.clock = clock
        self.rows: list[dict[str, Any]] = []
        self.fail = False

    def positions(self, now: datetime) -> tuple[list[dict[str, Any]], datetime | None]:
        if self.fail:
            raise ConnectionError("mirror down (test)")
        return [dict(r) for r in self.rows], self.clock()


def idea(n: int | str = 1, clock: Clock | None = None, **over: Any) -> dict[str, Any]:
    now = (clock or Clock())()
    raw = {"idea_id": f"idea-{n}", "symbol": "EURUSD", "market": "forex", "direction": "BUY", "entry": "1.10010",
           "stop_loss": "1.09810", "take_profits": ["1.10510"], "timeframe": "H1", "strategy": "trend_pullback",
           "created_at": now.isoformat(), "expires_at": (now + timedelta(hours=2)).isoformat(),
           "analysis_version": f"test:{n}:v1", "correlation_id": f"corr-{n}", "schema_version": "1.0",
           "ai_summary": "Test idea.", "ai_confidence": 62,
           "evidence": [{"source_module": "module_03", "finding_id": "finding-1", "description": "Bullish break of structure on H1"}]}
    raw.update(over)
    return raw


class H:
    """One test's world."""

    def __init__(self, tmp_path: Path, template_root: Path, policy: Policy | None = None):
        p = tmp_path / "m07.db"
        shutil.copyfile(template(template_root), p)
        self.engine = make_engine(f"sqlite:///{p.as_posix()}")
        self.clock = Clock()
        self.risk = FakeRisk(self.clock)
        self.positions = StaticPositions(self.clock)
        self.settings = _Settings()
        self.auth = AuthService(self.engine, iterations=1000, clock=self.clock)
        self.svc = ApprovalService(self.engine, self.settings, self.risk, None, None, self.positions, clock=self.clock,
                                   seed_policy=policy or Policy())
        self.users: dict[str, Principal] = {}
        for name, role in (("alice", "admin"), ("tom", "trader"), ("rita", "reviewer"), ("olly", "observer")):
            self.user(name, role)

    def user(self, name: str, role: str, account_scope: list[str] | None = None) -> Principal:
        self.auth.create_user(name, PASSWORD, role, name.title(), f"{name}@example.test", account_scope)
        self.users[name] = self.auth.login(name, PASSWORD).principal
        return self.users[name]

    def service_principal(self, scopes: list[str]) -> Principal:
        token, _ = self.auth.create_service_token(f"svc-{uuid.uuid4().hex[:6]}", "test", scopes)
        p = self.auth.resolve_service_token(token)
        assert p is not None
        return p

    def ingest(self, n: int | str = 1, **over: Any) -> dict[str, Any]:
        from aurum.approvals.service import SYSTEM
        return self.svc.ingest(idea(n, self.clock, **over), source="event", who=SYSTEM)

    def view(self, approval_id: str) -> dict[str, Any]:
        return self.svc.detail(approval_id, self.users["alice"])["request"]

    def approve_body(self, approval_id: str, **over: Any) -> dict[str, Any]:
        r = self.view(approval_id)
        body = {"client_request_id": str(uuid.uuid4()), "request_version": r["version"], "evidence_hash": r["evidence_hash"],
                "acknowledged_warnings": [w["rule_id"] for w in r["risk_warnings"]],
                "confirmation": {"symbol": r["symbol"], "direction": r["direction"], "entry": str(r["entry"]),
                                 "stop_loss": str(r["stop_loss"]), "take_profit": str(r["take_profit"]),
                                 "position_size": None if r["position_size"] is None else str(r["position_size"]),
                                 "max_risk_amount": None if r["risk_amount"] is None else str(r["risk_amount"])}}
        body.update(over)
        return body

    def approve(self, approval_id: str, who: str = "tom", **over: Any) -> dict[str, Any]:
        return self.svc.approve(approval_id, self.users[who], self.approve_body(approval_id, **over))


class _Settings:
    enabled_instruments: list[str] = []
    approvals_config = None
    approvals_events_webhook_url = None
    approvals_worker_interval_seconds = 15
    notify_email_enabled = False
    smtp_host = smtp_from = smtp_username = None
    smtp_password = None
    smtp_port = 587
    smtp_use_tls = True
    telegram_bot_token = None
    telegram_chat_id = None


class FakeExecution:
    """Module 09 test adapter. Uses only the public contract: read the queue, report results with a service token."""

    def __init__(self, h: H):
        self.h = h
        self.p = h.service_principal(["execution:read", "execution:report", "approvals:view"])

    def take(self) -> list[dict[str, Any]]:
        return self.h.svc.execution_queue(self.p)

    def report(self, execution_request_id: str, status: str, **extra: Any) -> dict[str, Any]:
        return self.h.svc.report_execution(self.p, {"event_id": str(uuid.uuid4()), "execution_request_id": execution_request_id,
                                                    "status": status, **extra})
