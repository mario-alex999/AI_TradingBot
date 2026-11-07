"""Walk-forward replay: step through stored history and run templates, gates and score with only what was known then.

Costs are charged in R: the spread at entry plus a configured slippage, both already known or assumed at decision time. The
AI reviewer and the account mirror take no part: the first would know the future, the second has no history. Replay runs a
lighter Module 04 analysis without its own Module 03 pass, because a full one takes seconds per step.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Iterable

import pandas as pd

from aurum.config import TIMEFRAME_SECONDS
from aurum.decision.candidates.setups import Candidate, generate
from aurum.decision.config import DecisionConfig
from aurum.decision.context import ContextBuilder, DecisionContext
from aurum.decision.gates import first_failure, run_gates
from aurum.decision.outcomes import label
from aurum.decision.regime import assess_regime
from aurum.decision.scoring import score
from aurum.learning.features import session_of

from .metrics import SimTrade

log = logging.getLogger(__name__)


@dataclass
class ReplayResult:
    trades: list[SimTrade] = field(default_factory=list)
    steps: int = 0
    candidates: int = 0
    blocked: dict[str, int] = field(default_factory=dict)
    open_at_end: int = 0


def time_steps(start: datetime, end: datetime, timeframe: str) -> list[datetime]:
    step = timedelta(seconds=TIMEFRAME_SECONDS[timeframe])
    out, t = [], start
    while t <= end:
        out.append(t)
        t += step
    return out


def candidates_at(ctx: DecisionContext, cfg: DecisionConfig, templates: Iterable[str] | None = None,
                  timeframes: Iterable[str] | None = None) -> list[tuple[Candidate, Any]]:
    """Candidates with their scores, strongest first. Shared by replay and live runs."""
    found, _ = generate(ctx, cfg, templates, timeframes)
    return sorted(((c, score(ctx, c, cfg)) for c in found), key=lambda x: (-x[1].confidence, x[0].key))


def cost_in_r(c: Candidate, spread_price: float | None, slippage_r: float, commission_r: float = 0.0) -> float:
    risk = abs(c.entry_mid - c.stop_loss)
    fixed = slippage_r + commission_r
    return (spread_price or 0.0) / risk + fixed if risk > 0 else fixed


def replay_symbol(symbol: str, start: datetime, end: datetime, cfg: DecisionConfig, builder: ContextBuilder,
                  candles: pd.DataFrame, templates: Iterable[str] | None = None,
                  progress: Callable[[str], None] | None = None) -> ReplayResult:
    """``candles`` is the outcome timeframe for ``symbol``, with ts_utc, high, low and close."""
    bt, out = cfg.backtest, ReplayResult()
    seconds = TIMEFRAME_SECONDS[cfg.outcomes["candle_timeframe"]]
    seen: set[str] = set()
    busy: list[dict[str, Any]] = []       # simulated trades still running, for the duplication gate
    say = progress or (lambda m: None)
    steps = time_steps(start, end, bt["step_timeframe"])
    for i, t in enumerate(steps):
        out.steps += 1
        if i % 50 == 0:
            say(f"{symbol}: {i}/{len(steps)} steps, {len(out.trades)} trades")
        ctx = builder.build(symbol, t)
        busy = [b for b in busy if b["until"] is None or b["until"] > t]
        for c, s in candidates_at(ctx, cfg, templates):
            if c.key in seen:
                continue
            out.candidates += 1
            failed = first_failure(run_gates(ctx, c, cfg, busy))
            if failed:
                out.blocked[failed] = out.blocked.get(failed, 0) + 1
                continue
            seen.add(c.key)
            after = candles[candles["ts_utc"] >= t]        # candles opening at or after the decision
            o = label(c.direction, c.entry_low, c.entry_high, c.stop_loss, c.take_profit, c.expires_at, after,
                      cfg.outcomes["max_hold_candles"])
            if o.result == "open":
                out.open_at_end += 1
                busy.append({"symbol": c.symbol, "direction": c.direction, "key": c.key, "until": None})
                continue
            if not o.filled:
                continue
            until = o.resolved_at + timedelta(seconds=seconds) if o.resolved_at else None
            busy.append({"symbol": c.symbol, "direction": c.direction, "key": c.key, "until": until})
            active = ctx.sessions.get("active_sessions")
            net = o.r_multiple - cost_in_r(c, ctx.spread_price, bt["slippage_r"], bt["commission_r"])
            out.trades.append(SimTrade(c.symbol, c.template, c.timeframe, c.direction, t, active[0] if active else session_of(t),
                                       s.confidence, o.result, round(net, 3), o.resolved_at, assess_regime(ctx, cfg)["regime"]))
    return out
