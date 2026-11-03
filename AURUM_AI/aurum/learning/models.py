"""Data model for the Trade Learning Agent."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable


@dataclass(frozen=True)
class Trade:
    """One closed trade. ``pnl`` is the net result in account currency (profit + swap + commission + fee)."""

    ticket: str
    symbol: str
    side: str  # "buy" | "sell"
    volume: float
    open_time: datetime  # UTC, tz-aware
    close_time: datetime  # UTC, tz-aware
    open_price: float
    close_price: float
    pnl: float
    strategy: str | None = None

    @property
    def won(self) -> bool:
        return self.pnl > 0


# A pattern is a small set of (feature, value) conditions, e.g. (("session", "london"), ("side", "buy")).
Pattern = tuple[tuple[str, str], ...]


def describe(pattern: Pattern) -> str:
    return " and ".join(f"{k} = {v}" for k, v in pattern) if pattern else "all trades"


@dataclass(frozen=True)
class Stats:
    """Performance of a set of trades. Every figure is computed from the trades; nothing is estimated."""

    trades: int
    wins: int
    net: float
    gross_profit: float
    gross_loss: float  # positive number
    expectancy: float  # mean pnl per trade
    profit_factor: float | None  # None when there are no losing trades
    win_rate: float
    t_stat: float  # mean / standard error; how far the average is from zero relative to its noise

    @classmethod
    def of(cls, trades: Iterable[Trade]) -> "Stats":
        pnl = [t.pnl for t in trades]
        n = len(pnl)
        if n == 0:
            return cls(0, 0, 0.0, 0.0, 0.0, 0.0, None, 0.0, 0.0)
        wins = sum(1 for p in pnl if p > 0)
        gp = sum(p for p in pnl if p > 0)
        gl = -sum(p for p in pnl if p < 0)
        mean = sum(pnl) / n
        var = sum((p - mean) ** 2 for p in pnl) / (n - 1) if n > 1 else 0.0
        se = math.sqrt(var / n) if var > 0 else 0.0
        t = mean / se if se > 0 else (math.inf if mean > 0 else 0.0)
        return cls(n, wins, sum(pnl), gp, gl, mean, (gp / gl) if gl > 0 else None, wins / n, t)

    def as_dict(self) -> dict:
        return {
            "trades": self.trades,
            "wins": self.wins,
            "net": round(self.net, 2),
            "expectancy": round(self.expectancy, 2),
            "profit_factor": None if self.profit_factor is None else round(self.profit_factor, 2),
            "win_rate": round(self.win_rate * 100, 1),
            "t_stat": None if math.isinf(self.t_stat) else round(self.t_stat, 2),
        }


@dataclass
class LearningConfig:
    """Rules of the cycle. Defaults are deliberately conservative."""

    discovery_window: int = 100  # most recent closed trades used to look for what works
    min_pattern_trades: int = 20  # a pattern needs at least this many trades in the window
    min_profit_factor: float = 1.3  # ... and at least this profit factor
    max_conditions: int = 2  # patterns combine at most this many conditions
    trial_trades: int = 10  # matching forward trades needed to judge a trial
    trial_min_profit_factor: float = 1.2  # a trial passes when PF >= this and net > 0
    trial_max_wait: int = 300  # closed trades to wait for enough matches before the trial is judged inconclusive
    max_failed_trials: int = 3  # consecutive failed trials before the agent terminates itself
    max_generations: int = 20  # hard cap on replication depth
    features: tuple[str, ...] = ("symbol", "side", "session", "weekday", "strategy", "holding")


@dataclass
class AgentEvent:
    at: datetime
    kind: str  # discovered | no_pattern | trial_started | trial_passed | trial_failed | replicated | terminated | capped
    message: str
    data: dict = field(default_factory=dict)
