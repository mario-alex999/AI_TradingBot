"""AuthService: users, password sign-in with lockout, server-side sessions and service tokens."""
from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy import select, update
from sqlalchemy.engine import Engine

from aurum.auth import passwords
from aurum.auth.db_models import AuthSession, ServiceToken, User
from aurum.auth.roles import HUMAN_ROLES, Role, permissions_for
from aurum.database.connection import session_scope

log = logging.getLogger(__name__)
USERNAME = re.compile(r"^[a-z0-9][a-z0-9._-]{2,63}$")


class AuthError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


@dataclass(frozen=True)
class Principal:
    """Who is calling. ``kind`` is ``user`` (a signed-in person) or ``service`` (a module or n8n)."""
    id: str
    username: str
    display_name: str
    role: Role
    kind: str
    permissions: frozenset[str]
    account_scope: tuple[str, ...] | None = None
    session_id: str | None = None
    csrf_hash: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def is_human(self) -> bool:
        return self.kind == "user"

    def can(self, permission: str) -> bool:
        return permission in self.permissions

    def may_access_account(self, login: str | int | None) -> bool:
        return self.account_scope is None or login is None or str(login) in self.account_scope

    def public(self) -> dict[str, Any]:
        return {"id": self.id, "username": self.username, "display_name": self.display_name, "role": self.role.value,
                "kind": self.kind, "permissions": sorted(self.permissions),
                "account_scope": list(self.account_scope) if self.account_scope is not None else None}


@dataclass(frozen=True)
class IssuedSession:
    token: str            # goes in the httpOnly cookie; never stored
    csrf: str             # returned to the client; only its hash is stored
    principal: Principal
    expires_at: datetime


