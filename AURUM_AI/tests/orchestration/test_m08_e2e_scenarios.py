"""The ten end-to-end scenarios required for Module 08 Core, against a real SQLite schema and the CONTROLLED test adapter
(scripted n8n outcomes; this is not a live n8n - see test_n8n_live.py for that)."""
from __future__ import annotations

import httpx
import pytest
from m08_harness import ALICE, N8N, SECRET, TOM, H
from sqlalchemy import func, select

from aurum.orchestration.common import OrchError
from aurum.orchestration.db_models import OrchAuditLog, OrchDeadLetter, OrchExecutionAttempt
from aurum.orchestration.engines import DispatchResult
from aurum.orchestration.service import TEST_WORKFLOW_ID


def ping(**over):
    base = {"eventType": "orchestration.test.ping", "sourceModule": "module_08", "environment": "test",
            "payload": {"message": "hello"}}
    base.update(over)
    return base


def subscribe(h):
    return h.svc.create_subscription(ALICE, {"eventPattern": "orchestration.test.ping", "targetKind": "workflow",
                                             "workflowId": TEST_WORKFLOW_ID})


def security_events(h, code=None):
    with h.svc.repo.read() as s:
        q = select(OrchAuditLog).where(OrchAuditLog.entity_type == "security")
        if code:
            q = q.where(OrchAuditLog.entity_id == code)
        return s.execute(q).scalars().all()


# 1 ---------------------------------------------------------------------------------------------------------------------------
def test_1_event_published_dispatched_called_back_and_updated(ha):
    subscribe(ha)
    ev, created = ha.svc.publish(N8N, ping())
    assert created and ev["status"] == "routed"
    assert ha.svc.deliver_due() == {"delivered": 1, "retrying": 0, "dead": 0}
    eid = ha.svc.event_deliveries(ev["event_id"])["items"][0]["execution_id"]
    assert ha.status(eid) == "QUEUED"
    assert ha.svc.dispatch_due()["accepted"] == 1
    assert ha.status(eid) == "WAITING_CALLBACK"
    call = ha.n8n.calls[0]
    assert call["path"] == TEST_WORKFLOW_ID and call["request"]["executionId"] == eid
    assert call["request"]["input"]["event"]["event_id"] == ev["event_id"]
    assert ha.callback(eid, "started", attempt=1)[1]["status"] == "RUNNING"
    code, body = ha.callback(eid, "completed", attempt=1, output={"echo": "hello"})
    assert code == 200 and body["status"] == "SUCCEEDED"
    x = ha.svc.get_execution(eid)
    assert x["output"] == {"echo": "hello"} and x["causation_id"] == ev["event_id"]
    assert [t["to"] for t in x["timeline"]] == ["RECEIVED", "VALIDATING", "QUEUED", "DISPATCHED", "WAITING_CALLBACK",
                                                "RUNNING", "SUCCEEDED"]
    assert ha.svc.get_event(ev["event_id"])["status"] == "delivered"
    types = [e["event_type"] for e in ha.svc.list_events(event_type="orchestration.workflow.*")["items"]]
    assert {"orchestration.workflow.triggered", "orchestration.workflow.started", "orchestration.workflow.completed"} <= set(types)


# 2 ---------------------------------------------------------------------------------------------------------------------------
def test_2_duplicate_event_is_not_dispatched_twice(ha):
    subscribe(ha)
    a, c1 = ha.svc.publish(N8N, ping(eventId="evt-dup-1"))
    b, c2 = ha.svc.publish(N8N, ping(eventId="evt-dup-1"))
    c, c3 = ha.svc.publish(N8N, ping(idempotencyKey=a["idempotency_key"]))
    assert c1 and not c2 and not c3 and a["event_id"] == b["event_id"] == c["event_id"]
    ha.svc.deliver_due()
    ha.svc.deliver_due()
    ha.svc.dispatch_due()
    ha.svc.dispatch_due()
    assert len(ha.n8n.calls) == 1
    # Same for direct triggers: the same key returns the first execution.
    x1, n1 = ha.trigger("k-1")
    x2, n2 = ha.trigger("k-1")
    assert n1 and not n2 and x1["execution_id"] == x2["execution_id"]
    with pytest.raises(OrchError) as ei:
        ha.trigger("k-1", {"message": "different"})
    assert ei.value.code == "IDEMPOTENCY_CONFLICT"


