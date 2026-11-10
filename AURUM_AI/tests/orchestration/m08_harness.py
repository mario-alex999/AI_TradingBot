"""Test harness for Module 08.

- A real SQLite database with the full schema (copied from a per-session template), so constraints are real.
- ``ControlledTestEngine``: a CONTROLLED stand-in for n8n with scripted dispatch outcomes. One test file instead runs the
  real ``N8nEngine`` against an HTTP mock transport (test_n8n_adapter.py), and an opt-in test uses a live n8n.
- Users: alice (admin), tom (trader), olly (observer); a service principal ``n8n-svc``.
"""
from __future__ import annotations

import json
import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from aurum.auth import roles
from aurum.config import Settings
from aurum.database.connection import init_database, make_engine
from aurum.orchestration.common import Actor
from aurum.orchestration.engines import ControlledTestEngine
from aurum.orchestration.security import sign
from aurum.orchestration.service import TEST_WORKFLOW_ID, OrchestrationService

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)
SECRET = "test-shared-secret-0123456789"
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
        p = root / "m08-template.db"
        eng = make_engine(f"sqlite:///{p.as_posix()}")
        init_database(eng)
        eng.dispose()
        _TEMPLATE["p"] = p
    return _TEMPLATE["p"]


def actor(name: str, role: str) -> Actor:
    r = roles.Role(role)
    return Actor(id=f"id-{name}", name=name, kind="service" if r is roles.Role.SERVICE else "user", role=role,
                 perms=roles.permissions_for(r))


ALICE, TOM, OLLY, N8N = actor("alice", "admin"), actor("tom", "trader"), actor("olly", "observer"), actor("n8n-svc", "service")


def settings(**kw: Any) -> Settings:
    base = dict(orchestration_environment="test", n8n_webhook_shared_secret=SECRET, n8n_base_url="http://n8n.test:5678",
                orchestration_callback_base_url="http://aurum.test/api/v1/orchestration",
                aurum_internal_base_url="http://aurum.test/api/v1", n8n_api_key=None, n8n_webhook_auth_token=None,
                orchestration_service_token=None, n8n_webhook_shared_secret_previous=None)
    base.update(kw)
    return Settings(_env_file=None, **base)


class H:
    def __init__(self, tmp: Path, root: Path, *, engine: Any = None, approvals: dict[str, str] | None = None,
                 **settings_kw: Any):
        db = tmp / f"m08-{uuid.uuid4().hex[:8]}.db"
        shutil.copy(template(root), db)
        self.engine = make_engine(f"sqlite:///{db.as_posix()}")
        self.clock = Clock()
        self.n8n = engine or ControlledTestEngine("test")
        self.approval_status: dict[str, str] = approvals or {}
        self.settings = settings(**settings_kw)
        self.svc = OrchestrationService(self.engine, self.settings, workflow_engine=self.n8n, clock=self.clock,
                                        approvals=lambda aid: self.approval_status.get(aid))
        self.svc.ensure_ready()

    # ---- setup helpers ---------------------------------------------------------------------------------------------------
    def activate_test_workflow(self, **update: Any) -> dict[str, Any]:
        wid = TEST_WORKFLOW_ID
        self.svc.update_workflow(ALICE, wid, {"n8n_workflow_id": "wfTEST1", **update})
        if isinstance(self.n8n, ControlledTestEngine):
            self.n8n.workflows["wfTEST1"] = {"id": "wfTEST1", "active": True, "webhook_paths": [wid]}
        v = self.svc.validate_version(ALICE, wid)["version"]
        self.svc.activate_version(ALICE, wid, v["version_id"], "test setup")
        return self.svc.set_workflow_state(ALICE, wid, "ACTIVE", "test setup")

    def register(self, wid: str, category: str = "other", triggers: list[str] | None = None, **kw: Any) -> dict[str, Any]:
        self.svc.register_workflow(ALICE, {"workflowId": wid, "name": wid, "category": category,
                                           "triggerTypes": triggers or ["api", "manual", "event", "schedule"],
                                           "n8nWorkflowId": f"n8n-{wid}", "webhookPath": wid, **kw,
                                           "initialVersion": {"version": "1.0.0",
                                                              "inputSchema": kw.pop("input_schema", {"type": "object"})}})
        if isinstance(self.n8n, ControlledTestEngine):
            self.n8n.workflows[f"n8n-{wid}"] = {"id": f"n8n-{wid}", "active": True, "webhook_paths": [wid]}
        v = self.svc.validate_version(ALICE, wid)["version"]
        self.svc.activate_version(ALICE, wid, v["version_id"], "setup")
        return self.svc.set_workflow_state(ALICE, wid, "ACTIVE", "setup")

    def trigger(self, key: str | None = None, inp: dict[str, Any] | None = None, wid: str = TEST_WORKFLOW_ID,
                who: Actor = N8N, **kw: Any) -> tuple[dict[str, Any], bool]:
        body = {"workflowId": wid, "triggerType": kw.pop("trigger_type", "api"), "input": inp or {"message": "hi"},
                "idempotencyKey": key or uuid.uuid4().hex, **kw}
        return self.svc.trigger(who, body)

    def callback(self, execution_id: str, status: str, *, attempt: int | None = None, cb_id: str | None = None,
                 sign_it: bool = True, env: str = "test", workflow_id: str = TEST_WORKFLOW_ID, secret: str = SECRET,
                 ts_offset: int = 0, **extra: Any) -> tuple[int, dict[str, Any]]:
        body = {"callbackId": cb_id or uuid.uuid4().hex, "executionId": execution_id, "workflowId": workflow_id,
                "status": status, "environment": env, "sentAt": self.clock().isoformat(), **extra}
        if attempt is not None:
            body["attempt"] = attempt
        raw = json.dumps(body).encode()
        headers: dict[str, str] = {}
        if sign_it:
            ts = int(self.clock().timestamp()) + ts_offset
            headers = {"X-Aurum-Timestamp": str(ts), "X-Aurum-Signature": sign(secret, ts, raw)}
        return self.svc.handle_callback(N8N, raw, headers)

    def status(self, execution_id: str) -> str:
        return self.svc.get_execution(execution_id)["status"]
