"""Approval and trade-management state machines. Every status change in Module 07 goes through ``check``.

Approval lifecycle::

    PENDING ──> UNDER_REVIEW ──> APPROVED ──> EXECUTION_PENDING ──> EXECUTION_SUBMITTED ──> EXECUTED
       │             │              │                │                       └────────────> EXECUTION_FAILED
       │             │              │                └──> EXECUTION_FAILED (Module 09 rejected before submitting)
       │             │              └──> INVALIDATED | CANCELLED (before the handoff)
       ├─────────────┴──> REJECTED | MODIFICATION_REQUESTED | EXPIRED | CANCELLED | INVALIDATED

Guarantees (tested exhaustively in tests/approvals/test_state_machine.py):
- APPROVED never returns to PENDING or UNDER_REVIEW; nothing returns to PENDING.
- REJECTED, EXPIRED, CANCELLED, INVALIDATED and MODIFICATION_REQUESTED are terminal: they can never become APPROVED or
  be executed. A modification creates a *new* request for the new trade version.
- Only PENDING and UNDER_REVIEW can expire, so expiry can never undo an approval.
- Only an APPROVED request can be handed to execution.
"""
from __future__ import annotations

from enum import Enum


class ApprovalStatus(str, Enum):
    PENDING = "PENDING"
    UNDER_REVIEW = "UNDER_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    MODIFICATION_REQUESTED = "MODIFICATION_REQUESTED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"
    INVALIDATED = "INVALIDATED"
    EXECUTION_PENDING = "EXECUTION_PENDING"
    EXECUTION_SUBMITTED = "EXECUTION_SUBMITTED"
    EXECUTED = "EXECUTED"
    EXECUTION_FAILED = "EXECUTION_FAILED"


A = ApprovalStatus
_OPEN_EXITS = frozenset({A.APPROVED, A.REJECTED, A.MODIFICATION_REQUESTED, A.EXPIRED, A.CANCELLED, A.INVALIDATED})

APPROVAL_TRANSITIONS: dict[ApprovalStatus, frozenset[ApprovalStatus]] = {
    A.PENDING: _OPEN_EXITS | {A.UNDER_REVIEW},
    A.UNDER_REVIEW: _OPEN_EXITS,
    A.APPROVED: frozenset({A.EXECUTION_PENDING, A.INVALIDATED, A.CANCELLED}),
    A.EXECUTION_PENDING: frozenset({A.EXECUTION_SUBMITTED, A.EXECUTION_FAILED}),
    A.EXECUTION_SUBMITTED: frozenset({A.EXECUTED, A.EXECUTION_FAILED}),
    A.REJECTED: frozenset(),
    A.MODIFICATION_REQUESTED: frozenset(),
    A.EXPIRED: frozenset(),
    A.CANCELLED: frozenset(),
    A.INVALIDATED: frozenset(),
    A.EXECUTED: frozenset(),
    A.EXECUTION_FAILED: frozenset(),
}

OPEN = frozenset({A.PENDING, A.UNDER_REVIEW})                 # awaiting a human decision
ACTIVE = frozenset({A.PENDING, A.UNDER_REVIEW, A.APPROVED, A.EXECUTION_PENDING, A.EXECUTION_SUBMITTED})
TERMINAL = frozenset(s for s, nxt in APPROVAL_TRANSITIONS.items() if not nxt)
EXPIRABLE = OPEN


class ManagementStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"
    INVALIDATED = "INVALIDATED"
    EXECUTION_PENDING = "EXECUTION_PENDING"
    EXECUTION_SUBMITTED = "EXECUTION_SUBMITTED"
    EXECUTED = "EXECUTED"
    EXECUTION_FAILED = "EXECUTION_FAILED"


M = ManagementStatus
MANAGEMENT_TRANSITIONS: dict[ManagementStatus, frozenset[ManagementStatus]] = {
    M.PENDING: frozenset({M.APPROVED, M.REJECTED, M.EXPIRED, M.CANCELLED, M.INVALIDATED}),
    M.APPROVED: frozenset({M.EXECUTION_PENDING, M.INVALIDATED}),
    M.EXECUTION_PENDING: frozenset({M.EXECUTION_SUBMITTED, M.EXECUTION_FAILED}),
    M.EXECUTION_SUBMITTED: frozenset({M.EXECUTED, M.EXECUTION_FAILED}),
    M.REJECTED: frozenset(), M.EXPIRED: frozenset(), M.CANCELLED: frozenset(), M.INVALIDATED: frozenset(),
    M.EXECUTED: frozenset(), M.EXECUTION_FAILED: frozenset(),
}
MANAGEMENT_ACTIVE = frozenset({M.PENDING, M.APPROVED, M.EXECUTION_PENDING, M.EXECUTION_SUBMITTED})


class IllegalTransition(ValueError):
    def __init__(self, current: str, target: str, kind: str = "approval request"):
        super().__init__(f"An {kind} cannot move from {current} to {target}.")
        self.current, self.target = current, target


def check(current: str, target: str) -> None:
    try:
        allowed = ApprovalStatus(target) in APPROVAL_TRANSITIONS.get(ApprovalStatus(current), frozenset())
    except ValueError:
        allowed = False             # an unknown status is never a legal move
    if not allowed:
        raise IllegalTransition(current, target)


def check_management(current: str, target: str) -> None:
    try:
        allowed = ManagementStatus(target) in MANAGEMENT_TRANSITIONS.get(ManagementStatus(current), frozenset())
    except ValueError:
        allowed = False
    if not allowed:
        raise IllegalTransition(current, target, "trade-management request")


def transition_sql_pairs() -> list[tuple[str, str]]:
    """Every allowed (from, to) pair, for the PostgreSQL trigger in the migration."""
    return sorted((a.value, b.value) for a, nxt in APPROVAL_TRANSITIONS.items() for b in nxt)
