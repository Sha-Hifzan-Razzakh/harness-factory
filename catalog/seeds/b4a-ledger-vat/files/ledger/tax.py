"""VAT on amounts that exclude it."""

from decimal import Decimal

from ledger.money import D, round_money

VAT_RATE = Decimal("0.05")


def vat(net) -> "Decimal":
    """VAT due on a net amount, rounded to the fils."""
    return round_money(D(net) * VAT_RATE)


def with_vat(net) -> "Decimal":
    return round_money(D(net)) + vat(net)
