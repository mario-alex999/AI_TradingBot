"""Risk configuration: profiles, validation, fingerprints and the "does this change weaken a limit?" check.

The active configuration is versioned in the database (see repository.py); ``config/risk.yaml`` only seeds version 1.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aurum.config import PROJECT_ROOT

RISK_YAML = PROJECT_ROOT / "config" / "risk.yaml"
PROFILES = ("conservative", "moderate", "aggressive", "custom")


class AtrCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    timeframe: str = "H1"
    min_stop_atr: float = Field(0.25, ge=0)
    max_stop_atr: float = Field(6, gt=0)


class TradingDay(BaseModel):
    model_config = ConfigDict(extra="forbid")
    timezone: str = "America/New_York"
    rollover: str = "17:00"

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str) -> str:
        from zoneinfo import ZoneInfo
        ZoneInfo(v)  # raises for an unknown zone
        return v

    @field_validator("rollover")
    @classmethod
    def _hhmm(cls, v: str) -> str:
        h, m = v.split(":")
        if not (0 <= int(h) <= 23 and 0 <= int(m) <= 59):
            raise ValueError("rollover must be HH:MM")
        return f"{int(h):02d}:{int(m):02d}"


class Correlation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    timeframe: str = "H1"
    lookback_bars: int = Field(500, ge=20)
    min_samples: int = Field(120, ge=10)
    threshold: float = Field(0.7, gt=0, le=1)
    max_age_hours: float = Field(72, gt=0)
    unreliable_policy: Literal["assume_correlated", "ignore"] = "assume_correlated"


class NewsPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    block_states: list[str] = ["HIGH_EVENT_RISK", "RELEASE_WINDOW"]
    warn_states: list[str] = ["EVENT_APPROACHING", "POST_EVENT_VOLATILITY"]
    unknown_policy: Literal["review", "block"] = "review"


class VolatilityPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    block_regimes: list[str] = ["VERY_HIGH"]
    warn_regimes: list[str] = ["HIGH", "VERY_LOW"]
    unknown_policy: Literal["review", "block"] = "review"


class SessionPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    blocked_sessions: dict[str, list[str]] = {}   # symbol or asset class -> session names
    blocked_weekdays: list[int] = []

    @field_validator("blocked_weekdays")
    @classmethod
    def _days(cls, v: list[int]) -> list[int]:
        if any(d < 0 or d > 6 for d in v):
            raise ValueError("weekdays are 0 (Monday) to 6 (Sunday)")
        return sorted(set(v))


class RiskConfig(BaseModel):
    """One complete, validated configuration. Unknown keys are rejected so a typo can never silently disable a limit."""
    model_config = ConfigDict(extra="forbid")
    profile: Literal["conservative", "moderate", "aggressive", "custom"] = "moderate"
    risk_per_trade_pct: float = Field(1, gt=0, le=10)
    max_risk_per_trade_pct: float = Field(2, gt=0, le=10)
    max_daily_loss_pct: float = Field(3, gt=0, le=100)
    max_weekly_loss_pct: float = Field(6, gt=0, le=100)
    max_monthly_loss_pct: float = Field(10, gt=0, le=100)
    max_drawdown_pct: float = Field(10, gt=0, le=100)
    max_open_positions: int = Field(5, ge=1, le=200)
    max_total_open_risk_pct: float = Field(3, gt=0, le=100)
    max_symbol_risk_pct: float = Field(2, gt=0, le=100)
    max_asset_class_risk_pct: float = Field(3, gt=0, le=100)
    max_correlated_risk_pct: float = Field(2, gt=0, le=100)
    max_consecutive_losses: int = Field(3, ge=1, le=100)
    max_trades_per_day: int = Field(6, ge=1, le=1000)
    min_risk_reward: float = Field(1.5, gt=0, le=20)
    max_margin_utilization_pct: float = Field(30, gt=0, le=100)
    min_free_margin: float = Field(0, ge=0)
    max_slippage_points: dict[str, float] = {"default": 0}
    commission_per_lot: dict[str, float] = {"default": 0}
    max_spread_points: dict[str, float] = {}
    max_spread_median_multiple: float = Field(3, gt=1)
    min_stop_spread_multiple: float = Field(2, ge=0)
    atr_check: AtrCheck = AtrCheck()
    loss_basis: Literal["equity", "balance"] = "equity"
    breakeven_tolerance_pct: float = Field(0.05, ge=0, le=5)
    trading_day: TradingDay = TradingDay()
    period_start_tolerance_minutes: int = Field(120, ge=0)
    reset_policy: dict[str, Literal["next_period", "manual"]] = {
        "daily_loss": "next_period", "weekly_loss": "next_period", "monthly_loss": "next_period",
        "drawdown": "manual", "consecutive_losses": "manual"}
    consecutive_loss_cooldown_minutes: int = Field(0, ge=0)
    assessment_ttl_minutes: int = Field(15, ge=1, le=1440)
    account_max_age_minutes: float = Field(10, gt=0)
    quote_max_age_minutes: float = Field(5, gt=0)
    spec_max_age_hours: float = Field(72, gt=0)
    conversion_max_age_minutes: float = Field(30, gt=0)
    correlation: Correlation = Correlation()
    news: NewsPolicy = NewsPolicy()
    volatility: VolatilityPolicy = VolatilityPolicy()
    sessions: SessionPolicy = SessionPolicy()
    data_unavailable_policy: Literal["review", "block"] = "review"
    rules: dict[str, dict[str, Any]] = {}

    @field_validator("max_slippage_points", "commission_per_lot", "max_spread_points")
    @classmethod
    def _non_negative(cls, v: dict[str, float]) -> dict[str, float]:
        if any(x < 0 for x in v.values()):
            raise ValueError("values must not be negative")
        return v

    @model_validator(mode="after")
    def _consistent(self) -> "RiskConfig":
        if self.risk_per_trade_pct > self.max_risk_per_trade_pct:
            raise ValueError("risk_per_trade_pct cannot exceed max_risk_per_trade_pct")
        if self.risk_per_trade_pct > self.max_total_open_risk_pct:
            raise ValueError("risk_per_trade_pct cannot exceed max_total_open_risk_pct")
        if self.atr_check.min_stop_atr >= self.atr_check.max_stop_atr:
            raise ValueError("atr_check.min_stop_atr must be below max_stop_atr")
        if self.correlation.min_samples > self.correlation.lookback_bars:
            raise ValueError("correlation.min_samples cannot exceed lookback_bars")
        unknown = set(self.reset_policy) - {"daily_loss", "weekly_loss", "monthly_loss", "drawdown", "consecutive_losses"}
        if unknown:
            raise ValueError(f"unknown reset_policy keys: {sorted(unknown)}")
        return self

    # --- per-symbol lookups -------------------------------------------------------------------------------------------
    def per_symbol(self, table: dict[str, float], symbol: str) -> float | None:
        return table.get(symbol, table.get("default"))

    def rule_override(self, rule_id: str) -> dict[str, Any]:
        return self.rules.get(rule_id) or {}

    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(self.model_dump(mode="json"), sort_keys=True).encode()).hexdigest()[:32]


def load_yaml(path: Path = RISK_YAML) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def profile_config(profile: str, raw: dict[str, Any] | None = None, overrides: dict[str, Any] | None = None) -> RiskConfig:
    """Base settings + one profile + optional overrides. ``custom`` starts from the default profile."""
    raw = raw if raw is not None else load_yaml()
    if profile not in PROFILES:
        raise ValueError(f"Unknown profile {profile!r}; choose one of {', '.join(PROFILES)}.")
    start = raw.get("default_profile", "moderate") if profile == "custom" else profile
    data: dict[str, Any] = copy.deepcopy(raw.get("base") or {})
    data.update(copy.deepcopy((raw.get("profiles") or {}).get(start) or {}))
    data["profile"] = profile
    if overrides:
        data = deep_merge(data, overrides)
    return RiskConfig.model_validate(data)


def default_config() -> RiskConfig:
    raw = load_yaml()
    return profile_config(raw.get("default_profile", "moderate"), raw)


def deep_merge(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(a)
    for k, v in b.items():
        out[k] = deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else copy.deepcopy(v)
    return out


# Direction in which each scalar becomes LOOSER. +1: a larger value is looser; -1: a smaller value is looser.
_LOOSER = {
    "risk_per_trade_pct": 1, "max_risk_per_trade_pct": 1, "max_daily_loss_pct": 1, "max_weekly_loss_pct": 1,
    "max_monthly_loss_pct": 1, "max_drawdown_pct": 1, "max_open_positions": 1, "max_total_open_risk_pct": 1,
    "max_symbol_risk_pct": 1, "max_asset_class_risk_pct": 1, "max_correlated_risk_pct": 1, "max_consecutive_losses": 1,
    "max_trades_per_day": 1, "min_risk_reward": -1, "max_margin_utilization_pct": 1, "min_free_margin": -1,
    "max_spread_median_multiple": 1, "min_stop_spread_multiple": -1, "breakeven_tolerance_pct": 1,
    "assessment_ttl_minutes": 1, "account_max_age_minutes": 1, "quote_max_age_minutes": 1, "spec_max_age_hours": 1,
    "conversion_max_age_minutes": 1, "consecutive_loss_cooldown_minutes": 0,
}


def weakened_fields(old: RiskConfig, new: RiskConfig) -> list[str]:
    """Every setting the new configuration makes less strict. Used to require an explicit acknowledgement and a reason."""
    out: list[str] = []
    for key, sign in _LOOSER.items():
        a, b = getattr(old, key), getattr(new, key)
        if sign and (b - a) * sign > 0:
            out.append(f"{key}: {a} -> {b}")
    if old.consecutive_loss_cooldown_minutes == 0 and new.consecutive_loss_cooldown_minutes > 0:
        out.append("consecutive_loss_cooldown_minutes: manual release -> timed cooldown")
    elif 0 < new.consecutive_loss_cooldown_minutes < old.consecutive_loss_cooldown_minutes:
        out.append(f"consecutive_loss_cooldown_minutes: {old.consecutive_loss_cooldown_minutes} -> {new.consecutive_loss_cooldown_minutes}")
    for sym in set(old.max_spread_points) | set(new.max_spread_points):
        a, b = old.max_spread_points.get(sym), new.max_spread_points.get(sym)
        if a is not None and (b is None or b > a):
            out.append(f"max_spread_points.{sym}: {a} -> {b}")
    for sym in set(old.max_slippage_points) | set(new.max_slippage_points):
        a, b = old.per_symbol(old.max_slippage_points, sym) or 0, new.per_symbol(new.max_slippage_points, sym) or 0
        if b < a:
            out.append(f"max_slippage_points.{sym}: {a} -> {b}")
    if old.atr_check.enabled and not new.atr_check.enabled:
        out.append("atr_check.enabled: true -> false")
    for name, o, n in (("news.block_states", old.news.block_states, new.news.block_states),
                       ("volatility.block_regimes", old.volatility.block_regimes, new.volatility.block_regimes),
                       ("sessions.blocked_weekdays", old.sessions.blocked_weekdays, new.sessions.blocked_weekdays)):
        dropped = sorted(set(map(str, o)) - set(map(str, n)))
        if dropped:
            out.append(f"{name}: removed {dropped}")
    for key in set(old.sessions.blocked_sessions) | set(new.sessions.blocked_sessions):
        dropped = sorted(set(old.sessions.blocked_sessions.get(key, [])) - set(new.sessions.blocked_sessions.get(key, [])))
        if dropped:
            out.append(f"sessions.blocked_sessions.{key}: removed {dropped}")
    for name, o, n in (("news.unknown_policy", old.news.unknown_policy, new.news.unknown_policy),
                       ("volatility.unknown_policy", old.volatility.unknown_policy, new.volatility.unknown_policy),
                       ("data_unavailable_policy", old.data_unavailable_policy, new.data_unavailable_policy)):
        if o == "block" and n == "review":
            out.append(f"{name}: block -> review")
    if old.correlation.unreliable_policy == "assume_correlated" and new.correlation.unreliable_policy == "ignore":
        out.append("correlation.unreliable_policy: assume_correlated -> ignore")
    if new.correlation.threshold > old.correlation.threshold:
        out.append(f"correlation.threshold: {old.correlation.threshold} -> {new.correlation.threshold}")
    for key, pol in old.reset_policy.items():
        if pol == "manual" and new.reset_policy.get(key) == "next_period":
            out.append(f"reset_policy.{key}: manual -> next_period")
    for rid in set(old.rules) | set(new.rules):
        if old.rule_override(rid).get("enabled", True) and not new.rule_override(rid).get("enabled", True):
            out.append(f"rules.{rid}: disabled")
    if old.loss_basis != new.loss_basis:
        out.append(f"loss_basis: {old.loss_basis} -> {new.loss_basis} (changes how losses are measured)")
    return out
