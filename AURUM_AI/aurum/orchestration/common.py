"""Shared types for the Module 08 service parts (kept separate to avoid import cycles)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class OrchError(Exception):
    def __init__(self, status: int, code: str, message: str, details: Any = None):
        super().__init__(message)
        self.status, self.code, self.message, self.details = status, code, message, details


@dataclass(frozen=True)
class Actor:
    id: str
    name: str
    kind: str                                   # user | service | system | n8n
    role: str | None = None
    perms: frozenset[str] = field(default_factory=frozenset)

    def can(self, permission: str) -> bool:
        return self.kind == "system" or permission in self.perms

    @property
    def is_human(self) -> bool:
        return self.kind == "user"

    @classmethod
    def from_principal(cls, p: Any) -> "Actor":
        kind = "user" if p.is_human else "service"
        return cls(id=str(p.id), name=str(getattr(p, "username", None) or getattr(p, "name", None) or p.id), kind=kind,
                   role=str(getattr(p.role, "value", p.role)) if getattr(p, "role", None) is not None else None,
                   perms=frozenset(getattr(p, "permissions", None) or ()))


SYSTEM = Actor("system", "module_08", "system")


def pick(body: dict[str, Any], name: str, default: Any = None) -> Any:
    """Reads ``name`` (snake_case) or its camelCase form from a request body."""
    if name in body:
        return body[name]
    head, *rest = name.split("_")
    camel = head + "".join(p[:1].upper() + p[1:] for p in rest)
    return body.get(camel, default)
