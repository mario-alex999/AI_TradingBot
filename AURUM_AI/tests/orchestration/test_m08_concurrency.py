"""Races: the same trigger, dispatcher and callback from many threads and two service instances (two "workers")."""
from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor

from m08_harness import N8N

from aurum.orchestration.common import OrchError
from aurum.orchestration.service import TEST_WORKFLOW_ID, OrchestrationService


def second_worker(h):
    svc = OrchestrationService(h.engine, h.settings, workflow_engine=h.n8n, clock=h.clock, approvals=lambda a: None)
    svc.ensure_ready()
    return svc


def test_same_idempotency_key_from_many_threads_creates_one_execution(ha):
    svc2 = second_worker(ha)
    body = {"workflowId": TEST_WORKFLOW_ID, "input": {"message": "race"}, "idempotencyKey": "race-1"}
    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(lambda i: (ha.svc if i % 2 else svc2).trigger(N8N, dict(body)), range(16)))
    assert len({r[0]["execution_id"] for r in results}) == 1
    assert sum(1 for r in results if r[1]) == 1


def test_parallel_dispatchers_send_each_execution_once(ha):
    svc2 = second_worker(ha)
    ids = [ha.trigger(f"pd-{i}")[0]["execution_id"] for i in range(5)]
    with ThreadPoolExecutor(6) as pool:
        list(pool.map(lambda i: (ha.svc if i % 2 else svc2).dispatch_due(), range(12)))
    sent = [c["request"]["executionId"] for c in ha.n8n.calls]
    assert sorted(sent) == sorted(ids)                                  # each exactly once
    assert all(ha.status(e) == "WAITING_CALLBACK" for e in ids)


def test_concurrent_conflicting_callbacks_resolve_to_one_final_state(ha):
    x, _ = ha.trigger("cc")
    eid = x["execution_id"]
    ha.svc.dispatch_due()

    def send(i):
        st = "completed" if i % 2 else "failed"
        try:
            return ha.callback(eid, st, attempt=1, cb_id=f"cb-{i}", error={"category": "WORKFLOW_ERROR", "message": "m"})[1]
        except OrchError as exc:
            return {"error": exc.code}
    with ThreadPoolExecutor(8) as pool:
        out = list(pool.map(send, range(16)))
    applied = [o for o in out if o.get("outcome") == "applied"]
    assert len(applied) == 1
    final = ha.status(eid)
    assert final in ("SUCCEEDED", "FAILED")
    assert all(o.get("outcome") in ("applied", "stale") for o in out)
    assert ha.svc.get_execution(eid)["status"] == final


def test_only_one_lock_holder(h):
    def grab(i):
        try:
            return h.svc.acquire_lock(N8N, "symbol", "EURUSD", 60, owner=f"w{i}")["owner"]
        except OrchError:
            return None
    with ThreadPoolExecutor(8) as pool:
        owners = [o for o in pool.map(grab, range(16)) if o]
    assert len(set(owners)) == 1


def test_duplicate_events_from_many_threads_are_stored_once(h):
    eid = str(uuid.uuid4())
    ev = {"eventId": eid, "eventType": "orchestration.test.ping", "sourceModule": "module_08", "environment": "test",
          "payload": {"message": "x"}}
    with ThreadPoolExecutor(8) as pool:
        created = [c for _, c in pool.map(lambda _: h.svc.publish(N8N, dict(ev)), range(16))]
    assert created.count(True) == 1
    assert h.svc.list_events(event_type="orchestration.test.ping")["total"] == 1
