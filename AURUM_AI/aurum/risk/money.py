"""Decimal helpers. Every monetary and sizing calculation in Module 06 runs on Decimal, never on binary floats."""
from __future__ import annotations

from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

ZERO = Decimal("0")
HUNDRED = Decimal("100")


class MoneyError(ValueError):
    """An input that cannot be a valid financial number (NaN, infinity, text)."""


def D(value: Any) -> Decimal:
    """Exact Decimal from int/str/Decimal; floats go through ``repr`` so 0.1 becomes Decimal('0.1'), not its binary
    expansion. NaN and infinity are rejected."""
    if isinstance(value, Decimal):
        out = value
    elif isinstance(value, bool):
        raise MoneyError("A boolean is not a number.")
    elif isinstance(value, (int, str)):
        try:
            out = Decimal(value)
        except InvalidOperation as exc:
            raise MoneyError(f"Not a number: {value!r}") from exc
    elif isinstance(value, float):
        out = Decimal(repr(value))
    else:
        raise MoneyError(f"Not a number: {value!r}")
    if not out.is_finite():
        raise MoneyError(f"Not a finite number: {value!r}")
    return out


def D_or_none(value: Any) -> Decimal | None:
    return None if value is None else D(value)


def floor_to_step(value: Decimal, step: Decimal) -> Decimal:
    """Largest multiple of ``step`` that is <= value. Volume is ALWAYS rounded down, never up."""
    if step <= 0:
        raise MoneyError("Step must be positive.")
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


def is_multiple(value: Decimal, step: Decimal) -> bool:
    return step > 0 and (value / step) == (value / step).to_integral_value()


def money(value: Decimal, places: str = "0.01") -> Decimal:
    """Rounded for display and storage. Calculations keep full precision."""
    return value.quantize(Decimal(places), rounding=ROUND_HALF_UP)


def pct(part: Decimal, whole: Decimal) -> Decimal | None:
    return None if whole <= 0 else part / whole * HUNDRED


def fnum(value: Decimal | None, places: int = 8) -> float | None:
    """Decimal -> float for JSON responses only, after the calculation is finished."""
    return None if value is None else float(round(value, places))
