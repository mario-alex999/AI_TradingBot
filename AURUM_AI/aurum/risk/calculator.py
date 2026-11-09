"""Trade-level risk mathematics: value per price unit, stop/target distances, costs, position size and margin estimate.

No universal pip formula is used. Money per price unit per lot comes from the broker's own specification:

* MT5 calculation modes FOREX (0), CFD (2), CFDLEVERAGE (4) and FOREX_NO_LEVERAGE (5) compute profit as
  ``price change x contract size x lots`` in the PROFIT currency. Module 06 uses that formula, converts it to the account
  currency with a fresh rate, and cross-checks it against the broker's tick value when no conversion is involved.
* Any other mode (futures, CFDINDEX, exchange instruments) uses the broker's tick value directly, and only while the
  specification is fresh enough to trust its embedded currency conversion.

Anything that cannot be computed honestly raises CalcError, which the engine turns into an ERROR decision.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Callable

from aurum.risk.money import D, D_or_none, HUNDRED, ZERO, floor_to_step, is_multiple

FORMULA_MODES = {0, 2, 4, 5}
MODE_NAMES = {0: "FOREX", 1: "FUTURES", 2: "CFD", 3: "CFDINDEX", 4: "CFDLEVERAGE", 5: "FOREX_NO_LEVERAGE"}
BUY, SELL = "BUY", "SELL"


class CalcError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


@dataclass(frozen=True)
class InstrumentSpec:
    symbol: str
    asset_class: str
    digits: int | None
    point: Decimal
    tick_size: Decimal
    tick_value: Decimal | None
    tick_value_loss: Decimal | None
    contract_size: Decimal
    volume_min: Decimal
    volume_max: Decimal
    volume_step: Decimal
    currency_base: str | None
    currency_profit: str
    calc_mode: int | None
    stops_level_points: Decimal
    captured_at: datetime
    source: str = "module_01.instrument_specs"

    @classmethod
    def from_row(cls, symbol: str, asset_class: str, row: dict[str, Any]) -> "InstrumentSpec":
        """From a Module 01 ``instrument_specs`` row (plus its ``raw`` MT5 symbol_info). Missing essentials raise CalcError."""
        raw = row.get("raw") or {}
        need = {"point": row.get("point"), "tick_size": row.get("tick_size"), "contract_size": row.get("contract_size"),
                "volume_min": row.get("volume_min"), "volume_max": row.get("volume_max"),
                "volume_step": row.get("volume_step"), "currency_profit": row.get("currency_profit"),
                "captured_at": row.get("captured_at")}
        missing = [k for k, v in need.items() if v in (None, "", 0)]
        if missing:
            raise CalcError("SPEC_INCOMPLETE", f"{symbol}: instrument specification is missing {', '.join(missing)}.")
        mode = raw.get("trade_calc_mode")
        tvl = raw.get("trade_tick_value_loss")
        spec = cls(symbol=symbol, asset_class=asset_class or "unknown", digits=row.get("digits"), point=D(row["point"]),
                   tick_size=D(row["tick_size"]), tick_value=D_or_none(row.get("tick_value")),
                   tick_value_loss=D_or_none(tvl) if tvl not in (None, 0, 0.0) else None,
                   contract_size=D(row["contract_size"]), volume_min=D(row["volume_min"]), volume_max=D(row["volume_max"]),
                   volume_step=D(row["volume_step"]), currency_base=row.get("currency_base"),
                   currency_profit=str(row["currency_profit"]), calc_mode=int(mode) if mode is not None else None,
                   stops_level_points=D(raw.get("trade_stops_level") or 0), captured_at=row["captured_at"])
        spec.validate()
        return spec

    def validate(self) -> None:
        for name in ("point", "tick_size", "contract_size", "volume_min", "volume_max", "volume_step"):
            if getattr(self, name) <= 0:
                raise CalcError("SPEC_INVALID", f"{self.symbol}: {name} must be positive.")
        if self.volume_min > self.volume_max:
            raise CalcError("SPEC_INVALID", f"{self.symbol}: volume_min is above volume_max.")
        if self.tick_value is not None and self.tick_value < 0:
            raise CalcError("SPEC_INVALID", f"{self.symbol}: negative tick value.")


@dataclass(frozen=True)
class Rate:
    """``1 unit of from_ccy = rate units of to_ccy``."""
    from_ccy: str
    to_ccy: str
    rate: Decimal
    at: datetime | None
    source: str


@dataclass(frozen=True)
class Quote:
    bid: Decimal
    ask: Decimal
    at: datetime
    source: str

    @property
    def spread(self) -> Decimal:
        return self.ask - self.bid


@dataclass
class TradeMath:
    """Everything the calculator worked out, in the account currency unless named otherwise."""
    account_currency: str
    value_per_price_unit: Decimal            # money per 1.0 price move per 1 lot
    value_method: str
    conversion: dict[str, Any] | None
    stop_distance: Decimal
    tp_distance: Decimal | None
    spread_price: Decimal | None
    slippage_price: Decimal
    commission_per_lot: Decimal
    loss_per_lot_at_stop: Decimal
    cost_per_lot: Decimal                    # spread + slippage + commission
    total_loss_per_lot: Decimal
    reward_per_lot: Decimal | None
    net_reward_per_lot: Decimal | None
    rr: Decimal | None
    adjusted_rr: Decimal | None
    risk_pct: Decimal
    risk_amount: Decimal                     # permitted money at risk
    raw_volume: Decimal
    recommended_volume: Decimal | None       # None when even the minimum lot exceeds the budget
    capped_by_max_volume: bool
    min_volume_risk_pct: Decimal
    final_volume: Decimal | None             # the volume this assessment covers
    final_volume_source: str
    estimated_loss: Decimal | None           # final_volume x total loss per lot
    estimated_loss_pct: Decimal | None
    estimated_reward: Decimal | None
    transaction_cost: Decimal | None
    notional: Decimal | None
    margin_estimate: Decimal | None
    margin_method: str | None
    notes: list[str] = field(default_factory=list)


def value_per_price_unit(spec: InstrumentSpec, account_ccy: str, now: datetime, max_conv_age: timedelta,
                         rate_fn: Callable[[str, str], Rate | None]) -> tuple[Decimal, str, dict[str, Any] | None, list[str]]:
    notes: list[str] = []
    same = spec.currency_profit == account_ccy
    spec_fresh = now - spec.captured_at <= max_conv_age
    broker_tv = spec.tick_value_loss or spec.tick_value
    if spec.calc_mode in FORMULA_MODES:
        if same:
            vpu = spec.contract_size
            if spec.tick_value:
                expected = spec.tick_size * spec.contract_size
                if abs(spec.tick_value - expected) > expected * D("0.01"):
                    raise CalcError("SPEC_INCONSISTENT",
                                    f"{spec.symbol}: broker tick value {spec.tick_value} does not match tick size x contract size "
                                    f"({expected}) for a {MODE_NAMES.get(spec.calc_mode)} instrument; the specification is not "
                                    "trusted.")
            return vpu, f"contract_size ({MODE_NAMES.get(spec.calc_mode)}, profit currency = account currency)", None, notes
        rate = rate_fn(spec.currency_profit, account_ccy)
        if rate is not None and rate.at is not None and now - rate.at <= max_conv_age and rate.rate > 0:
            conv = {"from": rate.from_ccy, "to": rate.to_ccy, "rate": str(rate.rate), "at": rate.at, "source": rate.source}
            return spec.contract_size * rate.rate, "contract_size x conversion rate", conv, notes
        if broker_tv and spec_fresh:
            notes.append("No fresh conversion quote; the broker's tick value from a recent specification was used.")
            return broker_tv / spec.tick_size, "broker tick value (fresh specification)", {"source": "broker_tick_value",
                                                                                         "at": spec.captured_at}, notes
        raise CalcError("CONVERSION_UNAVAILABLE",
                        f"{spec.symbol}: no fresh {spec.currency_profit}->{account_ccy} rate and no recent broker tick value, so "
                        "the risk cannot be expressed in the account currency.")
    # Calculation modes without a simple formula: only the broker's own tick value is trustworthy.
    if not broker_tv:
        raise CalcError("SPEC_INCOMPLETE", f"{spec.symbol}: calculation mode {spec.calc_mode} needs the broker tick value.")
    if not same and not spec_fresh:
        raise CalcError("CONVERSION_UNAVAILABLE",
                        f"{spec.symbol}: the broker tick value embeds a {spec.currency_profit}->{account_ccy} conversion and the "
                        "specification is too old to trust it.")
    return broker_tv / spec.tick_size, f"broker tick value (mode {spec.calc_mode})", None, notes


def calculate(*, spec: InstrumentSpec, direction: str, entry: Decimal, stop: Decimal, take_profit: Decimal | None,
              account_ccy: str, equity: Decimal, risk_pct: Decimal, max_risk_pct: Decimal, requested_volume: Decimal | None,
              spread_price: Decimal | None, slippage_points: Decimal, commission_per_lot: Decimal, leverage: int | None,
              now: datetime, max_conv_age: timedelta, rate_fn: Callable[[str, str], Rate | None]) -> TradeMath:
    """Position size and money at risk. Assumes the stop and take-profit are already on the correct sides (validation.py)."""
    if equity <= 0:
        raise CalcError("ACCOUNT_INVALID", "Account equity must be positive.")
    if entry <= 0 or stop <= 0 or (take_profit is not None and take_profit <= 0):
        raise CalcError("PRICE_INVALID", "Prices must be positive.")
    if risk_pct <= 0:
        raise CalcError("RISK_INVALID", "Risk percentage must be positive.")
    vpu, method, conv, notes = value_per_price_unit(spec, account_ccy, now, max_conv_age, rate_fn)
    stop_distance = abs(entry - stop)
    if stop_distance == 0:
        raise CalcError("STOP_INVALID", "Stop-loss equals the entry price.")
    tp_distance = abs(take_profit - entry) if take_profit is not None else None
    slippage_price = slippage_points * spec.point
    spread_cost = (spread_price or ZERO) * vpu
    loss_at_stop = stop_distance * vpu
    cost = spread_cost + slippage_price * vpu + commission_per_lot
    total_loss = loss_at_stop + cost
    reward = tp_distance * vpu if tp_distance is not None else None
    net_reward = reward - spread_cost - commission_per_lot if reward is not None else None
    rr = tp_distance / stop_distance if tp_distance is not None else None
    adj = net_reward / total_loss if net_reward is not None else None

    used_pct = min(risk_pct, max_risk_pct)
    if used_pct < risk_pct:
        notes.append(f"Requested risk {risk_pct}% was capped at the maximum {max_risk_pct}%.")
    risk_amount = equity * used_pct / HUNDRED
    raw = risk_amount / total_loss
    capped = raw > spec.volume_max
    vol = floor_to_step(min(raw, spec.volume_max), spec.volume_step)
    recommended = vol if vol >= spec.volume_min else None
    min_risk_pct = spec.volume_min * total_loss / equity * HUNDRED

    final, source = recommended, "recommended"
    if requested_volume is not None:
        final, source = requested_volume, "requested"
    est_loss = final * total_loss if final is not None else None
    notional = margin = margin_method = None
    if final is not None and spec.calc_mode in FORMULA_MODES:
        # In formula modes vpu = contract size x (profit -> account rate), so lots x price x vpu is the notional value.
        notional = final * entry * vpu
        if spec.calc_mode in (0, 4) and leverage:
            margin, margin_method = notional / D(leverage), f"notional / account leverage 1:{leverage} (estimate)"
        elif spec.calc_mode in (2, 5):
            margin, margin_method = notional, "full notional (conservative estimate, no leverage applied)"
    return TradeMath(
        account_currency=account_ccy, value_per_price_unit=vpu, value_method=method, conversion=conv,
        stop_distance=stop_distance, tp_distance=tp_distance, spread_price=spread_price, slippage_price=slippage_price,
        commission_per_lot=commission_per_lot, loss_per_lot_at_stop=loss_at_stop, cost_per_lot=cost,
        total_loss_per_lot=total_loss, reward_per_lot=reward, net_reward_per_lot=net_reward, rr=rr, adjusted_rr=adj,
        risk_pct=used_pct, risk_amount=risk_amount, raw_volume=raw, recommended_volume=recommended, capped_by_max_volume=capped,
        min_volume_risk_pct=min_risk_pct, final_volume=final, final_volume_source=source, estimated_loss=est_loss,
        estimated_loss_pct=est_loss / equity * HUNDRED if est_loss is not None else None,
        estimated_reward=final * net_reward if final is not None and net_reward is not None else None,
        transaction_cost=final * cost if final is not None else None, notional=notional, margin_estimate=margin,
        margin_method=margin_method, notes=notes)


def volume_problems(spec: InstrumentSpec, volume: Decimal) -> list[str]:
    out = []
    if volume <= 0:
        out.append("Volume must be positive.")
    if volume < spec.volume_min:
        out.append(f"Volume {volume} is below the broker minimum {spec.volume_min}.")
    if volume > spec.volume_max:
        out.append(f"Volume {volume} is above the broker maximum {spec.volume_max}.")
    if not is_multiple(volume, spec.volume_step):
        out.append(f"Volume {volume} is not a multiple of the lot step {spec.volume_step}.")
    return out
