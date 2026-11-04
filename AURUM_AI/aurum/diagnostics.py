"""Read-only, step-by-step diagnosis of the MT5 connection and the database connection.

``python -m aurum.cli diagnose`` runs every step and names the first thing that is actually wrong, e.g. MT5 error -6
(no authorised account session) versus a terminal that is not running, or a database that refuses the password
versus one whose service is not running.

Safety: MT5 is only reached through :class:`~aurum.gateways.mt5_gateway.ReadOnlyMT5` (read functions only; this
module does not even add symbols to Market Watch). The account is never changed, the database is never created,
dropped or migrated, and no password, token or full database URL is ever printed - error texts are scrubbed.
"""
from __future__ import annotations

import importlib.metadata
import platform
import shutil
import socket
import struct
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from aurum.config import InstrumentConfig, Settings

OK, WARN, FAIL, SKIP = "ok", "warning", "failed", "skipped"


@dataclass
class Step:
    area: str            # "mt5" | "database"
    name: str
    status: str          # ok | warning | failed | skipped
    detail: str
    hint: str = ""


class _Report:
    def __init__(self, area: str, secrets: Iterable[str] = ()):
        self.area = area
        self.steps: list[Step] = []
        self.secrets = [s for s in secrets if s]

    def scrub(self, text: Any) -> str:
        out = str(text)
        for s in self.secrets:
            out = out.replace(s, "***")
        return out

    def add(self, name: str, status: str, detail: Any, hint: str = "") -> Step:
        st = Step(self.area, name, status, self.scrub(detail), self.scrub(hint))
        self.steps.append(st)
        return st


def _secrets(s: Settings) -> list[str]:
    vals = [x.get_secret_value() for x in (s.mt5_password, s.db_password, s.database_url, s.aurum_admin_api_key)
            if x is not None]
    from urllib.parse import quote_plus
    return vals + [quote_plus(v) for v in vals]


# =============================================================================================== MT5
def _running_terminals() -> list[str] | None:
    """Paths of running terminal64.exe processes (Windows), [] if none, None if it cannot be determined."""
    if platform.system() != "Windows":
        return None
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "Get-Process terminal64 -ErrorAction SilentlyContinue | ForEach-Object { $_.Path }"],
            capture_output=True, text=True, timeout=20)
        return [ln.strip() for ln in out.stdout.splitlines() if ln.strip()]
    except (OSError, subprocess.SubprocessError):
        return None


def _same_path(a: str, b: str) -> bool:
    try:
        return Path(a).resolve().as_posix().lower() == Path(b).resolve().as_posix().lower()
    except OSError:
        return a.lower() == b.lower()


