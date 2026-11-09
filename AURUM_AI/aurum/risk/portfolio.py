"""Portfolio exposure, concentration and correlation.

Open risk of a position = adverse distance from its open price to its stop x money per price unit x volume (0 once the
stop locks in profit). A position with no stop-loss has UNBOUNDED risk, which is reported, never treated as zero.
Reserved risk = the estimated loss of unexpired PASS/REVIEW assessments, so two assessments made seconds apart cannot
both spend the same budget.

Correlation: Pearson correlation of log returns of closed candles on ONE timeframe, joined on identical bar timestamps
(no resampling; bars missing on either side are dropped). It is reported with its sample count, window and freshness and
is never inferred from instrument names. Correlation describes past co-movement only; it is not a forecast.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Callable, Iterable

import pandas as pd

from aurum.risk.money import HUNDRED, ZERO


@dataclass(frozen=True)
class Position:
    id: str
    symbol: str
    side: str                  # BUY | SELL
    volume: Decimal
    open_price: Decimal
    stop_loss: Decimal | None
    take_profit: Decimal | None
    open_time: datetime | None
    strategy: str | None
    asset_class: str


@dataclass(frozen=True)
class Reservation:
    assessment_id: str
    symbol: str
    side: str
    asset_class: str
    strategy: str | None
    risk: Decimal
    expires_at: datetime


@dataclass
class ExposureItem:
    kind: str                  # position | reservation | proposed
    ref: str
    symbol: str
    side: str
    asset_class: str
    strategy: str | None
    risk: Decimal | None       # None = unbounded (no stop) or not computable
    risk_note: str | None = None
    notional: Decimal | None = None


@dataclass
class CorrelationResult:
    a: str
    b: str
    coefficient: float | None
    samples: int
    lookback_bars: int
    timeframe: str
    last_common_bar: datetime | None
    reliable: bool
    reason: str | None


@dataclass
class Portfolio:
    items: list[ExposureItem] = field(default_factory=list)
    equity: Decimal | None = None

    def bounded(self) -> bool:
        return all(i.risk is not None for i in self.items)

    def unbounded(self) -> list[ExposureItem]:
        return [i for i in self.items if i.risk is None]

    def total(self, where: Callable[[ExposureItem], bool] = lambda i: True) -> Decimal:
        return sum((i.risk for i in self.items if where(i) and i.risk is not None), ZERO)

    def pct(self, amount: Decimal) -> Decimal | None:
        return amount / self.equity * HUNDRED if self.equity and self.equity > 0 else None

    def group(self, key: Callable[[ExposureItem], str | None]) -> dict[str, Decimal]:
        out: dict[str, Decimal] = {}
        for i in self.items:
            k = key(i) or "unknown"
            out[k] = out.get(k, ZERO) + (i.risk or ZERO)
        return out

    def positions_count(self) -> int:
        return sum(1 for i in self.items if i.kind in ("position", "reservation"))


def position_risk(p: Position, value_per_price_unit: Decimal | None) -> tuple[Decimal | None, str | None]:
    if p.stop_loss is None or p.stop_loss <= 0:
        return None, "No stop-loss: the loss is unbounded."
    if value_per_price_unit is None:
        return None, "Instrument value per price unit is unavailable."
    adverse = p.open_price - p.stop_loss if p.side == "BUY" else p.stop_loss - p.open_price
    return max(ZERO, adverse) * value_per_price_unit * p.volume, None


def currency_exposure(items: Iterable[tuple[str, str, Decimal | None, str | None, str | None]]) -> dict[str, Decimal]:
    """Net notional by currency (account currency). Each item: (symbol, side, notional, base_ccy, profit_ccy). A BUY of
    EURUSD is long EUR and short USD; an instrument whose base is not a currency (XAU, an index) counts under its symbol."""
    out: dict[str, Decimal] = {}
    for symbol, side, notional, base, profit in items:
        if notional is None:
            continue
        sign = 1 if side == "BUY" else -1
        long_leg = base if base and base != profit else symbol
        out[long_leg] = out.get(long_leg, ZERO) + sign * notional
        if profit:
            out[profit] = out.get(profit, ZERO) - sign * notional
    return out


def correlation(a: str, b: str, series_a: pd.Series | None, series_b: pd.Series | None, timeframe: str, lookback: int,
                min_samples: int, max_age: timedelta, now: datetime) -> CorrelationResult:
    """Pearson correlation of log returns of two close series (indexed by UTC bar time)."""
    if a == b:
        return CorrelationResult(a, b, 1.0, 0, lookback, timeframe, None, True, "Same instrument.")
    if series_a is None or series_b is None or series_a.empty or series_b.empty:
        return CorrelationResult(a, b, None, 0, lookback, timeframe, None, False, "Price history unavailable.")
    joined = pd.concat([series_a.rename("a"), series_b.rename("b")], axis=1, join="inner").dropna()
    joined = joined[(joined["a"] > 0) & (joined["b"] > 0)].sort_index().tail(lookback + 1)
    if len(joined) < 3:
        return CorrelationResult(a, b, None, max(0, len(joined) - 1), lookback, timeframe, None, False,
                                 "Too few bars with a common timestamp.")
    rets = joined.apply(lambda s: s.apply(math.log)).diff().dropna()
    last = joined.index[-1].to_pydatetime()
    samples = len(rets)
    if samples < min_samples:
        return CorrelationResult(a, b, None, samples, lookback, timeframe, last, False,
                                 f"{samples} common returns, below the minimum of {min_samples}.")
    coef = rets["a"].corr(rets["b"])
    if coef is None or math.isnan(coef):
        return CorrelationResult(a, b, None, samples, lookback, timeframe, last, False, "Correlation is undefined (flat prices).")
    if now - last > max_age:
        return CorrelationResult(a, b, round(float(coef), 4), samples, lookback, timeframe, last, False,
                                 f"Newest common bar is {(now - last).total_seconds() / 3600:.0f} h old.")
    return CorrelationResult(a, b, round(float(coef), 4), samples, lookback, timeframe, last, True, None)


def same_way(side_a: str, side_b: str, coef: float) -> bool:
    """True when two positions lose money together: same side and positive correlation, or opposite sides and negative."""
    return (side_a == side_b) == (coef > 0)
