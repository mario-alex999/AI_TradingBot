"""Service-level tests: registry and versions, permissions, safety guards, events (bridge, webhook egress, replay),
dead letters, locks, schedules, configuration, audit chain, metrics, purge."""
from __future__ import annotations

import json
import uuid
from datetime import timedelta

import httpx
import pytest
from m08_harness import ALICE, N8N, NOW, OLLY, SECRET, TOM, H
from sqlalchemy import select, text

from aurum.orchestration.common import OrchError
from aurum.orchestration.db_models import OrchAuditLog
from aurum.orchestration.security import verify
from aurum.orchestration.service import TEST_WORKFLOW_ID


def codes(fn, *a, **k):
    with pytest.raises(OrchError) as ei:
        fn(*a, **k)
    return ei.value.code


# ---- seeding --------------------------------------------------------------------------------------------------------------------
def test_seed_registers_test_workflow_and_placeholders_only(h):
    ws = {w["workflow_id"]: w for w in h.svc.list_workflows()["items"]}
    assert set(ws) == {TEST_WORKFLOW_ID, "wf08-01-market-monitoring", "wf08-02-market-analysis", "wf08-03-risk-approval",
                       "wf08-04-execution-handoff"}
    assert all(ws[w]["placeholder"] for w in ws if w.startswith("wf08-"))
    assert all(w["state"] == "REGISTERED" for w in ws.values())       # nothing is active until a person activates it
    h.svc.ensure_ready()                                               # idempotent
    assert len(h.svc.list_workflows()["items"]) == 5
    ok, n, err = h.svc.repo.verify_audit()
    assert ok and n >= 1, err


def test_placeholders_cannot_be_activated(h):
    assert codes(h.svc.set_workflow_state, ALICE, "wf08-04-execution-handoff", "ACTIVE", "x") in (
        "PLACEHOLDER_NOT_IMPLEMENTED", "NO_ACTIVE_VERSION")


# ---- registry and versions --------------------------------------------------------------------------------------------------
def test_version_lifecycle_activation_and_rollback(ha):
    v2 = ha.svc.add_version(ALICE, TEST_WORKFLOW_ID, {"version": "1.1.0", "inputSchema": {"type": "object"},
                                                      "changeNote": "looser input"})
    assert v2["status"] == "DRAFT"
    assert codes(ha.svc.activate_version, ALICE, TEST_WORKFLOW_ID, v2["version_id"], "x") in ("VERSION_NOT_ACTIVATABLE",
                                                                                                 "INVALID_TRANSITION")
    ha.svc.validate_version(ALICE, TEST_WORKFLOW_ID, v2["version_id"])
    assert codes(ha.svc.activate_version, ALICE, TEST_WORKFLOW_ID, v2["version_id"], "") == "REASON_REQUIRED"
    ha.svc.activate_version(ALICE, TEST_WORKFLOW_ID, v2["version_id"], "ship 1.1")
    vs = {v["version"]: v["status"] for v in ha.svc.list_versions(TEST_WORKFLOW_ID)["items"]}
    assert vs == {"1.0.0": "DEPRECATED", "1.1.0": "ACTIVE"}
    x, _ = ha.trigger("k-v", {"anything": 1})
    assert x["workflow_version"] == "1.1.0"
    old = next(v for v in ha.svc.list_versions(TEST_WORKFLOW_ID)["items"] if v["version"] == "1.0.0")
    ha.svc.activate_version(ALICE, TEST_WORKFLOW_ID, old["version_id"], "rollback")
    assert ha.svc.get_workflow(TEST_WORKFLOW_ID)["active_version"] == "1.0.0"
    assert codes(ha.trigger, "k-v2", {"anything": 1}) == "INPUT_INVALID"     # back on the strict schema
    assert codes(ha.svc.add_version, ALICE, TEST_WORKFLOW_ID, {"version": "1.1.0"}) in ("VERSION_EXISTS", "DUPLICATE_VERSION")
    assert codes(ha.svc.add_version, ALICE, TEST_WORKFLOW_ID, {"version": "one"}) == "INVALID_VERSION"
    assert codes(ha.svc.add_version, ALICE, TEST_WORKFLOW_ID, {"version": "2.0.0", "inputSchema": {"oneOf": []}}) in (
        "INVALID_SCHEMA", "INVALID_VERSION")


