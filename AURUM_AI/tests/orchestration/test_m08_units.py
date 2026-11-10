"""Unit tests: state machines, event catalogue and envelope validation, schemas, scheduling, retry classification."""
from __future__ import annotations

from datetime import datetime, timezone
from itertools import product

import pytest

from aurum.orchestration import catalogue as cat
from aurum.orchestration import scheduler, schemas
from aurum.orchestration.retry import RetryPolicy, classify_exception, classify_http, kind_of
from aurum.orchestration.states import (EXECUTION_FINAL, EXECUTION_TRANSITIONS, VERSION_TRANSITIONS, WORKFLOW_TRANSITIONS,
                                        ExecutionStatus, IllegalTransition, VersionStatus, WorkflowState, check_execution,
                                        check_version, check_workflow, execution_sql_pairs)

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)
E = ExecutionStatus


# ---- state machines -----------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("a,b", list(product(list(E), list(E))))
def test_every_execution_pair(a, b):
    allowed = b in EXECUTION_TRANSITIONS[a]
    if allowed:
        check_execution(a.value, b.value)
    else:
        with pytest.raises(IllegalTransition):
            check_execution(a.value, b.value)


def test_final_execution_states_never_change():
    for s in EXECUTION_FINAL:
        if s is E.DEAD_LETTERED:
            assert EXECUTION_TRANSITIONS[s] == {E.RETRY_PENDING}      # only a deliberate manual retry
        else:
            assert not EXECUTION_TRANSITIONS[s]
    # Nothing can overwrite a success, and a time-out can still learn the truth.
    assert all(E.SUCCEEDED not in nxt or a in (E.DISPATCHED, E.RUNNING, E.WAITING_CALLBACK, E.TIMED_OUT)
               for a, nxt in EXECUTION_TRANSITIONS.items())
    assert {E.SUCCEEDED, E.FAILED} <= EXECUTION_TRANSITIONS[E.TIMED_OUT]


def test_unknown_statuses_are_refused():
    for fn in (check_execution, check_workflow, check_version):
        with pytest.raises(IllegalTransition):
            fn("NOPE", "ACTIVE")


@pytest.mark.parametrize("a,b", list(product(list(WorkflowState), list(WorkflowState))))
def test_every_workflow_pair(a, b):
    if b in WORKFLOW_TRANSITIONS[a]:
        check_workflow(a.value, b.value)
    else:
        with pytest.raises(IllegalTransition):
            check_workflow(a.value, b.value)


@pytest.mark.parametrize("a,b", list(product(list(VersionStatus), list(VersionStatus))))
def test_every_version_pair(a, b):
    if b in VERSION_TRANSITIONS[a]:
        check_version(a.value, b.value)
    else:
        with pytest.raises(IllegalTransition):
            check_version(a.value, b.value)


def test_retired_is_terminal_and_rollback_allowed():
    assert not WORKFLOW_TRANSITIONS[WorkflowState.RETIRED] and not VERSION_TRANSITIONS[VersionStatus.RETIRED]
    assert VersionStatus.ACTIVE in VERSION_TRANSITIONS[VersionStatus.DEPRECATED]


def test_sql_pairs_cover_the_python_table():
    assert len(execution_sql_pairs()) == sum(len(v) for v in EXECUTION_TRANSITIONS.values())


# ---- catalogue ---------------------------------------------------------------------------------------------------------------
SPEC_TYPES = """market.data.requested market.data.updated market.data.failed market.data.stale cot.data.updated
cot.analysis.completed cot.analysis.failed technical.analysis.requested technical.analysis.completed technical.analysis.failed
market.intelligence.updated market.intelligence.alert market.news.detected ai.analysis.requested ai.analysis.completed
ai.analysis.failed trade_idea.created trade_idea.updated trade_idea.cancelled risk.assessment.requested
risk.assessment.completed risk.assessment.blocked risk.assessment.failed approval.requested approval.approved approval.rejected
approval.expired approval.invalidated trade.approved_for_execution execution.requested execution.submitted execution.completed
execution.failed position.updated position.closed trade_management.requested trade_management.approved
trade_management.rejected trade_management.completed orchestration.workflow.triggered orchestration.workflow.started
orchestration.workflow.completed orchestration.workflow.failed orchestration.workflow.retrying
orchestration.workflow.timed_out""".split()


def test_catalogue_covers_every_specified_event_type():
    assert set(SPEC_TYPES) <= set(cat.CATALOGUE)


