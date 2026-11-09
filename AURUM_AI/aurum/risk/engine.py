"""The central risk decision engine.

assess() gathers account, instrument, market and portfolio data (slow reads happen first, outside the lock), then inside one
serialised transaction reads live reservations and locks, evaluates every rule, activates any automatic locks, and stores
the assessment, its rule results, the account/portfolio snapshots, the outbox events and the audit row together.
Identical idempotency keys inside the validity window return the stored assessment instead of creating a second one.
"""
from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Callable

from aurum.risk import ENGINE_VERSION
from aurum.risk.account import AccountRisk, evaluate_account
from aurum.risk.calculator import CalcError, InstrumentSpec, TradeMath, calculate, value_per_price_unit
from aurum.risk.calendar import TradingCalendar
from aurum.risk.config import RiskConfig
from aurum.risk.db_models import RiskAccountSnapshot, RiskAssessment, RiskPortfolioSnapshot, RiskRuleEvaluation
from aurum.risk.models import (BLOCK, ERROR, HARD, PASS, REVIEW, WARNING, AssessmentContext, MarketState, RuleResult,
                               TradeProposal)
from aurum.risk.money import HUNDRED, D, fnum, money
from aurum.risk.portfolio import ExposureItem, Portfolio, correlation, currency_exposure
from aurum.risk.providers import RiskData
from aurum.risk.repository import RiskRepository, jsonable, stable_hash
from aurum.risk.rules import LOCKING_RULES, RULES, correlated_items, decide, evaluate

log = logging.getLogger(__name__)
DISCLAIMER = ("Estimated risk at the stop-loss. Gaps, slippage beyond the assumption, commissions, liquidity and execution "
              "conditions can make an actual loss larger. Module 06 never places, modifies or closes orders.")
STATUS_RULES = {"account": ("R001", "R030", "R031", "R032", "R033", "R034", "R035", "R036", "R037"),
                "portfolio": ("R040", "R041", "R042", "R043", "R046", "R047"),
                "correlation": ("R044", "R045"),
                "market": ("R050", "R051", "R052", "R053", "R054", "R055", "R056")}


class ProposalError(ValueError):
    """Malformed input (HTTP 422). Distinct from a proposal that is well-formed but unsafe (a BLOCK decision)."""


def status_label(results: list[RuleResult], ids: tuple[str, ...]) -> str:
    """WITHIN_LIMITS | WARNING | BLOCKED | DATA_UNAVAILABLE. Never 'within limits' when data is missing."""
    sel = [r for r in results if r.rule_id in ids]
    if any(r.outcome == "fail" and r.severity == HARD for r in sel):
        return "BLOCKED"
    if any(r.outcome == "error" or (r.outcome == "fail" and r.reason_code.endswith(("UNAVAILABLE", "UNKNOWN", "INCOMPLETE")))
           for r in sel):
        return "DATA_UNAVAILABLE"
    if any(r.outcome == "fail" and r.severity == WARNING for r in sel):
        return "WARNING"
    return "WITHIN_LIMITS"


def streak_fingerprint(ids: list[str]) -> str:
    return hashlib.sha256("|".join(sorted(ids)).encode()).hexdigest()