def test_registration_rules(h):
    assert codes(h.svc.register_workflow, TOM, {"workflowId": "x", "name": "x", "category": "other"}) in (
        "FORBIDDEN", "PERMISSION_DENIED")
    assert codes(h.svc.register_workflow, ALICE, {"workflowId": "Bad Id!", "name": "x", "category": "other"}) == "INVALID_WORKFLOW"
    assert codes(h.svc.register_workflow, ALICE, {"workflowId": "x1", "name": "x", "category": "trading_brain"}) == "INVALID_WORKFLOW"
    h.register("my-flow")
    assert codes(h.svc.register_workflow, ALICE, {"workflowId": "my-flow", "name": "x", "category": "other"}) in (
        "WORKFLOW_EXISTS", "DUPLICATE_WORKFLOW")
    w = h.svc.get_workflow("my-flow")
    assert codes(h.svc.update_workflow, ALICE, "my-flow", {"webhookPath": "elsewhere", "rowVersion": w["row_version"]}) in (
        "WORKFLOW_ACTIVE", "INVALID_STATE")
    assert codes(h.svc.update_workflow, ALICE, "my-flow", {"description": "d", "rowVersion": 999}) == "STALE_VERSION"


def test_permissions_and_human_only_actions(ha):
    assert codes(ha.trigger, "p1", who=OLLY) in ("FORBIDDEN", "PERMISSION_DENIED")
    # A service cannot press "manual" or "admin" buttons, or change the registry.
    assert codes(ha.trigger, "p2", trigger_type="manual") in ("FORBIDDEN", "PERMISSION_DENIED", "HUMAN_REQUIRED")
    assert codes(ha.svc.set_workflow_state, N8N, TEST_WORKFLOW_ID, "PAUSED", "x") in ("FORBIDDEN", "PERMISSION_DENIED",
                                                                                      "HUMAN_REQUIRED")
    # Event and schedule triggers come only from Module 08 itself.
    assert codes(ha.trigger, "p3", trigger_type="event") == "TRIGGER_TYPE_NOT_ALLOWED"
    assert codes(ha.svc.trigger, N8N, {"workflowId": TEST_WORKFLOW_ID, "input": {}}) == "IDEMPOTENCY_KEY_REQUIRED"
    x, created = ha.trigger(None, who=TOM, trigger_type="manual")
    assert created and x["trigger_type"] == "manual"
    with ha.svc.repo.read() as s:
        assert s.execute(select(OrchAuditLog).where(OrchAuditLog.action == "execution.manual_trigger")).first()


def test_inputs_never_carry_credentials_and_are_size_limited(ha):
    assert codes(ha.trigger, "s1", {"message": "x", "apiKey": "k"}) == "SENSITIVE_FIELD"
    assert codes(ha.trigger, "s2", {"message": "x" * 300_000}) == "INPUT_TOO_LARGE"