def diagnose_mt5(settings: Settings, instruments: list[InstrumentConfig], mt5_module: Any | None = None,
                 process_lister: Callable[[], list[str] | None] = _running_terminals) -> list[Step]:
    from aurum.gateways.mt5_gateway import ACCOUNT_TRADE_MODES, ReadOnlyMT5, explain_mt5_error, load_mt5_module

    r = _Report("mt5", _secrets(settings))
    bits = struct.calcsize("P") * 8
    py = f"Python {platform.python_version()} {bits}-bit on {platform.system()} ({sys.executable})"
    if platform.system() != "Windows" and mt5_module is None:
        # Only the real MetaTrader5 package needs Windows; a supplied module (tests, simulators) runs anywhere.
        r.add("Python interpreter", FAIL, py, "The MetaTrader5 package only runs on Windows.")
        return r.steps
    r.add("Python interpreter", OK if bits == 64 else FAIL, py,
          "" if bits == 64 else "Install 64-bit Python: the MetaTrader5 package needs a 64-bit interpreter.")

    # package
    try:
        mod = mt5_module if mt5_module is not None else load_mt5_module()
    except Exception as exc:  # noqa: BLE001
        r.add("MetaTrader5 package", FAIL, exc, "Run: .\\.venv\\Scripts\\python.exe -m pip install MetaTrader5")
        return r.steps
    try:
        pkg_version = importlib.metadata.version("MetaTrader5")
    except importlib.metadata.PackageNotFoundError:
        pkg_version = getattr(mod, "__version__", "unknown")
    r.add("MetaTrader5 package", OK, f"installed, version {pkg_version}")
    mt5 = ReadOnlyMT5(mod)

    # terminal path + process
    path = settings.mt5_terminal_path
    if not path:
        r.add("Terminal path", WARN, "MT5_TERMINAL_PATH is empty - initialize() attaches to the last-used terminal",
              "Set MT5_TERMINAL_PATH to the full path of terminal64.exe so the right terminal is always used.")
    elif not Path(path).is_file():
        r.add("Terminal path", FAIL, f"{path} does not exist or is not a file",
              "MT5_TERMINAL_PATH must be the full path of terminal64.exe, e.g. "
              "C:\\Program Files\\MetaTrader 5\\terminal64.exe (not the folder).")
    elif Path(path).name.lower() != "terminal64.exe":
        r.add("Terminal path", WARN, f"{path} is not terminal64.exe", "Point MT5_TERMINAL_PATH at terminal64.exe.")
    else:
        r.add("Terminal path", OK, path)
    running = process_lister()
    if running is None:
        r.add("Terminal process", SKIP, "could not list processes")
    elif not running:
        r.add("Terminal process", WARN, "no terminal64.exe is running",
              "initialize() will try to start it; open the terminal yourself and log in to the DEMO account first.")
    elif path and Path(path).is_file() and not any(_same_path(p, path) for p in running):
        r.add("Terminal process", WARN, f"running terminal(s): {', '.join(running)} - not the configured one",
              "AURUM uses the terminal at MT5_TERMINAL_PATH. Log in there, or point the setting at the running one.")
    else:
        r.add("Terminal process", OK, f"running: {', '.join(running)}")

    # login configuration (never the password)
    if settings.mt5_login is None:
        r.add("Login configuration", OK, "MT5_LOGIN empty: the account already logged in to the terminal is used "
                                         "(AURUM never logs in or switches accounts by itself)")
    else:
        missing = [n for n, v in (("MT5_PASSWORD", settings.mt5_password), ("MT5_SERVER", settings.mt5_server)) if not v]
        r.add("Login configuration", FAIL if missing else OK,
              f"MT5_LOGIN={settings.mt5_login}, MT5_SERVER={settings.mt5_server or '<empty>'}, password "
              f"{'set' if settings.mt5_password else 'missing'}",
              f"Set {' and '.join(missing)} or clear MT5_LOGIN to use the terminal's own login." if missing else "")

    # initialize
    kwargs: dict[str, Any] = {"timeout": settings.mt5_timeout_ms}
    if path:
        kwargs["path"] = path
    if settings.mt5_login is not None and settings.mt5_password and settings.mt5_server:
        kwargs.update(login=int(settings.mt5_login), password=settings.mt5_password.get_secret_value(),
                      server=settings.mt5_server)
    try:
        ok = mt5.initialize(**kwargs)
    except Exception as exc:  # noqa: BLE001
        r.add("Connect (initialize)", FAIL, f"initialize() raised {type(exc).__name__}: {exc}")
        return r.steps
    if not ok:
        code, msg = _last_error(mt5)
        r.add("Connect (initialize)", FAIL, f"error {code}: {msg}", explain_mt5_error(code))
        _shutdown(mt5)
        return r.steps
    try:
        ver = mt5.version()
        r.add("Connect (initialize)", OK, f"terminal API reachable (terminal version {ver[1] if ver else '?'})")
        term = mt5.terminal_info()
        if term is None:
            code, msg = _last_error(mt5)
            r.add("Terminal status", FAIL, f"terminal_info() returned nothing (error {code}: {msg})",
                  explain_mt5_error(code))
            return r.steps
        tpath = str(getattr(term, "path", ""))
        if not getattr(term, "connected", False):
            r.add("Terminal status", FAIL, f"terminal at {tpath} is running but NOT connected to the broker server",
                  "Check the internet connection and the server in the terminal's status bar (bottom right); "
                  "use File > Login to Trade Account if it shows 'Invalid account'.")
            return r.steps
        r.add("Terminal status", OK, f"connected to the broker ({getattr(term, 'name', '')}, build "
                                     f"{getattr(term, 'build', '?')}, {tpath})")
        acc = mt5.account_info()
        if acc is None:
            code, msg = _last_error(mt5)
            r.add("Account authorisation", FAIL, f"no account session (error {code}: {msg})",
                  explain_mt5_error(code if code is not None and code < 0 else -6))
            return r.steps
        mode = ACCOUNT_TRADE_MODES.get(getattr(acc, "trade_mode", -1), f"UNKNOWN({getattr(acc, 'trade_mode', None)})")
        demo = getattr(acc, "trade_mode", None) == getattr(mt5, "ACCOUNT_TRADE_MODE_DEMO", 0)
        r.add("Account authorisation", OK if demo else FAIL,
              f"account {acc.login} on {acc.server} ({getattr(acc, 'company', '')}) - {mode}",
              "" if demo else "AURUM only works with DEMO accounts. Log the terminal in to a demo account; AURUM "
                              "will not change the account for you.")
        if not demo:
            return r.steps
        _symbols_and_history(r, mt5, settings, instruments)
    finally:
        _shutdown(mt5)
    return r.steps


