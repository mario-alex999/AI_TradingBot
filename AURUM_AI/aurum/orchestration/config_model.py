"""Non-secret orchestration configuration: seeded from ``config/orchestration.yaml`` as version 1, then changed only through
``PUT /orchestration/config`` (a new, audited, append-only version with a reason). Secrets are never part of it: they stay
in the environment, and a key that looks like a credential is refused."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from aurum.orchestration.catalogue import find_sensitive
from aurum.orchestration.retry import RetryPolicy
from aurum.orchestration.scheduler import zone


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DispatchCfg(_M):
    max_parallel: int = Field(5, ge=1, le=50)          # starting point for the demo; raise only after load testing
    batch_size: int = Field(50, ge=1, le=500)
    lease_seconds: int = Field(120, ge=10, le=3600)


class TimeoutCfg(_M):
    default_execution_seconds: int = Field(300, ge=10, le=24 * 3600)
    n8n_request_seconds: float = Field(10, gt=0, le=120)
    module_request_seconds: float = Field(10, gt=0, le=120)


class CallbackCfg(_M):
    max_skew_seconds: int = Field(300, ge=30, le=3600)
    signature_required: bool = False                  # when true, unsigned callbacks are refused even with a valid token
    max_output_bytes: int = Field(65536, ge=1024, le=1_048_576)


class EventCfg(_M):
    delivery_max_attempts: int = Field(6, ge=1, le=20)
    retention_days: int = Field(30, ge=1, le=3650)
    bridge_sources: list[Literal["module_06", "module_07"]] = ["module_06", "module_07"]
    allowed_webhook_hosts: list[str] = ["localhost", "127.0.0.1", "host.docker.internal", "n8n", "aurum-n8n"]


class SchedulingCfg(_M):
    timezone: str = "Africa/Lagos"
    catch_up: Literal["latest", "skip"] = "latest"     # after downtime: run the most recent missed slot once, or none

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str) -> str:
        zone(v)
        return v


class HealthCfg(_M):
    interval_seconds: int = Field(60, ge=15, le=3600)
    history_days: int = Field(7, ge=1, le=90)
    degraded_failure_rate: float = Field(0.5, gt=0, le=1)
    min_executions_for_rate: int = Field(4, ge=1, le=1000)
    event_lag_warn_seconds: int = Field(300, ge=10)


class AlertCfg(_M):
    dead_letter_warn: int = Field(1, ge=1)
    failure_rate_warn: float = Field(0.25, gt=0, le=1)


class ModuleCfg(_M):
    base_url: str | None = None
    timeout: float | None = Field(None, gt=0, le=120)
    enabled: bool = True


class MarketMonitoringCfg(_M):
    """WF08-01 Market Monitoring Scheduler. ``enabled`` is its backend-enforced off switch: when false the monitoring API
    tells the workflow to stop before it checks or requests anything (pausing the workflow in the registry also works)."""
    enabled: bool = True
    symbols: list[str] = ["EURUSD", "GBPUSD", "XAUUSD", "US30"]
    timeframes: list[Literal["D1", "H4", "H1", "M15", "M5"]] = ["D1", "H4", "H1", "M15", "M5"]
    settle_seconds: int = Field(60, ge=0, le=3600)         # wait after a candle closes so Module 01 has stored it
    stale_factor: float = Field(3.0, ge=1.0, le=100.0)     # a closed candle older than factor x period is reported stale
    request_event: Literal["ai.analysis.requested", "technical.analysis.requested"] = "ai.analysis.requested"
    require_module_01_data: bool = True                     # stop when Module 01 has stored no candles at all

    @field_validator("symbols")
    @classmethod
    def _symbols(cls, v: list[str]) -> list[str]:
        out = [str(x).strip().upper() for x in v if str(x).strip()]
        if not out or len(set(out)) != len(out):
            raise ValueError("market_monitoring.symbols must be a non-empty list without duplicates.")
        return out

    @field_validator("timeframes")
    @classmethod
    def _timeframes(cls, v: list[str]) -> list[str]:
        if not v or len(set(v)) != len(v):
            raise ValueError("market_monitoring.timeframes must be a non-empty list without duplicates.")
        return v


class OrchestrationConfig(_M):
    retry: dict[str, Any] = Field(default_factory=dict)
    dispatch: DispatchCfg = DispatchCfg()
    timeouts: TimeoutCfg = TimeoutCfg()
    callbacks: CallbackCfg = CallbackCfg()
    events: EventCfg = EventCfg()
    scheduling: SchedulingCfg = SchedulingCfg()
    health: HealthCfg = HealthCfg()
    alerts: AlertCfg = AlertCfg()
    modules: dict[str, ModuleCfg] = Field(default_factory=dict)
    market_monitoring: MarketMonitoringCfg = MarketMonitoringCfg()

    @field_validator("retry")
    @classmethod
    def _retry(cls, v: dict[str, Any]) -> dict[str, Any]:
        return RetryPolicy.from_dict(v).as_dict()

    @field_validator("modules")
    @classmethod
    def _modules(cls, v: dict[str, ModuleCfg]) -> dict[str, ModuleCfg]:
        allowed = {f"module_{i:02d}" for i in range(1, 11)}
        bad = set(v) - allowed
        if bad:
            raise ValueError(f"Unknown modules {sorted(bad)}; use module_01 ... module_10.")
        return v

    def retry_policy(self) -> RetryPolicy:
        return RetryPolicy.from_dict(self.retry)

    def dump(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


def parse_config(data: dict[str, Any]) -> OrchestrationConfig:
    leak = find_sensitive(data, "config")
    if leak:
        raise ValueError(f"Secrets are not part of the orchestration configuration ({leak}); set them in the environment.")
    return OrchestrationConfig.model_validate(data)


def load_file(path: Path) -> OrchestrationConfig:
    if not path.exists():
        return OrchestrationConfig()
    return parse_config(yaml.safe_load(path.read_text(encoding="utf-8")) or {})
