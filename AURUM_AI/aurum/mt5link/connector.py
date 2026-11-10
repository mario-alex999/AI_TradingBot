"""One connector process per linked account: logs a terminal slot in to the person's DEMO account, then mirrors it
read-only until they sign out, their session ends, or they disconnect.

    python -m aurum.mt5link.connector --connection-id ID --slot-folder DIR --login N --server NAME   (password on stdin)

The broker password arrives on stdin, is handed to ``MetaTrader5.initialize`` once and is never written to the
database, a file, the command line or a log. The terminal is reached through ``MT5Gateway`` (read-only wrapper and
DEMO-only check), so nothing here can place, modify or close an order.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.engine import Engine

from aurum.account.recorder import AccountRecorder
from aurum.config import Settings
from aurum.database.connection import session_scope
from aurum.mt5link.db_models import Mt5Connection

log = logging.getLogger(__name__)

POLL_SECONDS = 2.0          # how quickly a disconnect request or a sign-out is noticed


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ConnectionStore:
    """The connector's view of its own ``mt5_connections`` row (and of the sign-in session behind it)."""

    def __init__(self, engine: Engine, conn_id: str, idle_minutes: int, clock: Callable[[], datetime] = _now):
        self.engine, self.id, self.idle, self.clock = engine, conn_id, timedelta(minutes=idle_minutes), clock

    def _update(self, **values: Any) -> None:
        with session_scope(self.engine) as s:
            row = s.get(Mt5Connection, self.id)
            if row is not None:
                for k, v in values.items():
                    setattr(row, k, v)

    def connected(self, report: Any) -> None:
        now = self.clock()
        self._update(status="CONNECTED", connected_at=now, heartbeat_at=now, company=report.company[:128] or None,
                     currency=report.currency[:16] or None, leverage=report.leverage or None, trade_mode=report.trade_mode,
                     error_code=None, error_message=None)

    def failed(self, code: str, message: str) -> None:
        self._update(status="FAILED", error_code=code, error_message=message[:400], disconnected_at=self.clock(),
                     disconnect_reason="connect_failed")

    def synced(self, summary: dict[str, Any]) -> None:
        now = self.clock()
        self._update(heartbeat_at=now, last_sync_at=now, last_sync=summary, error_code=None, error_message=None)

    def sync_error(self, message: str) -> None:
        self._update(heartbeat_at=self.clock(), error_code="SYNC_FAILED", error_message=message[:400])

    def heartbeat(self) -> None:
        self._update(heartbeat_at=self.clock())

    def disconnected(self, reason: str) -> None:
        with session_scope(self.engine) as s:
            row = s.get(Mt5Connection, self.id)
            if row is not None and row.status not in ("FAILED", "LOST"):
                row.status, row.disconnected_at = "DISCONNECTED", self.clock()
                row.disconnect_reason = row.disconnect_reason or reason

    def stop_reason(self) -> str | None:
        """Why the connector should stop now, or None to keep going."""
        from aurum.auth.db_models import AuthSession, User
        now = self.clock()
        with session_scope(self.engine) as s:
            row = s.get(Mt5Connection, self.id)
            if row is None:
                return "removed"
            if row.status not in ("CONNECTING", "CONNECTED"):
                return row.disconnect_reason or "disconnect_requested"
            if row.session_id is None:
                return None
            found = s.execute(select(AuthSession, User.is_active).join(User, User.id == AuthSession.user_id)
                              .where(AuthSession.id == row.session_id)).first()
            if found is None:
                return "session_ended"
            sess, active = found
            if not active:
                return "user_disabled"
            if sess.revoked_at is not None:
                return "signed_out" if sess.revoke_reason == "logout" else "session_ended"
            if now >= sess.expires_at or now - sess.last_seen_at > self.idle:
                return "session_expired"
        return None


