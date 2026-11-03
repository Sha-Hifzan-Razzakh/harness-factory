"""Money helpers: amounts are Decimals in a currency's main unit, rounded to 2 places (fils, cents)."""

from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")


def D(x) -> Decimal:
    """A Decimal from an int, a str or a float (floats go through str, so 0.1 stays 0.1)."""
    return x if isinstance(x, Decimal) else Decimal(str(x))


def round_money(x) -> Decimal:
    """Round to 2 places, halves away from zero."""
    return D(x).quantize(CENT, rounding=ROUND_HALF_UP)


def fmt(x) -> str:
    """1234.5 -> '1,234.50'."""
    return f"{round_money(x):,.2f}"
