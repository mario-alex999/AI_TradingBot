"""Account-level risk: snapshot health, drawdown, daily/weekly/monthly loss, consecutive losses and trade count.

Definitions (also in docs/MODULE_06_RISK_FORMULAS.md):

* Balance = closed-trade cash. Equity = balance + floating P/L. Realised P/L = net P/L of trades closed in the period
  (profit + swap + commission + fee). Unrealised P/L = MT5 floating profit on the latest snapshot.
* Period loss on the configured basis (equity by default):
  ``loss = start_value - current_value + net_deposits_after_start`` (withdrawals count as negative deposits, so moving money
  never looks like a trading loss or gain); ``loss% = max(0, loss) / start_value x 100``. Only cash moved after the
  start snapshot counts: anything before it is already inside the start value.
* Start value policy: the stored snapshot closest to the period start within ``period_start_tolerance_minutes`` (one at or
  before the start is preferred). Without one, the earliest snapshot inside the period is used and flagged uncertain.
  The first value found for a period is persisted (risk_period_records) so it never drifts.
* Drawdown = ``(peak equity - current equity) / peak equity x 100``, peak over the stored equity history.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Callable, Iterable

from aurum.risk.calendar import Period, TradingCalendar
from aurum.risk.config import RiskConfig
from aurum.risk.money import D, HUNDRED, ZERO


@dataclass(frozen=True)
class Snapshot:
    captured_at: datetime
    login: int | None
    currency: str
    trade_mode: str
    leverage: int | None
    balance: Decimal
    equity: Decimal
    margin: Decimal | None
    margin_free: Decimal | None
    margin_level: Decimal | None
    floating: Decimal | None


@dataclass(frozen=True)
class EquityPoint:
    at: datetime
    equity: Decimal
    balance: Decimal


@dataclass(frozen=True)
class ClosedTrade:
    id: str
    symbol: str
    open_time: datetime
    close_time: datetime
    pnl: Decimal


@dataclass(frozen=True)
class BalanceOperation:
    at: datetime
    amount: Decimal          # deposits positive, withdrawals negative


@dataclass
class PeriodLoss:
    kind: str
    label: str
    start: datetime
    end: datetime
    start_value: Decimal | None
    start_source: str
    start_uncertain: bool
    current_value: Decimal | None
    net_deposits: Decimal
    realized_pnl: Decimal
    loss: Decimal | None
    loss_pct: Decimal | None


@dataclass
class AccountRisk:
    status: str                          # ok | missing | stale | invalid
    reason: str | None
    snapshot: Snapshot | None
    age_minutes: float | None
    loss_basis: str
    periods: dict[str, PeriodLoss] = field(default_factory=dict)
    peak_equity: Decimal | None = None
    drawdown_pct: Decimal | None = None
    margin_utilization_pct: Decimal | None = None
    consecutive_losses: int = 0
    streak_trade_ids: list[str] = field(default_factory=list)
    recent_results: list[dict[str, Any]] = field(default_factory=list)
    trades_today: int = 0

    @property
    def usable(self) -> bool:
        return self.status == "ok"


_SOURCE_TIME = re.compile(r"snapshot(?: in period,)? (?:at )?(\d{4}-\d{2}-\d{2}T[0-9:.+\-]+)")


def anchor_time(source: str) -> datetime | None:
    """The time of the snapshot a period's start value came from. It is encoded in ``start_source`` (written by
    ``evaluate_account`` below and persisted in risk_period_records), e.g. ``snapshot at 2026-11-08T22:57:05+00:00``."""
    m = _SOURCE_TIME.search(source)
    try:
        return datetime.fromisoformat(m.group(1)) if m else None
    except ValueError:
        return None


AnchorFn = Callable[[Period, Callable[[], tuple[Decimal | None, str, bool]]], tuple[Decimal | None, str, bool]]


def classify(pnl: Decimal, equity: Decimal, tolerance_pct: float) -> str:
    band = equity * D(tolerance_pct) / HUNDRED
    if pnl > band:
        return "win"
    if pnl < -band:
        return "loss"
    return "breakeven"


def loss_streak(trades: Iterable[ClosedTrade], equity: Decimal, tolerance_pct: float) -> tuple[int, list[str], list[dict[str, Any]]]:
    """Consecutive losses ending at the most recent closed trade. A win ends the streak; a breakeven trade neither counts
    as a loss nor resets the streak. Trades are de-duplicated by id (one row per position leg in the account mirror)."""
    seen: dict[str, ClosedTrade] = {}
    for t in trades:
        seen.setdefault(t.id, t)
    ordered = sorted(seen.values(), key=lambda t: (t.close_time, t.id), reverse=True)
    streak, ids, results = 0, [], []
    for t in ordered:
        kind = classify(t.pnl, equity, tolerance_pct)
        results.append({"id": t.id, "symbol": t.symbol, "close_time": t.close_time, "pnl": t.pnl, "result": kind})
        if kind == "win":
            break
        if kind == "loss":
            streak += 1
            ids.append(t.id)
    return streak, ids, results[:20]


def evaluate_account(cfg: RiskConfig, cal: TradingCalendar, now: datetime, snap: Snapshot | None,
                     history: list[EquityPoint], closed: list[ClosedTrade], operations: list[BalanceOperation],
                     opened_today: int, anchor: AnchorFn | None = None) -> AccountRisk:
    if snap is None:
        return AccountRisk("missing", "No account snapshot is stored; run `python -m aurum.cli sync-account`.", None, None,
                           cfg.loss_basis)
    age = (now - snap.captured_at).total_seconds() / 60
    if snap.trade_mode != "DEMO":
        return AccountRisk("invalid", f"Account trade mode is {snap.trade_mode}; AURUM works with DEMO accounts only.", snap,
                           age, cfg.loss_basis)
    if snap.equity <= 0 or snap.balance < 0:
        return AccountRisk("invalid", "Account snapshot has non-positive equity or negative balance.", snap, age, cfg.loss_basis)
    if snap.captured_at > now + timedelta(minutes=2):
        return AccountRisk("invalid", "Account snapshot is timestamped in the future.", snap, age, cfg.loss_basis)
    status, reason = "ok", None
    if age > cfg.account_max_age_minutes:
        status, reason = "stale", (f"Latest account snapshot is {age:.0f} min old (limit {cfg.account_max_age_minutes:g} min); "
                                   "keep `stream-ticks` or `sync-account --loop` running.")
    out = AccountRisk(status, reason, snap, round(age, 1), cfg.loss_basis)
    # The latest snapshot is itself an equity observation (the mirror may not have stored it in the history yet).
    points = sorted([p for p in history if p.at <= now] + [EquityPoint(snap.captured_at, snap.equity, snap.balance)],
                    key=lambda p: p.at)
    basis = (lambda p: p.equity) if cfg.loss_basis == "equity" else (lambda p: p.balance)
    current = snap.equity if cfg.loss_basis == "equity" else snap.balance
    tolerance = timedelta(minutes=cfg.period_start_tolerance_minutes)

    for kind in ("day", "week", "month"):
        period = cal.period(kind, now)

        def compute(period: Period = period) -> tuple[Decimal | None, str, bool]:
            before = [p for p in points if period.start - tolerance <= p.at <= period.start]
            if before:
                return basis(before[-1]), f"snapshot at {before[-1].at.isoformat()}", False
            after = [p for p in points if period.start < p.at <= min(now, period.start + tolerance)]
            if after:
                return basis(after[0]), f"snapshot at {after[0].at.isoformat()}", False
            inside = [p for p in points if period.start < p.at <= now]
            if inside:
                return basis(inside[0]), f"earliest snapshot in period, {inside[0].at.isoformat()} (no snapshot near the start)", True
            return None, "no snapshot in this period", True

        start_value, source, uncertain = anchor(period, compute) if anchor else compute()
        # Cash moved before the start snapshot is already in the start value; counting it again would turn a deposit
        # made earlier in the period into a phantom loss (e.g. deposit on day 2, first snapshot on day 8).
        anchor_at = anchor_time(source)
        if anchor_at is not None and anchor_at > period.start:
            deposits = sum((o.amount for o in operations if anchor_at < o.at <= now), ZERO)
        else:
            deposits = sum((o.amount for o in operations if period.start <= o.at <= now), ZERO)
        realized = sum((t.pnl for t in closed if period.start <= t.close_time <= now), ZERO)
        loss = loss_pct = None
        if start_value is not None and start_value > 0:
            loss = start_value - current + deposits
            loss_pct = max(ZERO, loss) / start_value * HUNDRED
        out.periods[kind] = PeriodLoss(kind, period.label, period.start, period.end, start_value, source, uncertain, current,
                                       deposits, realized, loss, loss_pct)

    peak = max([p.equity for p in points] + [snap.equity])
    out.peak_equity = peak
    out.drawdown_pct = (peak - snap.equity) / peak * HUNDRED if peak > 0 else None
    if snap.margin is not None:
        out.margin_utilization_pct = snap.margin / snap.equity * HUNDRED
    out.consecutive_losses, out.streak_trade_ids, out.recent_results = loss_streak(closed, snap.equity,
                                                                                    cfg.breakeven_tolerance_pct)
    out.trades_today = opened_today
    return out
