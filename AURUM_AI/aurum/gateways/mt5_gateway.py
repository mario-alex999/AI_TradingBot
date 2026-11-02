"""Read-only gateway to the MetaTrader 5 terminal.

SECURITY: this module is strictly READ-ONLY. The MetaTrader5 package is wrapped in
:class:`ReadOnlyMT5`, which exposes only an explicit allowlist of read functions. Any attempt to
reach trading functions (order_send, order_check, positions_*, ...) raises ReadOnlyViolation.
Only DEMO accounts are accepted; real and contest accounts are rejected and disconnected.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import numpy as np
import pandas as pd

from aurum.config import TIMEFRAME_SECONDS, Settings

log = logging.getLogger(__name__)

ACCOUNT_TRADE_MODES = {0: "DEMO", 1: "CONTEST", 2: "REAL"}
SYMBOL_TRADE_MODES = {0: "DISABLED", 1: "LONGONLY", 2: "SHORTONLY", 3: "CLOSEONLY", 4: "FULL"}


class MT5Error(RuntimeError):
    """Base class for gateway errors."""


class MT5UnavailableError(MT5Error):
    """The MetaTrader5 Python package is not installed (it is Windows-only)."""


class MT5ConnectionError(MT5Error):
    """The terminal could not be initialised, is not running, or is not connected."""


class NonDemoAccountError(MT5Error):
    """The connected account is not a DEMO account."""


class ReadOnlyViolation(MT5Error):
    """Code tried to use an MT5 function outside the read-only allowlist."""


class ReadOnlyMT5:
    """Proxy around the MetaTrader5 module that only exposes read-only functions and constants."""

    ALLOWED_FUNCTIONS = frozenset({
        "initialize", "shutdown", "last_error", "version", "account_info", "terminal_info",
        "symbols_total", "symbols_get", "symbol_info", "symbol_info_tick", "symbol_select",
        "copy_rates_from_pos", "copy_rates_from", "copy_rates_range",
    })
    ALLOWED_CONSTANT_PREFIXES = ("TIMEFRAME_", "ACCOUNT_TRADE_MODE_", "SYMBOL_TRADE_MODE_")

    def __init__(self, module: Any):
        object.__setattr__(self, "_m", module)

    def __getattr__(self, name: str) -> Any:
        if name in self.ALLOWED_FUNCTIONS or name.startswith(self.ALLOWED_CONSTANT_PREFIXES):
            return getattr(self._m, name)
        raise ReadOnlyViolation(f"MT5 function '{name}' is blocked: AURUM Module 01 is read-only.")

    def __setattr__(self, name: str, value: Any) -> None:
        raise ReadOnlyViolation("The MT5 proxy is immutable.")


def load_mt5_module() -> Any:
    try:
        import MetaTrader5 as mt5  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - depends on platform
        raise MT5UnavailableError(
            "The 'MetaTrader5' package is not installed. It is only available for Windows: "
            "run 'pip install MetaTrader5' inside your Windows virtual environment.") from exc
    return mt5


# --------------------------------------------------------------------------- broker clock
class BrokerClock:
    """Converts MT5 server timestamps to UTC.

    MT5 returns candle/tick times as seconds since 1970 *in the broker server's wall-clock time*.
    With an IANA zone (e.g. Europe/Athens) DST is handled correctly; with a fixed offset it is not.
    """

    def __init__(self, timezone_name: str = "Etc/UTC", fixed_offset_seconds: int | None = None):
        self.timezone_name = timezone_name
        self.fixed_offset_seconds = fixed_offset_seconds
        self.tz: ZoneInfo | None = None
        if fixed_offset_seconds is None:
            try:
                self.tz = ZoneInfo(timezone_name)
            except (ZoneInfoNotFoundError, ValueError) as exc:
                raise ValueError(f"Unknown MT5_SERVER_TIMEZONE '{timezone_name}'. Use an IANA name such as "
                                 f"'Europe/Athens' or 'Etc/UTC' (on Windows: pip install tzdata).") from exc

    def describe(self) -> str:
        if self.fixed_offset_seconds is not None:
            return f"fixed offset {self.fixed_offset_seconds / 3600:+.1f}h (auto-detected, no DST handling)"
        return self.timezone_name

    def series_to_utc(self, server_seconds: pd.Series | np.ndarray) -> pd.Series:
        """Vectorised conversion of server epoch seconds to tz-aware UTC timestamps."""
        s = pd.Series(np.asarray(server_seconds, dtype="float64"))
        if self.fixed_offset_seconds is not None:
            return pd.to_datetime(s - self.fixed_offset_seconds, unit="s", utc=True)
        wall = pd.to_datetime(s, unit="s")  # naive = server wall-clock time
        # ambiguous wall times (DST fall-back hour) are taken as the first (DST) occurrence;
        # non-existent times (spring-forward gap) are shifted forward. Both are rare and documented.
        local = wall.dt.tz_localize(self.tz, ambiguous=np.ones(len(wall), dtype=bool), nonexistent="shift_forward")
        return local.dt.tz_convert("UTC")

    def to_utc(self, server_seconds: float) -> datetime:
        return self.series_to_utc(np.array([server_seconds])).iloc[0].round("ms").to_pydatetime()


# --------------------------------------------------------------------------- results
@dataclass(frozen=True)
class ConnectionReport:
    login: int
    server: str
    company: str
    currency: str
    leverage: int
    trade_mode: str
    terminal_name: str
    terminal_build: int | None
    terminal_path: str
    terminal_connected: bool
    broker_clock: str


def _asdict(obj: Any) -> dict[str, Any]:
    if obj is None:
        return {}
    if hasattr(obj, "_asdict"):
        return dict(obj._asdict())
    return {k: getattr(obj, k) for k in dir(obj) if not k.startswith("_") and not callable(getattr(obj, k))}


# --------------------------------------------------------------------------- gateway
class MT5Gateway:
    """Read-only access to an MT5 terminal. Use as a context manager to guarantee shutdown."""

    def __init__(self, settings: Settings, mt5_module: Any | None = None):
        self.settings = settings
        self.mt5 = ReadOnlyMT5(mt5_module if mt5_module is not None else load_mt5_module())
        self.connected = False
        self.clock: BrokerClock | None = None
        self.report: ConnectionReport | None = None

    # ---- lifecycle
    def __enter__(self) -> "MT5Gateway":
        self.connect()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.shutdown()

    def _last_error(self) -> str:
        try:
            code, msg = self.mt5.last_error()
            return f"{code}: {msg}"
        except Exception:  # pragma: no cover - defensive
            return "unknown error"

    def connect(self) -> ConnectionReport:
        """Initialise MT5, verify terminal + DEMO account. Raises on any failure (after shutdown)."""
        s = self.settings
        kwargs: dict[str, Any] = {"timeout": s.mt5_timeout_ms}
        if s.mt5_terminal_path:
            kwargs["path"] = s.mt5_terminal_path
        if s.mt5_login is not None:
            if s.mt5_password is None or not s.mt5_server:
                raise MT5ConnectionError("MT5_LOGIN is set, so MT5_PASSWORD and MT5_SERVER must be set too.")
            kwargs.update(login=int(s.mt5_login), password=s.mt5_password.get_secret_value(), server=s.mt5_server)
        log.info("Initialising MetaTrader 5", extra={"terminal_path": s.mt5_terminal_path or "<running terminal>",
                                                      "login": s.mt5_login, "server": s.mt5_server})
        try:
            ok = self.mt5.initialize(**kwargs)
        except Exception as exc:
            raise MT5ConnectionError(f"MT5 initialize() raised: {exc}") from exc
        if not ok:
            err = self._last_error()
            self._safe_shutdown()
            raise MT5ConnectionError(f"MT5 initialize() failed ({err}). Is the MT5 terminal installed, "
                                     f"running and logged in? Check MT5_TERMINAL_PATH / credentials.")
        self.connected = True
        try:
            term = self.mt5.terminal_info()
            if term is None:
                raise MT5ConnectionError(f"MT5 terminal is not running or not reachable ({self._last_error()}).")
            if not getattr(term, "connected", False):
                raise MT5ConnectionError("MT5 terminal is running but not connected to the broker server.")
            acc = self.mt5.account_info()
            if acc is None:
                raise MT5ConnectionError(f"Could not read account information ({self._last_error()}). "
                                         f"Log in to an account in the terminal.")
            demo_mode = getattr(self.mt5, "ACCOUNT_TRADE_MODE_DEMO", 0)
            mode = ACCOUNT_TRADE_MODES.get(getattr(acc, "trade_mode", -1), f"UNKNOWN({getattr(acc, 'trade_mode', None)})")
            if getattr(acc, "trade_mode", None) != demo_mode:
                raise NonDemoAccountError(f"Account {acc.login} on {acc.server} is a {mode} account. "
                                          f"AURUM Module 01 only connects to DEMO accounts - disconnecting.")
            self.clock = self._build_clock()
            self.report = ConnectionReport(
                login=int(acc.login), server=str(acc.server), company=str(getattr(acc, "company", "")),
                currency=str(getattr(acc, "currency", "")), leverage=int(getattr(acc, "leverage", 0) or 0),
                trade_mode=mode, terminal_name=str(getattr(term, "name", "")),
                terminal_build=getattr(term, "build", None), terminal_path=str(getattr(term, "path", "")),
                terminal_connected=bool(term.connected), broker_clock=self.clock.describe())
            log.info("Connected to MT5 demo account", extra={"login": acc.login, "server": acc.server,
                                                             "broker_clock": self.clock.describe()})
            return self.report
        except Exception:
            self._safe_shutdown()
            raise

    def _build_clock(self) -> BrokerClock:
        tzname = self.settings.mt5_server_timezone
        if tzname.lower() != "auto":
            return BrokerClock(tzname)
        # estimate the offset from the most recent tick of any visible symbol
        for sym in self._probe_symbols():
            tick = self.mt5.symbol_info_tick(sym)
            if tick is None or not getattr(tick, "time", 0):
                continue
            offset = tick.time - datetime.now(timezone.utc).timestamp()
            rounded = int(round(offset / 1800.0) * 1800)
            if abs(rounded) <= 14 * 3600 and abs(offset - rounded) < 600:
                log.warning("MT5_SERVER_TIMEZONE=auto: using fixed offset %+.1fh (no DST handling). "
                            "Set an IANA zone for exact history.", rounded / 3600)
                return BrokerClock(fixed_offset_seconds=rounded)
        raise MT5ConnectionError("MT5_SERVER_TIMEZONE=auto could not detect the broker offset (market closed or "
                                 "no recent ticks). Set MT5_SERVER_TIMEZONE to an IANA zone, e.g. Europe/Athens.")

    def _probe_symbols(self) -> list[str]:
        return [self.settings.enabled_instruments[0]] if self.settings.enabled_instruments else []

    def _safe_shutdown(self) -> None:
        try:
            self.mt5.shutdown()
        except Exception:  # pragma: no cover - defensive
            pass
        self.connected = False

    def shutdown(self) -> None:
        """Close the MT5 connection cleanly (safe to call more than once)."""
        if self.connected:
            self._safe_shutdown()
            log.info("MT5 connection closed")

    def _require(self) -> BrokerClock:
        if not self.connected or self.clock is None:
            raise MT5ConnectionError("Not connected to MT5. Call connect() first.")
        return self.clock

    # ---- symbols
    def ensure_symbol(self, broker_symbol: str) -> Any | None:
        """Return symbol_info, selecting it in Market Watch if needed. None if the broker lacks it."""
        self._require()
        info = self.mt5.symbol_info(broker_symbol)
        if info is None:
            log.warning("Symbol not available at broker", extra={"broker_symbol": broker_symbol,
                                                                 "mt5_error": self._last_error()})
            return None
        if not getattr(info, "visible", True):
            if not self.mt5.symbol_select(broker_symbol, True):
                log.warning("Could not select symbol in Market Watch",
                            extra={"broker_symbol": broker_symbol, "mt5_error": self._last_error()})
                return None
            info = self.mt5.symbol_info(broker_symbol) or info
        return info

    def symbol_spec(self, broker_symbol: str) -> dict[str, Any] | None:
        """Instrument specification as a plain dict (None if unavailable)."""
        info = self.ensure_symbol(broker_symbol)
        if info is None:
            return None
        d = _asdict(info)
        tick = self.mt5.symbol_info_tick(broker_symbol)
        return {
            "broker_symbol": broker_symbol,
            "digits": d.get("digits"), "point": d.get("point"),
            "contract_size": d.get("trade_contract_size"), "tick_size": d.get("trade_tick_size"),
            "tick_value": d.get("trade_tick_value"), "volume_min": d.get("volume_min"),
            "volume_max": d.get("volume_max"), "volume_step": d.get("volume_step"),
            "spread_points": d.get("spread"), "bid": getattr(tick, "bid", d.get("bid")),
            "ask": getattr(tick, "ask", d.get("ask")),
            "trade_mode": SYMBOL_TRADE_MODES.get(d.get("trade_mode"), str(d.get("trade_mode"))),
            "trading_available": d.get("trade_mode") not in (0, None),
            "currency_base": d.get("currency_base"), "currency_profit": d.get("currency_profit"),
            "description": d.get("description"), "path": d.get("path"),
            "raw": {k: (v if isinstance(v, (int, float, str, bool)) or v is None else str(v)) for k, v in d.items()},
        }

    def tick(self, broker_symbol: str) -> dict[str, Any] | None:
        """Latest bid/ask/last with UTC timestamp. None if unavailable."""
        clock = self._require()
        t = self.mt5.symbol_info_tick(broker_symbol)
        if t is None:
            log.warning("No tick available", extra={"broker_symbol": broker_symbol, "mt5_error": self._last_error()})
            return None
        msc = getattr(t, "time_msc", 0) or int(t.time) * 1000
        info = self.mt5.symbol_info(broker_symbol)
        point = getattr(info, "point", None) if info is not None else None
        spread = (t.ask - t.bid) / point if point else None
        return {"broker_symbol": broker_symbol, "ts_utc": clock.to_utc(msc / 1000.0), "mt5_time_msc": int(msc),
                "bid": float(t.bid), "ask": float(t.ask), "last": float(getattr(t, "last", 0.0) or 0.0) or None,
                "volume": float(getattr(t, "volume", 0) or 0), "spread_points": spread}

    # ---- candles
    def copy_rates(self, broker_symbol: str, timeframe: str, start_pos: int, count: int) -> np.ndarray | None:
        """Raw ``copy_rates_from_pos`` result (structured numpy array), or None on failure."""
        self._require()
        if timeframe not in TIMEFRAME_SECONDS:
            raise ValueError(f"Unsupported timeframe {timeframe}")
        tf_const = getattr(self.mt5, f"TIMEFRAME_{timeframe}")
        rates = self.mt5.copy_rates_from_pos(broker_symbol, tf_const, start_pos, count)
        if rates is None:
            log.warning("copy_rates_from_pos returned no data",
                        extra={"broker_symbol": broker_symbol, "timeframe": timeframe, "mt5_error": self._last_error()})
            return None
        return rates