# 3 ---------------------------------------------------------------------------------------------------------------------------
def test_3_n8n_unavailable_then_recovers_without_duplication(ha):
    ha.n8n.script = [httpx.ConnectError("refused"), DispatchResult("transient", 503, error_category="SERVER_ERROR",
                                                                   error="HTTP 503")]
    x, _ = ha.trigger("k-3")
    eid = x["execution_id"]
    assert ha.svc.dispatch_due()["retrying"] == 1 and ha.status(eid) == "RETRY_PENDING"
    assert ha.svc.dispatch_due()["claimed"] == 0                 # backoff: not due yet
    ha.clock.advance(minutes=1)
    assert ha.svc.dispatch_due()["retrying"] == 1
    ha.clock.advance(minutes=2)
    assert ha.svc.dispatch_due()["accepted"] == 1
    assert ha.callback(eid, "completed", attempt=3, output={"ok": True})[1]["status"] == "SUCCEEDED"
    assert [c["request"]["attempt"] for c in ha.n8n.calls] == [1, 2, 3]
    assert {c["request"]["executionId"] for c in ha.n8n.calls} == {eid}    # n8n deduplicates by executionId
    ha.clock.advance(hours=1)
    assert ha.svc.dispatch_due()["claimed"] == 0 and len(ha.n8n.calls) == 3
    with ha.svc.repo.read() as s:
        outcomes = [a.outcome for a in s.execute(select(OrchExecutionAttempt).where(
            OrchExecutionAttempt.execution_id == eid).order_by(OrchExecutionAttempt.attempt)).scalars()]
    assert outcomes == ["transient", "transient", "accepted"]


# 4 ---------------------------------------------------------------------------------------------------------------------------
def test_4_workflow_failure_callback_and_retry_policy(ha):
    x, _ = ha.trigger("k-4a")
    ha.svc.dispatch_due()
    code, body = ha.callback(x["execution_id"], "failed", attempt=1,
                             error={"category": "WORKFLOW_ERROR", "message": "node X failed"})
    assert code == 200 and body["status"] == "FAILED"            # default policy: workflow failures are not retried
    # A transient category reported by n8n follows the retry policy.
    y, _ = ha.trigger("k-4b")
    ha.svc.dispatch_due()
    assert ha.callback(y["execution_id"], "failed", attempt=1,
                       error={"category": "RATE_LIMITED", "message": "upstream 429"})[1]["status"] == "RETRY_PENDING"
    # A business failure is final and cannot be retried by an operator either.
    z, _ = ha.trigger("k-4c")
    ha.svc.dispatch_due()
    assert ha.callback(z["execution_id"], "failed", attempt=1,
                       error={"category": "RISK_BLOCKED", "message": "Module 06 blocked"})[1]["status"] == "FAILED"
    with pytest.raises(OrchError) as ei:
        ha.svc.retry_execution(TOM, z["execution_id"], {"reason": "try again"})
    assert ei.value.code == "BUSINESS_RULE_FAILURE"
    # The workflow-error one can be retried manually, with a reason.
    with pytest.raises(OrchError):
        ha.svc.retry_execution(TOM, x["execution_id"], {})
    ha.n8n.executions[ha.svc.get_execution(x["execution_id"])["n8n_execution_id"]]["status"] = "error"
    r = ha.svc.retry_execution(TOM, x["execution_id"], {"reason": "fixed node X"})
    assert r["status"] == "WAITING_CALLBACK" and r["attempt"] == 2


# 5 ---------------------------------------------------------------------------------------------------------------------------
def test_5_invalid_signature_rejected_and_security_event_recorded(ha):
    x, _ = ha.trigger("k-5")
    ha.svc.dispatch_due()
    for kw, code in (({"secret": "wrong-secret-value-xxxxxxxx"}, "SIGNATURE_INVALID"), ({"ts_offset": -3600}, "TIMESTAMP_EXPIRED")):
        with pytest.raises(OrchError) as ei:
            ha.callback(x["execution_id"], "completed", attempt=1, **kw)
        assert ei.value.status == 401 and ei.value.code == code
        assert security_events(ha, code)
    assert ha.status(x["execution_id"]) == "WAITING_CALLBACK"     # nothing applied
    # A tampered body fails even with a valid-looking signature header.
    import json
    from aurum.orchestration.security import sign
    raw = json.dumps({"callbackId": "c", "executionId": x["execution_id"], "workflowId": TEST_WORKFLOW_ID,
                      "status": "completed", "environment": "test"}).encode()
    ts = int(ha.clock().timestamp())
    with pytest.raises(OrchError):
        ha.svc.handle_callback(N8N, raw.replace(b"completed", b"failed"), {"X-Aurum-Timestamp": str(ts),
                                                                             "X-Aurum-Signature": sign(SECRET, ts, raw)})
    assert ha.callback(x["execution_id"], "completed", attempt=1)[1]["status"] == "SUCCEEDED"


