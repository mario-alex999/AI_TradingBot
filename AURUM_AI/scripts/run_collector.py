"""AURUM AI - collector supervisor (keeps `aurum.cli collect --loop` running).

Started at Windows sign-in by the "AURUM AI Collector" scheduled task (scripts/install_autostart.ps1),
or by hand:  .\\.venv\\Scripts\\pythonw.exe scripts\\run_collector.py

What it does, forever:
  1. Honour the on/off switch: while the file ``COLLECTOR_DISABLED`` exists in the project root, nothing runs
     (a running collector is stopped). Delete the file to resume.
  2. Make sure Docker Desktop is running and the TimescaleDB container is up (only when the database is
     PostgreSQL; SQLite needs nothing).
  3. Run ``python -m aurum.cli collect --loop``. MT5 is opened via MT5_TERMINAL_PATH and must be logged in to
     a DEMO account; if it is not (or the connection, database or broker clock check fails) the collector
     exits, and this supervisor retries with back-off (30 s doubling to 10 min; reset after a healthy run).

Only one supervisor can run at a time (lock file ``logs/supervisor.lock``). Output: ``logs/supervisor.log``
(supervisor events) and ``logs/collector.out.txt`` (collector console output, rotated at 20 MB).
"""
from __future__ import annotations

import msvcrt
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOGS = ROOT / "logs"
DISABLED_FLAG = ROOT / "COLLECTOR_DISABLED"
DOCKER_EXE = Path(r"C:\Program Files\Docker\Docker\resources\bin\docker.exe")
DOCKER_DESKTOP = Path(r"C:\Program Files\Docker\Docker\Docker Desktop.exe")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
MIN_BACKOFF, MAX_BACKOFF, HEALTHY_RUN_S = 30, 600, 900
MAX_OUT_BYTES = 20 * 1024 * 1024

sys.path.insert(0, str(ROOT))


def log(msg: str) -> None:
    line = f"{datetime.now().astimezone().isoformat(timespec='seconds')} {msg}\n"
    with open(LOGS / "supervisor.log", "a", encoding="utf-8") as f:
        f.write(line)


def acquire_lock():
    LOGS.mkdir(exist_ok=True)
    fh = open(LOGS / "supervisor.lock", "a+")
    try:
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        return None
    return fh  # keep open for the life of the process


def run(cmd: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=timeout, creationflags=NO_WINDOW)


def uses_postgres() -> bool:
    from aurum.config import Settings
    return Settings().sqlalchemy_url.startswith("postgresql")


def docker_ready() -> bool:
    try:
        return DOCKER_EXE.exists() and run([str(DOCKER_EXE), "info", "--format", "{{.ServerVersion}}"], 30).returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def ensure_database() -> bool:
    """Start Docker Desktop and the TimescaleDB container if needed. True when the DB is reachable."""
    if not uses_postgres():
        return True
    if not docker_ready():
        if DOCKER_DESKTOP.exists():
            log("Docker engine not running - starting Docker Desktop")
            subprocess.Popen([str(DOCKER_DESKTOP)], creationflags=NO_WINDOW)
        for _ in range(60):          # up to ~5 min for the engine
            if docker_ready():
                break
            time.sleep(5)
        else:
            log("Docker engine still not ready")
            return False
    try:
        r = run([str(DOCKER_EXE), "compose", "--env-file", ".env", "up", "-d"], 300)
        if r.returncode != 0:
            log(f"docker compose up failed: {r.stderr.strip()[-400:]}")
            return False
    except subprocess.TimeoutExpired:
        log("docker compose up timed out")
        return False
    from aurum.config import Settings
    from aurum.database.connection import check_database, engine_from_settings
    for _ in range(30):              # container healthy -> accepting connections
        try:
            if check_database(engine_from_settings(Settings()))["schema_ready"]:
                return True
            log("Database reachable but schema missing - run: python -m aurum.cli init-db")
            return False
        except Exception:
            time.sleep(5)
    log("Database not reachable after starting the container")
    return False


def rotate_output(path: Path) -> None:
    if path.exists() and path.stat().st_size > MAX_OUT_BYTES:
        old = path.with_suffix(".old.txt")
        old.unlink(missing_ok=True)
        path.rename(old)


def main() -> int:
    lock = acquire_lock()
    if lock is None:
        return 0                    # another supervisor is already running
    python = Path(sys.executable).with_name("python.exe")
    log(f"Supervisor started (pid {os.getpid()})")
    backoff = MIN_BACKOFF
    while True:
        if DISABLED_FLAG.exists():
            time.sleep(15)
            continue
        if not ensure_database():
            log(f"Database not ready - retrying in {backoff}s")
            time.sleep(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF)
            continue
        out_path = LOGS / "collector.out.txt"
        rotate_output(out_path)
        started = time.monotonic()
        with open(out_path, "a", encoding="utf-8", errors="replace") as out:
            out.write(f"\n===== collector start {datetime.now().astimezone().isoformat(timespec='seconds')} =====\n")
            out.flush()
            env = {**os.environ, "PYTHONIOENCODING": "utf-8", "COLUMNS": "120"}
            child = subprocess.Popen([str(python), "-m", "aurum.cli", "collect", "--loop"], cwd=ROOT, stdout=out,
                                     stderr=subprocess.STDOUT, creationflags=NO_WINDOW, env=env)
            (LOGS / "collector.pid").write_text(str(child.pid), encoding="ascii")
            log(f"Collector started (pid {child.pid})")
            while child.poll() is None:
                if DISABLED_FLAG.exists():
                    log("COLLECTOR_DISABLED found - stopping collector")
                    child.terminate()
                    try:
                        child.wait(30)
                    except subprocess.TimeoutExpired:
                        child.kill()
                    break
                time.sleep(10)
        ran = time.monotonic() - started
        if DISABLED_FLAG.exists():
            continue
        if ran >= HEALTHY_RUN_S:
            backoff = MIN_BACKOFF
        log(f"Collector exited with code {child.returncode} after {int(ran)}s "
            f"(MT5 not logged in / disconnected, DB or clock problem - see logs/collector.out.txt); "
            f"restarting in {backoff}s")
        time.sleep(backoff)
        backoff = min(backoff * 2, MAX_BACKOFF)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # pragma: no cover - last-resort logging (pythonw has no console)
        log(f"Supervisor crashed: {type(exc).__name__}: {exc}")
        raise
