"""Benchmark: every rule change must beat the original rules on the same history and under the same costs.

* ``PROFILES`` - named rule sets. ``baseline`` is frozen: the engine as it was before the 22 Nov 2026 changes (no minimum
  stop, no one-signal-per-idea, counter-trend reversals at any grade). It is the version others must outperform.
* ``COST_SCENARIOS`` - spread multiple, slippage and commission (both in R), applied after the replay to each trade's
  gross result, so one replay is priced several ways and every profile meets exactly the same costs.
* ``summarize`` / ``breakdown`` - results overall and split by grade band (A/B vs C/D), template, symbol, higher-timeframe
  alignment, regime and session, so it is clear which kinds of trade make or lose money.
* ``verdict`` - a candidate beats the baseline only if, in the base AND the high-cost scenario, its net R and profit factor
  are at least the baseline's and its worst drawdown (in R) is at most 25% deeper.

A good backtest is not a forecast: results are simulated on stored history, with labelled fills, no partial fills and no
news slippage. A high profit factor on few trades means little.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from aurum.decision.backtest.metrics import SimTrade

ALL_GRADES = ["A", "B", "C", "D", "F"]

PROFILES: dict[str, dict[str, Any]] = {
    # Frozen. Do not change: this is the benchmark (the engine before the 22 Nov 2026 rule changes).
    "baseline": {"levels": {"min_stop_atr": 0.0},
                 "selection": {"dedupe": False, "counter_trend_min_grades": ALL_GRADES}},
    "current": {},                                                          # config/ai_engine.yaml as it is now
    "selection": {"levels": {"min_stop_atr": 0.0}},                         # one signal per idea + counter-trend rule
    "min_stop_1atr": {"levels": {"min_stop_atr": 1.0},
                      "selection": {"dedupe": False, "counter_trend_min_grades": ALL_GRADES}},
    "no_choch": {"levels": {"min_stop_atr": 0.0}, "selection": {"dedupe": False, "counter_trend_min_grades": ALL_GRADES},
                 "templates": {"choch_reversal": {"enabled": False}}},
    # Reversal templates (CHoCH, sweep, range edge) never trade against both D1 and H4, at any grade; with the trend
    # they still trade. Isolates "counter-trend" from "CHoCH as such".
    "no_counter_trend": {"levels": {"min_stop_atr": 0.0}, "selection": {"dedupe": False, "counter_trend_min_grades": []}},
    "dedupe_only": {"levels": {"min_stop_atr": 0.0}, "selection": {"dedupe": True, "counter_trend_min_grades": ALL_GRADES}},
    "no_counter_trend_dedupe": {"levels": {"min_stop_atr": 0.0}, "selection": {"dedupe": True, "counter_trend_min_grades": []}},
}
# Profiles that replay only some templates (the rules are the baseline's).
TEMPLATE_ONLY: dict[str, tuple[str, list[str]]] = {"choch_only": ("baseline", ["choch_reversal"])}

COST_SCENARIOS: dict[str, dict[str, float]] = {
    "low": {"spread_multiple": 1.0, "slippage_r": 0.02, "commission_r": 0.0},
    "base": {"spread_multiple": 1.0, "slippage_r": 0.05, "commission_r": 0.0},     # = config backtest costs
    "high": {"spread_multiple": 1.5, "slippage_r": 0.10, "commission_r": 0.05},
    "stress": {"spread_multiple": 2.0, "slippage_r": 0.20, "commission_r": 0.10},
}


def band(grade: str | None) -> str:
    return "A/B" if grade in ("A", "B") else ("C/D" if grade in ("C", "D") else "F/unknown")


def net_r(t: SimTrade, costs: dict[str, float]) -> float:
    """The trade's result under ``costs``. Trades without a gross result (older runs) keep their stored net result."""
    if t.gross_r is None:
        return t.r_multiple
    return t.gross_r - (t.spread_r or 0.0) * costs["spread_multiple"] - costs["slippage_r"] - costs["commission_r"]


