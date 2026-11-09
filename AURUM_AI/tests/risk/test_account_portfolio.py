"""Calendar, account monitor and portfolio/correlation units (pure functions, no database)."""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from aurum.risk.account import BalanceOperation, ClosedTrade, EquityPoint, Snapshot, evaluate_account, loss_streak
from aurum.risk.calendar import TradingCalendar
from aurum.risk.config import RiskConfig, TradingDay
from aurum.risk.money import D
from aurum.risk.portfolio import ExposureItem, Portfolio, Position, correlation, currency_exposure, position_risk, same_way

UTC = timezone.utc
NOW = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
CFG = RiskConfig()
CAL = TradingCalendar(TradingDay())


def snap(equity="10000", balance="10000", at=NOW, mode="DEMO", margin="0") -> Snapshot:
    return Snapshot(at, 1, "USD", mode, 100, D(balance), D(equity), D(margin), D(equity) - D(margin), None, D(equity) - D(balance))


# ---- calendar ----------------------------------------------------------------------------------------------------------
def test_trading_day_starts_17_new_york_across_dst():
    summer = CAL.day(datetime(2026, 7, 15, 12, tzinfo=UTC))
    winter = CAL.day(datetime(2026, 12, 15, 12, tzinfo=UTC))
    assert summer.start == datetime(2026, 7, 14, 21, 0, tzinfo=UTC)     # 17:00 EDT
    assert winter.start == datetime(2026, 12, 14, 22, 0, tzinfo=UTC)    # 17:00 EST


def test_dst_change_week_has_25_hour_day():
    # New York leaves daylight time on Sunday 2026-11-01.
    d = CAL.day(datetime(2026, 11, 2, 12, tzinfo=UTC))
    assert d.start == datetime(2026, 11, 1, 22, 0, tzinfo=UTC)
    prev = CAL.day(datetime(2026, 10, 31, 12, tzinfo=UTC))
    assert prev.start == datetime(2026, 10, 30, 21, 0, tzinfo=UTC)


def test_after_rollover_belongs_to_next_trading_day():
    assert CAL.trading_date(datetime(2026, 11, 4, 22, 30, tzinfo=UTC)).isoformat() == "2026-11-05"
    assert CAL.trading_date(datetime(2026, 11, 4, 21, 59, tzinfo=UTC)).isoformat() == "2026-11-04"


def test_week_and_month_boundaries():
    w = CAL.week(NOW)
    assert w.start == datetime(2026, 11, 1, 22, 0, tzinfo=UTC) and w.label == "2026-W45"
    m = CAL.month(NOW)
    assert m.start == datetime(2026, 10, 31, 21, 0, tzinfo=UTC) and m.label == "2026-11"


def test_naive_time_rejected():
    with pytest.raises(ValueError):
        CAL.day(datetime(2026, 11, 4, 12))


def test_utc_midnight_calendar_supported():
    cal = TradingCalendar(TradingDay(timezone="UTC", rollover="00:00"))
    assert cal.day(NOW).start == datetime(2026, 11, 4, tzinfo=UTC)


# ---- account ------------------------------------------------------------------------------------------------------------
def history(equity="10000"):
    pts, d = [], CAL.trading_date(CAL.month(NOW).start) - timedelta(days=1)
    while d <= CAL.trading_date(NOW):
        pts.append(EquityPoint(CAL.day_start(d), D(equity), D(equity)))
        d += timedelta(days=1)
    return pts


def test_missing_snapshot_is_reported():
    a = evaluate_account(CFG, CAL, NOW, None, [], [], [], 0)
    assert a.status == "missing" and not a.usable


def test_stale_snapshot():
    a = evaluate_account(CFG, CAL, NOW, snap(at=NOW - timedelta(minutes=30)), history(), [], [], 0)
    assert a.status == "stale"


@pytest.mark.parametrize("mode", ["REAL", "CONTEST"])
def test_non_demo_account_invalid(mode):
    assert evaluate_account(CFG, CAL, NOW, snap(mode=mode), history(), [], [], 0).status == "invalid"


