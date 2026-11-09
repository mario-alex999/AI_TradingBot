"""Builds ApprovalService with the real module adapters. Shared by the API and the CLI so both behave the same."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from aurum.approvals.gateways import AccountMirrorPositions, InProcessRiskGateway, Module04Evidence, Module05Source
from aurum.approvals.service import ApprovalService


def lazy(factory: Callable[[], Any]) -> Callable[[], Any]:
    holder: dict[str, Any] = {}

    def get() -> Any:
        if "v" not in holder:
            holder["v"] = factory()
        return holder["v"]
    return get


def build_service(engine: Any, settings: Any, risk: Callable[[], Any] | None = None, decision: Callable[[], Any] | None = None,
                  intelligence: Callable[[], Any] | None = None) -> ApprovalService:
    if intelligence is None:
        def _intel() -> Any:
            from aurum.market_intelligence.service import MarketIntelligenceService
            return MarketIntelligenceService(settings, engine)
        intelligence = lazy(_intel)
    if risk is None:
        def _risk() -> Any:
            from aurum.risk.service import RiskService
            return RiskService(engine, settings, intelligence_service=intelligence())
        risk = lazy(_risk)
    if decision is None:
        def _decision() -> Any:
            from aurum.decision.service import DecisionService
            return DecisionService(settings, engine, intelligence=intelligence())
        decision = lazy(_decision)

    def quote(symbol: str, now: datetime) -> Any:
        return risk().data.quote(symbol, now)

    return ApprovalService(engine, settings, InProcessRiskGateway(risk), Module05Source(decision), Module04Evidence(intelligence),
                           AccountMirrorPositions(engine, quote))
