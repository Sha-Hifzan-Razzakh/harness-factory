"""Bulk discounts. These are commercial terms, unrelated to VAT."""

from ledger.money import D, round_money

BULK_MIN_ITEMS = 10              # orders with at least this many items get the bulk rate
BULK_RATE = 0.05


def bulk_discount(subtotal, items: int):
    """The discount on an order: BULK_RATE of the subtotal for BULK_MIN_ITEMS items or more, otherwise 0."""
    if items >= BULK_MIN_ITEMS:
        return round_money(D(subtotal) * D(BULK_RATE))
    return round_money(0)
