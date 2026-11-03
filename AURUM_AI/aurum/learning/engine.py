"""Trade Learning Agent engine.

The cycle, run over closed trades in the order they closed:

1. **Discover** - in the most recent ``discovery_window`` trades, find the pattern (one or two
   conditions such as ``session = london and side = buy``) with the strongest positive edge:
   enough trades, profit factor above the bar, and the highest t-statistic of its mean result.
2. **Trial** - watch the trades that close *after* discovery. The pattern is judged on the next
   ``trial_trades`` trades that match it (out-of-sample, never the trades it was found on).
3. **Pass -> replicate** - the agent retires and creates one child (next generation). The child
   repeats the whole process on the newest trades.
4. **Fail -> retry, then terminate** - the agent rejects that pattern and looks again. After
   ``max_failed_trials`` consecutive failed trials it terminates itself.

Safety: one active agent per lineage (replication creates exactly one child, never a fan-out),
replication stops at ``max_generations``, and nothing here can place, modify or close a trade.
"""
from __future__ import annotations

import itertools
import math
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable

from .features import features_of, matches
from .models import AgentEvent, LearningConfig, Pattern, Stats, Trade, describe


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class AgentRecord:
    id: str
    generation: int
    parent: str | None
    created_at: datetime
    status: str = "discovering"  # discovering | trialling | replicated | terminated | capped
    pattern: Pattern | None = None
    discovery_stats: dict | None = None
    trial_matched: list[Trade] = field(default_factory=list)
    trial_seen: int = 0
    failed_trials: int = 0
    rejected: list[Pattern] = field(default_factory=list)
    trials: list[dict] = field(default_factory=list)
    events: list[AgentEvent] = field(default_factory=list)
    ended_at: datetime | None = None
    _no_pattern_logged_at: int = -1

    @property
    def active(self) -> bool:
        return self.status in ("discovering", "trialling")


@dataclass
class Lineage:
    id: str
    created_at: datetime
    config: LearningConfig
    agents: list[AgentRecord] = field(default_factory=list)
    window: deque = field(default_factory=deque)  # most recent closed trades (bounded)
    seen_tickets: deque = field(default_factory=lambda: deque(maxlen=20000))
    trades_observed: int = 0
    last_close_time: datetime | None = None

    @property
    def active_agent(self) -> AgentRecord | None:
        return next((a for a in reversed(self.agents) if a.active), None)

    @property
    def status(self) -> str:
        if self.active_agent:
            return "running"
        last = self.agents[-1] if self.agents else None
        return last.status if last else "empty"


