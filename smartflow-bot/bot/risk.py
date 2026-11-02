"""Position sizing and account-level guards."""
from __future__ import annotations


class RiskManager:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.equity = cfg["account_size"]
        self.day = None
        self.day_pnl = 0.0
        self.open = 0

    def new_bar(self, day):
        if day != self.day:
            self.day, self.day_pnl = day, 0.0

    def can_trade(self) -> bool:
        limit = -self.cfg["max_daily_loss_pct"] / 100 * self.equity
        return self.day_pnl > limit and self.open < self.cfg["max_open_trades"]

    def size(self, entry: float, stop: float, score: float | None = None, ai_multiplier: float = 1.0) -> float:
        """Units so that a stop-out loses risk_per_trade_pct of equity.
        A+ setups (score >= 85) get full risk, others 75%; AI agent can reduce further."""
        risk_cash = self.equity * self.cfg["risk_per_trade_pct"] / 100
        if score is not None and score < 85:
            risk_cash *= 0.75
        risk_cash *= max(0.0, min(ai_multiplier, 1.0))
        return risk_cash / abs(entry - stop)

    def close(self, pnl: float):
        self.equity += pnl
        self.day_pnl += pnl
        self.open -= 1