# ---- execution-handoff guard (Module 07 owns approval) -----------------------------------------------------------------------
def test_execution_handoff_requires_module07_approval(tmp_path, m08_template_root):
    h = H(tmp_path, m08_template_root, approvals={"ap-ok": "APPROVED", "ap-pend": "PENDING_REVIEW"})
    try:
        h.register("handoff-test", category="execution_handoff", triggers=["api", "event"])
        assert codes(h.trigger, "h1", {}, wid="handoff-test") == "APPROVAL_REQUIRED"
        assert codes(h.trigger, "h2", {}, wid="handoff-test", approval_id="ap-pend") == "APPROVAL_NOT_APPROVED"
        assert codes(h.trigger, "h3", {}, wid="handoff-test", approval_id="ap-unknown") == "APPROVAL_NOT_APPROVED"
        x, _ = h.trigger("h4", {}, wid="handoff-test", approval_id="ap-ok")
        assert x["status"] == "QUEUED" and x["approval_id"] == "ap-ok"
        # Only Module 07's authorisation event may feed it.
        assert codes(h.svc.create_subscription, ALICE, {"eventPattern": "ai.analysis.completed", "workflowId": "handoff-test"}) == \
            "INVALID_SUBSCRIPTION"
        h.svc.create_subscription(ALICE, {"eventPattern": "trade.approved_for_execution", "workflowId": "handoff-test"})
        # Approval withdrawn before a manual retry: the retry is refused.
        h.svc.dispatch_due()
        h.callback(x["execution_id"], "failed", attempt=1, workflow_id="handoff-test",
                   error={"category": "WORKFLOW_ERROR", "message": "x"})
        h.approval_status["ap-ok"] = "CANCELLED"
        h.n8n.executions[h.svc.get_execution(x["execution_id"])["n8n_execution_id"]]["status"] = "error"
        assert codes(h.svc.retry_execution, TOM, x["execution_id"], {"reason": "again"}) == "APPROVAL_NOT_APPROVED"
        with h.svc.repo.read() as s:
            refusals = s.execute(select(OrchAuditLog.details).where(OrchAuditLog.action == "trigger.refused")).scalars().all()
        assert {"APPROVAL_REQUIRED", "APPROVAL_NOT_APPROVED"} <= {d["code"] for d in refusals}
    finally:
        h.engine.dispose()


# ---- dispatch outcomes ------------------------------------------------------------------------------------------------------------
def test_unknown_outcome_is_not_blindly_retried(tmp_path, m08_template_root):
    from aurum.orchestration.engines import DispatchResult
    h = H(tmp_path, m08_template_root)
    try:
        h.register("non-idem")                                          # idempotent_dispatch False
        h.n8n.script = [DispatchResult("unknown", None, error_category="OUTCOME_UNKNOWN", error="read timeout")]
        x, _ = h.trigger("u1", {}, wid="non-idem")
        assert h.svc.dispatch_due()["unknown"] == 1
        e = h.svc.get_execution(x["execution_id"])
        assert e["status"] == "DISPATCHED" and any("outcome unknown" in w for w in e["warnings"])
        h.clock.advance(minutes=30)
        assert h.svc.dispatch_due()["claimed"] == 0 and len(h.n8n.calls) == 1
        assert h.svc.sweep_timeouts()["timed_out"] == 1
        e = h.svc.get_execution(x["execution_id"])
        assert e["status"] == "TIMED_OUT" and e["error_category"] == "OUTCOME_UNKNOWN"
        assert codes(h.svc.retry_execution, TOM, x["execution_id"], {"reason": "r"}) == "UNKNOWN_OUTCOME_CONFIRMATION_REQUIRED"
        r = h.svc.retry_execution(TOM, x["execution_id"], {"reason": "checked n8n: never ran", "confirmUnknownOutcome": True})
        assert r["attempt"] == 2 and len(h.n8n.calls) == 2
    finally:
        h.engine.dispose()


def test_timeout_reconciles_with_n8n_before_giving_up(ha):
    x, _ = ha.trigger("t1")
    eid = x["execution_id"]
    ha.svc.dispatch_due()
    n8n_id = ha.svc.get_execution(eid)["n8n_execution_id"]
    ha.n8n.executions[n8n_id]["status"] = "running"
    ha.clock.advance(minutes=3)
    assert ha.svc.sweep_timeouts()["extended"] == 1                    # n8n says it is still running
    ha.n8n.executions[n8n_id]["status"] = "success"
    ha.clock.advance(minutes=3)
    assert ha.svc.sweep_timeouts()["reconciled"] == 1
    assert ha.status(eid) == "SUCCEEDED"
    # A late callback after reconciliation changes nothing.
    assert ha.callback(eid, "failed", attempt=1)[1]["outcome"] == "stale"