def test_critical_events_are_owned_by_their_modules_and_never_by_n8n():
    for e in cat.CATALOGUE.values():
        if e.family in ("approval", "execution", "trade_management") or e.name.startswith("risk.assessment.") and e.name != "risk.assessment.requested":
            assert "n8n" not in e.owners and "module_08" not in e.owners, e.name
    assert cat.CATALOGUE["trade.approved_for_execution"].owners == ("module_07",)
    for name in ("trade.approved_for_execution", "execution.requested", "approval.approved", "risk.assessment.completed"):
        assert not cat.CATALOGUE[name].replayable


def env(**over):
    base = {"eventType": "market.data.updated", "sourceModule": "module_01", "environment": "test",
            "payload": {"symbol": "EURUSD", "timeframe": "H1", "as_of": "2026-11-09T11:00:00Z"}}
    base.update(over)
    return base


def test_valid_envelope_camel_case_and_generated_ids():
    e = cat.validate_envelope(env(), environment="test", now=NOW)
    assert e.event_id and e.idempotency_key.startswith("module_01:") and e.schema_version == "1.0"
    assert cat.validate_envelope(env(sourceModule="module_01_market"), environment="test", now=NOW).source_module == "module_01"


@pytest.mark.parametrize("over,code", [
    ({"eventType": "made.up"}, "UNKNOWN_EVENT_TYPE"),
    ({"eventVersion": "9.0"}, "UNSUPPORTED_EVENT_VERSION"),
    ({"schemaVersion": "2.0"}, "UNSUPPORTED_SCHEMA_VERSION"),
    ({"environment": "production"}, "CROSS_ENVIRONMENT"),
    ({"environment": "moon"}, "INVALID_EVENT"),
    ({"sourceModule": "module_05"}, "EVENT_OWNER_MISMATCH"),
    ({"payload": {"symbol": "EURUSD"}}, "INVALID_PAYLOAD"),
    ({"payload": {"symbol": "EURUSD", "timeframe": 5, "as_of": "x"}}, "INVALID_PAYLOAD"),
    ({"payload": {"symbol": "E", "timeframe": "H1", "as_of": "x", "nested": {"api_key": "k"}}}, "SENSITIVE_FIELD"),
    ({"metadata": {"password": "p"}}, "SENSITIVE_FIELD"),
    ({"occurredAt": "2026-11-09T11:00:00"}, "INVALID_EVENT"),
    ({"payload": []}, "INVALID_EVENT"),
])
def test_invalid_envelopes(over, code):
    with pytest.raises(cat.EventError) as ei:
        cat.validate_envelope(env(**over), environment="test", now=NOW)
    assert ei.value.code == code


def test_n8n_may_publish_only_requests():
    ok = cat.validate_envelope({"eventType": "ai.analysis.requested", "sourceModule": "n8n", "environment": "test",
                                "payload": {"symbol": "XAUUSD"}}, environment="test", now=NOW, via_n8n=True)
    assert ok.source_module == "n8n"
    for etype, payload in (("trade.approved_for_execution", {"approval_id": "a"}), ("approval.approved", {"approval_id": "a"}),
                           ("risk.assessment.completed", {"assessment_id": "x", "decision": "PASS"})):
        with pytest.raises(cat.EventError) as ei:
            cat.validate_envelope({"eventType": etype, "sourceModule": "n8n", "environment": "test", "payload": payload},
                                  environment="test", now=NOW, via_n8n=True)
        assert ei.value.code == "EVENT_NOT_ALLOWED_FROM_N8N"
    with pytest.raises(cat.EventError):     # n8n cannot pretend to be Module 07 either
        cat.validate_envelope({"eventType": "approval.approved", "sourceModule": "module_07", "environment": "test",
                               "payload": {"approval_id": "a"}}, environment="test", now=NOW, via_n8n=True)


def test_patterns():
    assert cat.matches("approval.*", "approval.requested") and cat.matches("*", "x.y")
    assert not cat.matches("approval.*", "approvals.x") and not cat.matches("risk.assessment.completed", "risk.assessment.blocked")


