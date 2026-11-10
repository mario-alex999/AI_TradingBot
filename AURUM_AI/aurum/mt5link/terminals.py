"""Terminal slots: portable copies of the installed MetaTrader 5 terminal, one per simultaneously linked account.

The MetaTrader5 Python package drives one terminal per process, and a terminal is logged in to one account at a time,
so N people linked at once need N terminal copies. A slot is ``<mt5_slots_dir>/slot<N>/terminal64.exe`` started in
portable mode (its data stays inside the slot folder).
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_SOURCE = Path(r"C:\Program Files\MetaTrader 5")
EXE = "terminal64.exe"
# Copying these would carry another account's history, logs or saved logins into the slot.
_SKIP = shutil.ignore_patterns("logs", "Logs", "bases", "Bases", "Tester", "tester", "accounts.dat", "*.log")


@dataclass(frozen=True)
class Slot:
    number: int
    folder: Path

    @property
    def exe(self) -> Path:
        return self.folder / EXE

    @property
    def ready(self) -> bool:
        return self.exe.is_file()


def slots(slots_dir: Path, count: int) -> list[Slot]:
    return [Slot(n, Path(slots_dir) / f"slot{n}") for n in range(1, count + 1)]


def find_servers_dat() -> Path | None:
    """The broker server list of the installed terminal (so a fresh slot can resolve server names such as
    'MetaQuotes-Demo'). The most recently changed copy wins."""
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    found = [p for p in (Path(appdata) / "MetaQuotes" / "Terminal").glob("*/config/servers.dat") if p.is_file()]
    return max(found, key=lambda p: p.stat().st_mtime) if found else None


def setup_slots(source: Path, slots_dir: Path, count: int, servers_dat: Path | None = None) -> list[dict]:
    """Create (or complete) ``count`` slots by copying the installed terminal. Existing slots are left as they are."""
    if not (source / EXE).is_file():
        raise FileNotFoundError(f"{source / EXE} not found. Install MetaTrader 5 or pass --source.")
    servers_dat = servers_dat if servers_dat is not None else find_servers_dat()
    out = []
    for slot in slots(slots_dir, count):
        created = False
        if not slot.ready:
            shutil.copytree(source, slot.folder, ignore=_SKIP, dirs_exist_ok=True)
            created = True
        cfg = slot.folder / "Config"
        cfg.mkdir(parents=True, exist_ok=True)
        copied = False
        if servers_dat and servers_dat.is_file() and not (cfg / "servers.dat").exists():
            shutil.copy2(servers_dat, cfg / "servers.dat")
            copied = True
        out.append({"slot": slot.number, "folder": str(slot.folder), "created": created, "serversDat": copied or (cfg / "servers.dat").exists()})
    return out


def stop_terminal(exe: Path) -> None:
    """End the terminal running from this slot (so the next person never inherits a logged-in account).
    The path goes through an environment variable, never into the command text."""
    if os.name != "nt":
        return
    env = {**os.environ, "AURUM_SLOT_EXE": str(Path(exe).resolve())}
    cmd = ("Get-Process terminal64 -ErrorAction SilentlyContinue | "
           "Where-Object { $_.Path -and ((Resolve-Path $_.Path).Path -eq $env:AURUM_SLOT_EXE) } | Stop-Process -Force")
    try:
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd], env=env, timeout=30,
                       capture_output=True, check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception as exc:  # noqa: BLE001 - best effort; the slot is still released
        log.warning("Could not stop the slot terminal: %s", type(exc).__name__)


def forget_saved_logins(folder: Path) -> None:
    """Remove the slot terminal's saved-account file, so no broker credential stays on disk after a disconnect."""
    for name in ("accounts.dat",):
        for p in (Path(folder) / "Config" / name, Path(folder) / "config" / name):
            try:
                p.unlink(missing_ok=True)
            except OSError as exc:
                log.warning("Could not remove %s: %s", p.name, type(exc).__name__)