class TradeLearningAgent:
    """Drives a :class:`Lineage` forward as closed trades arrive. Feed it with :meth:`observe`."""

    def __init__(self, lineage: Lineage):
        self.lineage = lineage
        self.cfg = lineage.config
        if not lineage.agents:
            self._spawn(parent=None, at=lineage.created_at)

    # ------------------------------------------------------------------ public
    @classmethod
    def start(cls, config: LearningConfig | None = None, lineage_id: str = "L1", at: datetime | None = None) -> "TradeLearningAgent":
        return cls(Lineage(id=lineage_id, created_at=at or _now(), config=config or LearningConfig()))

    def observe(self, trades: Iterable[Trade]) -> list[AgentEvent]:
        """Process newly closed trades (duplicates and already-seen tickets are ignored)."""
        new_events: list[AgentEvent] = []
        seen = set(self.lineage.seen_tickets)
        for t in sorted(trades, key=lambda x: (x.close_time, x.ticket)):
            if t.ticket in seen:
                continue
            seen.add(t.ticket)
            self.lineage.seen_tickets.append(t.ticket)
            new_events.extend(self._on_trade(t))
        return new_events

    # ------------------------------------------------------------------ cycle
    def _on_trade(self, t: Trade) -> list[AgentEvent]:
        lin = self.lineage
        lin.trades_observed += 1
        lin.last_close_time = t.close_time
        lin.window.append(t)
        while len(lin.window) > self.cfg.discovery_window:
            lin.window.popleft()

        agent = lin.active_agent
        if agent is None:
            return []
        before = len(agent.events)
        if agent.status == "trialling":
            self._advance_trial(agent, t)
        if agent.status == "discovering" and len(lin.window) >= self.cfg.discovery_window:
            self._discover(agent, t.close_time)
        # a child created during this trade also gets its first discovery attempt
        child = lin.active_agent
        out = agent.events[before:]
        if child is not None and child is not agent and child.status == "discovering":
            self._discover(child, t.close_time)
            out = out + child.events
        return out

    def _discover(self, agent: AgentRecord, at: datetime) -> None:
        trades = list(self.lineage.window)
        best = self._best_pattern(trades, exclude=agent.rejected)
        if best is None:
            # log once per full window of new trades, not on every trade
            if agent._no_pattern_logged_at < 0 or self.lineage.trades_observed - agent._no_pattern_logged_at >= self.cfg.discovery_window:
                agent._no_pattern_logged_at = self.lineage.trades_observed
                self._event(agent, at, "no_pattern",
                            f"No pattern in the last {len(trades)} trades meets the bar "
                            f"(≥{self.cfg.min_pattern_trades} trades, profit factor ≥{self.cfg.min_profit_factor}, positive average). Still watching.")
            return
        pattern, stats = best
        agent.pattern = pattern
        agent.discovery_stats = stats.as_dict()
        agent.status = "trialling"
        agent.trial_matched = []
        agent.trial_seen = 0
        self._event(agent, at, "discovered",
                    f"What works: {describe(pattern)} — {stats.trades} trades, net {stats.net:,.2f}, "
                    f"profit factor {_pf(stats)}, win rate {stats.win_rate:.0%}.",
                    {"pattern": [list(c) for c in pattern], "stats": stats.as_dict()})
        self._event(agent, at, "trial_started",
                    f"Trial {len(agent.trials) + 1}: judging '{describe(pattern)}' on its next {self.cfg.trial_trades} matching trades.")

    def _advance_trial(self, agent: AgentRecord, t: Trade) -> None:
        assert agent.pattern is not None
        agent.trial_seen += 1
        if matches(t, agent.pattern):
            agent.trial_matched.append(t)
        if len(agent.trial_matched) >= self.cfg.trial_trades:
            self._judge(agent, t.close_time, inconclusive=False)
        elif agent.trial_seen >= self.cfg.trial_max_wait:
            self._judge(agent, t.close_time, inconclusive=True)

    def _judge(self, agent: AgentRecord, at: datetime, inconclusive: bool) -> None:
        stats = Stats.of(agent.trial_matched)
        pf_ok = stats.profit_factor is None or stats.profit_factor >= self.cfg.trial_min_profit_factor
        passed = (not inconclusive) and stats.net > 0 and pf_ok
        result = {
            "number": len(agent.trials) + 1,
            "pattern": [list(c) for c in agent.pattern or ()],
            "passed": passed,
            "inconclusive": inconclusive,
            "stats": stats.as_dict(),
            "tickets": [x.ticket for x in agent.trial_matched],
            "at": at.isoformat(),
        }
        agent.trials.append(result)
        if passed:
            agent.failed_trials = 0
            self._event(agent, at, "trial_passed",
                        f"Trial {result['number']} passed: {stats.trades} matching trades, net {stats.net:,.2f}, profit factor {_pf(stats)}.",
                        {"stats": stats.as_dict()})
            self._replicate(agent, at)
            return

        agent.failed_trials += 1
        if agent.pattern:
            agent.rejected.append(agent.pattern)
        reason = (f"only {stats.trades} matching trades in {agent.trial_seen} closed trades" if inconclusive
                  else f"net {stats.net:,.2f}, profit factor {_pf(stats)}")
        self._event(agent, at, "trial_failed",
                    f"Trial {result['number']} failed ({reason}). Failed trials in a row: {agent.failed_trials} of {self.cfg.max_failed_trials}.",
                    {"stats": stats.as_dict(), "inconclusive": inconclusive})
        agent.pattern = None
        agent.trial_matched = []
        agent.trial_seen = 0
        if agent.failed_trials >= self.cfg.max_failed_trials:
            agent.status = "terminated"
            agent.ended_at = at
            self._event(agent, at, "terminated",
                        f"Terminated itself after {agent.failed_trials} failed trials in a row. The lineage stops here.")
        else:
            agent.status = "discovering"
            self._discover(agent, at)

    def _replicate(self, agent: AgentRecord, at: datetime) -> None:
        agent.status = "replicated"
        agent.ended_at = at
        if agent.generation >= self.cfg.max_generations:
            agent.status = "capped"
            self._event(agent, at, "capped",
                        f"Worked, but the generation limit ({self.cfg.max_generations}) is reached, so it does not replicate.")
            return
        child = self._spawn(parent=agent, at=at)
        self._event(agent, at, "replicated", f"Replicated: {child.id} (generation {child.generation}) continues the cycle.",
                    {"child": child.id})

    # ------------------------------------------------------------------ helpers
    def _spawn(self, parent: AgentRecord | None, at: datetime) -> AgentRecord:
        gen = 1 if parent is None else parent.generation + 1
        rec = AgentRecord(id=f"{self.lineage.id}-G{gen}", generation=gen, parent=parent.id if parent else None, created_at=at)
        self.lineage.agents.append(rec)
        self._event(rec, at, "created",
                    "Started: watching closed trades." if parent is None else f"Created by {parent.id}; repeating the cycle on the newest trades.")
        return rec

    def _event(self, agent: AgentRecord, at: datetime, kind: str, message: str, data: dict | None = None) -> None:
        agent.events.append(AgentEvent(at=at, kind=kind, message=message, data=data or {}))

    def _best_pattern(self, trades: list[Trade], exclude: list[Pattern]) -> tuple[Pattern, Stats] | None:
        cfg = self.cfg
        feats = [features_of(t) for t in trades]
        values: dict[str, set[str]] = {f: {x[f] for x in feats} for f in cfg.features}
        conditions = [(f, v) for f in cfg.features for v in sorted(values[f])]
        best: tuple[Pattern, Stats] | None = None
        for size in range(1, cfg.max_conditions + 1):
            for combo in itertools.combinations(conditions, size):
                if len({k for k, _ in combo}) < size:  # one value per feature
                    continue
                pattern: Pattern = tuple(sorted(combo))
                if pattern in exclude:
                    continue
                subset = [t for t, f in zip(trades, feats) if all(f[k] == v for k, v in pattern)]
                if len(subset) < cfg.min_pattern_trades:
                    continue
                s = Stats.of(subset)
                pf_ok = s.profit_factor is None or s.profit_factor >= cfg.min_profit_factor
                if s.expectancy <= 0 or not pf_ok:
                    continue
                key = (s.t_stat, s.trades, -len(pattern))
                if best is None or key > (best[1].t_stat, best[1].trades, -len(best[0])):
                    best = (pattern, s)
        return best


def _pf(s: Stats) -> str:
    if s.profit_factor is None:
        return "∞ (no losses)" if s.trades else "—"
    return f"{s.profit_factor:.2f}" if not math.isinf(s.profit_factor) else "∞"