def test_drawdown_and_daily_loss_by_hand():
    pts = history() + [EquityPoint(NOW - timedelta(days=10), D(12000), D(12000))]
    a = evaluate_account(CFG, CAL, NOW, snap(equity="9600", balance="9900"), pts, [], [], 0)
    assert a.peak_equity == D(12000)
    assert a.drawdown_pct == D(2400) / D(12000) * 100                  # 20%
    assert a.periods["day"].loss_pct == D(400) / D(10000) * 100        # 4%, equity basis includes floating loss


def test_balance_basis_ignores_floating():
    cfg = RiskConfig(loss_basis="balance")
    a = evaluate_account(cfg, CAL, NOW, snap(equity="9600", balance="9900"), history(), [], [], 0)
    assert a.periods["day"].loss_pct == D(1)


def test_withdrawal_is_not_a_loss_and_deposit_not_a_gain():
    ops = [BalanceOperation(NOW - timedelta(hours=1), D(-2000))]
    a = evaluate_account(CFG, CAL, NOW, snap(equity="8000", balance="8000"), history(), [], ops, 0)
    assert a.periods["day"].loss_pct == 0
    ops = [BalanceOperation(NOW - timedelta(hours=1), D(5000))]
    a = evaluate_account(CFG, CAL, NOW, snap(equity="14700", balance="15000"), history(), [], ops, 0)
    assert a.periods["day"].loss_pct == D(300) / D(10000) * 100


def test_profitable_closed_trade_does_not_hide_floating_loss():
    closed = [ClosedTrade("1", "EURUSD", NOW - timedelta(hours=3), NOW - timedelta(hours=2), D(150))]
    a = evaluate_account(CFG, CAL, NOW, snap(equity="9650", balance="10150"), history(), closed, [], 0)
    assert a.periods["day"].realized_pnl == D(150)
    assert a.periods["day"].loss_pct == D("3.5")


def test_weekly_and_monthly_losses():
    pts = history()
    pts = [EquityPoint(p.at, D(10500) if p.at <= CAL.week(NOW).start else p.equity, p.balance) for p in pts]
    a = evaluate_account(CFG, CAL, NOW, snap(equity="9800"), pts, [], [], 0)
    assert a.periods["week"].loss_pct == D(700) / D(10500) * 100
    assert a.periods["month"].loss_pct == D(700) / D(10500) * 100


def test_period_start_uncertain_without_boundary_snapshot():
    pts = [EquityPoint(NOW - timedelta(hours=2), D(10000), D(10000))]
    a = evaluate_account(CFG, CAL, NOW, snap(), pts, [], [], 0)
    assert a.periods["day"].start_uncertain


def test_deposit_before_first_snapshot_is_not_a_loss():
    # Regression (live DEMO account): 500 deposited on day 2, first snapshot on day 8. The deposit is already inside the
    # start value, so the month must show 0% loss, not 100%.
    first = NOW - timedelta(minutes=5)
    ops = [BalanceOperation(CAL.month(NOW).start + timedelta(days=1), D(500))]
    a = evaluate_account(CFG, CAL, NOW, snap(equity="500", balance="500", at=first), [], [], ops, 0)
    m = a.periods["month"]
    assert m.start_uncertain and m.start_value == D(500)
    assert m.net_deposits == 0 and m.loss_pct == 0


def test_deposit_after_start_snapshot_still_counts():
    first = NOW - timedelta(hours=3)
    ops = [BalanceOperation(NOW - timedelta(hours=1), D(500))]
    a = evaluate_account(CFG, CAL, NOW, snap(equity="1400", balance="1400", at=NOW), [EquityPoint(first, D(1000), D(1000))], [], ops, 0)
    m = a.periods["month"]
    assert m.net_deposits == D(500) and m.loss == D(100) and m.loss_pct == D(10)


def ct(i, pnl, minutes):
    t = NOW - timedelta(minutes=minutes)
    return ClosedTrade(str(i), "EURUSD", t - timedelta(minutes=5), t, D(pnl))


def test_loss_streak_counts_losses_until_a_win():
    n, ids, _ = loss_streak([ct(1, 50, 50), ct(2, -40, 40), ct(3, -30, 30), ct(4, -20, 20)], D(10000), 0.05)
    assert n == 3 and ids == ["4", "3", "2"]