def test_permanent_dispatch_failure_dead_letters_immediately(ha):
    from aurum.orchestration.engines import DispatchResult
    ha.n8n.script = [DispatchResult("permanent", 404, error_category="WORKFLOW_NOT_FOUND", error="HTTP 404")]
    x, _ = ha.trigger("p404")
    assert ha.svc.dispatch_due()["dead_lettered"] == 1
    assert ha.status(x["execution_id"]) == "DEAD_LETTERED" and len(ha.n8n.calls) == 1


def test_backpressure_limits_parallel_dispatch(ha):
    for i in range(8):
        ha.trigger(f"bp{i}")
    assert ha.svc.dispatch_due()["claimed"] == 5                       # dispatch.max_parallel
    assert ha.svc.dispatch_due().get("backpressure") == 1


def test_cancel(ha):
    x, _ = ha.trigger("c1")
    ha.svc.dispatch_due()
    assert codes(ha.svc.cancel_execution, TOM, x["execution_id"], {}) == "REASON_REQUIRED"
    r = ha.svc.cancel_execution(TOM, x["execution_id"], {"reason": "not needed"})
    assert r["status"] == "CANCELLED" and ha.n8n.stopped
    assert ha.callback(x["execution_id"], "completed", attempt=1)[1]["outcome"] == "stale"


# ---- callbacks ------------------------------------------------------------------------------------------------------------------
def test_callback_validation(ha):
    x, _ = ha.trigger("cb1")
    ha.svc.dispatch_due()
    eid = x["execution_id"]
    assert ha.callback(str(uuid.uuid4()), "completed")[0] == 404
    assert ha.callback(eid, "completed", workflow_id="other-flow")[1]["code"] == "WORKFLOW_MISMATCH"
    assert ha.callback(eid, "completed", workflowVersion="9.9.9")[1]["code"] == "VERSION_MISMATCH"
    assert codes(ha.callback, eid, "exploded") == "INVALID_CALLBACK"
    assert codes(ha.callback, eid, "completed", output={"token": "t"}) == "SENSITIVE_FIELD"
    # Unsigned callbacks are accepted only inside the timestamp window (the service token is the primary credential).
    raw = json.dumps({"callbackId": "u1", "executionId": eid, "workflowId": TEST_WORKFLOW_ID, "status": "started",
                      "environment": "test", "sentAt": (NOW - timedelta(hours=1)).isoformat()}).encode()
    assert codes(ha.svc.handle_callback, N8N, raw, {}) == "TIMESTAMP_EXPIRED"
    raw = raw.replace(b"u1", b"u2").replace((NOW - timedelta(hours=1)).isoformat().encode(), NOW.isoformat().encode())
    assert ha.svc.handle_callback(N8N, raw, {})[1]["status"] == "RUNNING"


def test_signature_required_mode_and_secret_rotation(tmp_path, m08_template_root):
    h = H(tmp_path, m08_template_root, n8n_webhook_shared_secret="new-secret-abcdefghijklmnop",
          n8n_webhook_shared_secret_previous=SECRET)
    try:
        h.activate_test_workflow()
        cfg = h.svc.get_config()
        cfg["config"]["callbacks"]["signature_required"] = True
        h.svc.put_config(ALICE, {"config": cfg["config"], "reason": "require signatures", "version": cfg["version"]})
        x, _ = h.trigger("r1")
        h.svc.dispatch_due()
        assert codes(h.callback, x["execution_id"], "started", sign_it=False) == "SIGNATURE_MISSING"
        assert h.callback(x["execution_id"], "started", secret=SECRET)[1]["status"] == "RUNNING"      # previous still valid
        assert h.callback(x["execution_id"], "completed", secret="new-secret-abcdefghijklmnop")[1]["status"] == "SUCCEEDED"
    finally:
        h.engine.dispose()


