"""Module 08 state machines: workflow registrations, workflow versions and executions.

Every status change goes through ``check_*``; PostgreSQL also enforces the execution pairs with a trigger (migration 0010).

Execution lifecycle::

    RECEIVED -> VALIDATING -> QUEUED -> DISPATCHED -> RUNNING -> SUCCEEDED
                                          |  ^          |  \\-> WAITING_CALLBACK -> SUCCEEDED | FAILED | TIMED_OUT
                                          v  |          v
                                   RETRY_PENDING <- FAILED -> DEAD_LETTERED
                                                    TIMED_OUT -> SUCCEEDED | FAILED (a late callback is the truth)

Guarantees (tests/orchestration/test_states.py checks every pair):
- SUCCEEDED, CANCELLED and DEAD_LETTERED never change again; a late or duplicate callback cannot overwrite them.
- TIMED_OUT is not treated as "did not happen": a later callback may still record SUCCEEDED or FAILED, and a retry
  after a time-out needs a person (``manual`` retry), never the automatic policy.
- DEAD_LETTERED leaves only through a deliberate manual retry (back to RETRY_PENDING).
"""
from __future__ import annotations

from enum import Enum


class IllegalTransition(ValueError):
    def __init__(self, kind: str, current: str, target: str):
        super().__init__(f"A {kind} cannot move from {current} to {target}.")
        self.kind, self.current, self.target = kind, current, target


# ---- executions -------------------------------------------------------------------------------------------------------------
class ExecutionStatus(str, Enum):
    RECEIVED = "RECEIVED"
    VALIDATING = "VALIDATING"
    QUEUED = "QUEUED"
    DISPATCHED = "DISPATCHED"
    RUNNING = "RUNNING"
    WAITING_CALLBACK = "WAITING_CALLBACK"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    RETRY_PENDING = "RETRY_PENDING"
    DEAD_LETTERED = "DEAD_LETTERED"
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"


E = ExecutionStatus
EXECUTION_TRANSITIONS: dict[E, frozenset[E]] = {
    E.RECEIVED: frozenset({E.VALIDATING, E.FAILED, E.CANCELLED}),
    E.VALIDATING: frozenset({E.QUEUED, E.FAILED, E.CANCELLED}),
    E.QUEUED: frozenset({E.DISPATCHED, E.CANCELLED, E.FAILED}),
    E.DISPATCHED: frozenset({E.RUNNING, E.WAITING_CALLBACK, E.SUCCEEDED, E.FAILED, E.RETRY_PENDING, E.TIMED_OUT, E.CANCELLED}),
    E.RUNNING: frozenset({E.WAITING_CALLBACK, E.SUCCEEDED, E.FAILED, E.TIMED_OUT, E.CANCELLED}),
    E.WAITING_CALLBACK: frozenset({E.RUNNING, E.SUCCEEDED, E.FAILED, E.TIMED_OUT, E.CANCELLED}),
    E.FAILED: frozenset({E.RETRY_PENDING, E.DEAD_LETTERED}),
    E.TIMED_OUT: frozenset({E.SUCCEEDED, E.FAILED, E.RETRY_PENDING, E.DEAD_LETTERED}),
    E.RETRY_PENDING: frozenset({E.DISPATCHED, E.CANCELLED, E.DEAD_LETTERED, E.FAILED}),
    E.DEAD_LETTERED: frozenset({E.RETRY_PENDING}),
    E.SUCCEEDED: frozenset(),
    E.CANCELLED: frozenset(),
}
EXECUTION_FINAL = frozenset({E.SUCCEEDED, E.CANCELLED, E.DEAD_LETTERED})
EXECUTION_IN_FLIGHT = frozenset({E.DISPATCHED, E.RUNNING, E.WAITING_CALLBACK})
EXECUTION_OPEN = frozenset({E.RECEIVED, E.VALIDATING, E.QUEUED, E.RETRY_PENDING}) | EXECUTION_IN_FLIGHT
CANCELLABLE = frozenset(s for s, nxt in EXECUTION_TRANSITIONS.items() if E.CANCELLED in nxt)


# ---- workflow registrations ----------------------------------------------------------------------------------------------------
class WorkflowState(str, Enum):
    REGISTERED = "REGISTERED"
    INACTIVE = "INACTIVE"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    DEGRADED = "DEGRADED"
    FAILED = "FAILED"
    RETIRED = "RETIRED"


W = WorkflowState
WORKFLOW_TRANSITIONS: dict[W, frozenset[W]] = {
    W.REGISTERED: frozenset({W.INACTIVE, W.ACTIVE, W.RETIRED}),
    W.INACTIVE: frozenset({W.ACTIVE, W.RETIRED}),
    W.ACTIVE: frozenset({W.PAUSED, W.INACTIVE, W.DEGRADED, W.FAILED}),
    W.PAUSED: frozenset({W.ACTIVE, W.INACTIVE, W.RETIRED}),
    W.DEGRADED: frozenset({W.ACTIVE, W.PAUSED, W.INACTIVE, W.FAILED}),
    W.FAILED: frozenset({W.ACTIVE, W.INACTIVE, W.PAUSED, W.RETIRED}),
    W.RETIRED: frozenset(),
}
DISPATCHABLE = frozenset({W.ACTIVE, W.DEGRADED})   # DEGRADED still runs, with a warning on every execution


# ---- workflow versions ---------------------------------------------------------------------------------------------------------
class VersionStatus(str, Enum):
    DRAFT = "DRAFT"
    VALIDATED = "VALIDATED"
    PUBLISHED = "PUBLISHED"
    ACTIVE = "ACTIVE"
    DEPRECATED = "DEPRECATED"
    RETIRED = "RETIRED"


V = VersionStatus
VERSION_TRANSITIONS: dict[V, frozenset[V]] = {
    V.DRAFT: frozenset({V.VALIDATED, V.RETIRED}),
    V.VALIDATED: frozenset({V.PUBLISHED, V.ACTIVE, V.RETIRED}),
    V.PUBLISHED: frozenset({V.ACTIVE, V.DEPRECATED, V.RETIRED}),
    V.ACTIVE: frozenset({V.DEPRECATED}),
    V.DEPRECATED: frozenset({V.ACTIVE, V.RETIRED}),        # DEPRECATED -> ACTIVE is a rollback
    V.RETIRED: frozenset(),
}
ACTIVATABLE_VERSIONS = frozenset({V.VALIDATED, V.PUBLISHED, V.DEPRECATED})


def _check(kind: str, enum: type[Enum], table: dict, current: str, target: str) -> None:
    try:
        ok = enum(target) in table.get(enum(current), frozenset())
    except ValueError:
        ok = False                      # an unknown status is never a legal move
    if not ok:
        raise IllegalTransition(kind, current, target)


def check_execution(current: str, target: str) -> None:
    _check("workflow execution", ExecutionStatus, EXECUTION_TRANSITIONS, current, target)


def check_workflow(current: str, target: str) -> None:
    _check("workflow registration", WorkflowState, WORKFLOW_TRANSITIONS, current, target)


def check_version(current: str, target: str) -> None:
    _check("workflow version", VersionStatus, VERSION_TRANSITIONS, current, target)


def can_execution(current: str, target: str) -> bool:
    try:
        check_execution(current, target)
        return True
    except IllegalTransition:
        return False


def execution_sql_pairs() -> list[tuple[str, str]]:
    """Every allowed (from, to) execution pair, for the PostgreSQL trigger in the migration."""
    return sorted((a.value, b.value) for a, nxt in EXECUTION_TRANSITIONS.items() for b in nxt)