def _symbols_and_history(r: _Report, mt5: Any, settings: Settings, instruments: list[InstrumentConfig]) -> None:
    tf_const = {tf: getattr(mt5, f"TIMEFRAME_{tf}", None) for tf in settings.enabled_timeframes}
    for inst in instruments:
        info = mt5.symbol_info(inst.broker_symbol)
        mapping = (f"{inst.symbol} -> {inst.broker_symbol}" if inst.symbol != inst.broker_symbol else inst.symbol)
        if info is None:
            cands = _candidates(mt5, inst.symbol)
            r.add(f"Symbol {inst.symbol}", FAIL, f"{mapping}: broker symbol '{inst.broker_symbol}' not offered by this "
                                                 f"account", (f"Symbols on this server that contain '{inst.symbol}': "
                                                             f"{', '.join(cands)}. Set the right one as broker_symbol in "
                                                             f"config/instruments.yaml (AURUM never substitutes it)."
                                                             if cands else "No similar symbol found on this server."))
            continue
        visible = bool(getattr(info, "visible", True))
        r.add(f"Symbol {inst.symbol}", OK if visible else WARN,
              f"{mapping} (mapping from config/instruments.yaml), {'shown' if visible else 'NOT shown'} in Market Watch",
              "" if visible else "Collection adds it to Market Watch automatically; diagnose leaves it unchanged.")
        bars = []
        for tf, const in tf_const.items():
            rates = mt5.copy_rates_from_pos(inst.broker_symbol, const, 0, 10) if const is not None else None
            bars.append((tf, 0 if rates is None else len(rates)))
        empty = [tf for tf, n in bars if n == 0]
        r.add(f"History {inst.symbol}", OK if not empty else WARN,
              ", ".join(f"{tf}: {n} recent bars" for tf, n in bars),
              "" if not empty else (f"No bars for {', '.join(empty)}. Open a chart of {inst.broker_symbol} on that "
                                    f"timeframe once so the terminal downloads history, or check Tools > Options > "
                                    f"Charts > 'Max bars in chart'."))


def _candidates(mt5: Any, base: str) -> list[str]:
    try:
        found = mt5.symbols_get(group=f"*{base}*") or ()
    except Exception:  # noqa: BLE001 - optional helper
        return []
    return sorted(str(getattr(s, "name", s)) for s in found)[:10]


def _last_error(mt5: Any) -> tuple[int | None, str]:
    try:
        code, msg = mt5.last_error()
        return int(code), str(msg)
    except Exception:  # noqa: BLE001
        return None, "unknown error"


