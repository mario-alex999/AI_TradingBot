"""Application configuration.

* Runtime settings come from environment variables / a local ``.env`` file (Pydantic Settings).
* Instrument definitions and broker-symbol mappings come from ``config/instruments.yaml``,
  so symbols can be changed without editing Python source code.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, time
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent

#: Timeframes supported by the engine, with their candle length in seconds.
TIMEFRAME_SECONDS: dict[str, int] = {
    "M1": 60, "M5": 300, "M15": 900, "M30": 1800,
    "H1": 3600, "H4": 14400, "D1": 86400, "W1": 604800,
}
DAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]


def _split_csv(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return [v.strip().upper() for v in value.split(",") if v.strip()]
    return [str(v).strip().upper() for v in value]


class Settings(BaseSettings):
    """All runtime settings. Secrets are ``SecretStr`` and never printed."""

    model_config = SettingsConfigDict(env_file=(".env", str(PROJECT_ROOT / ".env")), env_file_encoding="utf-8", extra="ignore",
                                      case_sensitive=False)

    # MetaTrader 5
    mt5_terminal_path: str | None = None
    mt5_login: int | None = None
    mt5_password: SecretStr | None = None
    mt5_server: str | None = None
    mt5_timeout_ms: int = 60_000
    mt5_server_timezone: str = "Etc/UTC"
    mt5_portable: bool = False            # start the terminal with /portable (used by the per-person terminal slots)

    # Per-person MT5 link: after signing in, a person authorises their own DEMO account and AURUM mirrors it read-only
    # through a dedicated terminal copy ("slot"). 0 slots = the feature is off. Create slots with
    # `python -m aurum.cli mt5-slots setup --count N`. Broker passwords are only ever held in the connector's memory.
    mt5_slots_dir: Path = Path("C:/AURUM/mt5")
    mt5_slot_count: int = Field(0, ge=0, le=32)
    mt5_link_sync_seconds: int = Field(30, ge=5, le=3600)
    mt5_link_lookback_days: int = Field(90, ge=1, le=3650)
    mt5_link_connect_timeout_seconds: int = Field(120, ge=10, le=900)

    # Database
    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "aurum"
    db_user: str = "aurum"
    db_password: SecretStr = SecretStr("")
    database_url: SecretStr | None = None

    # Collection
    collection_interval_seconds: int = Field(300, ge=10)
    historical_candle_count: int = Field(500, ge=1, le=100_000)
    enabled_instruments: Annotated[list[str], NoDecode] = ["EURUSD", "GBPUSD", "XAUUSD", "US30"]
    enabled_timeframes: Annotated[list[str], NoDecode] = ["D1", "H4", "H1", "M15", "M5"]
    exclude_forming_candle: bool = True
    gap_range_multiple: float = Field(8.0, gt=0)

    # Paths & logging
    instruments_config: Path = Path("config/instruments.yaml")
    data_dir: Path = Path("data")
    log_dir: Path = Path("logs")
    log_level: str = "INFO"

    # Backend API (Module 02+). Admin operations (POST backfill/update/calculate) require the
    # X-AURUM-Admin-Key header to equal AURUM_ADMIN_API_KEY; when unset they are disabled.
    aurum_admin_api_key: SecretStr | None = None
    api_host: str = "127.0.0.1"
    api_port: int = Field(8000, ge=1, le=65535)
    api_cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    # User authentication (added with Module 07). Sessions are server-side rows; the browser holds only an httpOnly
    # cookie. Set AUTH_COOKIE_SECURE=true whenever the API is served over HTTPS.
    auth_session_idle_minutes: int = Field(30, ge=5, le=24 * 60)
    auth_session_absolute_hours: int = Field(12, ge=1, le=7 * 24)
    auth_cookie_secure: bool = False
    auth_password_iterations: int = Field(600_000, ge=1_000)   # PBKDF2-SHA256; tests lower it for speed
    auth_max_failed_logins: int = Field(5, ge=1)
    auth_lockout_minutes: int = Field(15, ge=1)

    # Module 07 - human approval. The worker expires requests, hands approved trades to Module 09's queue, dispatches
    # events and delivers notifications. Email is off until SMTP is configured; nothing is ever reported as sent without it.
    approvals_config: Path = Path("config/approvals.yaml")
    approvals_worker_interval_seconds: int = Field(15, ge=2)
    approvals_events_webhook_url: str | None = None
    notify_email_enabled: bool = False
    smtp_host: str | None = None
    smtp_port: int = Field(587, ge=1, le=65535)
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_from: str | None = None
    smtp_use_tls: bool = True
    telegram_bot_token: SecretStr | None = None
    telegram_chat_id: str | None = None

    # Module 08 - workflow orchestration with n8n. Secrets are SecretStr, never logged and never returned by the API.
    # N8N_BASE_URL is how AURUM reaches n8n (inside Docker use the service name, e.g. http://n8n:5678, never localhost);
    # ORCHESTRATION_CALLBACK_BASE_URL is how n8n reaches AURUM (from the n8n container: http://host.docker.internal:8000/...).
    orchestration_environment: Literal["development", "test", "demo", "production"] = "demo"
    orchestration_config: Path = Path("config/orchestration.yaml")
    orchestration_worker_interval_seconds: int = Field(10, ge=2)
    orchestration_callback_base_url: str = "http://host.docker.internal:8000/api/v1/orchestration"
    aurum_internal_base_url: str = "http://127.0.0.1:8000/api/v1"
    orchestration_service_token: SecretStr | None = None
    n8n_base_url: str = "http://localhost:5678"
    n8n_public_url: str | None = None                  # editor URL shown in the UI (defaults to N8N_BASE_URL)
    n8n_webhook_base_url: str | None = None            # defaults to N8N_BASE_URL/webhook
    n8n_environment: Literal["development", "test", "demo", "production"] | None = None   # must match when set
    n8n_api_key: SecretStr | None = None
    n8n_webhook_shared_secret: SecretStr | None = None
    n8n_webhook_shared_secret_previous: SecretStr | None = None
    n8n_webhook_auth_token: SecretStr | None = None
    n8n_request_timeout: float = Field(10, gt=0, le=120)
    n8n_retry_limit: int = Field(5, ge=1, le=20)       # upper bound on automatic dispatch attempts

    # Module 04 - market intelligence. Provider keys are secrets: never logged, never returned by the API.
    economic_calendar_enabled: bool = True
    economic_calendar_provider: str | None = None          # fmp | finnhub | tradingeconomics
    economic_calendar_api_key: SecretStr | None = None
    economic_calendar_base_url: str | None = None
    fred_api_key: SecretStr | None = None                  # optional: FRED release dates + US macro vintages
    official_data_enabled: bool = True                     # keyless official central-bank / statistics sources
    news_enabled: bool = True                              # official central-bank RSS feeds (+ Finnhub news if keyed)

    # Optional AI review: advisory only, off by default. It needs all three of AI_REVIEW_ENABLED=true, ANTHROPIC_API_KEY
    # and AI_REVIEW_MONTHLY_BUDGET_USD. The budget is a hard stop, enforced before each API call.
    ai_review_enabled: bool = False
    anthropic_api_key: SecretStr | None = None
    ai_review_monthly_budget_usd: float | None = Field(None, gt=0)
    ai_review_model: str = "claude-opus-5-5"
    ai_review_effort: Literal["low", "medium", "high", "xhigh", "max"] = "high"
    ai_review_max_tokens: int = Field(8000, ge=1024, le=16_000)
    ai_review_timeout_seconds: float = Field(300, gt=0, le=600)
    ai_review_max_calls_per_hour: int = Field(20, ge=1)    # Module 05 candidate reviews

    # Module 05 - AI provider. Claude is the default and the only provider the monthly budget can cap.
    ai_provider: Literal["anthropic", "openai", "local"] = "anthropic"
    ai_provider_model: str | None = None         # openai and local only
    ai_provider_base_url: str | None = None      # openai-compatible local server, for example Ollama
    openai_api_key: SecretStr | None = None

    # Module 05 - decision engine
    decision_loop_interval_seconds: int = Field(300, ge=30)

    # Risk monitoring thresholds (percent of equity / counts). Unset = not configured. AURUM places no orders, so these
    # are monitored, not enforced on the broker: a breach raises a risk_limit notification.
    risk_max_per_trade_pct: float | None = Field(None, gt=0)
    risk_max_daily_loss_pct: float | None = Field(None, gt=0)
    risk_max_open_positions: int | None = Field(None, ge=1)
    risk_max_total_exposure_pct: float | None = Field(None, gt=0)
    risk_max_correlated_exposure_pct: float | None = Field(None, gt=0)

    @field_validator("api_cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, v: Any) -> Any:
        return [x.strip() for x in v.split(",") if x.strip()] if isinstance(v, str) else v

    @field_validator("mt5_login", "mt5_terminal_path", "mt5_server", "mt5_password", "database_url",
                     "aurum_admin_api_key", "economic_calendar_provider", "economic_calendar_api_key",
                     "economic_calendar_base_url", "fred_api_key", "risk_max_per_trade_pct", "risk_max_daily_loss_pct",
                     "risk_max_open_positions", "risk_max_total_exposure_pct", "risk_max_correlated_exposure_pct", "anthropic_api_key", "openai_api_key", "ai_provider_model", "ai_provider_base_url",
                     "ai_review_monthly_budget_usd", "approvals_events_webhook_url", "smtp_host", "smtp_username",
                     "smtp_password", "smtp_from", "telegram_bot_token", "telegram_chat_id", "orchestration_service_token",
                     "n8n_public_url", "n8n_webhook_base_url", "n8n_environment", "n8n_api_key", "n8n_webhook_shared_secret",
                     "n8n_webhook_shared_secret_previous", "n8n_webhook_auth_token", mode="before")
    @classmethod
    def _blank_is_none(cls, v: Any) -> Any:
        return None if isinstance(v, str) and v.strip() == "" else v

    @field_validator("enabled_instruments", "enabled_timeframes", mode="before")
    @classmethod
    def _parse_list(cls, v: Any) -> list[str]:
        return _split_csv(v)

    @field_validator("enabled_timeframes")
    @classmethod
    def _check_timeframes(cls, v: list[str]) -> list[str]:
        bad = [tf for tf in v if tf not in TIMEFRAME_SECONDS]
        if bad:
            raise ValueError(f"Unsupported timeframe(s) {bad}. Supported: {list(TIMEFRAME_SECONDS)}")
        return v

    @field_validator("log_level")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper()

    def resolve(self, p: Path) -> Path:
        """Resolve a configured path relative to the project root."""
        return p if p.is_absolute() else PROJECT_ROOT / p

    @property
    def sqlalchemy_url(self) -> str:
        """Database URL. Built from DB_* values unless DATABASE_URL is set. Contains the password -
        never log it; use :pyattr:`safe_database_url` for display."""
        if self.database_url is not None:
            url = self.database_url.get_secret_value()
            if url.startswith("sqlite:///") and not url.startswith("sqlite:////") and ":memory:" not in url:
                url = "sqlite:///" + str(self.resolve(Path(url.removeprefix("sqlite:///"))))
            return url
        pw = quote_plus(self.db_password.get_secret_value())
        return f"postgresql+psycopg://{quote_plus(self.db_user)}:{pw}@{self.db_host}:{self.db_port}/{self.db_name}"

    @property
    def safe_database_url(self) -> str:
        url = self.sqlalchemy_url
        if "@" in url and "://" in url:
            scheme, rest = url.split("://", 1)
            creds, host = rest.rsplit("@", 1)
            user = creds.split(":", 1)[0]
            return f"{scheme}://{user}:***@{host}"
        return url


# --------------------------------------------------------------------------- instruments
@dataclass(frozen=True)
class TradingHours:
    """Expected market closures, used to classify candle gaps.

    Times and holiday dates are wall-clock values in ``timezone`` (an IANA name, default ``UTC``).
    Using the exchange's own zone (e.g. ``America/New_York``) makes the schedule follow daylight-saving
    changes automatically; with ``UTC`` the hours are fixed and must be edited twice a year.
    """

    weekly_close_day: int = 4          # 0=Mon ... 6=Sun
    weekly_close_time: time = time(21, 0)
    weekly_open_day: int = 6
    weekly_open_time: time = time(21, 0)
    daily_breaks: tuple[tuple[time, time], ...] = ()
    holidays: frozenset[date] = frozenset()
    timezone: str = "UTC"

    @property
    def tz(self) -> ZoneInfo:
        return _zoneinfo(self.timezone)

    @staticmethod
    def _parse_day_time(text: str) -> tuple[int, time]:
        day, hhmm = text.strip().upper().split()
        return DAYS.index(day[:3]), time.fromisoformat(hhmm)

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "TradingHours":
        d = d or {}
        cd, ct = cls._parse_day_time(d.get("weekly_close", "FRI 21:00"))
        od, ot = cls._parse_day_time(d.get("weekly_open", "SUN 21:00"))
        breaks = tuple((time.fromisoformat(a), time.fromisoformat(b)) for a, b in d.get("daily_breaks", []) or [])
        hols = frozenset(date.fromisoformat(str(h)) for h in d.get("holidays", []) or [])
        tzname = str(d.get("timezone") or "UTC").strip()
        _zoneinfo(tzname)  # fail fast on a typo
        return cls(cd, ct, od, ot, breaks, hols, tzname)


@lru_cache(maxsize=None)
def _zoneinfo(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"Unknown trading_hours timezone '{name}'. Use an IANA name such as "
                         f"'America/New_York' or 'UTC' (on Windows: pip install tzdata).") from exc


@dataclass(frozen=True)
class InstrumentConfig:
    symbol: str                      # canonical AURUM name
    broker_symbol: str               # exact broker Market Watch name
    asset_class: str = "unknown"
    description: str = ""
    trading_hours: TradingHours = field(default_factory=TradingHours)


class SymbolMappingError(ValueError):
    """Raised when an instrument has no broker-symbol mapping."""


def load_instruments(path: Path) -> dict[str, InstrumentConfig]:
    """Load ``instruments.yaml`` into ``{canonical_symbol: InstrumentConfig}``."""
    if not path.exists():
        raise FileNotFoundError(f"Instrument configuration not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    default_hours = (raw.get("defaults") or {}).get("trading_hours") or {}
    out: dict[str, InstrumentConfig] = {}
    for sym, spec in (raw.get("instruments") or {}).items():
        spec = spec or {}
        broker = str(spec.get("broker_symbol") or "").strip()
        if not broker:
            raise SymbolMappingError(f"Instrument '{sym}' has no broker_symbol in {path}")
        hours = {**default_hours, **(spec.get("trading_hours") or {})}
        out[str(sym).upper()] = InstrumentConfig(
            symbol=str(sym).upper(), broker_symbol=broker, asset_class=str(spec.get("asset_class", "unknown")),
            description=str(spec.get("description", "")), trading_hours=TradingHours.from_dict(hours))
    return out


def enabled_instruments(settings: Settings, instruments: dict[str, InstrumentConfig]) -> list[InstrumentConfig]:
    """Return enabled instruments in configured order. Unknown names raise SymbolMappingError."""
    missing = [s for s in settings.enabled_instruments if s not in instruments]
    if missing:
        raise SymbolMappingError(f"Enabled instrument(s) {missing} are not defined in "
                                 f"{settings.instruments_config}. Add a broker_symbol mapping for them.")
    return [instruments[s] for s in settings.enabled_instruments]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