# 6 ---------------------------------------------------------------------------------------------------------------------------
def test_6_maximum_retries_go_to_dead_letter(ha):
    ha.n8n.default = DispatchResult("transient", 503, error_category="SERVER_ERROR", error="HTTP 503")
    x, _ = ha.trigger("k-6")
    eid = x["execution_id"]
    for _ in range(10):
        ha.svc.dispatch_due()
        ha.clock.advance(hours=1)
    assert ha.status(eid) == "DEAD_LETTERED"
    assert len(ha.n8n.calls) == 5                                  # max_attempts from config, capped by N8N_RETRY_LIMIT
    dls = ha.svc.list_dead_letters()["items"]
    assert len(dls) == 1 and dls[0]["execution_id"] == eid and dls[0]["error_category"] == "SERVER_ERROR"
    assert dls[0]["delivery_count"] == 5
    # Recovery is a deliberate, audited action.
    ha.n8n.default = DispatchResult("accepted", 200)
    out = ha.svc.retry_dead_letter(TOM, dls[0]["id"], {"reason": "n8n back"})
    assert out["execution"]["status"] == "WAITING_CALLBACK"
    assert ha.svc.list_dead_letters(status="retried")["items"][0]["resolved_by"] == "tom"
    assert not ha.svc.list_dead_letters()["items"]


# 7 ---------------------------------------------------------------------------------------------------------------------------
def test_7_demo_event_aimed_at_production_is_rejected(tmp_path, m08_template_root):
    from aurum.orchestration.engines import ControlledTestEngine
    h = H(tmp_path, m08_template_root, engine=ControlledTestEngine("development"), orchestration_environment="development")
    try:
        with pytest.raises(OrchError) as ei:
            h.svc.publish(N8N, ping(environment="production"))
        assert ei.value.code == "CROSS_ENVIRONMENT" and security_events(h, "CROSS_ENVIRONMENT")
        h.activate_test_workflow()
        with pytest.raises(OrchError) as ei:
            h.trigger("k-7", environment="production")
        assert ei.value.code == "CROSS_ENVIRONMENT"
        x, _ = h.trigger("k-7b")
        h.svc.dispatch_due()
        with pytest.raises(OrchError) as ei:
            h.callback(x["execution_id"], "completed", attempt=1, env="production")
        assert ei.value.code == "CROSS_ENVIRONMENT"
        assert h.status(x["execution_id"]) == "WAITING_CALLBACK"
    finally:
        h.engine.dispose()


def test_7b_database_is_pinned_to_one_environment(tmp_path, m08_template_root):
    from m08_harness import settings
    from aurum.orchestration.engines import ControlledTestEngine
    from aurum.orchestration.service import OrchestrationService
    h = H(tmp_path, m08_template_root)
    try:
        other = OrchestrationService(h.engine, settings(orchestration_environment="development"),
                                     workflow_engine=ControlledTestEngine("development"), clock=h.clock)
        with pytest.raises(OrchError) as ei:
            other.ensure_ready()
        assert ei.value.code == "ENVIRONMENT_MISMATCH"
    finally:
        h.engine.dispose()


def test_7c_test_adapter_refused_in_demo_and_production(tmp_path, m08_template_root):
    from aurum.orchestration.engines import ControlledTestEngine
    for env in ("demo", "production"):
        with pytest.raises(ValueError, match="controlled test adapter"):
            H(tmp_path, m08_template_root, engine=ControlledTestEngine(env), orchestration_environment=env)


# 8 ---------------------------------------------------------------------------------------------------------------------------
def test_8_restart_during_processing_causes_no_duplicates(ha, tmp_path):
    from aurum.orchestration.service import OrchestrationService
    subscribe(ha)
    ev, _ = ha.svc.publish(N8N, ping())
    ha.svc.deliver_due()
    x, _ = ha.trigger("k-8")
    ha.svc.dispatch_due()                    # both sent once
    calls = len(ha.n8n.calls)
    # "Restart": a brand-new service object over the same database and the same n8n.
    svc2 = OrchestrationService(ha.engine, ha.settings, workflow_engine=ha.n8n, clock=ha.clock,
                                approvals=lambda a: None)
    svc2.ensure_ready()
    assert svc2.deliver_due() == {"delivered": 0, "retrying": 0, "dead": 0}
    assert svc2.dispatch_due()["claimed"] == 0
    assert len(ha.n8n.calls) == calls
    # Crash between "claimed" and "recorded": the execution sits in DISPATCHED. After the restart it is not re-sent;
    # it waits for the callback or the deadline (an unknown outcome is never assumed to mean "did not run").
    y, _ = ha.trigger("k-8b")
    import aurum.orchestration.executions as ex
    orig = ex.ExecutionMixin._record_dispatch
    ex.ExecutionMixin._record_dispatch = lambda *a, **k: (_ for _ in ()).throw(SystemExit("crash"))
    try:
        with pytest.raises(SystemExit):
            ha.svc.dispatch_due(only=y["execution_id"])
    finally:
        ex.ExecutionMixin._record_dispatch = orig
    assert svc2.get_execution(y["execution_id"])["status"] == "DISPATCHED"
    before = len(ha.n8n.calls)
    assert svc2.dispatch_due()["claimed"] == 0 and len(ha.n8n.calls) == before
    # Its callback still lands normally.
    assert ha.callback(y["execution_id"], "completed", attempt=1)[1]["status"] == "SUCCEEDED"
    # Schedules: one slot runs once, however many workers race.
    sch = ha.svc.create_schedule(ALICE, {"workflowId": TEST_WORKFLOW_ID, "kind": "interval", "expression": "300",
                                         "input": {"message": "tick"}, "name": "t"})
    ha.clock.advance(minutes=5)
    first = ha.svc.run_due_schedules()
    second = svc2.run_due_schedules()
    assert first["fired"] + second["fired"] == 1, (first, second, sch)
    assert len(ha.svc.schedule_history(sch["schedule_id"])["items"]) == 1