class AuthService:
    def __init__(self, engine: Engine, *, iterations: int = 600_000, idle_minutes: int = 30, absolute_hours: int = 12,
                 max_failed: int = 5, lockout_minutes: int = 15, clock: Callable[[], datetime] | None = None):
        self.engine = engine
        self.iterations = iterations
        self.idle = timedelta(minutes=idle_minutes)
        self.absolute = timedelta(hours=absolute_hours)
        self.max_failed = max_failed
        self.lockout = timedelta(minutes=lockout_minutes)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._dummy: str | None = None

    @classmethod
    def from_settings(cls, engine: Engine, settings: Any, clock: Callable[[], datetime] | None = None) -> "AuthService":
        return cls(engine, iterations=settings.auth_password_iterations, idle_minutes=settings.auth_session_idle_minutes,
                   absolute_hours=settings.auth_session_absolute_hours, max_failed=settings.auth_max_failed_logins,
                   lockout_minutes=settings.auth_lockout_minutes, clock=clock)

    # ---- users -----------------------------------------------------------------------------------------------------------
    def create_user(self, username: str, password: str, role: str, display_name: str | None = None,
                    email: str | None = None, account_scope: list[str] | None = None) -> dict[str, Any]:
        username = username.strip().lower()
        if not USERNAME.match(username):
            raise AuthError(422, "INVALID_USERNAME", "Usernames are 3-64 characters: letters, digits, dot, dash, underscore.")
        r = _human_role(role)
        try:
            passwords.check_strength(password, username)
        except passwords.WeakPassword as exc:
            raise AuthError(422, "WEAK_PASSWORD", str(exc)) from exc
        now = self.clock()
        with session_scope(self.engine) as s:
            if s.execute(select(User.id).where(User.username == username)).first():
                raise AuthError(409, "USERNAME_TAKEN", "That username already exists.")
            u = User(id=str(uuid.uuid4()), username=username, display_name=(display_name or username).strip()[:128],
                     email=email, role=r.value, password_hash=passwords.hash_password(password, self.iterations),
                     account_scope=[str(a) for a in account_scope] if account_scope else None,
                     password_changed_at=now, created_at=now, updated_at=now)
            s.add(u)
            s.flush()
            return _user_row(u)

    def list_users(self) -> list[dict[str, Any]]:
        with session_scope(self.engine) as s:
            return [_user_row(u) for u in s.execute(select(User).order_by(User.username)).scalars()]

    def set_active(self, username: str, active: bool) -> dict[str, Any]:
        with session_scope(self.engine) as s:
            u = self._user(s, username)
            u.is_active, u.updated_at = active, self.clock()
            if not active:
                self._revoke_all(s, u.id, "user_disabled")
            return _user_row(u)

    def set_role(self, username: str, role: str) -> dict[str, Any]:
        r = _human_role(role)
        with session_scope(self.engine) as s:
            u = self._user(s, username)
            u.role, u.updated_at = r.value, self.clock()
            self._revoke_all(s, u.id, "role_changed")       # new permissions apply from the next sign-in
            return _user_row(u)

    def set_password(self, username: str, password: str) -> None:
        with session_scope(self.engine) as s:
            u = self._user(s, username)
            try:
                passwords.check_strength(password, u.username)
            except passwords.WeakPassword as exc:
                raise AuthError(422, "WEAK_PASSWORD", str(exc)) from exc
            now = self.clock()
            u.password_hash, u.password_changed_at, u.updated_at = passwords.hash_password(password, self.iterations), now, now
            u.failed_logins, u.locked_until = 0, None
            self._revoke_all(s, u.id, "password_changed")

    def has_users(self) -> bool:
        with session_scope(self.engine) as s:
            return s.execute(select(User.id).limit(1)).first() is not None

    # ---- sign-in ---------------------------------------------------------------------------------------------------------
    def login(self, username: str, password: str, client_ip: str | None = None, user_agent: str | None = None) -> IssuedSession:
        """Same message for unknown user, wrong password and disabled account, so the response does not reveal which."""
        now = self.clock()
        bad = AuthError(401, "INVALID_CREDENTIALS", "Username or password is incorrect.")
        failure: AuthError | None = None
        # Failures are raised only after the transaction commits, so the failed-attempt count and lockout are saved.
        with session_scope(self.engine) as s:
            u = s.execute(select(User).where(User.username == (username or "").strip().lower())).scalar_one_or_none()
            if u is None:
                passwords.verify_password(password or "", self._dummy_hash())     # equal work for unknown usernames
                failure = bad
            elif u.locked_until is not None and u.locked_until > now:
                failure = AuthError(423, "ACCOUNT_LOCKED", "Too many failed sign-ins. Try again later or ask an administrator.")
            elif not passwords.verify_password(password or "", u.password_hash) or not u.is_active:
                if u.is_active:
                    u.failed_logins += 1
                    if u.failed_logins >= self.max_failed:
                        u.locked_until, u.failed_logins = now + self.lockout, 0
                        log.warning("Account locked after failed sign-ins", extra={"username": u.username})
                failure = bad
            if failure is None:
                assert u is not None
                if passwords.needs_rehash(u.password_hash, self.iterations):    # iterations were raised since it was set
                    u.password_hash = passwords.hash_password(password, self.iterations)
                u.failed_logins, u.locked_until, u.last_login_at = 0, None, now
                token = passwords.new_token()
                csrf = passwords.csrf_for(token)
                sess = AuthSession(id=str(uuid.uuid4()), token_hash=passwords.token_hash(token),
                                   csrf_hash=passwords.token_hash(csrf), user_id=u.id, created_at=now, last_seen_at=now,
                                   expires_at=now + self.absolute, client_ip=(client_ip or "")[:64] or None,
                                   user_agent=(user_agent or "")[:256] or None)
                s.add(sess)
                return IssuedSession(token, csrf, _principal(u, sess), sess.expires_at)
        raise failure

    def resolve_session(self, token: str | None) -> Principal | None:
        """The principal for a session cookie, or None if it is unknown, revoked, idle too long or past its absolute limit."""
        if not token:
            return None
        now = self.clock()
        with session_scope(self.engine) as s:
            row = s.execute(select(AuthSession, User).join(User, User.id == AuthSession.user_id)
                            .where(AuthSession.token_hash == passwords.token_hash(token))).first()
            if row is None:
                return None
            sess, u = row
            if sess.revoked_at is not None or not u.is_active:
                return None
            if now >= sess.expires_at or now - sess.last_seen_at > self.idle:
                sess.revoked_at, sess.revoke_reason = now, "expired"
                return None
            if now - sess.last_seen_at > timedelta(seconds=30):        # avoid a write on every request
                sess.last_seen_at = now
            return _principal(u, sess)

    def logout(self, session_id: str) -> None:
        with session_scope(self.engine) as s:
            s.execute(update(AuthSession).where(AuthSession.id == session_id, AuthSession.revoked_at.is_(None))
                      .values(revoked_at=self.clock(), revoke_reason="logout"))

    # ---- service tokens --------------------------------------------------------------------------------------------------
    def create_service_token(self, name: str, created_by: str, scopes: list[str] | None = None,
                             days: int | None = None) -> tuple[str, dict[str, Any]]:
        """Returns the token once. Only its hash is stored, so it cannot be shown again."""
        name = name.strip().lower()
        if not USERNAME.match(name):
            raise AuthError(422, "INVALID_NAME", "Token names are 3-64 characters: letters, digits, dot, dash, underscore.")
        token, now = passwords.new_token(), self.clock()
        with session_scope(self.engine) as s:
            if s.execute(select(ServiceToken.id).where(ServiceToken.name == name, ServiceToken.revoked_at.is_(None))).first():
                raise AuthError(409, "TOKEN_EXISTS", f"An active token named {name} exists; revoke it first.")
            old = s.execute(select(ServiceToken).where(ServiceToken.name == name)).scalar_one_or_none()
            if old is not None:
                s.delete(old)
                s.flush()
            t = ServiceToken(id=str(uuid.uuid4()), name=name, token_hash=passwords.token_hash(token), scopes=scopes,
                             created_by=created_by, created_at=now, expires_at=now + timedelta(days=days) if days else None)
            s.add(t)
            return token, {"id": t.id, "name": name, "scopes": scopes, "expires_at": t.expires_at}

    def revoke_service_token(self, name: str) -> bool:
        with session_scope(self.engine) as s:
            res = s.execute(update(ServiceToken).where(ServiceToken.name == name.strip().lower(), ServiceToken.revoked_at.is_(None))
                            .values(revoked_at=self.clock()))
            return bool(res.rowcount)

    def list_service_tokens(self) -> list[dict[str, Any]]:
        with session_scope(self.engine) as s:
            return [{"name": t.name, "scopes": t.scopes, "created_by": t.created_by, "created_at": t.created_at,
                     "last_used_at": t.last_used_at, "expires_at": t.expires_at, "revoked_at": t.revoked_at}
                    for t in s.execute(select(ServiceToken).order_by(ServiceToken.name)).scalars()]

    def resolve_service_token(self, token: str | None) -> Principal | None:
        if not token:
            return None
        now = self.clock()
        with session_scope(self.engine) as s:
            t = s.execute(select(ServiceToken).where(ServiceToken.token_hash == passwords.token_hash(token))).scalar_one_or_none()
            if t is None or t.revoked_at is not None or (t.expires_at is not None and t.expires_at <= now):
                return None
            if t.last_used_at is None or now - t.last_used_at > timedelta(minutes=1):
                t.last_used_at = now
            scopes = frozenset(t.scopes) if t.scopes is not None else None
            return Principal(id=f"service:{t.name}", username=t.name, display_name=f"Service: {t.name}", role=Role.SERVICE,
                             kind="service", permissions=permissions_for(Role.SERVICE, scopes))

    # ---- helpers ---------------------------------------------------------------------------------------------------------
    def _dummy_hash(self) -> str:
        if self._dummy is None:
            self._dummy = passwords.hash_password("not-a-real-password", self.iterations)
        return self._dummy

    def _user(self, s: Any, username: str) -> User:
        u = s.execute(select(User).where(User.username == username.strip().lower())).scalar_one_or_none()
        if u is None:
            raise AuthError(404, "USER_NOT_FOUND", f"No user named {username}.")
        return u

    def _revoke_all(self, s: Any, user_id: str, reason: str) -> None:
        s.execute(update(AuthSession).where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
                  .values(revoked_at=self.clock(), revoke_reason=reason))


def _human_role(role: str) -> Role:
    try:
        r = Role(str(role).strip().lower())
    except ValueError as exc:
        raise AuthError(422, "INVALID_ROLE", "Role must be admin, trader, reviewer or observer.") from exc
    if r not in HUMAN_ROLES:
        raise AuthError(422, "INVALID_ROLE", "People cannot have the service role; create a service token instead.")
    return r


def _principal(u: User, sess: AuthSession) -> Principal:
    role = Role(u.role)
    return Principal(id=u.id, username=u.username, display_name=u.display_name, role=role, kind="user",
                     permissions=permissions_for(role),
                     account_scope=tuple(u.account_scope) if u.account_scope is not None else None,
                     session_id=sess.id, csrf_hash=sess.csrf_hash)


def _user_row(u: User) -> dict[str, Any]:
    return {"id": u.id, "username": u.username, "display_name": u.display_name, "email": u.email, "role": u.role,
            "is_active": u.is_active, "account_scope": u.account_scope, "last_login_at": u.last_login_at,
            "created_at": u.created_at}
