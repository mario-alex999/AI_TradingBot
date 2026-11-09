"""Plain data passed between the Module 06 services (no database or HTTP here)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from aurum.risk.account import AccountRisk
from aurum.risk.calculator import CalcError, InstrumentSpec, Quote, TradeMath
from aurum.risk.config import RiskConfig
from aurum.risk.portfolio import CorrelationResult, Portfolio

PASS, REVIEW, BLOCK, ERROR = "PASS", "REVIEW", "BLOCK", "ERROR"
HARD, WARNING, INFO = "HARD_BLOCK", "WARNING", "INFORMATIONAL"


@dataclass(frozen=True)
class TradeProposal:
    symbol: str
    direction: str                         # BUY | SELL
    entry: Decimal
    stop_loss: Decimal | None
    take_profit: Decimal | None
    risk_pct: Decimal | None = None        # None -> configured risk per trade
    requested_volume: Decimal | None = None
    trade_idea_id: str | None = None
    strategy: str | None = None
    timeframe: str | None = None
    ai_confidence: float | None = None     # recorded for audit; never used in any decision
    source: str = "api"

    def canonical(self) -> dict[str, Any]:
        s = lambda v: None if v is None else str(v)  # noqa: E731
        return {"symbol": self.symbol, "direction": self.direction, "entry": s(self.entry), "stop_loss": s(self.stop_loss),
                "take_profit": s(self.take_profit), "risk_pct": s(self.risk_pct), "requested_volume": s(self.requested_volume),
                "trade_idea_id": self.trade_idea_id, "strategy": self.strategy, "timeframe": self.timeframe}


@dataclass
class MarketState:
    quote: Quote | None
    quote_age_minutes: float | None
    spread_points: Decimal | None
    median_spread_points: Decimal | None
    intelligence: dict[str, Any] | None    # Module 04 analysis, or None when unavailable
    intelligence_reason: str | None
    atr: Decimal | None
    atr_timeframe: str | None
    quality_issues: list[dict[str, Any]] = field(default_factory=list)

    @property
    def market_open(self) -> bool | None:
        return ((self.intelligence or {}).get("sessions") or {}).get("market_open")

    @property
    def active_sessions(self) -> list[str]:
        return list(((self.intelligence or {}).get("sessions") or {}).get("active_sessions") or [])

    @property
    def event_state(self) -> str:
        return ((self.intelligence or {}).get("event_risk") or {}).get("state") or "UNKNOWN"

    @property
    def calendar_complete(self) -> bool:
        return bool((((self.intelligence or {}).get("event_risk") or {}).get("coverage") or {}).get("complete"))

    @property
    def regime(self) -> str:
        return ((self.intelligence or {}).get("volatility") or {}).get("regime") or "UNKNOWN"


@dataclass
class Lock:
    lock_id: str
    scope: str                             # global | account | instrument | strategy
    scope_value: str | None
    reason: str
    trigger_source: str
    rule_id: str | None
    activated_at: datetime
    activated_by: str
    expires_at: datetime | None
    details: dict[str, Any] | None = None

    def applies(self, symbol: str, strategy: str | None, login: int | None) -> bool:
        if self.scope == "global":
            return True
        if self.scope == "instrument":
            return self.scope_value == symbol
        if self.scope == "strategy":
            return strategy is not None and self.scope_value == strategy
        if self.scope == "account":
            return login is None or self.scope_value == str(login)   # unknown account: fail safe
        return True                                                 # unknown scope: fail safe


@dataclass
class AssessmentContext:
    proposal: TradeProposal
    cfg: RiskConfig
    config_version: int
    now: datetime
    account: AccountRisk
    spec: InstrumentSpec | None
    spec_error: CalcError | None
    math: TradeMath | None
    calc_error: CalcError | None
    market: MarketState
    portfolio: Portfolio                   # existing positions + live reservations (the proposed trade is NOT included)
    correlations: list[Any]
    locks: list[Lock]
    active_idea_assessments: list[str]
    cot: dict[str, Any] | None
    asset_class: str


@dataclass
class RuleResult:
    rule_id: str
    name: str
    category: str
    severity: str                          # severity applied to this outcome
    outcome: str                           # pass | fail | error | skipped
    reason_code: str
    message: str
    evidence: dict[str, Any] = field(default_factory=dict)
    thresholds: dict[str, Any] = field(default_factory=dict)