# ---- events -----------------------------------------------------------------------------------------------------------------------
def _risk_row(h, etype, payload, source="module_06_risk"):
    from aurum.risk.db_models import RiskEvent
    with h.svc.repo.write() as s:
        s.add(RiskEvent(event_id=str(uuid.uuid4()), event_type=etype, schema_version=1, occurred_at=NOW,
                        correlation_id="corr-r", idempotency_key=str(uuid.uuid4()), source=source, payload=payload,
                        status="pending", attempts=0))


def _approval_row(h, etype, payload):
    from aurum.approvals.db_models import OutboxEvent
    with h.svc.repo.write() as s:
        s.add(OutboxEvent(event_id=str(uuid.uuid4()), event_type=etype, event_version="1.0", occurred_at=NOW,
                          source_module="module_07", correlation_id="corr-a", idempotency_key=str(uuid.uuid4()),
                          payload=payload, status="pending", attempts=0))


def test_bridge_reads_module06_and_07_outboxes_once_and_read_only(h):
    _risk_row(h, "risk.assessment.blocked", {"assessment_id": "ra1", "decision": "BLOCK", "symbol": "XAUUSD"})
    _approval_row(h, "approval.requested", {"approval_id": "ap1", "symbol": "XAUUSD", "password": "oops"})
    _approval_row(h, "module05.feedback", {"x": 1})
    out = h.svc.bridge_sources()
    assert out["module_06"]["ingested"] == 1 and out["module_07"] == {"ingested": 1, "skipped": 1}
    assert h.svc.bridge_sources() == {"module_06": {"ingested": 0, "skipped": 0}, "module_07": {"ingested": 0, "skipped": 0}}
    evs = {e["event_type"]: e for e in h.svc.list_events()["items"]}
    assert evs["risk.assessment.blocked"]["source_module"] == "module_06"
    assert evs["approval.requested"]["payload"]["password"] == "[redacted]"
    assert "validation_warnings" in evs["approval.requested"]["metadata"]
    # Source rows untouched: Module 06/07 keep their own delivery state.
    with h.svc.repo.read() as s:
        assert {r[0] for r in s.execute(text("SELECT status FROM outbox_events"))} == {"pending"}
        assert {r[0] for r in s.execute(text("SELECT status FROM risk_events"))} == {"pending"}
    # Critical events cannot be replayed from Module 08.
    rid = evs["risk.assessment.blocked"]["event_id"]
    assert codes(h.svc.replay_event, TOM, rid, {"reason": "again"}) == "REPLAY_NOT_ALLOWED"
    with h.svc.repo.read() as s:
        assert s.execute(select(OrchAuditLog).where(OrchAuditLog.action == "event.replay_refused")).first()


def test_n8n_may_not_publish_decisions(ha):
    assert codes(ha.svc.publish, N8N, {"eventType": "trade.approved_for_execution", "sourceModule": "module_07",
                                       "environment": "test", "payload": {"approval_id": "a"}}, via_n8n=True) == \
        "EVENT_NOT_ALLOWED_FROM_N8N"
    ev, _ = ha.svc.publish(N8N, {"eventType": "ai.analysis.requested", "sourceModule": "n8n", "environment": "test",
                                 "payload": {"symbol": "XAUUSD"}}, via_n8n=True)
    assert ev["ingress"] == "n8n"