class RiskEngine:
    def __init__(self, data: RiskData, repo: RiskRepository, clock: Callable[[], datetime] | None = None):
        self.data, self.repo = data, repo
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    # ---- inputs --------------------------------------------------------------------------------------------------------
    @staticmethod
    def normalise(raw: dict[str, Any], symbols: dict[str, str]) -> TradeProposal:
        def dec(name: str, required: bool = False, positive: bool = True) -> Decimal | None:
            v = raw.get(name)
            if v is None or v == "":
                if required:
                    raise ProposalError(f"{name} is required.")
                return None
            try:
                d = D(v)
            except ValueError as exc:
                raise ProposalError(f"{name} is not a number.") from exc
            if positive and d <= 0:
                raise ProposalError(f"{name} must be positive.")
            return d
        symbol = str(raw.get("symbol") or raw.get("instrument") or "").upper().strip()
        if not symbol:
            raise ProposalError("symbol is required.")
        if symbol not in symbols:
            raise ProposalError(f"Unknown instrument {symbol}; configured: {', '.join(sorted(symbols))}.")
        direction = str(raw.get("direction") or "").upper().strip()
        direction = {"LONG": "BUY", "SHORT": "SELL"}.get(direction, direction)
        if direction not in ("BUY", "SELL"):
            raise ProposalError("direction must be BUY or SELL.")
        conf = raw.get("ai_confidence")
        return TradeProposal(symbol=symbol, direction=direction, entry=dec("entry", True), stop_loss=dec("stop_loss"),
                             take_profit=dec("take_profit"), risk_pct=dec("risk_pct"), requested_volume=dec("requested_volume"),
                             trade_idea_id=(str(raw["trade_idea_id"])[:64] if raw.get("trade_idea_id") else None),
                             strategy=(str(raw["strategy"])[:64] if raw.get("strategy") else None),
                             timeframe=(str(raw["timeframe"])[:4] if raw.get("timeframe") else None),
                             ai_confidence=float(conf) if isinstance(conf, (int, float)) else None,
                             source=str(raw.get("source") or "api")[:32])

    def proposal_from_idea(self, idea_id: str, overrides: dict[str, Any]) -> dict[str, Any]:
        """A Module 05 trade candidate as a proposal. Entry is the zone mid-point unless overridden; Module 06 then validates
        every level independently and never trusts them because they came from Module 05."""
        idea = self.data.trade_idea(idea_id)
        if idea is None:
            raise ProposalError(f"Trade idea {idea_id} was not found in Module 05.")
        mid = (D(idea["entry_low"]) + D(idea["entry_high"])) / 2
        spec = self.data.spec(idea["symbol"])
        mid = (mid / spec.tick_size).to_integral_value() * spec.tick_size
        raw = {"symbol": idea["symbol"], "direction": idea["direction"], "entry": mid, "stop_loss": idea["stop_loss"],
               "take_profit": idea["take_profit"], "trade_idea_id": idea_id, "strategy": idea["strategy"],
               "timeframe": idea["timeframe"], "ai_confidence": idea["confidence"], "source": "module_05"}
        raw.update({k: v for k, v in overrides.items() if v is not None and k not in ("trade_idea_id",)})
        return raw

    def _account(self, cfg: RiskConfig, now: datetime, persist_anchor: bool = True) -> AccountRisk:
        cal = TradingCalendar(cfg.trading_day)
        month = cal.month(now)
        since = month.start - timedelta(minutes=cfg.period_start_tolerance_minutes)
        snap = self.data.account_snapshot()
        history = self.data.equity_history(since, now)
        closed = self.data.closed_trades(now - timedelta(days=90))
        ops = self.data.balance_operations(month.start)
        opened = self.data.opened_since(cal.day(now).start)
        anchor = None
        if persist_anchor and snap is not None:
            anchor = lambda p, compute: self.repo.period_anchor(p.kind, p.label, cfg.loss_basis, p.start, p.end, compute)  # noqa: E731
        return evaluate_account(cfg, cal, now, snap, history, closed, ops, opened, anchor)

    def _market(self, cfg: RiskConfig, symbol: str, now: datetime) -> MarketState:
        intel, reason = self.data.intelligence(symbol, now)
        quote = self.data.quote(symbol, now)
        current, med = self.data.spread_stats(symbol, now)
        atr = None
        tf = cfg.atr_check.timeframe
        try:
            raw_atr = (((intel or {}).get("volatility") or {}).get("timeframes") or {}).get(tf, {}).get("atr")
            atr = D(raw_atr) if raw_atr else None
        except ValueError:
            atr = None
        return MarketState(quote=quote, quote_age_minutes=round((now - quote.at).total_seconds() / 60, 1) if quote else None,
                           spread_points=current, median_spread_points=med, intelligence=intel, intelligence_reason=reason,
                           atr=atr, atr_timeframe=tf if atr else None, quality_issues=self.data.quality_issues(symbol, now))

    def _vpu(self, symbol: str, account_ccy: str, cfg: RiskConfig, now: datetime) -> tuple[Decimal | None, InstrumentSpec | None, str | None]:
        try:
            spec = self.data.spec(symbol)
            vpu, *_ = value_per_price_unit(spec, account_ccy, now, timedelta(minutes=cfg.conversion_max_age_minutes),
                                           lambda a, b: self.data.rate(a, b, now))
            return vpu, spec, None
        except CalcError as exc:
            return None, None, exc.message

    def _portfolio(self, cfg: RiskConfig, account: AccountRisk, now: datetime, reservations: list[Any]) -> tuple[Portfolio, list[dict[str, Any]]]:
        from aurum.risk.portfolio import position_risk
        snap = account.snapshot
        pf = Portfolio(equity=snap.equity if snap else None)
        ccy_rows = []
        for p in self.data.open_positions():
            vpu, spec, why = self._vpu(p.symbol, snap.currency, cfg, now) if snap else (None, None, "No account currency.")
            risk, note = position_risk(p, vpu)
            notional = p.volume * p.open_price * vpu if vpu is not None and spec is not None and spec.calc_mode in (0, 2, 4, 5) else None
            pf.items.append(ExposureItem("position", p.id, p.symbol, p.side, p.asset_class, p.strategy, risk, note or why, notional))
            if spec is not None:
                ccy_rows.append((p.symbol, p.side, notional, spec.currency_base, spec.currency_profit))
        for r in reservations:
            pf.items.append(ExposureItem("reservation", r.assessment_id, r.symbol, r.side, r.asset_class, r.strategy, r.risk))
        return pf, ccy_rows

    def _correlations(self, cfg: RiskConfig, symbol: str, others: list[str], now: datetime) -> list[Any]:
        c = cfg.correlation
        base = self.data.closes(symbol, c.timeframe, c.lookback_bars, now)
        out = []
        for other in sorted(set(others) - {symbol}):
            out.append(correlation(symbol, other, base, self.data.closes(other, c.timeframe, c.lookback_bars, now), c.timeframe,
                                   c.lookback_bars, c.min_samples, timedelta(hours=c.max_age_hours), now))
        return out

    def _math(self, cfg: RiskConfig, p: TradeProposal, spec: InstrumentSpec | None, account: AccountRisk, market: MarketState,
              now: datetime, equity_override: Decimal | None = None, currency_override: str | None = None) -> tuple[TradeMath | None, CalcError | None]:
        if spec is None or p.stop_loss is None:
            return None, None
        if (p.direction == "BUY" and p.stop_loss >= p.entry) or (p.direction == "SELL" and p.stop_loss <= p.entry):
            return None, None
        if p.take_profit is not None and ((p.direction == "BUY" and p.take_profit <= p.entry) or
                                          (p.direction == "SELL" and p.take_profit >= p.entry)):
            return None, None
        snap = account.snapshot
        equity = equity_override if equity_override is not None else (snap.equity if snap and account.status in ("ok", "stale") else None)
        ccy = currency_override or (snap.currency if snap else None)
        if equity is None:
            return None, None
        if ccy is None:
            return None, CalcError("ACCOUNT_CURRENCY_UNKNOWN", "Account currency is unknown: no account snapshot is stored. "
                                                               "Pass accountCurrency with the equity, or run sync-account.")
        spread = market.quote.spread if market.quote else (market.spread_points * spec.point if market.spread_points is not None else None)
        try:
            return calculate(spec=spec, direction=p.direction, entry=p.entry, stop=p.stop_loss, take_profit=p.take_profit,
                             account_ccy=ccy, equity=equity, risk_pct=p.risk_pct or D(cfg.risk_per_trade_pct),
                             max_risk_pct=D(cfg.max_risk_per_trade_pct), requested_volume=p.requested_volume, spread_price=spread,
                             slippage_points=D(cfg.per_symbol(cfg.max_slippage_points, p.symbol) or 0),
                             commission_per_lot=D(cfg.per_symbol(cfg.commission_per_lot, p.symbol) or 0),
                             leverage=snap.leverage if snap else None, now=now,
                             max_conv_age=timedelta(minutes=cfg.conversion_max_age_minutes),
                             rate_fn=lambda a, b: self.data.rate(a, b, now)), None
        except CalcError as exc:
            return None, exc

    # ---- position size (what-if, nothing stored) -----------------------------------------------------------------------
    def position_size(self, raw: dict[str, Any]) -> dict[str, Any]:
        now = self.clock()
        cfg, version, _ = self.repo.active_config()
        p = self.normalise(raw, self.data.symbols())
        equity = D(raw["equity"]) if raw.get("equity") not in (None, "") else None
        if equity is not None and equity <= 0:
            raise ProposalError("equity must be positive.")
        account = self._account(cfg, now, persist_anchor=False)
        try:
            spec, spec_error = self.data.spec(p.symbol), None
        except CalcError as exc:
            spec, spec_error = None, exc
        market = self._market_light(p.symbol, now)
        ccy = (raw.get("account_currency") or raw.get("currency") or (account.snapshot.currency if account.snapshot else None))
        math, calc_error = self._math(cfg, p, spec, account, market, now, equity, ccy)
        ctx = AssessmentContext(p, cfg, version, now, account, spec, spec_error, math, calc_error, market, Portfolio(), [], [], [],
                                None, self.data.symbols().get(p.symbol, "unknown"))
        ids = {"R002", "R003", "R010", "R011", "R012", "R013", "R014", "R016", "R017", "R018", "R019", "R020"}
        results = [r for r in evaluate(ctx) if r.rule_id in ids]
        if equity is None and not account.usable:
            results.append(RuleResult("R001", "Account data integrity", "data", HARD, "error", f"ACCOUNT_{account.status.upper()}",
                                      account.reason or "Account data unavailable; enter an equity to calculate a what-if size."))
        hard = [r for r in results if r.outcome == "fail" and r.severity == HARD]
        errors = [r for r in results if r.outcome == "error"]
        return jsonable({"symbol": p.symbol, "direction": p.direction, "valid": not hard and not errors and math is not None,
                         "equity_source": "request" if equity is not None else "account_mirror",
                         "account_currency": ccy, "config_version": version, "calculation": self._math_view(math),
                         "checks": [self._rule_view(r) for r in results], "failed_checks": [self._rule_view(r) for r in hard + errors],
                         "warnings": [self._rule_view(r) for r in results if r.outcome == "fail" and r.severity == WARNING],
                         "note": "What-if calculation only: nothing is reserved or stored, and account, portfolio and market "
                                 "rules are not applied. Use POST /risk/assess for a decision.", "disclaimer": DISCLAIMER})

    def _market_light(self, symbol: str, now: datetime) -> MarketState:
        quote = self.data.quote(symbol, now)
        current, med = self.data.spread_stats(symbol, now)
        return MarketState(quote, round((now - quote.at).total_seconds() / 60, 1) if quote else None, current, med, None,
                           "not used for position sizing", None, None)

    # ---- assessment ----------------------------------------------------------------------------------------------------
    def assess(self, raw: dict[str, Any], *, actor: str, correlation_id: str | None = None,
               idempotency_key: str | None = None) -> dict[str, Any]:
        self.repo.require_schema()
        now = self.clock()
        correlation_id = correlation_id or str(uuid.uuid4())
        if raw.get("trade_idea_id") and not raw.get("entry"):
            raw = self.proposal_from_idea(str(raw["trade_idea_id"]), raw)
        symbols = self.data.symbols()
        p = self.normalise(raw, symbols)
        cfg, version, meta = self.repo.active_config()
        key = (idempotency_key or "").strip()[:128] or "auto:" + stable_hash({"p": p.canonical(), "v": version})[:40]

        with self.repo.read() as s:
            existing = self.repo.latest_by_key(s, key)
            if existing is not None and existing.expires_at > now:
                if existing.result.get("proposal") != jsonable(p.canonical()):
                    raise IdempotencyConflict("This Idempotency-Key was already used with a different proposal.")
                return {**self.repo.assessment_view(existing), "replayed": True}

        # Slow reads first, outside the lock.
        account = self._account(cfg, now)
        try:
            spec, spec_error = self.data.spec(p.symbol), None
        except CalcError as exc:
            spec, spec_error = None, exc
        market = self._market(cfg, p.symbol, now)
        math, calc_error = self._math(cfg, p, spec, account, market, now)
        asset_class = symbols.get(p.symbol, "unknown")
        cot = ((market.intelligence or {}).get("integrations") or {}).get("module_02")

        with self.repo.serialized() as s:
            existing = self.repo.latest_by_key(s, key)
            if existing is not None and existing.expires_at > now:
                return {**self.repo.assessment_view(existing), "replayed": True}
            attempt = existing.attempt + 1 if existing is not None else 1
            self.repo.expire_locks(s, now)
            locks = self.repo.active_locks(s, now)
            reservations = self.repo.live_reservations(s, now, exclude_key=key)
            portfolio, ccy_rows = self._portfolio(cfg, account, now, reservations)
            correlations = self._correlations(cfg, p.symbol, [i.symbol for i in portfolio.items], now)
            ctx = AssessmentContext(p, cfg, version, now, account, spec, spec_error, math, calc_error, market, portfolio,
                                    correlations, locks, self.repo.active_for_idea(s, p.trade_idea_id, now, key), cot, asset_class)
            acknowledged = self.repo.acknowledged_streaks(s)
            results = evaluate(ctx)
            if account.consecutive_losses and streak_fingerprint(account.streak_trade_ids) in acknowledged:
                results = [self._acknowledge_streak(r) if r.rule_id == "R035" else r for r in results]
            decision = decide(results)
            assessment_id = str(uuid.uuid4())
            new_locks = self._auto_locks(s, ctx, results, now, correlation_id, assessment_id)
            if new_locks:
                lock_rule = next(r for r in RULES if r.rule_id == "R060")
                results = [r for r in results if r.rule_id != "R060"] + [RuleResult(
                    "R060", lock_rule.name, lock_rule.category, HARD, "fail", "RISK_LOCK_ACTIVATED",
                    "This assessment activated: " + "; ".join(lk.reason for lk in new_locks),
                    {"locks": [lk.lock_id for lk in new_locks]})]
                decision = decide(results)
            expires = now + timedelta(minutes=cfg.assessment_ttl_minutes)
            view = self._view(ctx, results, decision, assessment_id, key, attempt, meta, correlation_id, expires, actor, ccy_rows)
            snapshot = self._input_snapshot(ctx)
            reserved = math.estimated_loss if decision in (PASS, REVIEW) and math else None
            row = RiskAssessment(
                assessment_id=assessment_id, idempotency_key=key, attempt=attempt, trade_idea_id=p.trade_idea_id, symbol=p.symbol,
                asset_class=asset_class, direction=p.direction, strategy=p.strategy, decision=decision, state="active",
                requested_volume=p.requested_volume, recommended_volume=math.recommended_volume if math else None,
                final_volume=math.final_volume if math and decision in (PASS, REVIEW) else None,
                risk_pct=math.estimated_loss_pct if math else None, monetary_risk=math.estimated_loss if math else None,
                potential_reward=math.estimated_reward if math else None, risk_reward=math.rr if math else None,
                adjusted_risk_reward=math.adjusted_rr if math else None, reserved_risk=reserved,
                account_currency=account.snapshot.currency if account.snapshot else None,
                account_status=view["account_status"]["status"], portfolio_status=view["portfolio_status"]["status"],
                correlation_status=view["correlation_status"]["status"], market_status=view["market_status"]["status"],
                config_version=version, engine_version=ENGINE_VERSION, correlation_id=correlation_id,
                input_hash=stable_hash(snapshot), input_snapshot=snapshot, result=jsonable(view), source=p.source,
                created_by=actor, created_at=now, expires_at=expires)
            rule_rows = [RiskRuleEvaluation(assessment_id=assessment_id, rule_id=r.rule_id, name=r.name, category=r.category,
                                            severity=r.severity, outcome=r.outcome, reason_code=r.reason_code, message=r.message,
                                            evidence=jsonable(r.evidence), thresholds=jsonable(r.thresholds)) for r in results]
            self.repo.add_assessment(s, row, rule_rows, self._account_row(account, assessment_id, now),
                                     self._portfolio_row(portfolio, assessment_id, now))
            self.repo.add_correlations(s, correlations, now)
            for kind, rid in (("day", "R030"), ("week", "R031"), ("month", "R032")):
                per = account.periods.get(kind)
                if per:
                    hit = any(r.rule_id == rid and r.outcome == "fail" and r.severity == HARD for r in results)
                    self.repo.record_period_loss(s, kind, per.label, cfg.loss_basis, per.loss_pct, hit, now)
            self._publish(s, view, decision, correlation_id, now)
            self.repo.audit(s, actor, "assessment.created", "risk_assessment", assessment_id,
                            {"decision": decision, "symbol": p.symbol, "trade_idea_id": p.trade_idea_id, "config_version": version,
                             "input_hash": row.input_hash}, correlation_id, at=now)
        return {**view, "state": "active", "replayed": False}

    @staticmethod
    def _acknowledge_streak(r: RuleResult) -> RuleResult:
        return RuleResult(r.rule_id, r.name, r.category, r.severity, "pass", "STREAK_ACKNOWLEDGED",
                          "This losing streak's lock was released by an authorised user; a further loss locks again.",
                          r.evidence, r.thresholds)

    def _auto_locks(self, s: Any, ctx: AssessmentContext, results: list[RuleResult], now: datetime, correlation_id: str,
                    assessment_id: str) -> list[Any]:
        """Limits that are reached lock new approvals (global scope) until the configured reset or an authorised release."""
        cfg, cal = ctx.cfg, TradingCalendar(ctx.cfg.trading_day)
        created = []
        for r in results:
            key = LOCKING_RULES.get(r.rule_id)
            if key is None or r.outcome != "fail" or r.severity != HARD:
                continue
            policy = cfg.reset_policy.get(key, "manual")
            expires = None
            if r.rule_id == "R035":
                fp = streak_fingerprint(ctx.account.streak_trade_ids)
                dedupe = f"{r.rule_id}:global:{fp}"
                if cfg.consecutive_loss_cooldown_minutes > 0:
                    expires = now + timedelta(minutes=cfg.consecutive_loss_cooldown_minutes)
            else:
                kind = {"R030": "day", "R031": "week", "R032": "month"}.get(r.rule_id)
                label = cal.period(kind, now).label if kind else "all"
                dedupe = f"{r.rule_id}:global:{label}"
                if kind and policy == "next_period":
                    expires = cal.period(kind, now).end
            lock, new = self.repo.activate_lock(
                s, scope="global", scope_value=None, reason=f"{r.name}: {r.message}", trigger_source=f"auto:{r.rule_id}",
                rule_id=r.rule_id, actor="system", now=now, expires_at=expires,
                details={"assessment_id": assessment_id, "evidence": r.evidence, "reset_policy": policy},
                dedupe_key=dedupe, correlation_id=correlation_id)
            if new:
                created.append(lock)
                if r.rule_id == "R035":
                    self.repo.record_streak(s, ctx.account.consecutive_losses, ctx.account.streak_trade_ids, lock.lock_id,
                                            streak_fingerprint(ctx.account.streak_trade_ids), now)
                payload = {"lock_id": lock.lock_id, "rule_id": r.rule_id, "reason": lock.reason, "scope": "global",
                           "expires_at": expires, "assessment_id": assessment_id}
                self.repo.add_event(s, "risk.limit.reached", r.rule_id, payload, correlation_id=correlation_id,
                                    idempotency_key=f"limit:{dedupe}", now=now)
                self.repo.add_event(s, "risk.lock.activated", lock.lock_id, payload, correlation_id=correlation_id,
                                    idempotency_key=f"lock.activated:{lock.lock_id}", now=now)
        return created

    def _publish(self, s: Any, view: dict[str, Any], decision: str, correlation_id: str, now: datetime) -> None:
        aid = view["assessment_id"]
        payload = {k: view[k] for k in ("assessment_id", "trade_idea_id", "instrument", "direction", "decision",
                                        "recommended_volume", "final_volume", "monetary_risk", "risk_pct", "risk_reward",
                                        "config_version", "expires_at")}
        payload["rule_violations"] = [v["rule_id"] for v in view["rule_violations"]]
        payload["warnings"] = [v["rule_id"] for v in view["warnings"]]
        self.repo.add_event(s, "risk.assessment.completed", aid, payload, correlation_id=correlation_id,
                            idempotency_key=f"assessment.completed:{aid}", now=now)
        extra = {BLOCK: "risk.assessment.blocked", REVIEW: "risk.assessment.requires_review", ERROR: "risk.assessment.failed"}.get(decision)
        if extra:
            self.repo.add_event(s, extra, aid, payload, correlation_id=correlation_id, idempotency_key=f"{extra}:{aid}", now=now)

    # ---- views ---------------------------------------------------------------------------------------------------------
    @staticmethod
    def _rule_view(r: RuleResult) -> dict[str, Any]:
        return {"rule_id": r.rule_id, "name": r.name, "category": r.category, "severity": r.severity, "outcome": r.outcome,
                "reason_code": r.reason_code, "message": r.message, "evidence": r.evidence, "thresholds": r.thresholds}

    @staticmethod
    def _math_view(m: TradeMath | None) -> dict[str, Any] | None:
        if m is None:
            return None
        return {"account_currency": m.account_currency, "value_per_price_unit_per_lot": fnum(m.value_per_price_unit),
                "value_method": m.value_method, "conversion": m.conversion, "stop_distance": fnum(m.stop_distance),
                "take_profit_distance": fnum(m.tp_distance), "spread_price": fnum(m.spread_price),
                "slippage_price": fnum(m.slippage_price), "commission_per_lot": fnum(m.commission_per_lot, 2),
                "loss_per_lot_at_stop": fnum(money(m.loss_per_lot_at_stop)), "cost_per_lot": fnum(money(m.cost_per_lot)),
                "total_loss_per_lot": fnum(money(m.total_loss_per_lot)), "reward_per_lot": fnum(m.reward_per_lot and money(m.reward_per_lot)),
                "net_reward_per_lot": fnum(m.net_reward_per_lot and money(m.net_reward_per_lot)), "risk_reward": fnum(m.rr, 3),
                "adjusted_risk_reward": fnum(m.adjusted_rr, 3), "risk_pct_budget": fnum(m.risk_pct, 4),
                "max_risk_amount": fnum(money(m.risk_amount)), "raw_volume": fnum(m.raw_volume, 6),
                "recommended_volume": fnum(m.recommended_volume), "final_volume": fnum(m.final_volume),
                "final_volume_source": m.final_volume_source, "capped_by_max_volume": m.capped_by_max_volume,
                "min_volume_risk_pct": fnum(m.min_volume_risk_pct, 4), "estimated_loss": fnum(m.estimated_loss and money(m.estimated_loss)),
                "estimated_loss_pct": fnum(m.estimated_loss_pct, 4), "estimated_reward": fnum(m.estimated_reward and money(m.estimated_reward)),
                "transaction_cost": fnum(m.transaction_cost and money(m.transaction_cost)), "notional": fnum(m.notional and money(m.notional)),
                "margin_estimate": fnum(m.margin_estimate and money(m.margin_estimate)), "margin_method": m.margin_method, "notes": m.notes}

    @staticmethod
    def account_view(a: AccountRisk, cfg: RiskConfig) -> dict[str, Any]:
        s = a.snapshot
        periods = {k: {"label": p.label, "start": p.start, "end": p.end, "start_value": fnum(p.start_value, 2),
                       "start_source": p.start_source, "start_uncertain": p.start_uncertain, "current_value": fnum(p.current_value, 2),
                       "net_deposits": fnum(p.net_deposits, 2), "realized_pnl": fnum(p.realized_pnl, 2),
                       "loss": fnum(p.loss, 2), "loss_pct": fnum(p.loss_pct, 4),
                       "limit_pct": {"day": cfg.max_daily_loss_pct, "week": cfg.max_weekly_loss_pct, "month": cfg.max_monthly_loss_pct}[k]}
                   for k, p in a.periods.items()}
        return {"data_status": a.status, "reason": a.reason, "age_minutes": a.age_minutes, "loss_basis": a.loss_basis,
                "captured_at": s.captured_at if s else None, "currency": s.currency if s else None,
                "trade_mode": s.trade_mode if s else None, "leverage": s.leverage if s else None,
                "balance": fnum(s.balance, 2) if s else None, "equity": fnum(s.equity, 2) if s else None,
                "floating_pnl": fnum(s.floating, 2) if s else None, "margin": fnum(s.margin, 2) if s else None,
                "free_margin": fnum(s.margin_free, 2) if s else None, "margin_level": fnum(s.margin_level, 2) if s else None,
                "margin_utilization_pct": fnum(a.margin_utilization_pct, 4), "peak_equity": fnum(a.peak_equity, 2),
                "drawdown_pct": fnum(a.drawdown_pct, 4), "max_drawdown_pct": cfg.max_drawdown_pct, "periods": periods,
                "consecutive_losses": a.consecutive_losses, "max_consecutive_losses": cfg.max_consecutive_losses,
                "streak_trades": a.streak_trade_ids, "recent_results": a.recent_results, "trades_today": a.trades_today,
                "max_trades_per_day": cfg.max_trades_per_day}

    @staticmethod
    def portfolio_view(pf: Portfolio, correlations: list[Any], ccy_rows: list[Any]) -> dict[str, Any]:
        pct = lambda v: fnum(pf.pct(v), 4)  # noqa: E731
        total = pf.total()
        return {"equity": fnum(pf.equity, 2), "bounded": pf.bounded(), "total_open_risk": fnum(money(total)),
                "total_open_risk_pct": pct(total),
                "unbounded_positions": [{"ref": i.ref, "symbol": i.symbol, "note": i.risk_note} for i in pf.unbounded()],
                "positions": [{"kind": i.kind, "ref": i.ref, "symbol": i.symbol, "side": i.side, "asset_class": i.asset_class,
                               "strategy": i.strategy, "risk": fnum(i.risk and money(i.risk)), "risk_pct": pct(i.risk) if i.risk is not None else None,
                               "note": i.risk_note, "notional": fnum(i.notional and money(i.notional))} for i in pf.items],
                "by_instrument": {k: {"risk": fnum(money(v)), "risk_pct": pct(v)} for k, v in pf.group(lambda i: i.symbol).items()},
                "by_asset_class": {k: {"risk": fnum(money(v)), "risk_pct": pct(v)} for k, v in pf.group(lambda i: i.asset_class).items()},
                "by_direction": {k: {"risk": fnum(money(v)), "risk_pct": pct(v)} for k, v in pf.group(lambda i: i.side).items()},
                "by_strategy": {k: {"risk": fnum(money(v)), "risk_pct": pct(v)} for k, v in pf.group(lambda i: i.strategy).items()},
                "currency_exposure": {k: fnum(money(v)) for k, v in currency_exposure(ccy_rows).items()},
                "correlations": [{"a": c.a, "b": c.b, "coefficient": c.coefficient, "samples": c.samples, "lookback_bars": c.lookback_bars,
                                  "timeframe": c.timeframe, "last_common_bar": c.last_common_bar, "reliable": c.reliable,
                                  "reason": c.reason} for c in correlations]}

    def _view(self, ctx: AssessmentContext, results: list[RuleResult], decision: str, assessment_id: str, key: str, attempt: int,
              meta: dict[str, Any], correlation_id: str, expires: datetime, actor: str, ccy_rows: list[Any]) -> dict[str, Any]:
        m, p = ctx.math, ctx.proposal

        def section(name: str, extra: dict[str, Any]) -> dict[str, Any]:
            ids = STATUS_RULES[name]
            return {"status": status_label(results, ids), **extra,
                    "checks": [self._rule_view(r) for r in results if r.rule_id in ids]}
        _, affected, unreliable = correlated_items(ctx)
        mk = ctx.market
        return jsonable({
            "assessment_id": assessment_id, "trade_idea_id": p.trade_idea_id, "idempotency_key": key, "attempt": attempt,
            "decision": decision, "instrument": p.symbol, "asset_class": ctx.asset_class, "direction": p.direction,
            "strategy": p.strategy, "timeframe": p.timeframe, "proposal": p.canonical(),
            "requested_volume": fnum(p.requested_volume), "recommended_volume": fnum(m.recommended_volume) if m else None,
            "final_volume": fnum(m.final_volume) if m and decision in (PASS, REVIEW) else None,
            "risk_pct": fnum(m.estimated_loss_pct, 4) if m else None,
            "monetary_risk": fnum(money(m.estimated_loss)) if m and m.estimated_loss is not None else None,
            "potential_reward": fnum(money(m.estimated_reward)) if m and m.estimated_reward is not None else None,
            "risk_reward": fnum(m.adjusted_rr, 3) if m else None, "gross_risk_reward": fnum(m.rr, 3) if m else None,
            "account_currency": ctx.account.snapshot.currency if ctx.account.snapshot else None,
            "calculation": self._math_view(m),
            "account_status": section("account", {"summary": self.account_view(ctx.account, ctx.cfg)}),
            "portfolio_status": section("portfolio", {"summary": self.portfolio_view(ctx.portfolio, ctx.correlations, ccy_rows)}),
            "correlation_status": section("correlation", {"affected_positions": affected, "unreliable_symbols": unreliable}),
            "market_status": section("market", {"market_open": mk.market_open, "active_sessions": mk.active_sessions,
                                                "event_risk": mk.event_state, "calendar_complete": mk.calendar_complete,
                                                "volatility_regime": mk.regime, "atr": fnum(mk.atr),
                                                "spread_points": fnum(mk.spread_points, 2), "median_spread_points": fnum(mk.median_spread_points, 2),
                                                "quote_age_minutes": mk.quote_age_minutes, "module_04_available": mk.intelligence is not None,
                                                "module_04_reason": mk.intelligence_reason}),
            "passed_checks": [{"rule_id": r.rule_id, "name": r.name} for r in results if r.outcome == "pass"],
            "failed_checks": [self._rule_view(r) for r in results if r.outcome in ("fail", "error") and r.severity != "INFORMATIONAL"],
            "rule_violations": [self._rule_view(r) for r in results if r.outcome == "fail" and r.severity == HARD],
            "warnings": [self._rule_view(r) for r in results if r.outcome == "fail" and r.severity == WARNING],
            "errors": [self._rule_view(r) for r in results if r.outcome == "error"],
            "information": [self._rule_view(r) for r in results if r.severity == "INFORMATIONAL"],
            "skipped_checks": [{"rule_id": r.rule_id, "name": r.name, "message": r.message} for r in results if r.outcome == "skipped"],
            "active_locks": [{"lock_id": lk.lock_id, "scope": lk.scope, "scope_value": lk.scope_value, "reason": lk.reason} for lk in ctx.locks],
            "config_version": ctx.config_version, "config_profile": meta.get("profile"), "config_fingerprint": meta.get("fingerprint"),
            "engine_version": ENGINE_VERSION, "timestamp": ctx.now, "expires_at": expires, "correlation_id": correlation_id,
            "created_by": actor, "source": p.source,
            "module_07": {"eligible_for_human_review": decision in (PASS, REVIEW), "fully_risk_approved": decision == PASS,
                          "must_display_warnings": decision == REVIEW, "hard_block": decision == BLOCK,
                          "note": "Module 07 cannot override BLOCK or ERROR. Module 09 must revalidate before any order."},
            "disclaimer": DISCLAIMER})

    def _input_snapshot(self, ctx: AssessmentContext) -> dict[str, Any]:
        a, s, mk = ctx.account, ctx.spec, ctx.market
        return jsonable({
            "now": ctx.now, "engine_version": ENGINE_VERSION, "config_version": ctx.config_version,
            "config_fingerprint": ctx.cfg.fingerprint(), "proposal": ctx.proposal.canonical(),
            "account": {"status": a.status, "snapshot": a.snapshot.__dict__ if a.snapshot else None,
                        "periods": {k: p.__dict__ for k, p in a.periods.items()}, "peak_equity": a.peak_equity,
                        "consecutive_losses": a.consecutive_losses, "streak": a.streak_trade_ids, "trades_today": a.trades_today},
            "spec": s.__dict__ if s else None, "spec_error": ctx.spec_error.message if ctx.spec_error else None,
            "calc_error": ctx.calc_error.message if ctx.calc_error else None,
            "market": {"quote": mk.quote.__dict__ if mk.quote else None, "spread_points": mk.spread_points,
                       "median_spread_points": mk.median_spread_points, "market_open": mk.market_open,
                       "active_sessions": mk.active_sessions, "event_state": mk.event_state,
                       "calendar_complete": mk.calendar_complete, "regime": mk.regime, "atr": mk.atr,
                       "quality_issues": mk.quality_issues, "module_04_reason": mk.intelligence_reason},
            "portfolio": [i.__dict__ for i in ctx.portfolio.items],
            "correlations": [c.__dict__ for c in ctx.correlations],
            "locks": [lk.__dict__ for lk in ctx.locks], "active_idea_assessments": ctx.active_idea_assessments})

    @staticmethod
    def _account_row(a: AccountRisk, assessment_id: str | None, now: datetime) -> RiskAccountSnapshot:
        s = a.snapshot
        per = lambda k: a.periods[k].loss_pct if k in a.periods else None  # noqa: E731
        return RiskAccountSnapshot(assessment_id=assessment_id, computed_at=now, source_captured_at=s.captured_at if s else None,
                                   status=a.status, currency=s.currency if s else None, balance=s.balance if s else None,
                                   equity=s.equity if s else None, drawdown_pct=a.drawdown_pct, daily_loss_pct=per("day"),
                                   weekly_loss_pct=per("week"), monthly_loss_pct=per("month"),
                                   margin_utilization_pct=a.margin_utilization_pct, consecutive_losses=a.consecutive_losses,
                                   payload=jsonable({"reason": a.reason, "age_minutes": a.age_minutes, "streak": a.streak_trade_ids}))

    @staticmethod
    def _portfolio_row(pf: Portfolio, assessment_id: str | None, now: datetime) -> RiskPortfolioSnapshot:
        total = pf.total()
        return RiskPortfolioSnapshot(assessment_id=assessment_id, computed_at=now, total_open_risk=total,
                                     total_open_risk_pct=pf.pct(total),
                                     positions=sum(1 for i in pf.items if i.kind == "position"),
                                     reservations=sum(1 for i in pf.items if i.kind == "reservation"), bounded=pf.bounded(),
                                     payload=jsonable([i.__dict__ for i in pf.items]))

    # ---- account / portfolio reads for the dashboard -------------------------------------------------------------------
    def account_status(self) -> dict[str, Any]:
        now = self.clock()
        cfg, version, meta = self.repo.active_config()
        a = self._account(cfg, now)
        with self.repo.read() as s:
            locks = self.repo.active_locks(s, now)
        overall = ("DATA_UNAVAILABLE" if not a.usable else "BLOCKED" if locks else self._limit_status(a, cfg))
        return jsonable({"overall_status": overall, "config_version": version, "profile": meta.get("profile"),
                         "active_locks": len(locks), **self.account_view(a, cfg), "computed_at": now})

    @staticmethod
    def _limit_status(a: AccountRisk, cfg: RiskConfig) -> str:
        hits = []
        for k, lim in (("day", cfg.max_daily_loss_pct), ("week", cfg.max_weekly_loss_pct), ("month", cfg.max_monthly_loss_pct)):
            p = a.periods.get(k)
            if p is None or p.loss_pct is None:
                return "DATA_UNAVAILABLE"
            hits.append(p.loss_pct / D(lim))
        if a.drawdown_pct is not None:
            hits.append(a.drawdown_pct / D(cfg.max_drawdown_pct))
        hits.append(D(a.consecutive_losses) / D(cfg.max_consecutive_losses))
        worst = max(hits) if hits else D(0)
        return "BLOCKED" if worst >= 1 else "WARNING" if worst >= D("0.75") else "WITHIN_LIMITS"

    def portfolio_status(self, persist: bool = True) -> dict[str, Any]:
        now = self.clock()
        cfg, version, _ = self.repo.active_config()
        a = self._account(cfg, now)
        with self.repo.read() as s:
            reservations = self.repo.live_reservations(s, now)
        pf, ccy = self._portfolio(cfg, a, now, reservations)
        syms = sorted({i.symbol for i in pf.items})
        corr = [c for i, x in enumerate(syms) for c in self._correlations(cfg, x, syms[i + 1:], now)]
        view = self.portfolio_view(pf, corr, ccy)
        pct = pf.pct(pf.total())
        flags = []
        if not a.usable:
            flags.append(f"Account data {a.status}: exposure percentages cannot be trusted.")
        if not pf.bounded():
            flags.append("Some positions have no usable stop-loss: total risk is unbounded.")
        if pct is not None and pct > D(cfg.max_total_open_risk_pct):
            flags.append(f"Open risk {pct:.2f}% exceeds the {cfg.max_total_open_risk_pct}% limit.")
        for k, v in pf.group(lambda i: i.symbol).items():
            vp = pf.pct(v)
            if vp is not None and vp > D(cfg.max_symbol_risk_pct):
                flags.append(f"{k} risk {vp:.2f}% exceeds the {cfg.max_symbol_risk_pct}% per-instrument limit.")
        thr = cfg.correlation.threshold
        for c in corr:
            if c.reliable and c.coefficient is not None and abs(c.coefficient) >= thr:
                flags.append(f"{c.a} and {c.b} correlation {c.coefficient:+.2f} over {c.samples} {c.timeframe} returns.")
            elif not c.reliable:
                flags.append(f"{c.a}/{c.b} correlation unreliable: {c.reason}")
        status = ("DATA_UNAVAILABLE" if not a.usable else "BLOCKED" if (not pf.bounded() or (pct is not None and pct > D(cfg.max_total_open_risk_pct)))
                  else "WARNING" if flags else "WITHIN_LIMITS")
        if persist and self.repo.schema_ready():
            with self.repo.serialized() as s:
                s.add(self._portfolio_row(pf, None, now))
                self.repo.add_correlations(s, corr, now)
                self.repo.add_event(s, "risk.portfolio.exposure.updated", None,
                                    {"total_open_risk": view["total_open_risk"], "total_open_risk_pct": view["total_open_risk_pct"],
                                     "status": status, "positions": len(pf.items)},
                                    correlation_id=None, idempotency_key=f"exposure:{stable_hash(view)[:32]}:{now:%Y%m%d%H%M}", now=now)
        return jsonable({"status": status, "concentration_warnings": flags, "max_total_open_risk_pct": cfg.max_total_open_risk_pct,
                         "max_symbol_risk_pct": cfg.max_symbol_risk_pct, "max_asset_class_risk_pct": cfg.max_asset_class_risk_pct,
                         "max_correlated_risk_pct": cfg.max_correlated_risk_pct, "config_version": version, "computed_at": now, **view})


class IdempotencyConflict(ValueError):
    pass
