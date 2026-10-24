"""Causal indicator primitives; edge conventions are explicit, not TDX parity claims."""

from decimal import Decimal
from typing import Sequence


def _period(period: int, *, allow_zero: bool = False) -> None:
    if type(period) is not int or period < (0 if allow_zero else 1):
        raise ValueError("period must be an integer in the supported range")


def ma(values: Sequence[Decimal], period: int) -> list[Decimal | None]:
    """Simple mean; no value until a complete window is available."""
    _period(period)
    result: list[Decimal | None] = []
    total = Decimal(0)
    for i, value in enumerate(values):
        if not isinstance(value, Decimal) or not value.is_finite():
            raise ValueError("ma values must be finite Decimals")
        total += value
        if i >= period:
            total -= values[i - period]
        result.append(total / period if i >= period - 1 else None)
    return result


def ref(values: Sequence[Decimal | None], period: int = 1) -> list[Decimal | None]:
    """Shift into the past, padding missing observations with None."""
    _period(period, allow_zero=True)
    return [values[i - period] if i >= period else None for i in range(len(values))]


def cross(left: Sequence[Decimal | None], right: Sequence[Decimal | None]) -> list[bool]:
    """True when current left > right and prior left <= right; missing = False."""
    if len(left) != len(right):
        raise ValueError("cross inputs must have the same length")
    result = [False] * len(left)
    for i in range(1, len(left)):
        a0, b0, a1, b1 = left[i - 1], right[i - 1], left[i], right[i]
        if all(v is not None for v in (a0, b0, a1, b1)):
            result[i] = a0 <= b0 and a1 > b1  # type: ignore[operator]
    return result