def test_webhook_egress_is_signed_retried_and_dead_lettered(ha):
    seen = []
    replies = iter([httpx.Response(503), httpx.Response(200), httpx.Response(400)])

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return next(replies)
    ha.svc._webhook_transport = httpx.MockTransport(handler)
    assert codes(ha.svc.create_subscription, ALICE, {"eventPattern": "orchestration.test.ping", "targetKind": "webhook",
                                                     "webhookUrl": "http://evil.example/x"}) == "WEBHOOK_HOST_NOT_ALLOWED"
    assert codes(ha.svc.create_subscription, ALICE, {"eventPattern": "orchestration.test.ping", "targetKind": "webhook",
                                                     "webhookUrl": "http://u:p@localhost/x"}) in ("SENSITIVE_FIELD",)
    ha.svc.create_subscription(ALICE, {"eventPattern": "orchestration.test.ping", "targetKind": "webhook",
                                       "webhookUrl": "http://localhost:5678/webhook/aurum-events", "maxAttempts": 2})
    ev, _ = ha.svc.publish(N8N, {"eventType": "orchestration.test.ping", "sourceModule": "module_08", "environment": "test",
                                 "payload": {"message": "m"}})
    assert ha.svc.deliver_due()["retrying"] == 1
    ha.clock.advance(minutes=10)
    assert ha.svc.deliver_due()["delivered"] == 1
    r = seen[-1]
    verify(r.content, r.headers["X-Aurum-Timestamp"], r.headers["X-Aurum-Signature"], [SECRET], ha.clock(), 300)
    assert json.loads(r.content)["eventId"] == ev["event_id"] and r.headers["Idempotency-Key"] == ev["event_id"]
    assert ha.svc.get_event(ev["event_id"])["status"] == "delivered"
    ev2, _ = ha.svc.publish(N8N, {"eventType": "orchestration.test.ping", "sourceModule": "module_08", "environment": "test",
                                  "payload": {"message": "m2"}})
    assert ha.svc.deliver_due()["dead"] == 1                           # 400 is permanent
    dl = ha.svc.list_dead_letters(kind="delivery")["items"][0]
    assert dl["event_id"] == ev2["event_id"]
    out = ha.svc.retry_dead_letter(TOM, dl["id"], {"reason": "receiver fixed"})
    assert out["status"] == "pending"
    # Replay of a non-critical event creates a new numbered delivery.
    rep = ha.svc.replay_event(TOM, ev["event_id"], {"reason": "receiver lost it"})
    assert rep["replay"] == 1 and rep["deliveries"] == 1


# ---- locks --------------------------------------------------------------------------------------------------------------------
def test_locks_fencing_and_stale_takeover(h):
    a = h.svc.acquire_lock(N8N, "symbol", "XAUUSD", 30, owner="worker-a")
    assert a["fencing_token"] == 1 and a["held"]
    assert codes(h.svc.acquire_lock, N8N, "symbol", "XAUUSD", 30, owner="worker-b") == "LOCK_HELD"
    assert h.svc.acquire_lock(N8N, "symbol", "XAUUSD", 30, owner="worker-a")["fencing_token"] == 1   # re-entrant
    h.clock.advance(seconds=31)
    b = h.svc.acquire_lock(N8N, "symbol", "XAUUSD", 30, owner="worker-b")
    assert b["fencing_token"] == 2
    assert codes(h.svc.renew_lock, N8N, "symbol:XAUUSD", a["token"]) == "LOCK_LOST"
    assert codes(h.svc.release_lock, N8N, "symbol:XAUUSD", a["token"]) == "LOCK_LOST"
    h.svc.release_lock(N8N, "symbol:XAUUSD", b["token"])
    assert not h.svc.list_locks()["items"]
    assert codes(h.svc.acquire_lock, N8N, "galaxy", "x") == "INVALID_LOCK"


# ---- schedules ----------------------------------------------------------------------------------------------------------------
def test_schedules_fire_once_per_slot_and_collapse_missed_runs(ha):
    sch = ha.svc.create_schedule(ALICE, {"name": "every5", "workflowId": TEST_WORKFLOW_ID, "kind": "interval",
                                         "expression": "300", "input": {"message": "tick"}})
    assert codes(ha.svc.create_schedule, ALICE, {"name": "bad", "workflowId": TEST_WORKFLOW_ID, "kind": "cron",
                                                 "expression": "99 * * * *"}) == "INVALID_SCHEDULE"
    assert codes(ha.svc.create_schedule, TOM, {"name": "t", "workflowId": TEST_WORKFLOW_ID, "kind": "interval",
                                               "expression": "300"}) in ("FORBIDDEN", "PERMISSION_DENIED")
    assert ha.svc.run_due_schedules()["fired"] == 0
    ha.clock.advance(hours=1)                                          # 12 missed slots: catch_up latest -> one run
    assert ha.svc.run_due_schedules()["fired"] == 1
    assert ha.svc.run_due_schedules()["fired"] == 0
    ex = ha.svc.list_executions(workflow_id=TEST_WORKFLOW_ID)["items"]
    assert len(ex) == 1 and ex[0]["trigger_type"] == "schedule"
    ha.svc.set_schedule_enabled(ALICE, sch["schedule_id"], False)
    ha.clock.advance(hours=1)
    assert ha.svc.run_due_schedules()["fired"] == 0


