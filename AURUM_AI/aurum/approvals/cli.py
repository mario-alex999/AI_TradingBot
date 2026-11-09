"""CLI for Module 07.

python -m aurum.cli users create alice --role admin          (password asked twice, hidden; never an argument)
python -m aurum.cli users list | disable NAME | enable NAME | set-role NAME ROLE | reset-password NAME
python -m aurum.cli users token-create n8n --scope events:publish     (token printed once)
python -m aurum.cli users token-list | token-revoke NAME
python -m aurum.cli approvals worker [--interval 15]         expiry, Module 05 sync, handoff, outbox, notifications
python -m aurum.cli approvals tick | sync | expire | status

Nothing here approves a trade: decisions need a signed-in person in the web app.
"""
from __future__ import annotations

import time
from typing import Any, Optional

import typer

from aurum.ui import Table, console, error, kv_table

users_app = typer.Typer(help="People and service tokens for Module 07 sign-in.", no_args_is_help=True)
approvals_app = typer.Typer(help="Module 07 approval worker and status. Never approves or places trades.", no_args_is_help=True)


def _fail(msg: str) -> None:
    error(msg)
    raise typer.Exit(1)


def _ctx() -> tuple[Any, Any]:
    from aurum.config import get_settings
    from aurum.database.connection import engine_from_settings
    s = get_settings()
    return s, engine_from_settings(s)


def _auth() -> Any:
    from aurum.auth.service import AuthService
    s, engine = _ctx()
    return AuthService.from_settings(engine, s)


def _guard(fn: Any) -> Any:
    from aurum.auth.service import AuthError
    try:
        return fn()
    except AuthError as exc:
        _fail(exc.message)


def _password(username: str) -> str:
    pw = typer.prompt(f"Password for {username}", hide_input=True, confirmation_prompt=True)
    from aurum.auth.passwords import check_strength
    try:
        check_strength(pw, username)
    except ValueError as exc:
        _fail(str(exc))
    return pw


# ---- users ------------------------------------------------------------------------------------------------------------------
@users_app.command("create")
def create(username: str, role: str = typer.Option(..., "--role", help="admin | trader | reviewer | observer"),
           display_name: Optional[str] = typer.Option(None, "--name"), email: Optional[str] = typer.Option(None, "--email"),
           account: Optional[list[str]] = typer.Option(None, "--account", help="Restrict to these account logins (repeat)")) -> None:
    pw = _password(username)
    u = _guard(lambda: _auth().create_user(username, pw, role, display_name, email, account or None))
    console.print(kv_table("User created", [("Username", u["username"]), ("Role", u["role"]),
                                            ("Accounts", ", ".join(u.get("account_scope") or []) or "all")]))


@users_app.command("list")
def list_users() -> None:
    t = Table(title="Users")
    for c in ("Username", "Name", "Role", "Active", "Accounts", "Last sign-in"):
        t.add_column(c)
    for u in _auth().list_users():
        t.add_row(u["username"], u.get("display_name") or "", u["role"], "yes" if u["is_active"] else "no",
                  ", ".join(u.get("account_scope") or []) or "all", str(u.get("last_login_at") or ""))
    console.print(t)


@users_app.command("disable")
def disable(username: str) -> None:
    _guard(lambda: _auth().set_active(username, False))
    console.print(f"{username} disabled; their sessions are ended.")


@users_app.command("enable")
def enable(username: str) -> None:
    _guard(lambda: _auth().set_active(username, True))
    console.print(f"{username} enabled.")


@users_app.command("set-role")
def set_role(username: str, role: str) -> None:
    _guard(lambda: _auth().set_role(username, role))
    console.print(f"{username} is now {role}.")


@users_app.command("reset-password")
def reset_password(username: str) -> None:
    pw = _password(username)
    _guard(lambda: _auth().set_password(username, pw))
    console.print(f"Password changed for {username}; their sessions are ended.")


@users_app.command("token-create")
def token_create(name: str, scope: list[str] = typer.Option(..., "--scope", help="events:publish, execution:read, "
                                                                                  "execution:report, approvals:view ..."),
                 days: Optional[int] = typer.Option(None, "--days", min=1)) -> None:
    token, info = _guard(lambda: _auth().create_service_token(name, "cli", list(scope), days))
    console.print(kv_table("Service token created", [("Name", info["name"]), ("Scopes", ", ".join(info["scopes"])),
                                                     ("Expires", str(info.get("expires_at") or "never"))]))
    console.print("Copy it now; it is not stored and cannot be shown again:")
    console.print(token, markup=False, highlight=False)


@users_app.command("token-list")
def token_list() -> None:
    t = Table(title="Service tokens")
    for c in ("Name", "Scopes", "Created", "Last used", "Expires", "Revoked"):
        t.add_column(c)
    for k in _auth().list_service_tokens():
        t.add_row(k["name"], ", ".join(k["scopes"]), str(k["created_at"]), str(k.get("last_used_at") or ""),
                  str(k.get("expires_at") or ""), str(k.get("revoked_at") or ""))
    console.print(t)


@users_app.command("token-revoke")
def token_revoke(name: str) -> None:
    if not _guard(lambda: _auth().revoke_service_token(name)):
        _fail(f"No active token named {name}.")
    console.print(f"Token {name} revoked.")


# ---- approvals --------------------------------------------------------------------------------------------------------------
def _worker() -> Any:
    from aurum.approvals.wiring import build_service
    from aurum.approvals.worker import Worker
    s, engine = _ctx()
    return Worker(build_service(engine, s), s)


@approvals_app.command("worker")
def worker(interval: Optional[int] = typer.Option(None, "--interval", min=5, help="Seconds between ticks "
                                                                                  "(default APPROVALS_WORKER_INTERVAL_SECONDS)")) -> None:
    """Runs until Ctrl+C. Safe to run more than one: every step is idempotent and serialised in the database."""
    w = _worker()
    every = interval or int(getattr(w.settings, "approvals_worker_interval_seconds", 15))
    console.print(f"[muted]Module 07 worker every {every}s · Ctrl+C to stop. It never approves or executes.[/]")
    try:
        while True:
            out = w.tick()
            busy = {k: v for k, v in out.items() if isinstance(v, dict) and any(isinstance(x, int) and x for x in v.values())
                    or (isinstance(v, int) and v) or (isinstance(v, dict) and "error" in v)}
            if busy:
                console.print(busy)
            time.sleep(every)
    except KeyboardInterrupt:
        console.print("Stopped by user.")


@approvals_app.command("tick")
def tick() -> None:
    """One worker pass."""
    console.print(_worker().tick())


@approvals_app.command("sync")
def sync() -> None:
    """Take new Module 05 candidates in now."""
    console.print(_worker().svc.sync_module05())


@approvals_app.command("expire")
def expire() -> None:
    console.print(_worker().svc.expire_due())


@approvals_app.command("status")
def status() -> None:
    h = _worker().svc.health()
    if not h.get("schema_ready"):
        _fail(h.get("detail", "Module 07 tables are missing."))
    console.print(kv_table("Module 07", [("Status", h["status"]), ("Audit chain", "valid" if h["audit_chain"]["valid"] else
                                                                     f"BROKEN: {h['audit_chain']['error']}"),
                                         ("Audit rows", h["audit_chain"]["rows"]),
                                         ("Outbox pending / dead", f"{h['outbox']['pending']} / {h['outbox']['dead']}"),
                                         ("Module 06", "ok" if h["dependencies"]["module_06"]["ok"] else "unavailable"),
                                         ("Module 09", h["dependencies"]["module_09"]["detail"])]))
