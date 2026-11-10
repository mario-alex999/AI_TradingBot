"""Links a signed-in person's own MT5 DEMO account: picks a free terminal slot, starts a connector for it and reports
the status. Read-only and DEMO-only; broker passwords pass straight to the connector's stdin and are never kept.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from aurum.config import Settings
from aurum.database.connection import session_scope
from aurum.mt5link import terminals
from aurum.mt5link.db_models import ACTIVE, Mt5Connection

log = logging.getLogger(__name__)

HEARTBEAT_STALE_SECONDS = 90
SERVER_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._()\-]{1,127}$")


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Mt5LinkError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


class Launcher(Protocol):
    def start(self, conn_id: str, slot: terminals.Slot, login: int, server: str, password: str) -> int | None: ...
    def kill(self, pid: int) -> None: ...


class SubprocessLauncher:
    """Starts ``python -m aurum.mt5link.connector`` for one slot; the password goes to its stdin only."""

    def __init__(self, log_dir: Path):
        self.log_dir = Path(log_dir)

    def start(self, conn_id: str, slot: terminals.Slot, login: int, server: str, password: str) -> int | None:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        with open(self.log_dir / f"mt5link-slot{slot.number}.log", "ab") as err:
            proc = subprocess.Popen(
                [sys.executable, "-m", "aurum.mt5link.connector", "--connection-id", conn_id,
                 "--slot-folder", str(slot.folder), "--login", str(int(login)), "--server", server],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=err, close_fds=True, creationflags=flags)
        assert proc.stdin is not None
        proc.stdin.write((password + "\n").encode("utf-8"))
        proc.stdin.close()
        return proc.pid

    def kill(self, pid: int) -> None:
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(int(pid)), "/T", "/F"], capture_output=True, timeout=15, check=False)
            else:  # pragma: no cover
                os.kill(int(pid), 9)
        except Exception:  # noqa: BLE001 - already gone
            pass


def _cleanup_slot(slot: terminals.Slot) -> None:
    terminals.stop_terminal(slot.exe)
    terminals.forget_saved_logins(slot.folder)


def view(row: Mt5Connection | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {"id": row.id, "login": row.login, "server": row.server, "slot": row.slot, "status": row.status,
            "company": row.company, "currency": row.currency, "leverage": row.leverage, "trade_mode": row.trade_mode,
            "error_code": row.error_code, "error_message": row.error_message, "last_sync": row.last_sync,
            "created_at": row.created_at, "connected_at": row.connected_at, "last_sync_at": row.last_sync_at,
            "disconnected_at": row.disconnected_at, "disconnect_reason": row.disconnect_reason}


class Mt5LinkService:
    def __init__(self, engine: Engine, settings: Settings, launcher: Launcher | None = None,
                 clock: Callable[[], datetime] = _now, cleanup: Callable[[terminals.Slot], None] | None = None):
        self.engine, self.settings, self.clock = engine, settings, clock
        self.launcher = launcher or SubprocessLauncher(settings.log_dir)
        self.cleanup = cleanup or _cleanup_slot

    # ---- configuration
    def slots(self) -> list[terminals.Slot]:
        return terminals.slots(self.settings.mt5_slots_dir, self.settings.mt5_slot_count)

    def status(self) -> dict[str, Any]:
        slots = self.slots()
        self.reap()
        with session_scope(self.engine) as s:
            busy = s.execute(select(Mt5Connection.slot).where(Mt5Connection.status.in_(ACTIVE))).scalars().all()
        ready = [sl.number for sl in slots if sl.ready]
        return {"enabled": bool(ready), "slots": len(slots), "ready_slots": len(ready),
                "free_slots": len([n for n in ready if n not in set(busy)])}

    # ---- link / unlink
    def connect(self, principal: Any, login: Any, password: str, server: str) -> dict[str, Any]:
        if not getattr(principal, "is_human", False) or not principal.session_id:
            raise Mt5LinkError(403, "HUMAN_REQUIRED", "Only a signed-in person can link an MT5 account.")
        try:
            acct = int(str(login).strip())
        except ValueError:
            raise Mt5LinkError(422, "INVALID_LOGIN", "The MT5 account number must be digits only.") from None
        if acct <= 0 or acct >= 10 ** 12:
            raise Mt5LinkError(422, "INVALID_LOGIN", "The MT5 account number must be digits only.")
        server = (server or "").strip()
        if not SERVER_NAME.match(server):
            raise Mt5LinkError(422, "INVALID_SERVER", "Enter the broker server exactly as MetaTrader shows it, e.g. MetaQuotes-Demo.")
        if not password or len(password) > 128 or "\n" in password or "\r" in password:
            raise Mt5LinkError(422, "INVALID_PASSWORD", "Enter the account's password (up to 128 characters).")
        if not principal.may_access_account(acct):
            raise Mt5LinkError(403, "ACCOUNT_NOT_IN_SCOPE",
                               f"Account {acct} is not one of the accounts assigned to you. Ask an administrator to add it.")
        ready = [sl for sl in self.slots() if sl.ready]
        if not ready:
            raise Mt5LinkError(503, "MT5_LINK_NOT_CONFIGURED",
                               "No MT5 terminal slots are set up on the server yet. An administrator can run "
                               "`python -m aurum.cli mt5-slots setup --count N` and set MT5_SLOT_COUNT.")
        self.reap()
        now = self.clock()
        with session_scope(self.engine) as s:          # authorising the same account again is a no-op
            same = s.execute(select(Mt5Connection).where(Mt5Connection.session_id == principal.session_id,
                                                         Mt5Connection.login == acct,
                                                         Mt5Connection.status.in_(("CONNECTING", "CONNECTED")))).scalar_one_or_none()
            if same is not None:
                return view(same)
        self._end_previous(principal)
        conn_id = str(uuid.uuid4())
        try:
            with session_scope(self.engine) as s:
                if s.execute(select(Mt5Connection.id).where(Mt5Connection.login == acct,
                                                            Mt5Connection.status.in_(ACTIVE))).first():
                    raise Mt5LinkError(409, "ACCOUNT_IN_USE", f"Account {acct} is already linked in another session. "
                                                              "Disconnect it there first, or wait a minute and try again.")
                busy = set(s.execute(select(Mt5Connection.slot).where(Mt5Connection.status.in_(ACTIVE))).scalars().all())
                free = next((sl for sl in ready if sl.number not in busy), None)
                if free is None:
                    raise Mt5LinkError(503, "ALL_SLOTS_BUSY", f"All {len(ready)} MT5 terminal slot(s) are in use by other people. "
                                                              "Try again later, or ask an administrator to add slots.")
                s.add(Mt5Connection(id=conn_id, user_id=principal.id, session_id=principal.session_id, login=acct,
                                    server=server, slot=free.number, status="CONNECTING", created_at=now, updated_at=now))
        except IntegrityError:
            raise Mt5LinkError(409, "SLOT_RACE", "Another link was being set up at the same moment. Try again.") from None
        try:
            pid = self.launcher.start(conn_id, free, acct, server, password)
        except Exception as exc:  # noqa: BLE001
            log.error("Could not start the MT5 connector", extra={"connection_id": conn_id, "error": type(exc).__name__})
            self._set(conn_id, status="FAILED", error_code="LAUNCH_FAILED", error_message="The server could not start the MT5 connector.",
                      disconnected_at=self.clock(), disconnect_reason="launch_failed")
            raise Mt5LinkError(500, "LAUNCH_FAILED", "The server could not start the MT5 connector.") from None
        finally:
            del password
        if pid:
            self._set(conn_id, pid=int(pid))
        log.info("MT5 link requested", extra={"connection_id": conn_id, "user": principal.username, "login": acct,
                                              "slot": free.number})
        return self.get(conn_id)

    def get(self, conn_id: str) -> dict[str, Any] | None:
        with session_scope(self.engine) as s:
            return view(s.get(Mt5Connection, conn_id))

    def current(self, principal: Any) -> dict[str, Any] | None:
        """This sign-in session's most recent link (so a person sees their own link, never someone else's)."""
        if not principal.session_id:
            return None
        self.reap()
        with session_scope(self.engine) as s:
            row = s.execute(select(Mt5Connection).where(Mt5Connection.user_id == principal.id,
                                                        Mt5Connection.session_id == principal.session_id)
                            .order_by(Mt5Connection.created_at.desc()).limit(1)).scalar_one_or_none()
            return view(row)

    def disconnect(self, principal: Any, reason: str = "user_disconnected") -> dict[str, Any] | None:
        if principal.session_id:
            self.disconnect_session(principal.session_id, reason)
        return self.current(principal)

    def disconnect_session(self, session_id: str, reason: str) -> int:
        """Ask every connector of this sign-in session to stop (it notices within a couple of seconds, logs the terminal
        out and frees the slot). A link still CONNECTING whose process is gone is closed straight away."""
        now = self.clock()
        with session_scope(self.engine) as s:
            rows = s.execute(select(Mt5Connection).where(Mt5Connection.session_id == session_id,
                                                         Mt5Connection.status.in_(("CONNECTING", "CONNECTED")))).scalars().all()
            for r in rows:
                r.status, r.disconnect_reason, r.updated_at = "DISCONNECTING", reason, now
            return len(rows)

    def _end_previous(self, principal: Any) -> None:
        self.disconnect_session(principal.session_id, "replaced")

    # ---- housekeeping
    def reap(self) -> int:
        """Links whose connector stopped reporting (crash, reboot, killed) become LOST and free their slot."""
        now = self.clock()
        stale_hb = now - timedelta(seconds=HEARTBEAT_STALE_SECONDS)
        stale_connect = now - timedelta(seconds=self.settings.mt5_link_connect_timeout_seconds)
        lost: list[tuple[int | None, int]] = []
        with session_scope(self.engine) as s:
            rows = s.execute(select(Mt5Connection).where(Mt5Connection.status.in_(ACTIVE))).scalars().all()
            for r in rows:
                seen = r.heartbeat_at or r.created_at
                limit = stale_connect if r.status == "CONNECTING" else stale_hb
                if seen < limit:
                    r.status, r.disconnected_at, r.updated_at = "LOST", now, now
                    r.disconnect_reason = r.disconnect_reason or "no_heartbeat"
                    if r.error_code is None:
                        r.error_code, r.error_message = "CONNECTOR_LOST", "The MT5 connector stopped responding."
                    lost.append((r.pid, r.slot))
        for pid, slot in lost:
            if pid:
                self.launcher.kill(pid)
            sl = next((x for x in self.slots() if x.number == slot), None)
            if sl is not None:
                self.cleanup(sl)
        return len(lost)

    def _set(self, conn_id: str, **values: Any) -> None:
        with session_scope(self.engine) as s:
            row = s.get(Mt5Connection, conn_id)
            if row is not None:
                for k, v in values.items():
                    setattr(row, k, v)
                row.updated_at = self.clock()