# ---- configuration --------------------------------------------------------------------------------------------------------------
def test_config_versioned_audited_and_secret_free(h):
    cfg = h.svc.get_config()
    assert cfg["version"] == 1
    sec = {x["name"]: x for x in cfg["secrets"]}
    assert sec["n8n_webhook_shared_secret"]["configured"] is True
    assert SECRET not in json.dumps(cfg, default=str)                  # value never shown
    new = json.loads(json.dumps(cfg["config"]))
    new["dispatch"]["max_parallel"] = 3
    assert codes(h.svc.put_config, ALICE, {"config": new}) == "REASON_REQUIRED"
    assert codes(h.svc.put_config, ALICE, {"config": new, "reason": "r", "version": 7}) == "STALE_VERSION"
    assert codes(h.svc.put_config, ALICE, {"config": cfg["config"], "reason": "r"}) == "NO_CHANGE"
    bad = json.loads(json.dumps(new))
    bad["dispatch"]["api_key"] = "x"
    assert codes(h.svc.put_config, ALICE, {"config": bad, "reason": "r"}) == "INVALID_CONFIG"
    assert codes(h.svc.put_config, TOM, {"config": new, "reason": "r"}) in ("FORBIDDEN", "PERMISSION_DENIED")
    out = h.svc.put_config(ALICE, {"config": new, "reason": "slower", "version": 1})
    assert out["version"] == 2 and h.svc.config().dispatch.max_parallel == 3
    assert [x["version"] for x in out["history"]] == [2, 1]
    # (Append-only enforcement of orch_config_versions is a PostgreSQL trigger - see test_migration.py.)


def test_audit_chain_detects_tampering(ha):
    ok, n, _ = ha.svc.repo.verify_audit()
    assert ok and n > 3
    with ha.engine.begin() as c:
        c.execute(text("UPDATE orch_audit_logs SET actor='mallory' WHERE seq = (SELECT MIN(seq) FROM orch_audit_logs)"))
    ok, _, err = ha.svc.repo.verify_audit()
    assert not ok and err


# ---- overview, metrics, purge ------------------------------------------------------------------------------------------------
def test_overview_metrics_and_purge(ha):
    from aurum.orchestration.engines import DispatchResult
    ha.n8n.script = [DispatchResult("permanent", 404, error_category="WORKFLOW_NOT_FOUND", error="404")]
    ha.trigger("o1")
    ha.svc.dispatch_due()
    o = ha.svc.overview()
    assert o["environment"] == "test" and o["dead_letters_open"] == 1 and o["workflows"]["active"] == 1
    assert any("dead letter" in a["message"].lower() for a in o["alerts"])
    m = ha.svc.metrics_text()
    assert "# TYPE aurum_orch_executions gauge" in m and 'aurum_orch_executions{status="DEAD_LETTERED"} 1' in m
    assert SECRET not in m
    ha.svc.publish(N8N, {"eventType": "orchestration.test.ping", "sourceModule": "module_08", "environment": "test",
                         "payload": {"message": "old"}})
    ha.clock.advance(days=40)
    dry = ha.svc.purge(ALICE)
    assert dry["dry_run"] and dry["events"] >= 1
    done = ha.svc.purge(ALICE, dry_run=False)
    assert not done["dry_run"] and ha.svc.purge(ALICE)["events"] == 0
    assert ha.svc.list_executions()["total"] == 1                      # executions are kept
