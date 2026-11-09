"""The Module 06 rule catalog and the decision function.

Every rule has an id, a name, a category, a default severity and a pure evaluation function over an AssessmentContext,
so identical inputs and configuration always give identical results. Severities:

* HARD_BLOCK - a failure blocks the trade. Nothing (a warning, Module 05 confidence, Module 07, n8n) can override it.
* WARNING - a failure sends the trade to REVIEW: it is not fully risk-approved and Module 07 must show the warning.
* INFORMATIONAL - recorded context only.

Outcomes are pass | fail | error | skipped. ``error`` means the rule could not be evaluated from trustworthy data.

Decision: any HARD_BLOCK failure -> BLOCK; else any error -> ERROR; else any WARNING failure -> REVIEW; else PASS.
A definite hard violation (a lock, a stop on the wrong side) is reported as BLOCK even when other data is also missing;
ERROR is never turned into PASS.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable

from aurum.risk.calculator import volume_problems
from aurum.risk.models import BLOCK, ERROR, HARD, INFO, PASS, REVIEW, WARNING, AssessmentContext, RuleResult
from aurum.risk.money import HUNDRED, ZERO, D, fnum, is_multiple
from aurum.risk.portfolio import same_way

Outcome = tuple[str, str, str, dict[str, Any], str | None]   # outcome, reason code, message, evidence, severity override


def ok(msg: str = "Passed.", **ev: Any) -> Outcome:
    return "pass", "OK", msg, ev, None


def fail(code: str, msg: str, severity: str | None = None, **ev: Any) -> Outcome:
    return "fail", code, msg, ev, severity


def err(code: str, msg: str, **ev: Any) -> Outcome:
    return "error", code, msg, ev, None


def skip(msg: str, **ev: Any) -> Outcome:
    return "skipped", "NOT_APPLICABLE", msg, ev, None


def unknown(policy: str, code: str, msg: str, **ev: Any) -> Outcome:
    """Missing market-condition data is never a pass: per policy it is a warning (review) or a hard block."""
    return "fail", code, msg, ev, HARD if policy == "block" else WARNING


@dataclass(frozen=True)
class Rule:
    rule_id: str
    name: str
    category: str
    severity: str
    description: str
    fn: Callable[[AssessmentContext], Outcome]
    thresholds: Callable[[Any], dict[str, Any]] = lambda cfg: {}
    asset_classes: tuple[str, ...] = ()     # empty = all


def _pct(x: Decimal | None) -> float | None:
    return fnum(x, 4)


def _equity(ctx: AssessmentContext) -> Decimal | None:
    return ctx.account.snapshot.equity if ctx.account.snapshot else None


# ---- data integrity ------------------------------------------------------------------------------------------------------
def r_account(ctx: AssessmentContext) -> Outcome:
    a = ctx.account
    if a.status == "ok":
        return ok("Account snapshot is present, DEMO and fresh.", age_minutes=a.age_minutes)
    return err(f"ACCOUNT_{a.status.upper()}", a.reason or f"Account data is {a.status}.", age_minutes=a.age_minutes)


def r_spec(ctx: AssessmentContext) -> Outcome:
    if ctx.spec_error is not None:
        return err(ctx.spec_error.code, ctx.spec_error.message)
    if ctx.spec is None:
        return err("SPEC_MISSING", f"No validated instrument specification for {ctx.proposal.symbol}.")
    age_h = (ctx.now - ctx.spec.captured_at).total_seconds() / 3600
    if age_h > ctx.cfg.spec_max_age_hours:
        return err("SPEC_STALE", f"Instrument specification is {age_h:.0f} h old (limit {ctx.cfg.spec_max_age_hours:g} h); "
                                 "run `python -m aurum.cli specs`.", age_hours=round(age_h, 1))
    return ok("Broker specification is present and fresh.", age_hours=round(age_h, 1), captured_at=ctx.spec.captured_at)


def r_calc(ctx: AssessmentContext) -> Outcome:
    if ctx.calc_error is not None:
        return err(ctx.calc_error.code, ctx.calc_error.message)
    if ctx.math is None:
        return skip("Not calculated because the trade proposal or its inputs failed validation.")
    m = ctx.math
    return ok("Risk calculated.", value_method=m.value_method, conversion=m.conversion, notes=m.notes)


# ---- trade validation ----------------------------------------------------------------------------------------------------
def r_stop_required(ctx: AssessmentContext) -> Outcome:
    if ctx.proposal.stop_loss is None:
        return fail("STOP_MISSING", "A stop-loss is mandatory. Submit a corrected proposal; Module 06 never adds one.")
    return ok()


def r_stop_side(ctx: AssessmentContext) -> Outcome:
    p = ctx.proposal
    if p.stop_loss is None:
        return skip("No stop-loss.")
    if p.direction == "BUY" and p.stop_loss >= p.entry:
        return fail("STOP_WRONG_SIDE", f"BUY stop-loss {p.stop_loss} must be below entry {p.entry}.")
    if p.direction == "SELL" and p.stop_loss <= p.entry:
        return fail("STOP_WRONG_SIDE", f"SELL stop-loss {p.stop_loss} must be above entry {p.entry}.")
    return ok()


def r_take_profit(ctx: AssessmentContext) -> Outcome:
    p = ctx.proposal
    if p.take_profit is None:
        return fail("TP_MISSING", "A take-profit is required to measure reward-to-risk.")
    if p.direction == "BUY" and p.take_profit <= p.entry:
        return fail("TP_WRONG_SIDE", f"BUY take-profit {p.take_profit} must be above entry {p.entry}.")
    if p.direction == "SELL" and p.take_profit >= p.entry:
        return fail("TP_WRONG_SIDE", f"SELL take-profit {p.take_profit} must be below entry {p.entry}.")
    return ok()


def r_precision(ctx: AssessmentContext) -> Outcome:
    if ctx.spec is None:
        return skip("No specification.")
    tick = ctx.spec.tick_size
    bad = {name: str(v) for name, v in (("entry", ctx.proposal.entry), ("stop_loss", ctx.proposal.stop_loss),
                                         ("take_profit", ctx.proposal.take_profit)) if v is not None and not is_multiple(v, tick)}
    if bad:
        return fail("PRICE_NOT_ON_TICK", f"Prices must be multiples of the tick size {tick}: {bad}.", prices=bad, tick_size=str(tick))
    return ok(tick_size=str(tick))


def r_min_stop(ctx: AssessmentContext) -> Outcome:
    p, s = ctx.proposal, ctx.spec
    if s is None or p.stop_loss is None:
        return skip("No specification or stop.")
    dist = abs(p.entry - p.stop_loss)
    broker = s.stops_level_points * s.point
    if broker > 0 and dist < broker:
        return fail("STOP_INSIDE_BROKER_LEVEL", f"Stop distance {dist} is inside the broker stop level {broker}.",
                    stop_distance=str(dist), broker_min=str(broker))
    spread = ctx.market.quote.spread if ctx.market.quote else (ctx.market.spread_points * s.point if ctx.market.spread_points is not None else None)
    mult = D(ctx.cfg.min_stop_spread_multiple)
    if spread is not None and spread > 0 and mult > 0 and dist < mult * spread:
        return fail("STOP_TOO_TIGHT_FOR_SPREAD", f"Stop distance {dist} is under {mult} x the spread ({spread}).",
                    stop_distance=str(dist), spread=str(spread))
    return ok(stop_distance=str(dist), broker_min=str(broker), spread=None if spread is None else str(spread))


def r_stop_atr(ctx: AssessmentContext) -> Outcome:
    c = ctx.cfg.atr_check
    p = ctx.proposal
    if not c.enabled or p.stop_loss is None:
        return skip("ATR check disabled or no stop.")
    atr = ctx.market.atr
    if atr is None or atr <= 0:
        return fail("ATR_UNAVAILABLE", "ATR is unavailable, so the stop width could not be sanity-checked.", WARNING)
    ratio = abs(p.entry - p.stop_loss) / atr
    ev = {"stop_atr": fnum(ratio, 3), "atr": str(atr), "timeframe": ctx.market.atr_timeframe}
    if ratio < D(c.min_stop_atr):
        return fail("STOP_TIGHT_VS_ATR", f"Stop is {ratio:.2f} ATR, tighter than {c.min_stop_atr} ATR: likely to be hit by noise.", **ev)
    if ratio > D(c.max_stop_atr):
        return fail("STOP_WIDE_VS_ATR", f"Stop is {ratio:.2f} ATR, wider than {c.max_stop_atr} ATR.", **ev)
    return ok(**ev)


def r_min_rr(ctx: AssessmentContext) -> Outcome:
    m = ctx.math
    if m is None or m.adjusted_rr is None:
        return skip("Reward-to-risk not computed.")
    ev = {"rr": fnum(m.rr, 3), "adjusted_rr": fnum(m.adjusted_rr, 3)}
    if m.adjusted_rr < D(ctx.cfg.min_risk_reward):
        return fail("RR_BELOW_MINIMUM", f"Reward-to-risk after costs is {m.adjusted_rr:.2f}, below {ctx.cfg.min_risk_reward}.", **ev)
    return ok(**ev)


def r_risk_pct(ctx: AssessmentContext) -> Outcome:
    req = ctx.proposal.risk_pct
    if req is None:
        return ok("Configured risk per trade used.", risk_pct=ctx.cfg.risk_per_trade_pct)
    if req <= 0:
        return fail("RISK_PCT_INVALID", "Risk percentage must be positive.")
    if req > D(ctx.cfg.max_risk_per_trade_pct):
        return fail("RISK_PCT_EXCESSIVE", f"Requested risk {req}% exceeds the maximum {ctx.cfg.max_risk_per_trade_pct}% per trade.",
                    requested=str(req))
    return ok(requested=str(req))


def r_min_volume(ctx: AssessmentContext) -> Outcome:
    m, s = ctx.math, ctx.spec
    if m is None or s is None:
        return skip("Not computed.")
    if m.recommended_volume is None:
        return fail("MIN_VOLUME_EXCEEDS_BUDGET",
                    f"The broker minimum {s.volume_min} lots would risk {m.min_volume_risk_pct:.2f}% of equity, above the "
                    f"{m.risk_pct}% budget. The trade is rejected rather than risking more.",
                    min_volume_risk_pct=_pct(m.min_volume_risk_pct), raw_volume=fnum(m.raw_volume))
    return ok(recommended_volume=str(m.recommended_volume), raw_volume=fnum(m.raw_volume))


def r_requested_volume(ctx: AssessmentContext) -> Outcome:
    v, m, s = ctx.proposal.requested_volume, ctx.math, ctx.spec
    if v is None:
        return skip("No volume requested; the recommended volume is used.")
    if s is None:
        return skip("No specification.")
    problems = volume_problems(s, v)
    if problems:
        return fail("VOLUME_INVALID", " ".join(problems), requested=str(v))
    if m is not None and m.recommended_volume is not None and v > m.recommended_volume:
        return fail("VOLUME_OVER_BUDGET", f"Requested {v} lots exceeds the {m.recommended_volume} lots the risk budget allows.",
                    requested=str(v), recommended=str(m.recommended_volume))
    return ok(requested=str(v))


def r_max_volume(ctx: AssessmentContext) -> Outcome:
    m = ctx.math
    if m is None:
        return skip("Not computed.")
    if m.capped_by_max_volume:
        return fail("VOLUME_CAPPED_AT_MAX", f"The risk budget allows {m.raw_volume:.2f} lots; capped at the broker maximum.",
                    raw_volume=fnum(m.raw_volume))
    return ok()


# ---- account limits ------------------------------------------------------------------------------------------------------
def _period_rule(kind: str, limit_attr: str, code: str) -> Callable[[AssessmentContext], Outcome]:
    def fn(ctx: AssessmentContext) -> Outcome:
        if not ctx.account.snapshot:
            return skip("No account data.")
        p = ctx.account.periods.get(kind)
        limit = D(getattr(ctx.cfg, limit_attr))
        if p is None or p.loss_pct is None:
            return unknown(ctx.cfg.data_unavailable_policy, f"{code}_UNKNOWN",
                           f"The {kind} starting {ctx.cfg.loss_basis} is unknown, so the {kind} loss cannot be measured.")
        ev = {"period": p.label, "loss_pct": _pct(p.loss_pct), "start_value": fnum(p.start_value, 2),
              "current_value": fnum(p.current_value, 2), "net_deposits": fnum(p.net_deposits, 2),
              "realized_pnl": fnum(p.realized_pnl, 2), "start_source": p.start_source, "basis": ctx.cfg.loss_basis}
        if p.loss_pct >= limit:
            return fail(code, f"{kind.capitalize()} loss {p.loss_pct:.2f}% has reached the {limit}% limit.", **ev)
        return ok(**ev)
    return fn


def r_period_start(ctx: AssessmentContext) -> Outcome:
    uncertain = {k: p.start_source for k, p in ctx.account.periods.items() if p.start_uncertain}
    if uncertain:
        return fail("PERIOD_START_UNCERTAIN", "Starting equity for some periods was not captured near the period start, so "
                                              "those loss figures are approximate.", periods=uncertain)
    return ok() if ctx.account.periods else skip("No account data.")


def r_drawdown(ctx: AssessmentContext) -> Outcome:
    a = ctx.account
    if a.drawdown_pct is None:
        return skip("No account data.")
    ev = {"drawdown_pct": _pct(a.drawdown_pct), "peak_equity": fnum(a.peak_equity, 2)}
    if a.drawdown_pct >= D(ctx.cfg.max_drawdown_pct):
        return fail("MAX_DRAWDOWN_REACHED", f"Equity drawdown {a.drawdown_pct:.2f}% has reached the {ctx.cfg.max_drawdown_pct}% limit.", **ev)
    return ok(**ev)


def r_consecutive(ctx: AssessmentContext) -> Outcome:
    a = ctx.account
    if not a.snapshot:
        return skip("No account data.")
    ev = {"consecutive_losses": a.consecutive_losses, "trades": a.streak_trade_ids}
    if a.consecutive_losses >= ctx.cfg.max_consecutive_losses:
        return fail("CONSECUTIVE_LOSSES", f"{a.consecutive_losses} consecutive losing trades reached the limit of "
                                          f"{ctx.cfg.max_consecutive_losses}.", **ev)
    return ok(**ev)


def r_trades_per_day(ctx: AssessmentContext) -> Outcome:
    if not ctx.account.snapshot:
        return skip("No account data.")
    n = ctx.account.trades_today
    if n >= ctx.cfg.max_trades_per_day:
        return fail("TRADES_PER_DAY", f"{n} trades already opened this trading day (limit {ctx.cfg.max_trades_per_day}).", trades_today=n)
    return ok(trades_today=n)


def r_margin(ctx: AssessmentContext) -> Outcome:
    snap, m = ctx.account.snapshot, ctx.math
    if snap is None:
        return skip("No account data.")
    if snap.margin_free is not None and ctx.cfg.min_free_margin > 0 and snap.margin_free < D(ctx.cfg.min_free_margin):
        return fail("FREE_MARGIN_LOW", f"Free margin {snap.margin_free} is below the minimum {ctx.cfg.min_free_margin}.")
    used = snap.margin or ZERO
    if m is None or m.margin_estimate is None:
        cur = used / snap.equity * HUNDRED
        if cur >= D(ctx.cfg.max_margin_utilization_pct):
            return fail("MARGIN_UTILIZATION", f"Margin utilisation is already {cur:.1f}%.", utilization_pct=_pct(cur))
        return fail("MARGIN_ESTIMATE_UNAVAILABLE", "The new trade's margin could not be estimated for this instrument.",
                    WARNING, current_utilization_pct=_pct(cur))
    projected = (used + m.margin_estimate) / snap.equity * HUNDRED
    ev = {"projected_utilization_pct": _pct(projected), "margin_estimate": fnum(m.margin_estimate, 2), "method": m.margin_method}
    if snap.margin_free is not None and m.margin_estimate > snap.margin_free:
        return fail("INSUFFICIENT_FREE_MARGIN", "Estimated margin exceeds free margin.", **ev)
    if projected > D(ctx.cfg.max_margin_utilization_pct):
        return fail("MARGIN_UTILIZATION", f"Projected margin utilisation {projected:.1f}% exceeds {ctx.cfg.max_margin_utilization_pct}%.", **ev)
    return ok(**ev)


# ---- portfolio -----------------------------------------------------------------------------------------------------------
def _new_risk(ctx: AssessmentContext) -> Decimal | None:
    return ctx.math.estimated_loss if ctx.math else None


def r_positions(ctx: AssessmentContext) -> Outcome:
    n = ctx.portfolio.positions_count()
    if n + 1 > ctx.cfg.max_open_positions:
        return fail("MAX_POSITIONS", f"{n} open positions and live reservations; one more exceeds the limit of "
                                     f"{ctx.cfg.max_open_positions}.", open_and_reserved=n)
    return ok(open_and_reserved=n)


def r_total_risk(ctx: AssessmentContext) -> Outcome:
    pf, new = ctx.portfolio, _new_risk(ctx)
    if pf.unbounded():
        refs = [f"{i.symbol} {i.ref}" for i in pf.unbounded()]
        return fail("OPEN_RISK_UNBOUNDED", f"Open positions without a usable stop make total risk unbounded: {', '.join(refs)}.",
                    positions=refs, notes=[i.risk_note for i in pf.unbounded()])
    if new is None or pf.equity is None:
        return skip("New trade risk not computed.")
    total = pf.total() + new
    pct = pf.pct(total)
    ev = {"current_open_risk": fnum(pf.total(), 2), "projected_open_risk": fnum(total, 2), "projected_pct": _pct(pct)}
    if pct is not None and pct > D(ctx.cfg.max_total_open_risk_pct):
        return fail("TOTAL_OPEN_RISK", f"Projected open risk {pct:.2f}% exceeds {ctx.cfg.max_total_open_risk_pct}%.", **ev)
    return ok(**ev)


def r_symbol_risk(ctx: AssessmentContext) -> Outcome:
    new = _new_risk(ctx)
    if new is None:
        return skip("New trade risk not computed.")
    total = ctx.portfolio.total(lambda i: i.symbol == ctx.proposal.symbol) + new
    pct = ctx.portfolio.pct(total)
    if pct is not None and pct > D(ctx.cfg.max_symbol_risk_pct):
        return fail("SYMBOL_RISK", f"Projected risk on {ctx.proposal.symbol} is {pct:.2f}% (limit {ctx.cfg.max_symbol_risk_pct}%).",
                    projected_pct=_pct(pct))
    return ok(projected_pct=_pct(pct))


def r_class_risk(ctx: AssessmentContext) -> Outcome:
    new = _new_risk(ctx)
    if new is None:
        return skip("New trade risk not computed.")
    total = ctx.portfolio.total(lambda i: i.asset_class == ctx.asset_class) + new
    pct = ctx.portfolio.pct(total)
    if pct is not None and pct > D(ctx.cfg.max_asset_class_risk_pct):
        return fail("ASSET_CLASS_RISK", f"Projected {ctx.asset_class} risk is {pct:.2f}% (limit {ctx.cfg.max_asset_class_risk_pct}%).",
                    asset_class=ctx.asset_class, projected_pct=_pct(pct))
    return ok(asset_class=ctx.asset_class, projected_pct=_pct(pct))


def correlated_items(ctx: AssessmentContext) -> tuple[Decimal, list[dict[str, Any]], list[str]]:
    """Risk of existing items that would lose together with the new trade, plus symbols whose correlation is unreliable."""
    p, thr = ctx.proposal, ctx.cfg.correlation.threshold
    by_pair = {c.b: c for c in ctx.correlations}
    total, affected, unreliable = ZERO, [], []
    for i in ctx.portfolio.items:
        if i.symbol == p.symbol:
            if i.side == p.direction:
                total += i.risk or ZERO
                affected.append({"ref": i.ref, "symbol": i.symbol, "side": i.side, "coefficient": 1.0, "assumed": False})
            continue
        c = by_pair.get(i.symbol)
        if c is None or not c.reliable or c.coefficient is None:
            unreliable.append(i.symbol)
            if ctx.cfg.correlation.unreliable_policy == "assume_correlated":
                total += i.risk or ZERO
                affected.append({"ref": i.ref, "symbol": i.symbol, "side": i.side, "coefficient": None, "assumed": True})
            continue
        if abs(c.coefficient) >= thr and same_way(i.side, p.direction, c.coefficient):
            total += i.risk or ZERO
            affected.append({"ref": i.ref, "symbol": i.symbol, "side": i.side, "coefficient": c.coefficient, "assumed": False})
    return total, affected, sorted(set(unreliable))


def r_correlated(ctx: AssessmentContext) -> Outcome:
    new = _new_risk(ctx)
    if new is None:
        return skip("New trade risk not computed.")
    existing, affected, _ = correlated_items(ctx)
    pct = ctx.portfolio.pct(existing + new)
    ev = {"projected_pct": _pct(pct), "affected_positions": affected}
    if pct is not None and pct > D(ctx.cfg.max_correlated_risk_pct):
        return fail("CORRELATED_RISK", f"New trade plus correlated exposure is {pct:.2f}% (limit {ctx.cfg.max_correlated_risk_pct}%).", **ev)
    return ok(**ev)


def r_correlation_data(ctx: AssessmentContext) -> Outcome:
    _, _, unreliable = correlated_items(ctx)
    if unreliable:
        details = {c.b: c.reason for c in ctx.correlations if c.b in unreliable}
        return fail("CORRELATION_UNRELIABLE", f"Correlation with {', '.join(unreliable)} is unreliable; policy "
                                              f"'{ctx.cfg.correlation.unreliable_policy}' applied.", symbols=unreliable, reasons=details)
    return ok(pairs=len(ctx.correlations))


def r_duplicate_position(ctx: AssessmentContext) -> Outcome:
    same = [i.ref for i in ctx.portfolio.items if i.symbol == ctx.proposal.symbol and i.side == ctx.proposal.direction]
    if same:
        return fail("SAME_DIRECTION_EXPOSURE", f"Already exposed {ctx.proposal.direction} on {ctx.proposal.symbol}: {', '.join(same)}.", refs=same)
    return ok()


def r_duplicate_idea(ctx: AssessmentContext) -> Outcome:
    if ctx.active_idea_assessments:
        return fail("DUPLICATE_TRADE_IDEA", "This trade idea already has an unexpired PASS/REVIEW assessment with different "
                                            "parameters; let it expire or cancel it first.", assessments=ctx.active_idea_assessments)
    return ok()


# ---- market conditions ---------------------------------------------------------------------------------------------------
def r_market_open(ctx: AssessmentContext) -> Outcome:
    mk = ctx.market
    if mk.intelligence is None:
        return unknown(ctx.cfg.data_unavailable_policy, "SESSIONS_UNAVAILABLE",
                       f"Module 04 is unavailable ({mk.intelligence_reason}); market hours were not checked.")
    if mk.market_open is False:
        return fail("MARKET_CLOSED", f"{ctx.proposal.symbol} market is closed.")
    if mk.market_open is None:
        return unknown(ctx.cfg.data_unavailable_policy, "MARKET_HOURS_UNKNOWN", "Market open/closed state is unknown.")
    return ok(active_sessions=mk.active_sessions)


def r_quote(ctx: AssessmentContext) -> Outcome:
    mk = ctx.market
    if mk.quote is None:
        return unknown(ctx.cfg.data_unavailable_policy, "QUOTE_UNAVAILABLE", "No stored quote; price freshness was not checked.")
    ev = {"age_minutes": mk.quote_age_minutes, "bid": str(mk.quote.bid), "ask": str(mk.quote.ask), "source": mk.quote.source}
    if mk.quote.bid <= 0 or mk.quote.ask < mk.quote.bid:
        return err("QUOTE_INVALID", "Stored quote is invalid (non-positive bid or ask below bid).", **ev)
    if mk.quote_age_minutes is not None and mk.quote_age_minutes > ctx.cfg.quote_max_age_minutes and mk.market_open is not False:
        return fail("QUOTE_STALE", f"Latest quote is {mk.quote_age_minutes:.0f} min old while the market is not known to be "
                                   f"closed (limit {ctx.cfg.quote_max_age_minutes:g} min).", **ev)
    return ok(**ev)


def r_spread(ctx: AssessmentContext) -> Outcome:
    mk, cfg = ctx.market, ctx.cfg
    if mk.spread_points is None:
        return unknown(cfg.data_unavailable_policy, "SPREAD_UNAVAILABLE", "Current spread is unknown.")
    ev = {"spread_points": fnum(mk.spread_points, 2), "median_spread_points": fnum(mk.median_spread_points, 2)}
    cap = cfg.max_spread_points.get(ctx.proposal.symbol)
    if cap is not None and mk.spread_points > D(cap):
        return fail("SPREAD_ABOVE_MAX", f"Spread {mk.spread_points} points exceeds the {cap}-point maximum.", **ev)
    if mk.median_spread_points and mk.spread_points > D(cfg.max_spread_median_multiple) * mk.median_spread_points:
        return fail("SPREAD_ABNORMAL", f"Spread is more than {cfg.max_spread_median_multiple} x its median.", **ev)
    return ok(**ev)


def r_news(ctx: AssessmentContext) -> Outcome:
    mk, pol = ctx.market, ctx.cfg.news
    if mk.intelligence is None:
        return unknown(pol.unknown_policy, "NEWS_FILTER_UNAVAILABLE", "Module 04 is unavailable; economic events were NOT checked.")
    state = mk.event_state
    risk = (mk.intelligence.get("event_risk") or {})
    ev = {"state": state, "reason": risk.get("reason"), "calendar_complete": mk.calendar_complete,
          "next_event": risk.get("next_event")}
    if state in pol.block_states:
        return fail("HIGH_IMPACT_EVENT", f"Economic event risk is {state}.", **ev)
    if state == "UNKNOWN" or not mk.calendar_complete:
        return unknown(pol.unknown_policy, "CALENDAR_INCOMPLETE", "Economic calendar coverage is incomplete, so a quiet "
                                                                  "calendar cannot be confirmed.", **ev)
    if state in pol.warn_states:
        return fail("EVENT_APPROACHING", f"Economic event risk is {state}.", WARNING, **ev)
    return ok(**ev)


def r_volatility(ctx: AssessmentContext) -> Outcome:
    mk, pol = ctx.market, ctx.cfg.volatility
    regime = mk.regime
    if regime == "UNKNOWN":
        return unknown(pol.unknown_policy, "VOLATILITY_UNKNOWN", "Volatility regime is unknown.")
    if regime in pol.block_regimes:
        return fail("VOLATILITY_EXTREME", f"Volatility regime is {regime}.", regime=regime)
    if regime in pol.warn_regimes:
        return fail("VOLATILITY_UNUSUAL", f"Volatility regime is {regime}.", WARNING, regime=regime)
    return ok(regime=regime)


def r_session(ctx: AssessmentContext) -> Outcome:
    from aurum.risk.calendar import TradingCalendar
    pol, mk = ctx.cfg.sessions, ctx.market
    wd = TradingCalendar(ctx.cfg.trading_day).weekday(ctx.now)
    if wd in pol.blocked_weekdays:
        return fail("WEEKDAY_BLOCKED", f"Trading-day weekday {wd} is restricted.", weekday=wd)
    blocked = set(pol.blocked_sessions.get(ctx.proposal.symbol, [])) | set(pol.blocked_sessions.get(ctx.asset_class, []))
    if not blocked:
        return ok(weekday=wd)
    if mk.intelligence is None:
        return unknown(ctx.cfg.data_unavailable_policy, "SESSIONS_UNAVAILABLE", "Session restrictions exist but sessions are unknown.")
    hit = sorted(blocked & set(mk.active_sessions))
    if hit:
        return fail("SESSION_BLOCKED", f"Trading is restricted during {', '.join(hit)}.", sessions=hit)
    return ok(weekday=wd, active_sessions=mk.active_sessions)


def r_data_quality(ctx: AssessmentContext) -> Outcome:
    issues = ctx.market.quality_issues
    if issues:
        return fail("DATA_QUALITY_ISSUES", f"{len(issues)} unresolved recent Module 01 data-quality issue(s).", issues=issues[:10])
    return ok()


# ---- locks and context ---------------------------------------------------------------------------------------------------
def r_locks(ctx: AssessmentContext) -> Outcome:
    login = ctx.account.snapshot.login if ctx.account.snapshot else None
    hits = [lk for lk in ctx.locks if lk.applies(ctx.proposal.symbol, ctx.proposal.strategy, login)]
    if hits:
        return fail("RISK_LOCK_ACTIVE", "Active risk lock: " + "; ".join(f"{lk.scope} - {lk.reason}" for lk in hits),
                    locks=[{"lock_id": lk.lock_id, "scope": lk.scope, "scope_value": lk.scope_value, "reason": lk.reason} for lk in hits])
    return ok()


def r_cot(ctx: AssessmentContext) -> Outcome:
    c = ctx.cot or {}
    if not c.get("available"):
        return skip("COT context unavailable.", reason=c.get("reason"))
    lean = str(c.get("lean") or c.get("stance") or "").lower()
    against = ("bearish" in lean and ctx.proposal.direction == "BUY") or ("bullish" in lean and ctx.proposal.direction == "SELL")
    msg = "COT positioning leans against this direction (context only; never changes the risk decision)." if against else "COT context recorded."
    return fail("COT_OPPOSES", msg, stance=c.get("stance"), lean=c.get("lean")) if against else ok(msg, stance=c.get("stance"))


def r_ai(ctx: AssessmentContext) -> Outcome:
    return ok("Module 05 confidence is recorded for audit and is not used by any risk rule.",
              ai_confidence=ctx.proposal.ai_confidence)


def _t(*keys: str) -> Callable[[Any], dict[str, Any]]:
    def f(cfg: Any) -> dict[str, Any]:
        out = {}
        for k in keys:
            obj = cfg
            for part in k.split("."):
                obj = getattr(obj, part)
            out[k] = obj.model_dump() if hasattr(obj, "model_dump") else obj
        return out
    return f


RULES: tuple[Rule, ...] = (
    Rule("R001", "Account data integrity", "data", HARD, "Account snapshot must exist, be DEMO, valid and fresh.", r_account, _t("account_max_age_minutes")),
    Rule("R002", "Instrument specification", "data", HARD, "A validated, fresh broker specification is required.", r_spec, _t("spec_max_age_hours")),
    Rule("R003", "Risk calculation", "data", HARD, "Money at risk must be computable in the account currency.", r_calc, _t("conversion_max_age_minutes")),
    Rule("R010", "Stop-loss required", "trade", HARD, "Every trade needs a stop-loss.", r_stop_required),
    Rule("R011", "Stop-loss side", "trade", HARD, "BUY stop below entry; SELL stop above entry.", r_stop_side),
    Rule("R012", "Take-profit", "trade", HARD, "Take-profit required and on the profitable side.", r_take_profit),
    Rule("R013", "Price precision", "trade", HARD, "Entry, stop and target must sit on the tick grid.", r_precision),
    Rule("R014", "Minimum stop distance", "trade", HARD, "Stop outside broker stop level and a multiple of the spread.", r_min_stop, _t("min_stop_spread_multiple")),
    Rule("R015", "Stop width vs ATR", "trade", WARNING, "Optional ATR sanity check; never moves the stop.", r_stop_atr, _t("atr_check")),
    Rule("R016", "Minimum reward-to-risk", "trade", HARD, "Reward-to-risk after costs must meet the minimum.", r_min_rr, _t("min_risk_reward")),
    Rule("R017", "Risk per trade", "trade", HARD, "Requested risk percentage cannot exceed the maximum.", r_risk_pct, _t("risk_per_trade_pct", "max_risk_per_trade_pct")),
    Rule("R018", "Minimum lot within budget", "trade", HARD, "Reject when the minimum lot risks more than the budget.", r_min_volume),
    Rule("R019", "Requested volume", "trade", HARD, "A requested volume must be valid and within the risk budget.", r_requested_volume),
    Rule("R020", "Maximum volume cap", "trade", WARNING, "Budget allows more than the broker maximum lot.", r_max_volume),
    Rule("R030", "Daily loss limit", "account", HARD, "Daily loss below the limit.", _period_rule("day", "max_daily_loss_pct", "DAILY_LOSS_LIMIT"), _t("max_daily_loss_pct", "loss_basis")),
    Rule("R031", "Weekly loss limit", "account", HARD, "Weekly loss below the limit.", _period_rule("week", "max_weekly_loss_pct", "WEEKLY_LOSS_LIMIT"), _t("max_weekly_loss_pct", "loss_basis")),
    Rule("R032", "Monthly loss limit", "account", HARD, "Monthly loss below the limit.", _period_rule("month", "max_monthly_loss_pct", "MONTHLY_LOSS_LIMIT"), _t("max_monthly_loss_pct", "loss_basis")),
    Rule("R033", "Maximum drawdown", "account", HARD, "Equity drawdown from peak below the limit.", r_drawdown, _t("max_drawdown_pct")),
    Rule("R034", "Period start certainty", "account", WARNING, "Period starting equity captured near the boundary.", r_period_start, _t("period_start_tolerance_minutes")),
    Rule("R035", "Consecutive losses", "account", HARD, "Consecutive losing trades below the limit.", r_consecutive, _t("max_consecutive_losses", "breakeven_tolerance_pct")),
    Rule("R036", "Trades per day", "account", HARD, "Trades opened this trading day below the limit.", r_trades_per_day, _t("max_trades_per_day")),
    Rule("R037", "Margin", "account", HARD, "Free margin and projected margin utilisation within limits.", r_margin, _t("max_margin_utilization_pct", "min_free_margin")),
    Rule("R040", "Open positions", "portfolio", HARD, "Open positions plus reservations below the limit.", r_positions, _t("max_open_positions")),
    Rule("R041", "Total open risk", "portfolio", HARD, "Projected aggregate open risk within the limit; unbounded risk blocks.", r_total_risk, _t("max_total_open_risk_pct")),
    Rule("R042", "Risk per symbol", "portfolio", HARD, "Projected risk on one instrument within the limit.", r_symbol_risk, _t("max_symbol_risk_pct")),
    Rule("R043", "Risk per asset class", "portfolio", HARD, "Projected risk in one asset class within the limit.", r_class_risk, _t("max_asset_class_risk_pct")),
    Rule("R044", "Correlated exposure", "portfolio", HARD, "New trade plus positions correlated with it within the limit.", r_correlated, _t("max_correlated_risk_pct", "correlation")),
    Rule("R045", "Correlation reliability", "portfolio", WARNING, "Correlation estimates must be reliable.", r_correlation_data, _t("correlation")),
    Rule("R046", "Same-direction exposure", "portfolio", WARNING, "Already exposed in the same direction on this instrument.", r_duplicate_position),
    Rule("R047", "Duplicate trade idea", "portfolio", HARD, "One live assessment per trade idea.", r_duplicate_idea),
    Rule("R050", "Market open", "market", HARD, "Market must be open (Module 04 sessions).", r_market_open, _t("data_unavailable_policy")),
    Rule("R051", "Quote freshness", "market", HARD, "Latest quote must be recent while the market is open.", r_quote, _t("quote_max_age_minutes")),
    Rule("R052", "Spread", "market", HARD, "Spread below its cap and not abnormal versus its median.", r_spread, _t("max_spread_points", "max_spread_median_multiple")),
    Rule("R053", "Economic events", "market", HARD, "No high-impact event window (Module 04 calendar).", r_news, _t("news")),
    Rule("R054", "Volatility regime", "market", HARD, "Volatility regime allowed (Module 04).", r_volatility, _t("volatility")),
    Rule("R055", "Session and weekday restrictions", "market", HARD, "Configured session/weekday restrictions.", r_session, _t("sessions")),
    Rule("R056", "Market data quality", "market", WARNING, "No unresolved recent Module 01 data-quality issues.", r_data_quality),
    Rule("R060", "Risk locks", "protection", HARD, "No active risk lock applies.", r_locks),
    Rule("R070", "COT context", "context", INFO, "Module 02 positioning, informational only.", r_cot),
    Rule("R071", "AI confidence", "context", INFO, "Module 05 confidence, recorded and never used.", r_ai),
)
RULES_BY_ID = {r.rule_id: r for r in RULES}
NOT_DISABLEABLE = {"R001", "R002", "R003", "R010", "R011", "R018", "R019", "R041", "R060"}
LOCKING_RULES = {"R030": "daily_loss", "R031": "weekly_loss", "R032": "monthly_loss", "R033": "drawdown", "R035": "consecutive_losses"}


def evaluate(ctx: AssessmentContext) -> list[RuleResult]:
    out = []
    for rule in RULES:
        override = ctx.cfg.rule_override(rule.rule_id)
        thresholds = rule.thresholds(ctx.cfg)
        if override.get("enabled") is False and rule.rule_id not in NOT_DISABLEABLE:
            out.append(RuleResult(rule.rule_id, rule.name, rule.category, rule.severity, "skipped", "DISABLED",
                                  "Disabled by configuration.", {}, thresholds))
            continue
        if rule.asset_classes and ctx.asset_class not in rule.asset_classes:
            out.append(RuleResult(rule.rule_id, rule.name, rule.category, rule.severity, "skipped", "NOT_APPLICABLE",
                                  f"Not applied to {ctx.asset_class}.", {}, thresholds))
            continue
        try:
            outcome, code, msg, ev, sev = rule.fn(ctx)
        except Exception as exc:  # noqa: BLE001 - a broken rule must never pass silently
            outcome, code, msg, ev, sev = "error", "RULE_FAILED", f"Rule raised {type(exc).__name__}: {exc}", {}, None
        severity = sev or rule.severity
        if override.get("severity") in (HARD, WARNING) and outcome == "fail" and rule.severity != INFO:
            # An override may only make a rule stricter, never downgrade a hard block.
            severity = HARD if HARD in (severity, override["severity"]) else WARNING
        out.append(RuleResult(rule.rule_id, rule.name, rule.category, severity, outcome, code, msg, ev, thresholds))
    return out


def decide(results: list[RuleResult]) -> str:
    if any(r.outcome == "fail" and r.severity == HARD for r in results):
        return BLOCK
    if any(r.outcome == "error" for r in results):
        return ERROR
    if any(r.outcome == "fail" and r.severity == WARNING for r in results):
        return REVIEW
    return PASS