def test_breakeven_is_not_a_loss_and_does_not_reset():
    n, ids, res = loss_streak([ct(1, -40, 40), ct(2, 3, 30), ct(3, -30, 20)], D(10000), 0.05)   # 3 <= 0.05% of 10000 = 5
    assert n == 2 and [r["result"] for r in res] == ["loss", "breakeven", "loss"]


def test_duplicate_broker_records_not_double_counted():
    t = ct(9, -40, 10)
    assert loss_streak([t, t, t], D(10000), 0.05)[0] == 1


# ---- portfolio -----------------------------------------------------------------------------------------------------------
def pos(side="BUY", sl="1.09800", open_="1.10000"):
    return Position("p1", "EURUSD", side, D("0.5"), D(open_), D(sl) if sl else None, None, NOW, None, "forex")


def test_position_risk_and_unbounded():
    assert position_risk(pos(), D(100000))[0] == D("0.00200") * 100000 * D("0.5")
    assert position_risk(pos(sl=None), D(100000))[0] is None
    assert position_risk(pos(sl="1.10100"), D(100000))[0] == 0          # stop already in profit


def test_portfolio_groups_and_pct():
    pf = Portfolio(items=[ExposureItem("position", "1", "EURUSD", "BUY", "forex", "a", D(100)),
                          ExposureItem("reservation", "2", "XAUUSD", "SELL", "metal", "b", D(50))], equity=D(10000))
    assert pf.total() == D(150) and pf.pct(pf.total()) == D("1.5")
    assert pf.group(lambda i: i.asset_class) == {"forex": D(100), "metal": D(50)}


def test_currency_exposure_signs():
    out = currency_exposure([("EURUSD", "BUY", D(110000), "EUR", "USD"), ("XAUUSD", "SELL", D(265000), "XAU", "USD")])
    assert out == {"EUR": D(110000), "USD": D(-110000) + D(265000), "XAU": D(-265000)}


def series(values, start=NOW - timedelta(hours=600)):
    idx = pd.date_range(start=start, periods=len(values), freq="h", tz="UTC")
    return pd.Series(values, index=idx)


def test_pearson_matches_numpy():
    rng = np.random.default_rng(1)
    z = rng.normal(size=(300, 2))
    a = 1.1 * np.exp(np.cumsum(0.001 * z[:, 0]))
    b = 1.3 * np.exp(np.cumsum(0.001 * (0.8 * z[:, 0] + 0.6 * z[:, 1])))
    sa, sb = series(a, NOW - timedelta(hours=300)), series(b, NOW - timedelta(hours=300))
    r = correlation("A", "B", sa, sb, "H1", 500, 120, timedelta(hours=72), NOW)
    expected = np.corrcoef(np.diff(np.log(a)), np.diff(np.log(b)))[0, 1]
    assert r.reliable and math.isclose(r.coefficient, round(expected, 4), abs_tol=1e-4) and r.samples == 299


def test_correlation_insufficient_and_stale_and_missing():
    short = series(np.linspace(1, 2, 50), NOW - timedelta(hours=50))
    assert not correlation("A", "B", short, short * 2, "H1", 500, 120, timedelta(hours=72), NOW).reliable
    old = series(1 + np.random.default_rng(2).random(300), NOW - timedelta(days=60))
    r = correlation("A", "B", old, old * 1.5, "H1", 500, 120, timedelta(hours=72), NOW)
    assert not r.reliable and "old" in r.reason
    assert correlation("A", "B", None, old, "H1", 500, 120, timedelta(hours=72), NOW).reason == "Price history unavailable."


def test_correlation_only_on_common_timestamps():
    a = series(1 + 0.01 * np.random.default_rng(3).random(300), NOW - timedelta(hours=300))
    b = a.iloc[::2] * 3               # every other bar missing on one side
    r = correlation("A", "B", a, b, "H1", 500, 100, timedelta(hours=72), NOW)
    assert r.samples == len(b) - 1


def test_same_way():
    assert same_way("BUY", "BUY", 0.8) and same_way("BUY", "SELL", -0.8)
    assert not same_way("BUY", "SELL", 0.8)
