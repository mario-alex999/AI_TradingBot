"""Roles and permissions. The backend is the authority; the frontend only mirrors this to hide controls."""
from __future__ import annotations

from enum import Enum


class Role(str, Enum):
    ADMIN = "admin"
    TRADER = "trader"
    REVIEWER = "reviewer"
    OBSERVER = "observer"
    SERVICE = "service"       # other modules and n8n; never a human action


HUMAN_ROLES = (Role.ADMIN, Role.TRADER, Role.REVIEWER, Role.OBSERVER)

# Permission strings are stable API: tests and the frontend rely on them.
VIEW = "approvals:view"                    # dashboards, requests, evidence, history, positions
DECIDE = "approvals:decide"                # approve / reject / request modification of a trade
CREATE_REQUEST = "approvals:create"        # manual review request from a trade idea
CANCEL = "approvals:cancel"
REVALIDATE = "approvals:revalidate"
MANAGE_REQUEST = "management:request"      # propose a change to an open position
MANAGE_DECIDE = "management:decide"        # approve / reject a management action
EMERGENCY = "management:emergency"         # request and authorise an emergency exit
POLICY = "approvals:policy"                # approval policies, reviewer assignments, notification settings
AUDIT_ALL = "approvals:audit"              # every audit record
USERS = "users:manage"
EVENTS_IN = "events:publish"               # service-to-service inbound events
EXECUTION_REPORT = "execution:report"      # Module 09 reports execution results and positions
EXECUTION_READ = "execution:read"          # Module 09 reads its execution queue

ROLE_PERMISSIONS: dict[Role, frozenset[str]] = {
    Role.ADMIN: frozenset({VIEW, DECIDE, CREATE_REQUEST, CANCEL, REVALIDATE, MANAGE_REQUEST, MANAGE_DECIDE, EMERGENCY,
                           POLICY, AUDIT_ALL, USERS}),
    Role.TRADER: frozenset({VIEW, DECIDE, CREATE_REQUEST, CANCEL, REVALIDATE, MANAGE_REQUEST, MANAGE_DECIDE, EMERGENCY}),
    Role.REVIEWER: frozenset({VIEW, DECIDE, REVALIDATE}),
    Role.OBSERVER: frozenset({VIEW}),
    Role.SERVICE: frozenset({VIEW, EVENTS_IN, EXECUTION_REPORT, EXECUTION_READ}),
}

# Permissions that only a signed-in person may use, whatever a service token's scopes say.
HUMAN_ONLY = frozenset({DECIDE, CREATE_REQUEST, CANCEL, MANAGE_REQUEST, MANAGE_DECIDE, EMERGENCY, POLICY, USERS})


def permissions_for(role: Role, scopes: frozenset[str] | None = None) -> frozenset[str]:
    base = ROLE_PERMISSIONS[role]
    if role is Role.SERVICE:
        # A service token can be narrowed by its scopes but never widened, and never to a human-only permission.
        allowed = base if scopes is None else base & scopes
        return frozenset(p for p in allowed if p not in HUMAN_ONLY)
    return base
