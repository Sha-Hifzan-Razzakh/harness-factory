"""VAT on amounts that exclude it."""

from ledger import config
from ledger.money import D, round_money


def vat(net) -> "Decimal":
    """VAT due on a net amount, rounded to the fils."""
    return round_money(D(net) * D(config.VAT_RATE))


def with_vat(net) -> "Decimal":
    return round_money(D(net)) + vat(net)
