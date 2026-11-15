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
ORCH_VIEW = "orchestration:view"           # Module 08 dashboards, registry, executions, events, health, audit
ORCH_TRIGGER = "orchestration:trigger"     # trigger a registered workflow through the API (people and services)
ORCH_OPERATE = "orchestration:operate"     # manual trigger, retry, cancel, replay, dead letters, schedule pause/resume
ORCH_MANAGE = "orchestration:manage"       # register workflows and versions, activate, configuration, subscriptions
ORCH_CALLBACK = "orchestration:callback"   # n8n: execution callbacks and the events webhook (service tokens only)
ORCH_LOCKS = "orchestration:locks"         # acquire, renew and release coordination leases (services)
ORCH_TICK = "orchestration:tick"           # run one Module 08 worker pass over HTTP (an external timer; services)
MT5_LINK = "mt5:link"                      # link one's own MT5 DEMO account after signing in (read-only mirror)

ROLE_PERMISSIONS: dict[Role, frozenset[str]] = {
    Role.ADMIN: frozenset({VIEW, DECIDE, CREATE_REQUEST, CANCEL, REVALIDATE, MANAGE_REQUEST, MANAGE_DECIDE, EMERGENCY,
                           POLICY, AUDIT_ALL, USERS, ORCH_VIEW, ORCH_TRIGGER, ORCH_OPERATE, ORCH_MANAGE, MT5_LINK}),
    Role.TRADER: frozenset({VIEW, DECIDE, CREATE_REQUEST, CANCEL, REVALIDATE, MANAGE_REQUEST, MANAGE_DECIDE, EMERGENCY,
                            ORCH_VIEW, ORCH_TRIGGER, ORCH_OPERATE, MT5_LINK}),
    Role.REVIEWER: frozenset({VIEW, DECIDE, REVALIDATE, ORCH_VIEW, MT5_LINK}),
    Role.OBSERVER: frozenset({VIEW, ORCH_VIEW, MT5_LINK}),
    Role.SERVICE: frozenset({VIEW, EVENTS_IN, EXECUTION_REPORT, EXECUTION_READ, ORCH_VIEW, ORCH_TRIGGER, ORCH_CALLBACK,
                             ORCH_LOCKS, ORCH_TICK}),
}

# Permissions that only a signed-in person may use, whatever a service token's scopes say.
HUMAN_ONLY = frozenset({DECIDE, CREATE_REQUEST, CANCEL, MANAGE_REQUEST, MANAGE_DECIDE, EMERGENCY, POLICY, USERS, ORCH_OPERATE,
                        ORCH_MANAGE, MT5_LINK})


def permissions_for(role: Role, scopes: frozenset[str] | None = None) -> frozenset[str]:
    base = ROLE_PERMISSIONS[role]
    if role is Role.SERVICE:
        # A service token can be narrowed by its scopes but never widened, and never to a human-only permission.
        allowed = base if scopes is None else base & scopes
        return frozenset(p for p in allowed if p not in HUMAN_ONLY)
    return base