# ---- schemas -------------------------------------------------------------------------------------------------------------------
def test_schema_subset_and_validation():
    s = {"type": "object", "required": ["symbol"], "additionalProperties": False,
         "properties": {"symbol": {"type": "string", "pattern": "^[A-Z0-9]{3,10}$"}, "n": {"type": "integer", "minimum": 1},
                        "tfs": {"type": "array", "items": {"enum": ["H1", "H4"]}, "maxItems": 2}}}
    schemas.check_schema(s)
    assert schemas.validate({"symbol": "EURUSD", "n": 2, "tfs": ["H1"]}, s) == []
    probs = schemas.validate({"n": 0, "tfs": ["M1", "H1", "H4"], "x": 1, "symbol": "eu"}, s)
    assert {p["path"] for p in probs} >= {"$.n", "$.tfs[0]", "$.tfs", "$.x", "$.symbol"}
    assert schemas.validate({"symbol": "EURUSD", "n": True}, s)          # a boolean is not an integer
    with pytest.raises(schemas.SchemaError):
        schemas.check_schema({"type": "object", "oneOf": []})
    with pytest.raises(schemas.SchemaError):
        schemas.check_schema({"type": "nope"})


# ---- scheduling ----------------------------------------------------------------------------------------------------------------
def test_cron_in_lagos_time_stored_in_utc():
    # 07:30 Lagos (UTC+1) every weekday = 06:30 UTC
    t = scheduler.next_run("cron", "30 7 * * 1-5", "Africa/Lagos", NOW)
    assert t == datetime(2026, 11, 10, 6, 30, tzinfo=timezone.utc)      # Mon 9 Nov 12:00 UTC -> Tue 06:30 UTC
    assert scheduler.next_run("cron", "*/15 * * * *", "Africa/Lagos", NOW) == datetime(2026, 11, 9, 12, 15, tzinfo=timezone.utc)
    sat = scheduler.next_run("cron", "0 0 * * 6", "UTC", NOW)
    assert sat.weekday() == 5


def test_cron_dst_gap_is_skipped():
    # 02:30 local does not exist on 2027-03-14 in New York.
    t = scheduler.next_run("cron", "30 2 14 3 *", "America/New_York", datetime(2027, 3, 1, tzinfo=timezone.utc))
    assert t.year == 2028


def test_interval_is_anchored_and_once_runs_once():
    anchor = NOW
    assert scheduler.next_run("interval", "300", None, NOW.replace(minute=7), anchor=anchor) == NOW.replace(minute=10)
    assert scheduler.next_run("once", "2026-11-09T13:00:00Z", None, NOW) == NOW.replace(hour=13)
    assert scheduler.next_run("once", "2026-11-09T11:00:00Z", None, NOW) is None


@pytest.mark.parametrize("kind,expr", [("cron", "61 * * * *"), ("cron", "* * *"), ("interval", "30"), ("interval", "x"),
                                       ("once", "tomorrow"), ("weekly", "1")])
def test_bad_schedules(kind, expr):
    with pytest.raises(scheduler.ScheduleError):
        scheduler.validate(kind, expr, "Africa/Lagos")


def test_unknown_timezone():
    with pytest.raises(scheduler.ScheduleError):
        scheduler.validate("cron", "* * * * *", "Mars/Olympus")


# ---- retry -------------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("status,kind,category", [
    (429, "transient", "RATE_LIMITED"), (503, "transient", "SERVER_ERROR"), (500, "unknown", "OUTCOME_UNKNOWN"),
    (401, "permanent", "AUTH_FAILED"), (404, "permanent", "WORKFLOW_NOT_FOUND"), (422, "permanent", "INVALID_PAYLOAD"),
    (200, "ok", "OK")])
def test_http_classification(status, kind, category):
    c = classify_http(status, "12")
    assert (c.kind, c.category) == (kind, category)


def test_exception_classification_distinguishes_not_sent_from_unknown():
    import httpx
    assert classify_exception(httpx.ConnectError("x")).kind == "transient"
    assert classify_exception(httpx.ReadTimeout("x")).kind == "unknown"


def test_business_failures_are_never_retried():
    for c in ("RISK_BLOCKED", "APPROVAL_EXPIRED", "BUSINESS_RULE"):
        assert kind_of(c, True) == "permanent"
    assert kind_of("RATE_LIMITED") == "transient" and kind_of("OUTCOME_UNKNOWN") == "unknown"


def test_backoff_policy():
    p = RetryPolicy(max_attempts=3, base_seconds=10, factor=2, max_backoff_seconds=25, jitter=0)
    assert [p.delay(i).total_seconds() for i in (1, 2, 3)] == [10, 20, 25]
    assert p.delay(1, retry_after=60).total_seconds() == 25            # capped
    assert p.may_retry(2, NOW, NOW) and not p.may_retry(3, NOW, NOW)
    with pytest.raises(ValueError):
        RetryPolicy.from_dict({"max_attempts": 0})
