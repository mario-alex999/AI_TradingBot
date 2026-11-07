"""Backtest metrics in the frontend's PerformanceReport shape. Trade results are in R, the risk taken on the trade.

The money figures come from one assumption, a fixed percentage of equity risked per trade from a notional account, so
they show the shape of the results and not a forecast of what an account would have made.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from aurum.account.queries import _max_drawdown_pct


@dataclass
class SimTrade:
    symbol: str
    template: str
    timeframe: str
    direction: str
    as_of: datetime
    session: str | None
    confidence: float
    result: str
    r_multiple: float
    resolved_at: datetime | None
    regime: str | None = None


def _stats(trades: list[SimTrade]) -> dict[str, Any]:
    rs = [t.r_multiple for t in trades]
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    gross_win, gross_loss = sum(wins), -sum(losses)
    return {"trades": len(rs), "win_rate": round(len(wins) / len(rs) * 100, 2) if rs else 0.0,
            "expectancy_r": round(sum(rs) / len(rs), 3) if rs else 0.0,
            "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else None, "total_r": round(sum(rs), 2)}


def _grouped(trades: list[SimTrade], key: Callable[[SimTrade], str]) -> dict[str, list[SimTrade]]:
    groups: dict[str, list[SimTrade]] = defaultdict(list)
    for t in trades:
        groups[key(t)].append(t)
    return dict(groups)


def _breakdown(trades: list[SimTrade], key: Callable[[SimTrade], str], risk_money: float) -> list[dict[str, Any]]:
    return [{"name": name, "pnl": round(sum(t.r_multiple for t in group) * risk_money, 2)}
            for name, group in sorted(_grouped(trades, key).items())]


def _bucket(t: SimTrade) -> str:
    low = min(int(t.confidence // 20) * 20, 80)
    return f"{low}-{low + 20}"


def report(trades: list[SimTrade], risk_pct: float, account_start: float, split: datetime | None, small_sample: int) -> dict[str, Any]:
    ordered = sorted(trades, key=lambda t: t.resolved_at or t.as_of)
    equity = account_start
    curve, monthly_start, month_end = [], {}, {}
    for t in ordered:
        at = t.resolved_at or t.as_of
        month = at.strftime("%Y-%m")
        monthly_start.setdefault(month, equity)
        equity *= 1 + t.r_multiple * risk_pct / 100
        month_end[month] = equity
        curve.append({"time": at.isoformat(), "equity": round(equity, 2)})
    monthly = [{"month": m, "returnPct": round((month_end[m] / monthly_start[m] - 1) * 100, 2)} for m in sorted(month_end)]

    risk_money = account_start * risk_pct / 100
    rs = [t.r_multiple for t in ordered]
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    stats = _stats(ordered)
    metrics = {"totalTrades": len(rs), "winningTrades": len(wins), "losingTrades": len(losses), "winRate": stats["win_rate"],
               "profitFactor": stats["profit_factor"],
               "averageProfit": round(sum(wins) / len(wins) * risk_money, 2) if wins else 0.0,
               "averageLoss": round(sum(losses) / len(losses) * risk_money, 2) if losses else 0.0,
               "expectancy": round(stats["expectancy_r"] * risk_money, 2),
               "maxDrawdownPct": round(_max_drawdown_pct([account_start, *(p["equity"] for p in curve)]), 2), "sharpe": None}

    warnings = []
    if len(rs) < small_sample:
        warnings.append(f"Small sample: {len(rs)} trades, below {small_sample}. Treat these results as anecdotal.")
    for name, group in sorted(_grouped(ordered, lambda t: t.template).items()):
        if len(group) < small_sample:
            warnings.append(f"Small sample for {name}: {len(group)} trades.")
    samples = None
    if split is not None:
        samples = {"split_date": split.isoformat(), "in_sample": _stats([t for t in ordered if t.as_of < split]),
                   "out_of_sample": _stats([t for t in ordered if t.as_of >= split])}
    return {"kind": "backtest", "metrics": metrics, "equityCurve": curve, "monthly": monthly,
            "byInstrument": _breakdown(ordered, lambda t: t.symbol, risk_money),
            "bySession": _breakdown(ordered, lambda t: t.session or "unknown", risk_money),
            "byStrategy": _breakdown(ordered, lambda t: t.template, risk_money),
            "details": {"results_in": "R", "assumed_risk_pct": risk_pct, "account_start": account_start,
                        "ambiguous_trades": sum(1 for t in ordered if t.result == "ambiguous"),
                        "by_template": {k: _stats(v) for k, v in sorted(_grouped(ordered, lambda t: t.template).items())},
                        "by_confidence": {k: _stats(v) for k, v in sorted(_grouped(ordered, _bucket).items())},
                        "by_timeframe": {k: _stats(v) for k, v in sorted(_grouped(ordered, lambda t: t.timeframe).items())},
                        "by_regime": {k: _stats(v) for k, v in sorted(_grouped(ordered, lambda t: t.regime or "unknown").items())},
                        "samples": samples, "warnings": warnings}}
