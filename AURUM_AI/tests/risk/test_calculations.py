"""Unit and instrument-specific tests of the Module 06 money maths. Expected values are worked out by hand in each test, not
copied from the code, so the formulas are checked independently."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from aurum.risk.calculator import CalcError, InstrumentSpec, Rate, calculate, value_per_price_unit, volume_problems
from aurum.risk.money import D, MoneyError, floor_to_step, is_multiple
from aurum.risk.simulation import SAMPLE_SPECS

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)
FRESH = timedelta(minutes=30)


def spec(symbol: str, **over) -> InstrumentSpec:
    row = {**SAMPLE_SPECS[symbol], "captured_at": NOW, **over}
    if "raw" in over:
        row["raw"] = {**SAMPLE_SPECS[symbol]["raw"], **over["raw"]}
    return InstrumentSpec.from_row(symbol, row["asset_class"], row)


def no_rate(a, b):
    return None


def calc(symbol: str, direction: str, entry: str, stop: str, tp: str | None, *, equity: str = "10000", risk: str = "1",
         max_risk: str = "2", spread: str | None = None, slip: str = "0", commission: str = "0", volume: str | None = None,
         ccy: str = "USD", rate_fn=no_rate, leverage: int | None = 100, s: InstrumentSpec | None = None):
    return calculate(spec=s or spec(symbol), direction=direction, entry=D(entry), stop=D(stop), take_profit=D(tp) if tp else None,
                     account_ccy=ccy, equity=D(equity), risk_pct=D(risk), max_risk_pct=D(max_risk), requested_volume=D(volume) if volume else None,
                     spread_price=D(spread) if spread else None, slippage_points=D(slip), commission_per_lot=D(commission),
                     leverage=leverage, now=NOW, max_conv_age=FRESH, rate_fn=rate_fn)


# ---- Decimal utilities -----------------------------------------------------------------------------------------------------
def test_decimal_from_float_is_exact_decimal_text():
    assert D(0.1) == Decimal("0.1")
    assert D(1.10010) + D(0.0002) == Decimal("1.10030")


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), "abc", True, None])
def test_decimal_rejects_non_numbers(bad):
    with pytest.raises(MoneyError):
        D(bad)


@pytest.mark.parametrize("value,step,expected", [("0.4699", "0.01", "0.46"), ("0.46", "0.01", "0.46"), ("1.999999", "0.01", "1.99"),
                                                 ("0.0099", "0.01", "0.00"), ("12.37", "0.1", "12.3")])
def test_volume_always_rounds_down(value, step, expected):
    assert floor_to_step(D(value), D(step)) == D(expected)


def test_is_multiple():
    assert is_multiple(D("1.10010"), D("0.00001"))
    assert not is_multiple(D("1.100105"), D("0.00001"))


# ---- EURUSD (USD account, profit currency = account currency) ---------------------------------------------------------------
def test_eurusd_buy_position_size_by_hand():
    # 20-pip stop: 0.00200 x 100000 = 200 USD per lot. 1% of 10000 = 100 USD -> 0.5 lots exactly.
    m = calc("EURUSD", "BUY", "1.10010", "1.09810", "1.10510")
    assert m.value_per_price_unit == D(100000)
    assert m.loss_per_lot_at_stop == D("200.00000")
    assert m.risk_amount == D(100)
    assert m.recommended_volume == D("0.50")
    assert m.estimated_loss == D("100.0000000")
    assert m.rr == D("2.5")


def test_eurusd_costs_reduce_volume_and_rr():
    # Loss/lot = 200 at stop + 12 spread (0.00012 x 100000) + 5 slippage (5 points) + 7 commission = 224.
    m = calc("EURUSD", "BUY", "1.10010", "1.09810", "1.10510", spread="0.00012", slip="5", commission="7")
    assert m.total_loss_per_lot == D(224)
    assert m.recommended_volume == D("0.44")            # 100 / 224 = 0.446 -> 0.44, never 0.45
    assert m.estimated_loss == D("98.56")
    # Net reward/lot = 500 - 12 - 7 = 481; adjusted R:R = 481 / 224
    assert m.adjusted_rr == D(481) / D(224)
    assert m.estimated_loss <= m.risk_amount


def test_gbpusd_sell_by_hand():
    # 30-pip stop: 0.00300 x 100000 = 300/lot; 1% of 25000 = 250 -> 0.8333 -> 0.83 lots, risk 249.
    m = calc("GBPUSD", "SELL", "1.30000", "1.30300", "1.29400", equity="25000")
    assert m.recommended_volume == D("0.83")
    assert m.estimated_loss == D("249.0000")


def test_xauusd_uses_its_own_contract_size():
    # 6.000 stop x 100 oz = 600 USD per lot; 1% of 10000 = 100 -> 0.1666 -> 0.16 lots, risk 96.
    m = calc("XAUUSD", "SELL", "2650.000", "2656.000", "2638.000")
    assert m.value_per_price_unit == D(100)
    assert m.recommended_volume == D("0.16")
    assert m.estimated_loss == D("96.00")


def test_us30_cfdleverage_contract_size_one():
    # 100-point stop x contract size 1 = 100 USD/lot; 1% of 10000 -> 1.00 lot.
    m = calc("US30", "BUY", "44000.0", "43900.0", "44250.0")
    assert m.value_method.startswith("contract_size (CFDLEVERAGE")
    assert m.recommended_volume == D("1.00")
    assert m.margin_estimate == D("44000.0") * D(1) / D(100)


def test_supplied_metadata_is_used_not_assumed():
    # A broker with a 10-oz gold contract: same prices, ten times the volume.
    s = spec("XAUUSD", contract_size=10, tick_value=0.01)
    m = calc("XAUUSD", "SELL", "2650.000", "2656.000", "2638.000", s=s)
    assert m.recommended_volume == D("1.66")


def test_minimum_lot_above_budget_is_rejected_not_increased():
    # US30 min lot 0.05; a 3000-point stop costs 3000/lot; 1% of 1000 = 10 -> raw 0.0033 lots < 0.05.
    m = calc("US30", "BUY", "44000.0", "41000.0", "50000.0", equity="1000")
    assert m.recommended_volume is None
    assert m.min_volume_risk_pct == D("0.05") * D(3000) / D(1000) * 100   # 15% of equity would be at risk


def test_volume_capped_at_broker_maximum():
    m = calc("EURUSD", "BUY", "1.10010", "1.10000", "1.10510", equity="100000000")
    assert m.capped_by_max_volume and m.recommended_volume == D(200)


def test_requested_risk_is_capped_at_maximum():
    m = calc("EURUSD", "BUY", "1.10010", "1.09810", "1.10510", risk="5", max_risk="2")
    assert m.risk_pct == D(2) and m.recommended_volume == D("1.00")


def test_volume_problems():
    s = spec("US30")
    assert volume_problems(s, D("0.04"))
    assert volume_problems(s, D("0.055"))
    assert volume_problems(s, D("501"))
    assert volume_problems(s, D("0.06")) == []


# ---- cross currency -------------------------------------------------------------------------------------------------------
def test_gbp_account_converts_usd_profit_with_fresh_rate():
    # 1 USD = 0.75 GBP. EURUSD 20-pip stop = 200 USD/lot = 150 GBP/lot; 1% of 10000 GBP = 100 -> 0.66 lots.
    rate = lambda a, b: Rate("USD", "GBP", D("0.75"), NOW, "test")  # noqa: E731
    m = calc("EURUSD", "BUY", "1.10010", "1.09810", "1.10510", ccy="GBP", rate_fn=rate)
    assert m.value_per_price_unit == D("75000.00")
    assert m.recommended_volume == D("0.66")
    assert m.conversion["rate"] == "0.75"


def test_stale_conversion_falls_back_to_fresh_broker_tick_value_only():
    stale = lambda a, b: Rate("USD", "GBP", D("0.75"), NOW - timedelta(hours=5), "old")  # noqa: E731
    s = spec("EURUSD", tick_value=0.75)          # broker tick value already in GBP, spec captured now
    m = calc("EURUSD", "BUY", "1.10010", "1.09810", "1.10510", ccy="GBP", rate_fn=stale, s=s)
    assert m.value_method.startswith("broker tick value")
    old_spec = spec("EURUSD", tick_value=0.75, captured_at=NOW - timedelta(hours=5))
    with pytest.raises(CalcError) as exc:
        calc("EURUSD", "BUY", "1.10010", "1.09810", "1.10510", ccy="GBP", rate_fn=stale, s=old_spec)
    assert exc.value.code == "CONVERSION_UNAVAILABLE"


def test_conversion_unavailable_is_an_error_not_a_guess():
    s = spec("EURUSD", tick_value=None, captured_at=NOW - timedelta(days=1))
    with pytest.raises(CalcError) as exc:
        calc("EURUSD", "BUY", "1.10010", "1.09810", "1.10510", ccy="JPY", s=s)
    assert exc.value.code == "CONVERSION_UNAVAILABLE"


def test_inconsistent_broker_spec_is_not_trusted():
    with pytest.raises(CalcError) as exc:
        value_per_price_unit(spec("EURUSD", tick_value=10.0), "USD", NOW, FRESH, no_rate)
    assert exc.value.code == "SPEC_INCONSISTENT"


def test_unsupported_calc_mode_uses_broker_tick_value():
    s = spec("US30", raw={"trade_calc_mode": 3})
    vpu, method, _, _ = value_per_price_unit(s, "USD", NOW, FRESH, no_rate)
    assert vpu == D("0.1") / D("0.1") and "broker tick value" in method


@pytest.mark.parametrize("field", ["tick_size", "contract_size", "volume_step", "currency_profit"])
def test_incomplete_spec_raises(field):
    with pytest.raises(CalcError):
        spec("EURUSD", **{field: None})


def test_stop_equal_to_entry_is_an_error():
    with pytest.raises(CalcError):
        calc("EURUSD", "BUY", "1.10010", "1.10010", "1.10510")


def test_no_rounding_up_across_many_inputs():
    """Property check: for 2,000 deterministic pseudo-random trades the sized loss never exceeds the budget."""
    import random
    rnd = random.Random(42)
    for _ in range(2000):
        sym = rnd.choice(["EURUSD", "GBPUSD", "XAUUSD", "US30"])
        s = spec(sym)
        entry = {"EURUSD": D("1.10000"), "GBPUSD": D("1.30000"), "XAUUSD": D("2650.000"), "US30": D("44000.0")}[sym]
        ticks = rnd.randint(5, 5000)
        stop = entry - ticks * s.tick_size * (100 if sym in ("XAUUSD",) else 1)
        equity = D(rnd.randint(500, 500000))
        risk = D(rnd.choice(["0.25", "0.5", "1", "1.5", "2"]))
        m = calculate(spec=s, direction="BUY", entry=entry, stop=stop, take_profit=None, account_ccy="USD", equity=equity,
                      risk_pct=risk, max_risk_pct=D(2), requested_volume=None, spread_price=D(rnd.randint(0, 50)) * s.point,
                      slippage_points=D(rnd.randint(0, 20)), commission_per_lot=D(rnd.randint(0, 10)), leverage=100, now=NOW,
                      max_conv_age=FRESH, rate_fn=no_rate)
        if m.recommended_volume is not None:
            assert m.recommended_volume * m.total_loss_per_lot <= m.risk_amount
            assert is_multiple(m.recommended_volume, s.volume_step)