def _shutdown(mt5: Any) -> None:
    try:
        mt5.shutdown()
    except Exception:  # noqa: BLE001
        pass


# ========================================================================================== database
def classify_db_error(message: str) -> tuple[str, str]:
    """(category, hint) for a database connection error message."""
    m = message.lower()
    if "password authentication failed" in m or ("role" in m and "does not exist" in m):
        return "authentication", ("The server rejected DB_USER/DB_PASSWORD. Note: the Docker volume keeps the password "
                                  "it was first created with - changing DB_PASSWORD in .env later does not change it.")
    if "database" in m and "does not exist" in m:
        return "missing database", ("The server is reachable but DB_NAME does not exist. AURUM never creates or "
                                    "recreates a database automatically: check DB_NAME, or create it yourself.")
    if "timeout" in m or "timed out" in m:
        return "network timeout", ("No answer from the database host in time: the service is not running (start "
                                   "Docker Desktop / the container), a firewall blocks the port, or DB_HOST is wrong.")
    if "refused" in m or "could not connect" in m or "connection failed" in m:
        return "network", ("Nothing is listening on DB_HOST:DB_PORT: start Docker Desktop and the database "
                           "container (docker compose up -d), or check DB_PORT.")
    if "could not translate host name" in m or "name or service not known" in m or "getaddrinfo" in m:
        return "network", "DB_HOST cannot be resolved: check the host name."
    if "permission denied" in m:
        return "permissions", "The database user lacks a privilege; grant it or use the owner account."
    if "unable to open database file" in m:
        return "file", "The SQLite file's folder cannot be created or written: check DATABASE_URL and permissions."
    return "other", "See the error text above."


def _docker_status(container: str = "aurum-timescaledb") -> str | None:
    exe = shutil.which("docker")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "ps", "-a", "--filter", f"name={container}", "--format", "{{.Status}}"],
                             capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return "docker CLI found but the Docker engine is not running (start Docker Desktop)"
    return out.stdout.strip() or "container not found"


