"""Exhaustive state machine checks: every (from, to) pair is either allowed by the table or refused."""
from __future__ import annotations

import itertools

import pytest

from aurum.approvals.states import (ACTIVE, APPROVAL_TRANSITIONS, MANAGEMENT_TRANSITIONS, OPEN, TERMINAL, ApprovalStatus as S,
                                    IllegalTransition, ManagementStatus as M, check, check_management)


@pytest.mark.parametrize("a,b", list(itertools.product(list(S), list(S))))
def test_every_approval_pair(a, b):
    if b in APPROVAL_TRANSITIONS.get(a, frozenset()):
        check(a.value, b.value)
    else:
        with pytest.raises(IllegalTransition):
            check(a.value, b.value)


@pytest.mark.parametrize("a,b", list(itertools.product(list(M), list(M))))
def test_every_management_pair(a, b):
    if b in MANAGEMENT_TRANSITIONS.get(a, frozenset()):
        check_management(a.value, b.value)
    else:
        with pytest.raises(IllegalTransition):
            check_management(a.value, b.value)


def test_key_safety_properties():
    # nothing reaches execution without APPROVED first
    for s in S:
        if S.EXECUTION_PENDING in APPROVAL_TRANSITIONS.get(s, frozenset()):
            assert s == S.APPROVED
    # an approved request can never expire (expiry only applies to open requests)
    assert S.EXPIRED not in APPROVAL_TRANSITIONS[S.APPROVED]
    # terminal states have no exits
    for s in TERMINAL:
        assert not APPROVAL_TRANSITIONS.get(s)
    # rejected / expired / cancelled can never become approved
    for s in (S.REJECTED, S.EXPIRED, S.CANCELLED, S.INVALIDATED, S.MODIFICATION_REQUESTED):
        assert S.APPROVED not in APPROVAL_TRANSITIONS.get(s, frozenset())
    assert OPEN <= ACTIVE


def test_unknown_status_refused():
    with pytest.raises(IllegalTransition):
        check("PENDING", "SOMETHING")
    with pytest.raises(IllegalTransition):
        check("NOPE", "APPROVED")
