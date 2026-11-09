"""Approval policy: validated model, seed file, and expiry calculation."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

TIMEFRAMES = ("M5", "M15", "H1", "H4", "D1")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExpiryPolicy(_Strict):
    default_minutes: int = Field(60, ge=1)
    by_timeframe: dict[str, int] = Field(default_factory=lambda: {"M5": 5, "M15": 15, "H1": 60, "H4": 240, "D1": 1440})
    by_symbol: dict[str, int] = Field(default_factory=dict)
    by_strategy: dict[str, int] = Field(default_factory=dict)
    high_volatility_minutes: int | None = Field(None, ge=1)
    min_minutes: int = Field(2, ge=1)
    max_minutes: int = Field(1440, ge=1)
    honour_idea_expiry: bool = True
    expiring_soon_minutes: int = Field(2, ge=0)

    @field_validator("by_timeframe", "by_symbol", "by_strategy")
    @classmethod
    def _positive(cls, v: dict[str, int]) -> dict[str, int]:
        if any(int(m) < 1 for m in v.values()):
            raise ValueError("expiry minutes must be at least 1")
        return v


class MultiPerson(_Strict):
    enabled: bool = False
    required_approvals: int = Field(2, ge=2, le=5)
    min_risk_pct: Decimal | None = Field(None, ge=0)


class ReviewPolicy(_Strict):
    risk_review_policy: Literal["acknowledge", "block"] = "acknowledge"
    risk_max_age_seconds: int = Field(300, ge=10)
    market_data_max_age_seconds: int = Field(120, ge=5)
    require_comment_on_approve: bool = False
    rejection_reason_required: bool = True
    rejection_reasons: list[str] = Field(default_factory=lambda: ["trader_discretion", "other"])
    required_evidence: list[str] = Field(default_factory=lambda: ["trade", "risk"])
    multi_person: MultiPerson = Field(default_factory=MultiPerson)
    reviewers_see_assigned_only: bool = False


class HandoffPolicy(_Strict):
    revalidate_risk: Literal["always", "if_older_than"] = "always"
    revalidate_if_older_than_seconds: int = Field(60, ge=1)
    allowed_accounts: list[str] = Field(default_factory=list)


class EmergencyPolicy(_Strict):
    allow_self_authorisation: bool = True
    confirmation_phrase: str = "CLOSE {symbol}"
    expiry_minutes: int = Field(5, ge=1)


class ManagementPolicy(_Strict):
    expiry_minutes: int = Field(15, ge=1)
    allow_stop_widening: bool = False
    max_partial_close_fraction: Decimal = Field(Decimal("0.99"), gt=0, lt=1)
    emergency: EmergencyPolicy = Field(default_factory=EmergencyPolicy)


class NotificationPolicy(_Strict):
    channels: dict[str, bool] = Field(default_factory=lambda: {"in_app": True, "email": False, "telegram": False})
    email_events: list[str] = Field(default_factory=list)
    retry_max_attempts: int = Field(6, ge=1, le=20)


class Policy(_Strict):
    expiry: ExpiryPolicy = Field(default_factory=ExpiryPolicy)
    review: ReviewPolicy = Field(default_factory=ReviewPolicy)
    handoff: HandoffPolicy = Field(default_factory=HandoffPolicy)
    management: ManagementPolicy = Field(default_factory=ManagementPolicy)
    notifications: NotificationPolicy = Field(default_factory=NotificationPolicy)

    def dump(self) -> dict[str, Any]:
        return json.loads(self.model_dump_json())

    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(self.dump(), sort_keys=True).encode()).hexdigest()

    # ---- expiry -----------------------------------------------------------------------------------------------------------
    def expiry_window(self, timeframe: str, symbol: str, strategy: str, high_volatility: bool = False) -> int:
        """Minutes an approval request stays open. The shortest applicable policy wins, clamped to [min, max]."""
        e = self.expiry
        candidates = [e.by_timeframe.get(timeframe, e.default_minutes)]
        if symbol in e.by_symbol:
            candidates.append(e.by_symbol[symbol])
        if strategy in e.by_strategy:
            candidates.append(e.by_strategy[strategy])
        if high_volatility and e.high_volatility_minutes:
            candidates.append(e.high_volatility_minutes)
        return max(e.min_minutes, min(e.max_minutes, min(candidates)))

    def expires_at(self, now: datetime, timeframe: str, symbol: str, strategy: str, idea_expires_at: datetime | None,
                   high_volatility: bool = False) -> tuple[datetime, str]:
        window = self.expiry_window(timeframe, symbol, strategy, high_volatility)
        policy_end = now + timedelta(minutes=window)
        if self.expiry.honour_idea_expiry and idea_expires_at is not None and idea_expires_at < policy_end:
            return idea_expires_at, "the trade idea's own expiry"
        return policy_end, f"policy: {window} min for {timeframe}"


def load_seed(path: Path) -> Policy:
    if not path.exists():
        return Policy()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Policy.model_validate(raw)