def diagnose_database(settings: Settings, tcp_timeout: float = 5.0,
                      docker_status: Callable[[], str | None] = _docker_status) -> list[Step]:
    from sqlalchemy import text
    from sqlalchemy.engine import make_url

    from aurum.database.connection import MIGRATIONS_DIR, engine_from_settings

    r = _Report("database", _secrets(settings))
    try:
        url = make_url(settings.sqlalchemy_url)
    except Exception as exc:  # noqa: BLE001
        r.add("Connection string", FAIL, f"cannot parse the database URL ({type(exc).__name__})",
              "Check DATABASE_URL, or leave it empty and set DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD.")
        return r.steps
    backend = url.get_backend_name()
    source = "DATABASE_URL" if settings.database_url is not None else "DB_* settings"
    if backend == "sqlite":
        db_path = url.database or ":memory:"
        r.add("Connection string", OK, f"SQLite file {db_path} (from {source})")
        if db_path != ":memory:":
            parent = Path(db_path).parent
            exists = Path(db_path).exists()
            r.add("SQLite file", OK if exists else WARN, f"{'exists' if exists else 'does not exist yet'}; folder "
                                                         f"{parent} {'exists' if parent.exists() else 'missing'}",
                  "" if exists else "Run: python -m aurum.cli init-db")
    else:
        r.add("Connection string", OK, f"{url.drivername} host={url.host} port={url.port or 5432} "
                                       f"database={url.database} user={url.username} (from {source}); "
                                       f"connect timeout 10 s")
        driver = url.get_driver_name() or "psycopg2"
        try:
            ver = importlib.metadata.version("psycopg" if driver == "psycopg" else driver)
            r.add("Driver", OK, f"{driver} {ver} installed")
        except importlib.metadata.PackageNotFoundError:
            r.add("Driver", FAIL, f"database driver '{driver}' is not installed",
                  "Run: .\\.venv\\Scripts\\python.exe -m pip install -r requirements.txt")
            return r.steps
        host, port = url.host or "localhost", int(url.port or 5432)
        try:
            socket.getaddrinfo(host, port)
        except socket.gaierror as exc:
            r.add("Host name", FAIL, f"{host} cannot be resolved ({exc})", "Check DB_HOST.")
            return r.steps
        local = host in ("localhost", "127.0.0.1", "::1")
        dstat = docker_status() if local else None
        if local and dstat is None:
            r.add("Database service (Docker)", SKIP, "docker CLI not on PATH - container status not checked")
        elif dstat is not None:
            up = dstat.lower().startswith("up")
            r.add("Database service (Docker)", OK if up else WARN, f"aurum-timescaledb: {dstat}",
                  "" if up else "Start Docker Desktop, then: docker compose up -d  (in AURUM_AI)")
        try:
            with socket.create_connection((host, port), timeout=tcp_timeout):
                pass
            r.add("Network (TCP)", OK, f"{host}:{port} accepts connections")
        except socket.timeout:
            cat, hint = classify_db_error("timeout")
            r.add("Network (TCP)", FAIL, f"no answer from {host}:{port} within {tcp_timeout:.0f} s ({cat})", hint)
            return r.steps
        except OSError as exc:
            cat, hint = classify_db_error(f"connection refused {exc}")
            r.add("Network (TCP)", FAIL, f"{host}:{port} refused the connection ({cat})", hint)
            return r.steps

    engine = engine_from_settings(settings)
    try:
        with engine.connect() as c:
            c.execute(text("SELECT 1"))
            if backend == "postgresql":
                who = c.execute(text("select current_user, current_database(), version()")).one()
                r.add("Login / database", OK, f"connected as {who[0]} to {who[1]} ({who[2].split(',')[0]})")
                applied = set(c.execute(text("select version from schema_migrations")).scalars()) \
                    if c.execute(text("select to_regclass('public.schema_migrations')")).scalar() else set()
                files = sorted(p.stem for p in MIGRATIONS_DIR.glob("*.sql"))
                pending = [f for f in files if f not in applied]
                r.add("Migrations", OK if not pending else WARN,
                      f"applied: {', '.join(sorted(applied)) or 'none'}" + (f"; pending: {', '.join(pending)}"
                                                                            if pending else ""),
                      "" if not pending else "Run: python -m aurum.cli init-db  (additive; never drops data)")
                # one call per privilege: a comma list means "any of", not "all of"
                privs = {t: all(c.execute(text("select has_table_privilege(:t, :p)"), {"t": t, "p": p}).scalar()
                                for p in ("SELECT", "INSERT", "UPDATE"))
                         for t in ("market_candles", "cot_reports", "ta_zones")
                         if c.execute(text("select to_regclass(:t)"), {"t": f"public.{t}"}).scalar()}
                create = c.execute(text("select has_schema_privilege('public', 'CREATE')")).scalar()
                missing = [t for t, ok in privs.items() if not ok]
                r.add("Permissions", OK if not missing and create else WARN,
                      f"read/write on {', '.join(privs) or 'no AURUM tables yet'}: {'yes' if not missing else 'NO for ' + ', '.join(missing)}; "
                      f"create in schema public: {'yes' if create else 'no'}",
                      "" if not missing and create else "Grant the privileges to DB_USER or use the database owner.")
            else:
                ready = bool(c.execute(text("select name from sqlite_master where type='table' and name='market_candles'"))
                             .scalar())
                r.add("Login / database", OK, "SQLite opened")
                r.add("Migrations", OK if ready else WARN, "schema present" if ready else "schema not initialised",
                      "" if ready else "Run: python -m aurum.cli init-db")
    except Exception as exc:  # noqa: BLE001
        msg = str(getattr(exc, "orig", exc))
        cat, hint = classify_db_error(msg)
        r.add("Login / database", FAIL, f"{cat}: {msg.strip().splitlines()[0] if msg.strip() else type(exc).__name__}",
              hint)
    finally:
        engine.dispose()
    return r.steps