def run(conn_id: str, slot_folder: Path, login: int, server: str, password: str, engine: Engine, settings: Settings,
        mt5_module: Any = None, sleep: Callable[[float], None] = time.sleep, clock: Callable[[], datetime] = _now,
        max_syncs: int | None = None, terminal_ops: bool = True) -> int:
    """Connect, mirror until told to stop, always disconnect and free the slot. Returns a process exit code."""
    from aurum.gateways.mt5_gateway import MT5Gateway, MT5Error, NonDemoAccountError
    from aurum.mt5link import terminals

    store = ConnectionStore(engine, conn_id, settings.auth_session_idle_minutes, clock)
    exe = Path(slot_folder) / terminals.EXE
    if terminal_ops:
        terminals.stop_terminal(exe)      # never attach to a terminal still logged in to someone else's account
    gw_settings = settings.model_copy(update={"mt5_terminal_path": str(exe), "mt5_portable": True, "mt5_login": int(login),
                                              "mt5_password": SecretStr(password), "mt5_server": server})
    del password
    gw = MT5Gateway(gw_settings, mt5_module)
    reason = "stopped"
    try:
        try:
            report = gw.connect()
        except NonDemoAccountError:
            store.failed("NOT_DEMO", "This is not a DEMO account. AURUM only links DEMO accounts; nothing was synced.")
            return 3
        except MT5Error as exc:
            store.failed("CONNECT_FAILED", _public_error(str(exc)))
            return 2
        finally:
            gw.settings = settings.model_copy(update={"mt5_terminal_path": str(exe), "mt5_portable": True})  # drop the secret
            del gw_settings
        if report.login != int(login):
            store.failed("WRONG_ACCOUNT", f"The terminal logged in to account {report.login}, not {login}.")
            return 4
        store.connected(report)
        log.info("MT5 account linked", extra={"connection_id": conn_id, "login": report.login, "server": report.server})
        recorder = AccountRecorder(engine, login=int(login))
        syncs = 0
        while True:
            stop = store.stop_reason()
            if stop:
                reason = stop
                break
            try:
                summary = recorder.sync(gw, lookback_days=settings.mt5_link_lookback_days)
                store.synced({k: summary[k] for k in ("deals", "positions", "open", "cash_flows") if k in summary})
            except Exception as exc:  # noqa: BLE001 - keep the link; report the problem on the row
                log.warning("MT5 account sync failed", extra={"connection_id": conn_id, "error": type(exc).__name__})
                store.sync_error(_public_error(str(exc)))
            syncs += 1
            if max_syncs is not None and syncs >= max_syncs:
                reason = "test_limit"
                break
            waited = 0.0
            while waited < settings.mt5_link_sync_seconds:
                sleep(POLL_SECONDS)
                waited += POLL_SECONDS
                stop = store.stop_reason()
                if stop:
                    break
                store.heartbeat()
            if stop:
                reason = stop
                break
        return 0
    finally:
        gw.shutdown()
        if terminal_ops:
            terminals.stop_terminal(exe)
            terminals.forget_saved_logins(Path(slot_folder))
        store.disconnected(reason)
        log.info("MT5 account link ended", extra={"connection_id": conn_id, "reason": reason})


def _public_error(message: str) -> str:
    """Gateway messages name the terminal problem; they never contain the password, but keep them short."""
    return message.split(" For a step-by-step check")[0][:400]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--connection-id", required=True)
    ap.add_argument("--slot-folder", required=True)
    ap.add_argument("--login", required=True, type=int)
    ap.add_argument("--server", required=True)
    args = ap.parse_args(argv)
    password = sys.stdin.readline().rstrip("\r\n")
    sys.stdin.close()
    from aurum.config import get_settings
    from aurum.database.connection import engine_from_settings
    from aurum.logging_config import setup_logging
    settings = get_settings()
    setup_logging(settings.log_level, settings.log_dir, secrets=[password] if password else [])  # redacted if ever echoed
    engine = engine_from_settings(settings)
    try:
        return run(args.connection_id, Path(args.slot_folder), args.login, args.server, password, engine, settings)
    except Exception as exc:  # noqa: BLE001 - report the crash on the row instead of leaving it to time out as LOST
        log.exception("MT5 connector crashed", extra={"connection_id": args.connection_id})
        try:
            ConnectionStore(engine, args.connection_id, settings.auth_session_idle_minutes).failed(
                "CONNECTOR_ERROR", f"The MT5 connector failed ({type(exc).__name__}). See logs/mt5link-slot*.log on the server.")
        except Exception:  # noqa: BLE001
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
