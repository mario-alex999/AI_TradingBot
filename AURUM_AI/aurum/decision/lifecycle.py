"""Candidate status machine: new -> under_review -> approved | rejected | expired.

``executed`` is reserved for a future execution module. Nothing in Module 05 can reach it, by construction: it is in no
transition's target set.
"""
from __future__ import annotations

ACTIVE = ("new", "under_review")
STATUSES = ("new", "under_review", "approved", "rejected", "expired", "executed")

TRANSITIONS: dict[str, frozenset[str]] = {
    "new": frozenset({"under_review", "approved", "rejected", "expired"}),
    "under_review": frozenset({"approved", "rejected", "expired"}),
    # Only two ways back: a gate that clears (system) and an explicit human re-open with a note.
    "rejected": frozenset({"new", "under_review", "expired"}),
    "approved": frozenset(),
    "expired": frozenset(),
    "executed": frozenset(),
}


class IllegalTransition(ValueError):
    pass


def check(current: str, target: str) -> None:
    if target not in TRANSITIONS.get(current, frozenset()):
        raise IllegalTransition(f"A candidate cannot move from '{current}' to '{target}'.")