@dataclass
class Summary:
    trades: int
    wins: int
    losses: int
    win_rate: float | None
    net_r: float
    profit_factor: float | None
    avg_win_r: float | None
    avg_loss_r: float | None
    expectancy_r: float | None
    max_drawdown_r: float
    tp: int
    sl: int

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def summarize(trades: Iterable[SimTrade], costs: dict[str, float]) -> Summary:
    ordered = sorted(trades, key=lambda t: t.resolved_at or t.as_of)
    rs = [net_r(t, costs) for t in ordered]
    wins, losses = [r for r in rs if r > 0], [r for r in rs if r <= 0]
    gross_win, gross_loss = sum(wins), -sum(losses)
    peak = equity = worst = 0.0
    for r in rs:
        equity += r
        peak = max(peak, equity)
        worst = max(worst, peak - equity)
    n = len(rs)
    return Summary(n, len(wins), len(losses), round(len(wins) / n * 100, 1) if n else None, round(sum(rs), 2),
                   round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
                   round(gross_win / len(wins), 2) if wins else None, round(-gross_loss / len(losses), 2) if losses else None,
                   round(sum(rs) / n, 3) if n else None, round(worst, 2),
                   sum(1 for t in ordered if t.result == "tp"), sum(1 for t in ordered if t.result == "sl"))


DIMENSIONS: dict[str, Callable[[SimTrade], str]] = {
    "grade_band": lambda t: band(t.grade),
    "template": lambda t: t.template,
    "symbol": lambda t: t.symbol,
    "htf_alignment": lambda t: t.htf or "unknown",
    "regime": lambda t: t.regime or "unknown",
    "session": lambda t: t.session or "unknown",
}


def breakdown(trades: list[SimTrade], costs: dict[str, float], dims: Iterable[str] = DIMENSIONS) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for dim in dims:
        groups: dict[str, list[SimTrade]] = defaultdict(list)
        for t in trades:
            groups[DIMENSIONS[dim](t)].append(t)
        out[dim] = {k: summarize(v, costs).as_dict() for k, v in sorted(groups.items())}
    return out


def verdict(candidate: list[SimTrade], baseline: list[SimTrade], scenarios: Iterable[str] = ("base", "high")) -> dict[str, Any]:
    """Does ``candidate`` beat the baseline? Net R and profit factor at least the baseline's, and a worst drawdown at most
    25% deeper, in every scenario named. Reasons are listed when it does not."""
    reasons: list[str] = []
    for name in scenarios:
        c, b = summarize(candidate, COST_SCENARIOS[name]), summarize(baseline, COST_SCENARIOS[name])
        if c.net_r < b.net_r:
            reasons.append(f"{name}: net {c.net_r:+.2f}R below the baseline's {b.net_r:+.2f}R")
        if (c.profit_factor or 0) < (b.profit_factor or 0):
            reasons.append(f"{name}: profit factor {c.profit_factor} below the baseline's {b.profit_factor}")
        if c.max_drawdown_r > b.max_drawdown_r * 1.25 + 1e-9:
            reasons.append(f"{name}: drawdown {c.max_drawdown_r}R deeper than 1.25 x the baseline's {b.max_drawdown_r}R")
    return {"beats_baseline": not reasons, "reasons": reasons}


def report(results: dict[str, list[SimTrade]], stats: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """Every profile under every cost scenario, its breakdowns (base costs) and its verdict against the baseline."""
    base = results.get("baseline")
    out: dict[str, Any] = {"cost_scenarios": COST_SCENARIOS, "profiles": {}}
    for name, trades in results.items():
        entry: dict[str, Any] = {"scenarios": {s: summarize(trades, c).as_dict() for s, c in COST_SCENARIOS.items()},
                                 "breakdown": breakdown(trades, COST_SCENARIOS["base"]), "replay": (stats or {}).get(name)}
        if base is not None and name != "baseline" and name not in TEMPLATE_ONLY:
            entry["verdict"] = verdict(trades, base)
        out["profiles"][name] = entry
    out["disclaimer"] = ("Simulated on stored history with stated costs. A high profit factor on few trades is not evidence of "
                         "an edge, and backtests that look excellent can still fail in live markets.")
    return out