# 9 ---------------------------------------------------------------------------------------------------------------------------
def test_9_outdated_callback_cannot_overwrite_final_state(ha):
    x, _ = ha.trigger("k-9")
    eid = x["execution_id"]
    ha.svc.dispatch_due()
    assert ha.callback(eid, "completed", attempt=1, output={"v": 1})[1]["status"] == "SUCCEEDED"
    for st in ("started", "failed", "timed_out", "completed"):
        code, body = ha.callback(eid, st, attempt=1, output={"v": 2})
        assert code == 200 and body["outcome"] == "stale" and body["status"] == "SUCCEEDED"
    x2 = ha.svc.get_execution(eid)
    assert x2["status"] == "SUCCEEDED" and x2["output"] == {"v": 1}
    # The same callback id twice is acknowledged once.
    code, b1 = ha.callback(eid, "completed", attempt=1, cb_id="same")
    code, b2 = ha.callback(eid, "completed", attempt=1, cb_id="same")
    assert b2["duplicate"] is True
    # A callback for an older attempt cannot change a newer one.
    ha.n8n.script = [DispatchResult("transient", 503, error_category="SERVER_ERROR", error="503")]
    y, _ = ha.trigger("k-9b")
    ha.svc.dispatch_due()
    ha.clock.advance(minutes=5)
    ha.svc.dispatch_due()                                           # attempt 2 accepted
    assert ha.callback(y["execution_id"], "failed", attempt=1)[1]["outcome"] == "stale"
    assert ha.callback(y["execution_id"], "completed", attempt=7)[1]["code"] == "UNKNOWN_ATTEMPT"
    assert ha.status(y["execution_id"]) == "WAITING_CALLBACK"


# 10 --------------------------------------------------------------------------------------------------------------------------
def test_10_inactive_workflow_dispatch_is_rejected(h):
    # Never activated: REGISTERED.
    with pytest.raises(OrchError) as ei:
        h.trigger("k-10")
    assert ei.value.code == "WORKFLOW_NOT_ACTIVE"
    # Placeholders cannot run at all.
    with pytest.raises(OrchError) as ei:
        h.trigger("k-10p", wid="wf08-01-market-monitoring")
    assert ei.value.code in ("PLACEHOLDER_NOT_IMPLEMENTED", "WORKFLOW_NOT_ACTIVE")
    h.activate_test_workflow()
    x, _ = h.trigger("k-10b")                                       # queued while ACTIVE
    h.svc.set_workflow_state(ALICE, TEST_WORKFLOW_ID, "PAUSED", "maintenance")
    with pytest.raises(OrchError) as ei:
        h.trigger("k-10c")
    assert ei.value.code == "WORKFLOW_NOT_ACTIVE"
    assert h.svc.dispatch_due()["claimed"] == 0 and not h.n8n.calls  # queued work waits while paused
    with h.svc.repo.read() as s:
        refused = s.execute(select(func.count()).select_from(OrchAuditLog).where(
            OrchAuditLog.action == "trigger.refused")).scalar_one()
    assert refused >= 2
    h.svc.set_workflow_state(ALICE, TEST_WORKFLOW_ID, "ACTIVE", "maintenance done")
    assert h.svc.dispatch_due()["accepted"] == 1 and h.status(x["execution_id"]) == "WAITING_CALLBACK"
    with h.svc.repo.read() as s:
        assert s.execute(select(func.count()).select_from(OrchDeadLetter)).scalar_one() == 0
